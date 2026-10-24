"""Native authority guard for the common leased-item dispatch lifecycle."""

from dataclasses import replace
from datetime import datetime

from companion_delivery_protocol.authority import validate_authority_ref

from . import journal, reply_segments
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
        value = {"message_id": item["message_id"], "event_id": reply_segments.event_id(item),
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
        check = {"authority": self.authority, "message_id": item["message_id"],
            "event_id": reply_segments.event_id(item), "lease_token": item["lease_token"],
            "content_sha256": journal.digest(item["content"])}
        if "reply_revision" in item:
            check["reply_revision"] = item["reply_revision"]
        if self.prepared_voice is not None:
            check["voice"] = self.prepared_voice.check
            check["reply_revision"] = item["voice"]["binding"]["reply_revision"]
        if journal.digest(check) != self.delivery_binding["send_check_sha256"]:
            raise ValueError("OWNER_DELIVERY_BINDING_CHANGED")
        result = await bounded_post(self.client, "/internal/v1/owner-authority/send-check", check)
        if set(result) != {"approved", "lease_until"} or result["approved"] is not True:
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
        # Only a matching authenticated Core cancellation receipt can prove NOT_SENT.
        # Until that receipt exists, retain native uncertainty and never retry.
        return False

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
    account_scope = False
    voice_scope = False

    def __init__(self, guard, protocol):
        self.guard, self.config = guard, guard.config
        if (str(protocol.base_url).rstrip("/") != self.config.onebot_http_url.rstrip("/")
            or protocol.headers.get("Authorization") != "Bearer " + self.config.onebot_token):
            raise ValueError("OWNER_PROTOCOL_ENDPOINT_CHANGED")
        self.delegate = OneBotClient(replace(self.config, delivery_enabled=True),
                                     checked_http_client(protocol, guard.before_dispatch))

    async def probe(self):
        return await self.delegate.probe()

    async def send_content(self, content):
        item = self.guard.current_item
        if content != item["content"]:
            raise ValueError("OWNER_DELIVERY_BINDING_CHANGED")
        if "image" in content or "voice" in item:
            raise ValueError("OWNER_MEDIA_PREPARATION_REQUIRED")
        return await self.delegate.send_text(reply_segments.wire_text(item))
