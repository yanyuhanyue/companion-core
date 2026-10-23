"""Meme catalog, immutable delivery bindings and Core-owned discovery budgets.

Only public intent words cross the discovery boundary. Core retains the job,
revision and accounting authority; this adapter never schedules another model.
"""

import asyncio
import hashlib
import inspect
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from .images import resolve_public
from .meme_catalog import INTENTS, MOCKERY, MemeCatalog, Metadata, key_hash
from .meme_discovery import BraveImages, DiscoveryLimits, ReviewApproval, Usage, run_discovery
from .meme_library import CATEGORY, MemeLibrary
from .meme_packs import CommunityPacks, relative_path
from .reply_segments import event_id as delivery_event_id
from .spool import encode

BASE = "/internal/v1/memes/discovery"
EVENT = re.compile(r"evt_[a-f0-9]{64}")
JOB = re.compile(r"job_[a-f0-9]{32}")
SHA = re.compile(r"[a-f0-9]{64}")
CATEGORIES = {
    "celebrate": "happy",
    "comfort": "like",
    "like": "like",
    "surprised": "surprised",
    "morning": "morning",
    "sleep": "sleep",
    "playful": "happy",
}


def infer_intent(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 2000 or text.lstrip().startswith("/"):
        return None
    lowered = text.casefold()
    if re.search(
        r"自杀|想死|自残|诊断|病历|手术|去世|丧事|葬礼|抑郁|急救|报警|"
        r"(?:不要|不用|别|禁止).{0,12}(?:表情|图片|发图)|"
        r"\b(?:suicide|self.harm|funeral|diagnosis|emergency)\b",
        lowered,
    ):
        return None
    patterns = (
        ("comfort", r"抱抱|安慰|难过|委屈|\bcomfort\b"),
        ("sleep", r"晚安|要睡了|睡觉了|\bgood night\b"),
        ("morning", r"早安|早上好|\bgood morning\b"),
        ("celebrate", r"恭喜|庆祝|成功了|搞定了|太棒了|开心|\bcongratulations\b"),
        ("surprised", r"没想到|好意外|惊讶|\bsurprised\b"),
        ("like", r"好可爱|爱心|(?<!不)喜欢|\blove it\b"),
        ("playful", r"开玩笑|逗你|调皮|\bplayful\b"),
    )
    return next((intent for intent, pattern in patterns if re.search(pattern, lowered)), None)


class TrustedReviewer:
    """Local approval of exact repository revisions and actual license file bytes.

    Rules are trusted configuration, never fields copied out of an index or a
    search response. The pack parser independently binds completed captions and
    tags to the downloaded image's original hash and semantic entry identity.
    """

    def __init__(self, rules):
        self.rules = tuple(dict(rule) for rule in rules)
        self.rejected = 0
        if len(self.rules) > 32:
            raise ValueError("MEME_TRUST_RULE_INVALID")
        self.license_files = {}
        for rule in self.rules:
            if (
                set(rule)
                != {
                    "repo",
                    "revision",
                    "license",
                    "license_path",
                    "license_sha256",
                    "asset_prefix",
                    "usage_basis",
                }
                or not isinstance(rule["repo"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", rule["repo"])
                or any(part in {".", ".."} for part in rule["repo"].split("/"))
                or not isinstance(rule["revision"], str)
                or not re.fullmatch(r"[a-f0-9]{40}", rule["revision"])
                or not isinstance(rule["license_sha256"], str)
                or not SHA.fullmatch(rule["license_sha256"])
                or not isinstance(rule["license"], str)
                or not 1 <= len(rule["license"].strip()) <= 128
                or rule["license"].casefold() in {"unknown", "unlicensed", "none"}
                or not isinstance(rule["usage_basis"], str)
                or not 1 <= len(rule["usage_basis"].strip()) <= 256
            ):
                raise ValueError("MEME_TRUST_RULE_INVALID")
            relative_path(rule["license_path"])
            relative_path(rule["asset_prefix"])
            if (
                len(
                    rule["usage_basis"]
                    + "; license-file="
                    + relative_path(rule["license_path"]).as_posix()
                    + "; license-sha256="
                    + rule["license_sha256"]
                )
                > 512
            ):
                raise ValueError("MEME_TRUST_RULE_INVALID")
            identity = rule["repo"], rule["revision"]
            if self.license_files.get(identity, rule["license_path"]) != rule["license_path"]:
                raise ValueError("MEME_TRUST_RULE_INVALID")
            self.license_files[identity] = rule["license_path"]

    async def __call__(self, candidate, image, evidence):
        for rule in self.rules:
            prefix = f"https://raw.githubusercontent.com/{rule['repo']}/{rule['revision']}/"
            archive_url = f"https://codeload.github.com/{rule['repo']}/zip/{rule['revision']}"
            asset = urlsplit(evidence.discovered_asset_url)
            if (
                candidate.source_page
                != f"https://github.com/{rule['repo']}/tree/{rule['revision']}"
                or not evidence.discovered_asset_url.startswith(prefix)
                or asset.query
                or asset.fragment
                or candidate.asset_url != archive_url
                or evidence.fetch.final_url != archive_url
                or evidence.fetch.requested_url != archive_url
                or len(evidence.fetch.hops) != 1
                or evidence.fetch.hops[0].url != archive_url
                or evidence.fetch.hops[0].status != 200
                or candidate.revision != rule["revision"]
                or candidate.license != rule["license"]
                or candidate.license_sha256 != rule["license_sha256"]
                or candidate.semantic_verified is not True
                or not isinstance(candidate.description, str)
                or not candidate.description.strip()
                or not isinstance(candidate.tags, tuple)
                or not candidate.tags
                or evidence.image_sha256 != image.sha256
                or hashlib.sha256(image.data).hexdigest() != image.sha256
            ):
                continue
            try:
                path = relative_path(
                    unquote(evidence.discovered_asset_url[len(prefix) :])
                ).as_posix()
            except ValueError:
                continue
            member = rule["repo"].split("/")[-1] + "-" + rule["revision"] + "/" + path
            if evidence.archive_member != member or not path.startswith(
                relative_path(rule["asset_prefix"]).as_posix() + "/"
            ):
                continue
            words = (candidate.description + " " + " ".join(candidate.tags)).casefold()
            suitable = tuple(
                intent for intent, terms in INTENTS.items() if any(term in words for term in terms)
            )
            if not suitable:
                continue
            avoid = ("comfort",) if any(word in words for word in MOCKERY) else ()
            category = path.rsplit("/", 2)[-2]
            if not CATEGORY.fullmatch(category):
                category = CATEGORIES[suitable[0]]
            metadata = Metadata(
                category,
                candidate.description,
                candidate.tags,
                suitable,
                avoid,
                candidate.source_page,
                candidate.asset_url,
                rule["license"],
                rule["usage_basis"]
                + "; license-file="
                + relative_path(rule["license_path"]).as_posix()
                + "; license-sha256="
                + rule["license_sha256"],
                evidence.fetch.fetched_at,
                candidate.revision,
            )
            return ReviewApproval(metadata, evidence.sha256)
        self.rejected += 1
        return None


class MemeRuntime:
    def __init__(self, window, *, reviewer=None, transport=None, resolver=resolve_public):
        self.window, self.spool, self.db = window, window.spool, window.spool.db
        if self.db.in_transaction:
            raise RuntimeError("MEME_RUNTIME_REQUIRES_TRANSACTION_BOUNDARY")
        self.config = window.plan.config
        self.transport, self.resolver = transport, resolver
        self.reviewer = reviewer or TrustedReviewer(
            getattr(self.config, "meme_trusted_sources", ())
        )
        self.library = MemeLibrary(
            self.config.meme_directory or Path(self.spool.directory) / "memes",
            enabled=getattr(self.config, "meme_enabled", False),
        )
        self.catalog = MemeCatalog(
            self.db,
            self.library,
            clock=lambda: window.clock().timestamp(),
            cooldown=getattr(self.config, "meme_cooldown_seconds", 1800),
            max_assets=getattr(self.config, "meme_catalog_max_assets", 512),
            max_storage_bytes=getattr(
                self.config, "meme_catalog_max_storage_bytes", 64 * 1024 * 1024
            ),
            max_bindings=getattr(self.config, "meme_catalog_max_bindings", 10000),
        )
        for sql in (
            "CREATE TABLE IF NOT EXISTS meme_runtime_offers (event_id TEXT PRIMARY KEY,"
            "event_sha256 TEXT NOT NULL,window_sha256 TEXT NOT NULL,offer_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meme_runtime_bindings (delivery_key TEXT PRIMARY KEY,"
            "offer_key TEXT NOT NULL UNIQUE,event_id TEXT NOT NULL,candidate_id TEXT NOT NULL,"
            "category TEXT NOT NULL,window_sha256 TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meme_runtime_results (attempt_key TEXT PRIMARY KEY,"
            "job_id TEXT NOT NULL,body_json TEXT NOT NULL,state TEXT NOT NULL)",
        ):
            self.db.execute(sql)
        self.db.commit()
        self.running = False

    def _window_check(self):
        if not self.library.enabled:
            raise ValueError("MEME_RUNTIME_DISABLED")
        scope = self.window.plan.value.get("live_text", {})
        if (
            self.window.plan.mode != "live_text"
            or scope.get("automatic_reply") is not True
            or scope.get("companion_scope") != "owner_private_v1"
        ):
            raise ValueError("MEME_OWNER_PRIVATE_SCOPE_REQUIRED")
        self.window.verify(active=False, dispatch=True)
        if self.window.row()["state"] == "STOPPED":
            raise ValueError("WINDOW_STOPPED")
        if not self.window.plan.start <= self.window.clock() < self.window.plan.end:
            raise ValueError("WINDOW_OUTSIDE_INTERVAL")

    def _event_check(self, event_id, *, allow_missing=False):
        self._window_check()
        if not isinstance(event_id, str) or not EVENT.fullmatch(event_id):
            raise ValueError("MEME_INPUT_INVALID")
        row = self.db.execute(
            "SELECT rowid,state,plan_hash FROM window_inputs WHERE event_id=?", (event_id,)
        ).fetchone()
        if (
            (not row and not allow_missing)
            or row
            and (
                row[2] != self.window.plan.sha256
                or row[1] in {"CANCELLED", "SUPERSEDED", "RECALLED"}
                or self.db.execute(
                    "SELECT 1 FROM window_inputs WHERE plan_hash=? AND rowid>? LIMIT 1",
                    (self.window.plan.sha256, row[0]),
                ).fetchone()
            )
        ):
            raise ValueError("MEME_INPUT_CANCELLED")
        for table, clause, values in (
            ("automatic_recalls", "target_event_id=?", (event_id,)),
            ("automatic_received", "event_id<>?", (event_id,)),
        ):
            if (
                self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                and self.db.execute(
                    f"SELECT 1 FROM {table} WHERE window_sha256=? AND {clause} LIMIT 1",
                    (self.window.plan.sha256, *values),
                ).fetchone()
            ):
                raise ValueError("MEME_INPUT_CANCELLED")

    def prepare_offer(self, event, capture=None):
        if not self.library.enabled:
            return None
        event_id = event.get("event_id")
        prefix = f"{self.config.bot_qq_id}:{self.config.origin}:epoch:{self.config.protocol_epoch}:"
        source = event.get("source_event_id", "")
        if (
            event.get("source") != "qq.onebot"
            or not source.startswith(prefix)
            or not re.fullmatch(r"-?[1-9][0-9]{0,19}", source[len(prefix) :])
            or event_id != "evt_" + hashlib.sha256(source.encode()).hexdigest()
            or event.get("actor", {}).get("platform_user_id") != self.config.owner_qq_id
            or event.get("conversation", {}).get("platform_session_key") != self.config.origin
            or capture is not None
            and (
                capture.get("event_id") != event_id
                or capture.get("source_event_id") != source
                or capture.get("window_sha256") != self.window.plan.sha256
            )
        ):
            raise ValueError("MEME_INPUT_INVALID")
        event_sha256 = hashlib.sha256(encode(event).encode()).hexdigest()

        def check():
            self._event_check(event_id, allow_missing=capture is not None)

        # Replaying an already approved capture must preserve its exact body even
        # after supersession. Preparation and dispatch enforce current ownership.
        frozen = self.db.execute(
            "SELECT event_sha256,window_sha256,offer_json FROM meme_runtime_offers "
            "WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if frozen:
            if tuple(frozen[:2]) != (event_sha256, self.window.plan.sha256):
                raise ValueError("MEME_OFFER_CONFLICT")
            return json.loads(frozen[2])
        self.spool.capacity(8192)

        def freeze(offer, fence):
            with self.catalog.atomic(fence):
                fence()
                if self.db.execute("SELECT COUNT(*) FROM meme_runtime_offers").fetchone()[0] >= (
                    self.catalog.max_bindings
                ):
                    raise ValueError("MEME_BINDING_LIMIT")
                self.db.execute(
                    "INSERT INTO meme_runtime_offers VALUES (?,?,?,?)",
                    (event_id, event_sha256, self.window.plan.sha256, encode(offer)),
                )
            return offer

        try:
            check()
            intent = infer_intent(event.get("payload", {}).get("text"))
            offer = None
            if intent is not None:
                key = "meme:" + event_id
                candidates = self.catalog.candidates(key, intent, limit=8, check=check)
                offer = {"offer_key": key, "intent": intent, "candidates": candidates}
            return freeze(offer, check)
        except ValueError as error:
            if error.args == ("MEME_INPUT_CANCELLED",):
                return freeze(None, self._window_check)
            raise

    def missing_offer(self, event_id):
        if not isinstance(event_id, str) or not EVENT.fullmatch(event_id):
            raise ValueError("MEME_INPUT_INVALID")
        row = self.db.execute(
            "SELECT offer_json FROM meme_runtime_offers WHERE event_id=? AND window_sha256=?",
            (event_id, self.window.plan.sha256),
        ).fetchone()
        offer = json.loads(row[0]) if row else None
        return offer if offer is not None and not offer["candidates"] else None

    def _item(self, item):
        image = item.get("content", {}).get("image")
        event_id = delivery_event_id(item)
        if (
            not isinstance(image, dict)
            or set(image) != {"source", "offer_key", "candidate_id", "category"}
            or image["source"] != "companion.meme_catalog"
            or image["offer_key"] != "meme:" + event_id
            or not isinstance(image["candidate_id"], str)
            or not SHA.fullmatch(image["candidate_id"])
            or not isinstance(image["category"], str)
            or not CATEGORY.fullmatch(image["category"])
            or not isinstance(item.get("delivery_key"), str)
        ):
            raise ValueError("MEME_DELIVERY_BINDING_INVALID")
        key_hash(item["delivery_key"])
        return event_id, image

    def _binding(self, item, *, create=False):
        event_id, image = self._item(item)
        expected = (
            item["delivery_key"],
            image["offer_key"],
            event_id,
            image["candidate_id"],
            image["category"],
            self.window.plan.sha256,
        )
        row = self.db.execute(
            "SELECT * FROM meme_runtime_bindings WHERE delivery_key=? OR offer_key=?", expected[:2]
        ).fetchone()
        if row is not None and tuple(row) != expected:
            raise ValueError("MEME_DELIVERY_BINDING_CONFLICT")
        if row is None:
            if not create or not self.db.in_transaction:
                raise ValueError("MEME_DELIVERY_BINDING_MISSING")
            self.db.execute("INSERT INTO meme_runtime_bindings VALUES (?,?,?,?,?,?)", expected)
        return event_id, image

    def prepare(self, item, *, check):
        event_id, image = self._item(item)
        self.spool.capacity(4096)

        def fence():
            check()
            self._event_check(event_id)
            self._binding(item, create=True)
            row = self.db.execute(
                "SELECT metadata FROM meme_assets WHERE sha=?", (image["candidate_id"],)
            ).fetchone()
            if row and self.catalog.metadata(row[0]).category != image["category"]:
                raise ValueError("MEME_DELIVERY_BINDING_CONFLICT")

        return self.catalog.prepare_selection(
            image["offer_key"], image["candidate_id"], check=fence
        )

    def mark_sending(self, item, *, check):
        event_id, image = self._binding(item)

        def fence():
            check()
            self._event_check(event_id)
            self._binding(item)

        self.catalog.mark_sending(image["offer_key"], check=fence)

    def receipt(self, item, outcome):
        _, image = self._binding(item)
        self.catalog.receipt(image["offer_key"], outcome)

    def confirm_not_sent(self, item, operation, core_receipt):
        expected = {
            "cancelled": True,
            "message_id": item["message_id"],
            "state": "CANCELLED",
            "send_attempt_retained": True,
        }
        if (
            getattr(operation, "window", None) is not self.window
            or getattr(operation, "current_item", None) is not item
            or getattr(operation, "dispatched", None) is not False
            or not getattr(operation, "current", None)
            or operation.current[0] != item["message_id"]
            or encode(core_receipt) != encode(expected)
        ):
            raise ValueError("MEME_NOT_SENT_PROOF_REQUIRED")
        with self.catalog.atomic():
            _, image = self._binding(item)
            key = key_hash(image["offer_key"])
            pin = self.db.execute("SELECT sha,state FROM meme_pins WHERE key=?", (key,)).fetchone()
            if pin is None or pin[0] != image["candidate_id"]:
                raise ValueError("MEME_DELIVERY_BINDING_CONFLICT")
            if pin[1] not in {"PREPARED", "SENDING", "CANCELLED"}:
                raise ValueError("MEME_RECEIPT_CONFLICT")
            self.db.execute("UPDATE meme_pins SET state='CANCELLED' WHERE key=?", (key,))

    def cancel(self, event_or_offer):
        key = event_or_offer if event_or_offer.startswith("meme:") else "meme:" + event_or_offer
        if not EVENT.fullmatch(key.removeprefix("meme:")):
            raise ValueError("MEME_INPUT_INVALID")
        self.catalog.cancel(key)

    async def submit_missing(self, client, event_id, offer, *, reply_revision):
        if not getattr(self.config, "meme_discovery_enabled", False) or not self.library.enabled:
            return {"status": "DISABLED"}
        if not offer or offer["candidates"]:
            return {"status": "NOT_NEEDED"}
        try:
            self._event_check(event_id)
        except ValueError as error:
            if error.args == ("MEME_INPUT_CANCELLED",):
                return {"status": "CANCELLED", "code": "MEME_INPUT_CANCELLED"}
            raise
        provider = "brave" if self.config.meme_search_provider == "brave" else "community"
        if provider == "brave" and not self.config.meme_search_api_key:
            return {"status": "NOT_CONFIGURED", "code": "MEME_SEARCH_NOT_CONFIGURED"}
        body = {
            "event_id": event_id,
            "reply_revision": reply_revision,
            "intent": offer["intent"],
            "provider": provider,
        }
        try:
            result = await self.window.core_post(client, BASE + "/submit", body)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {403, 409, 429, 503}:
                return {"status": "DEFERRED", "code": "MEME_DISCOVERY_NOT_ADMITTED"}
            raise
        if (
            set(result) != {"job_id", "event_id", "reply_revision", "state", "duplicate"}
            or not isinstance(result["job_id"], str)
            or not JOB.fullmatch(result["job_id"])
            or result["event_id"] != event_id
            or type(result["reply_revision"]) is not int
            or result["reply_revision"] != reply_revision
            or type(result["duplicate"]) is not bool
            or result["state"] not in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}
        ):
            raise ValueError("MEME_SUBMIT_RECEIPT_INVALID")
        return result

    def _claim(self, value):
        keys = {
            "job_id",
            "event_id",
            "reply_revision",
            "intent",
            "provider",
            "lease_token",
            "lease_until",
            "attempt_key",
        }
        if (
            not isinstance(value, dict)
            or set(value) != keys
            or not isinstance(value["job_id"], str)
            or not JOB.fullmatch(value["job_id"])
            or not isinstance(value["event_id"], str)
            or not EVENT.fullmatch(value["event_id"])
            or type(value["reply_revision"]) is not int
            or value["reply_revision"] < 0
            or value["intent"] not in INTENTS
            or value["provider"] not in {"community", "brave"}
            or not isinstance(value["lease_token"], str)
            or not re.fullmatch(r"lease_[a-f0-9]{32}", value["lease_token"])
            or not isinstance(value["attempt_key"], str)
            or not 1 <= len(value["attempt_key"]) <= 256
        ):
            raise ValueError("MEME_LEASE_INVALID")
        until = datetime.fromisoformat(value["lease_until"])
        if until.utcoffset() is None or not self.window.clock() < until <= self.window.plan.end:
            raise ValueError("MEME_LEASE_EXPIRED")
        return value

    async def _flush_result(self, client, row):
        body = json.loads(row[2])
        response = await self.window.core_post(client, BASE + "/" + row[1] + "/result", body)
        if (
            set(response) != {"job_id", "attempt_key", "state"}
            or response["job_id"] != row[1]
            or response["attempt_key"] != row[0]
            or response["state"] not in {body["outcome"], "UNKNOWN"}
        ):
            raise ValueError("MEME_RESULT_RECEIPT_INVALID")
        with self.db:
            self.db.execute(
                "UPDATE meme_runtime_results SET state=? WHERE attempt_key=?",
                (response["state"], row[0]),
            )
        return response["state"]

    async def run_once(self, client):
        if self.running:
            return {"status": "BUSY"}
        self.running = True
        budget = None
        try:
            pending = self.db.execute(
                "SELECT * FROM meme_runtime_results WHERE state='PENDING' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if pending:
                state = await self._flush_result(client, pending)
                return {"status": "RESULT_RECONCILED", "state": state}
            if not self.library.enabled or not getattr(
                self.config, "meme_discovery_enabled", False
            ):
                return {"status": "DISABLED"}
            self._window_check()
            response = await self.window.core_post(
                client,
                BASE + "/lease",
                {
                    "worker_id": "onebot-meme-discovery",
                },
            )
            if set(response) != {"item"}:
                raise ValueError("MEME_LEASE_INVALID")
            if response["item"] is None:
                return {"status": "IDLE"}
            claim = self._claim(response["item"])
            budget = _Budget(self, client, claim)
            limits = DiscoveryLimits()

            async def check():
                self._event_check(claim["event_id"])
                if datetime.fromisoformat(claim["lease_until"]) <= self.window.clock():
                    raise ValueError("MEME_LEASE_EXPIRED")

            if claim["provider"] == "brave" and not self.config.meme_search_api_key:
                reservation = await budget.reserve(claim["attempt_key"], limits)
                await reservation.finish(Usage())
                return {"status": "NOT_CONFIGURED", "code": "MEME_SEARCH_NOT_CONFIGURED"}
            provider = (
                BraveImages(self.config.meme_search_api_key)
                if claim["provider"] == "brave"
                else CommunityPacks(getattr(self.reviewer, "license_files", {}))
            )
            rejected_before = getattr(self.reviewer, "rejected", 0)

            async def review(candidate, image, evidence):
                value = self.reviewer(candidate, image, evidence)
                value = await value if inspect.isawaitable(value) else value
                self.spool.capacity(len(image.data) * 3 + 8192)
                return value

            result = await run_discovery(
                claim["attempt_key"],
                claim["intent"],
                provider,
                self.catalog,
                budget=budget,
                check=check,
                review=review,
                commit_check=lambda: self._event_check(claim["event_id"]),
                limits=limits,
                transport=self.transport,
                resolver=self.resolver,
            )
            if budget.state == "UNKNOWN":
                result["status"] = "UNKNOWN"
            elif getattr(self.reviewer, "rejected", 0) > rejected_before and not result["accepted"]:
                result["status"] = "SOURCE_EVIDENCE_REQUIRED"
            return result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            pending = self.db.execute(
                "SELECT 1 FROM meme_runtime_results WHERE state='PENDING' LIMIT 1"
            ).fetchone()
            if pending:
                return {"status": "RESULT_PENDING", "code": "MEME_RESULT_UNCONFIRMED"}
            if budget is not None and budget.state in {"RESERVING", "RESERVED", "UNKNOWN"}:
                return {"status": "UNKNOWN", "code": "MEME_DISCOVERY_UNCERTAIN"}
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {
                403,
                409,
                429,
                503,
            }:
                return {"status": "DEFERRED", "code": "MEME_DISCOVERY_NOT_ADMITTED"}
            code = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 else None
            if isinstance(code, str) and code in {
                "MEME_INPUT_CANCELLED",
                "MEME_LEASE_EXPIRED",
                "WINDOW_STOPPED",
                "WINDOW_OUTSIDE_INTERVAL",
                "MEME_RUNTIME_DISABLED",
            }:
                return {"status": "CANCELLED", "code": code}
            return {"status": "FAILED", "code": "MEME_DISCOVERY_FAILED"}
        finally:
            self.running = False


class _Budget:
    def __init__(self, runtime, client, claim):
        self.runtime, self.client, self.claim = runtime, client, claim
        self.state = None

    @property
    def witness(self):
        return {key: self.claim[key] for key in ("lease_token", "attempt_key")}

    async def reserve(self, job_key, limits):
        if job_key != self.claim["attempt_key"] or self.state is not None:
            raise ValueError("MEME_RESERVATION_REPLAY")
        self.runtime._event_check(self.claim["event_id"])
        body = {**self.witness, "limits": asdict(limits)}
        self.state = "RESERVING"
        result = await self.runtime.window.core_post(
            self.client,
            BASE + "/" + self.claim["job_id"] + "/reserve",
            body,
        )
        if encode(result) != encode(
            {
                "job_id": self.claim["job_id"],
                "attempt_key": job_key,
                "state": "RESERVED",
                "lease_until": self.claim["lease_until"],
                "limits": asdict(limits),
            }
        ):
            raise ValueError("MEME_RESERVE_RECEIPT_INVALID")
        self.state = "RESERVED"
        return self

    async def check(self):
        result = await self.runtime.window.core_post(
            self.client,
            BASE + "/" + self.claim["job_id"] + "/check",
            self.witness,
        )
        if encode(result) != encode(
            {
                "approved": True,
                **{
                    key: self.claim[key]
                    for key in (
                        "job_id",
                        "attempt_key",
                        "event_id",
                        "reply_revision",
                        "lease_until",
                    )
                },
            }
        ):
            raise ValueError("MEME_CHECK_RECEIPT_INVALID")
        # The network check itself awaited. Sample the local source/STOP fence again.
        self.runtime._event_check(self.claim["event_id"])

    async def _result(self, outcome, usage):
        runtime, attempt = self.runtime, self.claim["attempt_key"]
        body = {**self.witness, "outcome": outcome, "usage": asdict(usage)}
        runtime.spool.capacity(8192)
        with runtime.db:
            row = runtime.db.execute(
                "SELECT * FROM meme_runtime_results WHERE attempt_key=?", (attempt,)
            ).fetchone()
            if row is None:
                if (
                    runtime.db.execute("SELECT COUNT(*) FROM meme_runtime_results").fetchone()[0]
                    >= runtime.catalog.max_bindings
                ):
                    raise ValueError("MEME_BINDING_LIMIT")
                runtime.db.execute(
                    "INSERT INTO meme_runtime_results VALUES (?,?,?,'PENDING')",
                    (attempt, self.claim["job_id"], encode(body)),
                )
                row = runtime.db.execute(
                    "SELECT * FROM meme_runtime_results WHERE attempt_key=?", (attempt,)
                ).fetchone()
            # A lost successful settlement ACK is retried verbatim, never changed
            # into a conflicting UNKNOWN or another public request.
        self.state = await runtime._flush_result(self.client, row)

    async def finish(self, usage):
        await self._result("SETTLED", usage)

    async def unknown(self, usage):
        await self._result("UNKNOWN", usage)
