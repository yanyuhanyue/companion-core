"""Native authority guard for the common leased-item dispatch lifecycle."""

from dataclasses import replace
from datetime import datetime

import httpx

from companion_delivery_protocol.authority import validate_authority_ref

from . import account_actions, journal, reply_segments
from .event_normalizer import decode
from .network import checked_http_client, private_request_matches
from .onebot_client import OneBotClient, bounded_post
from .window import seal_protocol_content


class OwnerSendGuard:
    def __init__(self, runtime, core_client):
        self.runtime, self.client = runtime, core_client
        self.config, self.db = runtime.config, runtime.db
        self.authority = dict(runtime.authority)
        self.current_item = None
        self.dispatched = False
        self.prepared_message = self.prepared_voice = self.prepared_account = None
        self.prepared_native = None
        self.item_hash = None
        self.delivery_binding = None
        self.lease_until = None

    def reserve(self, item):
        from .owner_native_resolution import binding

        self.runtime.verify()
        if self.current_item is not None or validate_authority_ref(
            item.get("authority")
        ) != self.authority:
            raise ValueError("OWNER_DELIVERY_BINDING_CHANGED")
        if self.db.execute("SELECT 1 FROM window_attempts WHERE message_id=?",
                           (item["message_id"],)).fetchone():
            raise ValueError("DELIVERY_ALREADY_ATTEMPTED")
        lease_until = datetime.fromisoformat(item["lease_until"])
        if lease_until.utcoffset() is None or self.runtime.clock() >= lease_until:
            raise ValueError("OWNER_DELIVERY_LEASE_LOST")
        event_id = (item["content"]["plan_id"] if account_actions.is_account(item)
                    else reply_segments.event_id(item))
        value = {"message_id": item["message_id"], "event_id": event_id,
            "fence": self.db.execute("SELECT COALESCE(MAX(fence),0)+1 FROM window_attempts"
                                     ).fetchone()[0],
            "state": "RESERVED", "text_hash": journal.digest(item["content"]),
            "approval_hash": journal.digest(self.authority), "provider_ack_hash": None,
            "item_hash": journal.digest(item), "plan_hash": journal.digest(self.authority)}
        self.delivery_binding = binding(self, item)
        with self.db:
            self.runtime.append("RESERVED", value | {"delivery_binding": self.delivery_binding})
            self.db.execute("INSERT INTO window_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                            tuple(value.values()))
        self.current_item, self.item_hash, self.lease_until = item, value["item_hash"], lease_until

    def _local(self):
        self.runtime.verify()
        if (self.current_item is None or self.item_hash != journal.digest(self.current_item)
            or self.dispatched or self.runtime.clock() >= self.lease_until):
            raise ValueError("OWNER_DELIVERY_SEND_FENCED")
        if self.pending_input():
            raise ValueError("OWNER_INPUT_PENDING")

    def pending_input(self):
        item = self.current_item
        if not item or not ("reply_revision" in item
            or item.get("delivery_key", "").startswith("poke-response:")
            or item.get("action_type") == "qq.private.proactive"
            or account_actions.is_account(item)):
            return None
        event_id = (item["content"]["plan_id"] if account_actions.is_account(item)
                    else reply_segments.event_id(item))
        row = self.db.execute("SELECT i.key FROM inbox i JOIN window_inputs w ON w.event_id=i.key "
            "WHERE i.state='pending' AND w.plan_hash=? AND i.key!=? "
            "AND json_extract(w.capture_json,'$.kind')='authenticated_onebot_ws' "
            "ORDER BY i.rowid LIMIT 1", (journal.digest(self.authority),
                                        event_id)).fetchone()
        return row[0] if row else None

    def _request(self, request):
        item = self.current_item
        if (request.method != "POST" or request.headers.get("Authorization")
            != "Bearer " + self.config.onebot_token
            or not private_request_matches(request, self.config.onebot_http_url,
                                           request.url.path)):
            raise ValueError("OWNER_PROTOCOL_REQUEST_CHANGED")
        if request.url.path in {"/get_login_info", "/get_status", "/get_version_info"}:
            if decode(request.content, 64) != {}:
                raise ValueError("OWNER_PROTOCOL_REQUEST_CHANGED")
            return False
        if account_actions.is_account(item):
            prepared = self.prepared_account
            expected = account_actions.prepare_owner(
                self.runtime, item, item["content"]["authorization_sha256"])
            if (prepared != expected or request.url.path != prepared.path
                or request.content != prepared.body):
                raise ValueError("OWNER_PROTOCOL_REQUEST_CHANGED")
            return True
        if item["action_type"] == "qq.private.poke":
            from .native_actions import prepare

            expected = prepare(item["content"], self.config.owner_qq_id)
            if (self.prepared_native != expected or request.url.path != expected.path
                or request.content != expected.body):
                raise ValueError("OWNER_PROTOCOL_REQUEST_CHANGED")
            return True
        if item["action_type"] not in {"qq.private.reply", "qq.private.proactive"}:
            raise ValueError("OWNER_NATIVE_PREPARATION_REQUIRED")
        message = (self.prepared_voice.message if self.prepared_voice is not None
                   else self.prepared_message or [
                       {"type": "text", "data": {"text": reply_segments.wire_text(item)}}])
        expected = {"user_id": int(self.config.owner_qq_id), "message": message,
                    "auto_escape": True}
        if request.url.path != "/send_private_msg" or decode(
            request.content, self.config.max_voice_lease_bytes, max_depth=20
        ) != expected:
            raise ValueError("OWNER_PROTOCOL_REQUEST_CHANGED")
        return True

    async def before_dispatch(self, request):
        self._local()
        sending = self._request(request)
        if not sending:
            await self.runtime.check(self.client)
            self._request(request)
            self._local()
            seal_protocol_content(request, request.content)
            return
        item = self.current_item
        account = account_actions.is_account(item)
        check = (account_actions.send_check_owner(self.authority, item) if account else {
            "authority": self.authority, "message_id": item["message_id"],
            "event_id": reply_segments.event_id(item), "lease_token": item["lease_token"],
            "content_sha256": journal.digest(item["content"])})
        if "reply_revision" in item:
            check["reply_revision"] = item["reply_revision"]
        if self.prepared_voice is not None:
            check["voice"] = self.prepared_voice.check
            check["reply_revision"] = item["voice"]["binding"]["reply_revision"]
        if journal.digest(check) != self.delivery_binding["send_check_sha256"]:
            raise ValueError("OWNER_DELIVERY_BINDING_CHANGED")
        path = "/internal/v1/" + ("account-actions" if account else "owner-authority")
        result = await bounded_post(self.client, path + "/send-check", check)
        fields = {"approved", "lease_until"} | ({"content_sha256", "payload_sha256"}
                                               if account else set())
        if (set(result) != fields or result["approved"] is not True
            or account and any(result[k] != check[k]
                               for k in ("content_sha256", "payload_sha256"))):
            raise ValueError("OWNER_DELIVERY_SEND_FENCED")
        expiry = datetime.fromisoformat(result["lease_until"])
        if expiry.utcoffset() is None:
            raise ValueError("OWNER_DELIVERY_LEASE_LOST")
        self.lease_until = min(self.lease_until, expiry)
        self._request(request)
        self._local()
        with self.db:
            self.runtime.append("IO_STARTED", {"message_id": item["message_id"],
                                                "provider_ack_hash": None})
            self.db.execute("UPDATE window_attempts SET state='IO_STARTED' WHERE message_id=?",
                            (item["message_id"],))
        self._local()
        seal_protocol_content(request, request.content)
        self.dispatched = True

    async def cancel_before_send(self, item):
        if (self.dispatched or item is not self.current_item
            or self.item_hash != journal.digest(item)):
            return False
        self.runtime.verify(active=False)
        attempt = self.db.execute("SELECT state FROM window_attempts WHERE message_id=?",
                                   (item["message_id"],)).fetchone()
        if not attempt or attempt[0] not in {"RESERVED", "IO_STARTED"}:
            return False
        body = {"authority": self.authority, "message_id": item["message_id"],
                "lease_token": item["lease_token"],
                "content_sha256": journal.digest(item["content"])}
        account = account_actions.is_account(item)
        if account:
            body = account_actions.send_check_owner(self.authority, item)
            body["reason"] = "BRIDGE_PREFLIGHT_FAILED"
        if "reply_revision" in item:
            body["reply_revision"] = item["reply_revision"]
        if pending := self.pending_input():
            if account:
                body["reason"] = "OWNER_INPUT_PENDING"
            else:
                body.update(cause="OWNER_INPUT_PENDING", pending_event_id=pending)
        try:
            path = "/internal/v1/" + ("account-actions" if account else "owner-authority")
            receipt = await bounded_post(self.client, path + "/cancel-before-send", body)
        except httpx.HTTPError:
            return False
        expected = {"cancelled": True, "message_id": item["message_id"],
                    "state": "CANCELLED", "send_attempt_retained": True}
        if account:
            expected = account_actions.cancelled_proof(receipt,
                message_id=item["message_id"], lease_token=item["lease_token"],
                content_sha256=body["content_sha256"], payload_sha256=body["payload_sha256"])
        if receipt != expected or self.dispatched or self.item_hash != journal.digest(item):
            raise ValueError("OWNER_CANCEL_RECEIPT_INVALID")
        self.runtime.verify(active=False)
        with self.db:
            self.runtime.append("NOT_SENT", {"message_id": item["message_id"],
                "provider_ack_hash": None, "core_receipt": receipt})
            self.db.execute("UPDATE window_attempts SET state='NOT_SENT' WHERE message_id=?",
                            (item["message_id"],))
            self.runtime.spool.cancel_delivery(item, receipt)
        self.runtime.verify(active=False)
        return True

    def complete(self, item, provider_id):
        from .native_actions import AccountAck, NativeAck

        if (item is not self.current_item or journal.digest(item) != self.item_hash
            or provider_id and not self.dispatched):
            raise ValueError("OWNER_DELIVERY_BINDING_CHANGED")
        state = "ACK" if provider_id else "UNKNOWN"
        ack = (provider_id.value(item) if isinstance(provider_id, AccountAck)
               else provider_id.value() if isinstance(provider_id, NativeAck) else provider_id)
        ack_hash = journal.digest(ack) if provider_id else None
        with self.db:
            self.runtime.append(state, {"message_id": item["message_id"],
                                        "provider_ack_hash": ack_hash})
            self.db.execute("UPDATE window_attempts SET state=?,provider_ack_hash=? "
                            "WHERE message_id=?", (state, ack_hash, item["message_id"]))
        if not provider_id:
            self.runtime.stop()


class OwnerOneBot:
    """Per-attempt protocol facade over the original checked HTTP transport."""

    companion = True
    account_scope = True
    voice_scope = True

    def __init__(self, guard, protocol):
        self.guard, self.config = guard, guard.config
        if (str(protocol.base_url).rstrip("/") != self.config.onebot_http_url.rstrip("/")
            or protocol.headers.get("Authorization") != "Bearer " + self.config.onebot_token):
            raise ValueError("OWNER_PROTOCOL_ENDPOINT_CHANGED")
        self.delegate = OneBotClient(replace(self.config, delivery_enabled=True),
                                     checked_http_client(protocol, guard.before_dispatch))

    async def probe(self):
        return await self.delegate.probe()

    async def prepare_delivery(self, item):
        if account_actions.is_account(item):
            permission = await self.guard.runtime.check(self.guard.client,
                                                        purpose="account_actions")
            await self.guard.runtime.check(self.guard.client, purpose="social")
            self.guard.prepared_account = account_actions.prepare_owner(self.guard.runtime,
                item, permission["capability_policies"]["account_actions"])
        elif item["action_type"] == "qq.private.poke":
            from .native_actions import prepare

            self.guard.runtime.verify()
            self.guard.prepared_native = prepare(item["content"], self.config.owner_qq_id)
        elif "voice" in item:
            from .voice_runtime import prepare_delivery

            permission = await self.guard.runtime.check(self.guard.client, purpose="voice")
            self.guard.prepared_voice = prepare_delivery(self.guard.runtime, item,
                voice_policy_sha256=permission["capability_policies"]["voice"])

    async def send_content(self, content):
        item = self.guard.current_item
        if content != item["content"]:
            raise ValueError("OWNER_DELIVERY_BINDING_CHANGED")
        if account_actions.is_account(item):
            return await self.delegate.send_account(self.guard.prepared_account, item)
        if item["action_type"] == "qq.private.poke":
            return await self.delegate.send_native(self.guard.prepared_native)
        if "voice" in item:
            if self.guard.prepared_voice is None:
                raise ValueError("OWNER_MEDIA_PREPARATION_REQUIRED")
            return await self.delegate.send_message(self.guard.prepared_voice.message)
        if "image" in content:
            raise ValueError("OWNER_MEDIA_PREPARATION_REQUIRED")
        return await self.delegate.send_text(reply_segments.wire_text(item))
