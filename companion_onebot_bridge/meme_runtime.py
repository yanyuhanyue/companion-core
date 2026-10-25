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

from . import journal
from .images import resolve_public
from .meme_caption import CaptionContext, CaptionOutcome, parse
from .meme_catalog import INTENTS, MOCKERY, MemeCatalog, Metadata
from .meme_discovery import BraveImages, DiscoveryLimits, ReviewApproval, Usage, run_discovery
from .meme_library import CATEGORY, MemeLibrary
from .meme_packs import CommunityPacks, relative_path
from .meme_semantic import SemanticLibrary, installed, profile, retain_usage
from .meme_semantic_http import CaptionEmbeddingHTTP, CaptionHTTP, SemanticHTTP, core_request
from .meme_transport import DeliveryCustody
from .onebot_client import bounded_post
from .rich_input import input_digest
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

    def __init__(self, rules, *, require_semantics=True):
        self.rules = tuple(dict(rule) for rule in rules)
        self.require_semantics = require_semantics
        self.rejected = 0
        if len(self.rules) > 32:
            raise ValueError("MEME_TRUST_RULE_INVALID")
        self.license_files = {}
        licenses = {}
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
            license_identity = rule["license_path"], rule["license_sha256"], rule["license"]
            if licenses.get(identity, license_identity) != license_identity:
                raise ValueError("MEME_TRUST_RULE_INVALID")
            licenses[identity] = license_identity
            if self.require_semantics:
                self.license_files[identity] = rule["license_path"]
            else:
                policy = self.license_files.setdefault(identity, {**rule, "asset_prefixes": ()})
                prefix = relative_path(rule["asset_prefix"]).as_posix()
                if prefix not in policy["asset_prefixes"]:
                    policy["asset_prefixes"] += (prefix,)

    @staticmethod
    def _exact_fetch(fetch, url):
        return (fetch.requested_url == fetch.final_url == url and len(fetch.hops) == 1
                and fetch.hops[0].url == url and fetch.hops[0].status == 200)

    def _file_source(self, rule, evidence, path):
        from urllib.parse import quote

        files = evidence.source_files
        parts = path.split("/")
        if (not isinstance(files, dict) or set(files) != {"manifest", "license", "listing"}
            or len(parts) < 3 or parts[-3] != "memes" or evidence.archive_member):
            return False
        prefix = f"https://raw.githubusercontent.com/{rule['repo']}/{rule['revision']}/"
        root = "/".join(parts[:-3])
        manifest_path = (root + "/" if root else "") + "manifest.json"
        expected = {
            "manifest": prefix + quote(manifest_path),
            "license": prefix + quote(relative_path(rule["license_path"]).as_posix()),
            "listing": f"https://api.github.com/repos/{rule['repo']}/contents/"
                       + quote("/".join(parts[:-1])) + "?ref=" + rule["revision"],
        }
        return (
            evidence.discovered_asset_url == prefix + quote(path)
            and self._exact_fetch(evidence.fetch, evidence.discovered_asset_url)
            and evidence.fetch.body_sha256 == evidence.image_sha256
            and all(self._exact_fetch(files[role], url) for role, url in expected.items())
            and files["license"].body_sha256 == rule["license_sha256"]
        )

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
                or candidate.asset_url != evidence.fetch.final_url
                or candidate.revision != rule["revision"]
                or candidate.license != rule["license"]
                or candidate.license_sha256 != rule["license_sha256"]
                or evidence.image_sha256 != image.sha256
                or hashlib.sha256(image.data).hexdigest() != image.sha256
            ):
                continue
            if self.require_semantics and (
                candidate.semantic_verified is not True
                or not isinstance(candidate.description, str)
                or not candidate.description.strip()
                or not isinstance(candidate.tags, tuple)
                or not candidate.tags
            ):
                continue
            try:
                path = relative_path(
                    unquote(evidence.discovered_asset_url[len(prefix) :])
                ).as_posix()
            except ValueError:
                continue
            member = rule["repo"].split("/")[-1] + "-" + rule["revision"] + "/" + path
            if not path.startswith(relative_path(rule["asset_prefix"]).as_posix() + "/"):
                continue
            if evidence.source_files is not None:
                if not self._file_source(rule, evidence, path):
                    continue
            elif evidence.archive_member != member or not self._exact_fetch(
                evidence.fetch, archive_url
            ):
                continue
            description = candidate.description if self.require_semantics else "待理解素材"
            tags = candidate.tags if self.require_semantics else ()
            words = (description + " " + " ".join(tags)).casefold()
            suitable = tuple(
                intent for intent, terms in INTENTS.items() if any(term in words for term in terms)
            )
            if self.require_semantics and not suitable:
                continue
            avoid = ("comfort",) if any(word in words for word in MOCKERY) else ()
            category = path.rsplit("/", 2)[-2]
            if not CATEGORY.fullmatch(category):
                category = CATEGORIES[suitable[0]] if suitable else "happy"
            metadata = Metadata(
                category,
                description,
                tags,
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


class MemeRuntime(DeliveryCustody):
    def __init__(self, window, *, reviewer=None, transport=None, resolver=resolve_public):
        self.window, self.spool, self.db = window, window.spool, window.spool.db
        if self.db.in_transaction:
            raise RuntimeError("MEME_RUNTIME_REQUIRES_TRANSACTION_BOUNDARY")
        self.config = window.config
        self.authority = dict(window.authority)
        self.authority_sha256 = journal.digest(self.authority)
        self.semantics_enabled = getattr(self.config, "meme_semantics_enabled", False)
        self.semantics = None
        self._profile_lock = asyncio.Lock()
        self._profile_refreshed = False
        self.transport, self.resolver = transport, resolver
        self.reviewer = reviewer or TrustedReviewer(
            getattr(self.config, "meme_trusted_sources", ()),
            require_semantics=not self.semantics_enabled,
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
        self.pins = self.catalog.pins
        for sql in (
            "CREATE TABLE IF NOT EXISTS meme_runtime_offers (event_id TEXT PRIMARY KEY,"
            "event_sha256 TEXT NOT NULL,window_sha256 TEXT NOT NULL,offer_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meme_runtime_bindings (delivery_key TEXT PRIMARY KEY,"
            "offer_key TEXT NOT NULL UNIQUE,event_id TEXT NOT NULL,candidate_id TEXT NOT NULL,"
            "category TEXT NOT NULL,window_sha256 TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meme_runtime_results (attempt_key TEXT PRIMARY KEY,"
            "job_id TEXT NOT NULL,body_json TEXT NOT NULL,state TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meme_runtime_sources (sha TEXT,attempt_key TEXT,"
            "claim_json TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(sha,attempt_key))",
            "CREATE TABLE IF NOT EXISTS meme_runtime_semantic_offers (event_id TEXT PRIMARY KEY,"
            "window_sha256 TEXT NOT NULL,reply_revision INTEGER,query_binding TEXT,"
            "offer_json TEXT,state TEXT NOT NULL)",
        ):
            self.db.execute(sql)
        self.db.commit()
        # Resume immutable selections with the last Core-issued profile. New work must
        # refresh it from this window's Core once, including after a process restart.
        if self.semantics_enabled and installed(self.db):
            saved = profile(self.db)
            if saved is not None:
                self.semantics = SemanticLibrary(
                    self.catalog, saved["caption"], saved["embedding"],
                    minimum_confidence=saved["minimum_confidence"],
                )
        self.running = False

    def _owner_check(self):
        if not self.library.enabled:
            raise ValueError("MEME_RUNTIME_DISABLED")
        self.window.verify()
        if self.window.authority != self.authority:
            raise ValueError("OWNER_AUTHORITY_BINDING_CHANGED")

    async def _core_post(self, client, path, body, *, check=None):
        return await core_request(self.window, client, path, body,
                                  check=check or self._owner_check)

    def _event_check(self, event_id, *, allow_missing=False):
        self._owner_check()
        if not isinstance(event_id, str) or not EVENT.fullmatch(event_id):
            raise ValueError("MEME_INPUT_INVALID")
        row = self.db.execute(
            "SELECT rowid,state,plan_hash FROM window_inputs WHERE event_id=?", (event_id,)
        ).fetchone()
        if (
            (not row and not allow_missing)
            or row
            and (
                row[2] != self.authority_sha256
                or row[1] in {"CANCELLED", "SUPERSEDED", "RECALLED"}
                or self.db.execute(
                    "SELECT 1 FROM window_inputs WHERE plan_hash=? AND rowid>? LIMIT 1",
                    (self.authority_sha256, row[0]),
                ).fetchone()
            )
        ):
            raise ValueError("MEME_INPUT_CANCELLED")
        if (self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='automatic_recalls'").fetchone()
            and self.db.execute("SELECT 1 FROM automatic_recalls WHERE target_event_id=? LIMIT 1",
                                (event_id,)).fetchone()):
            raise ValueError("MEME_INPUT_CANCELLED")

    def prepare_offer(self, event, capture=None):
        if not self.library.enabled or self.semantics_enabled:
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
                or capture.get("authority") != self.authority
                or capture.get("kind") != "authenticated_onebot_ws"
                or capture.get("input_sha256") != input_digest(event.get("payload", {}))
            )
        ):
            raise ValueError("MEME_INPUT_INVALID")
        retained = self.db.execute(
            "SELECT input_hash,capture_json,plan_hash FROM window_inputs WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if (retained is None or retained[0] != input_digest(event.get("payload", {}))
            or retained[2] != self.authority_sha256
            or capture is not None and encode(capture) != encode(json.loads(retained[1]))):
            raise ValueError("MEME_INPUT_INVALID")
        event_sha256 = hashlib.sha256(encode(event).encode()).hexdigest()

        def check():
            self._event_check(event_id)

        # Replaying an already approved capture must preserve its exact body even
        # after supersession. Preparation and dispatch enforce current ownership.
        frozen = self.db.execute(
            "SELECT event_sha256,window_sha256,offer_json FROM meme_runtime_offers "
            "WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if frozen:
            if tuple(frozen[:2]) != (event_sha256, self.authority_sha256):
                raise ValueError("MEME_OFFER_CONFLICT")
            return json.loads(frozen[2])
        self.spool.capacity(8192)

        def freeze(offer, fence):
            with self.catalog.atomic(fence):
                fence()
                if self.db.execute("SELECT COUNT(*) FROM meme_runtime_offers").fetchone()[0] >= (
                    self.catalog.max_bindings
                ):
                    # Capacity is monotonic within this frozen window: no receipt
                    # pruning or configuration changes can make a later replay
                    # select an offer. Optional media must not stop owner text.
                    return None
                self.db.execute(
                    "INSERT INTO meme_runtime_offers VALUES (?,?,?,?)",
                    (event_id, event_sha256, self.authority_sha256, encode(offer)),
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
                return freeze(None, self._owner_check)
            if error.args == ("MEME_BINDING_LIMIT",):
                return freeze(None, check)
            raise

    def missing_offer(self, event_id):
        if not isinstance(event_id, str) or not EVENT.fullmatch(event_id):
            raise ValueError("MEME_INPUT_INVALID")
        table = "meme_runtime_semantic_offers" if self.semantics_enabled else "meme_runtime_offers"
        row = self.db.execute(
            f"SELECT offer_json FROM {table} WHERE event_id=? AND window_sha256=?"
            + (" AND state='ACCEPTED'" if self.semantics_enabled else ""),
            (event_id, self.authority_sha256),
        ).fetchone()
        offer = json.loads(row[0]) if row else None
        return offer if offer is not None and not offer["candidates"] else None

    async def _ensure_semantics(self, client, check):
        async with self._profile_lock:
            check()
            if not self._profile_refreshed:
                identity, embedding = await SemanticHTTP(self.window, client, check=check).profile()
                check()
                self.spool.capacity(65536)
                minimum = self.semantics.settings["minimum_confidence"] if self.semantics else 0.8
                self.semantics = SemanticLibrary(
                    self.catalog, identity, embedding, minimum_confidence=minimum,
                )
            self.semantics._profile_check()
            self._profile_refreshed = True
        return self.semantics

    def _admitted_check(self, event_id):
        self._event_check(event_id)
        row = self.db.execute("SELECT state FROM inbox WHERE key=?", (event_id,)).fetchone()
        if not row or row[0] != "accepted":
            raise ValueError("MEME_INPUT_NOT_ADMITTED")

    async def semantic_offer(self, client, event_id, *, reply_revision):
        """One bounded post-inbox attempt; never edits the signed input offer."""
        if not self.semantics_enabled or not self.library.enabled:
            return {"status": "DISABLED"}

        def check():
            self._admitted_check(event_id)
            self.spool.capacity(65536)

        check()
        with self.catalog.atomic(check):
            if self.db.execute(
                "SELECT 1 FROM meme_runtime_semantic_offers WHERE event_id=?", (event_id,)
            ).fetchone():
                return {"status": "ALREADY_ATTEMPTED"}
            if self.db.execute(
                "SELECT COUNT(*) FROM meme_runtime_semantic_offers"
            ).fetchone()[0] >= self.catalog.max_bindings:
                raise ValueError("MEME_BINDING_LIMIT")
            self.db.execute(
                "INSERT INTO meme_runtime_semantic_offers VALUES (?,?,?,NULL,NULL,'REQUESTING')",
                (event_id, self.authority_sha256, reply_revision),
            )
        try:
            semantics = await self._ensure_semantics(client, check)
            http = SemanticHTTP(self.window, client, check=check)
            query = await http.query(event_id, reply_revision)
            offer = None
            if query["query"]["intent"] is not None:
                key = "meme:" + event_id
                candidates = semantics.candidates(
                    key, query["query"], query_vector=query["embedding"], check=check,
                )
                offer = {"offer_key": key, "intent": query["query"]["intent"],
                         "candidates": candidates}
            # Retain only opaque query identity and public asset metadata, never private text.
            binding = {key: query[key] for key in (
                "job_id", "event_id", "reply_revision", "query_sha256",
            )}
            with self.catalog.atomic(check):
                self.db.execute(
                    "UPDATE meme_runtime_semantic_offers SET query_binding=?,offer_json=?,"
                    "state='PREPARED' WHERE event_id=?",
                    (encode(binding), encode(offer), event_id),
                )
            await http.offer(query, offer)
            with self.catalog.atomic(check):
                self.db.execute(
                    "UPDATE meme_runtime_semantic_offers SET state='ACCEPTED' WHERE event_id=?",
                    (event_id,),
                )
            return {"status": "OFFER_ACCEPTED"}
        except BaseException:
            with self.db:
                self.db.execute(
                    "UPDATE meme_runtime_semantic_offers SET state='DEFERRED' WHERE event_id=?",
                    (event_id,),
                )
            raise

    async def _caption_receipt(self, client):
        """Close original accounting only; no profile, asset fetch or semantic commit."""
        self.window.verify(active=False)
        if not installed(self.db):
            return None
        row = self.db.execute(
            "SELECT request_sha,job_id FROM meme_caption_bindings WHERE job_id IS NOT NULL "
            "AND (usage_json IS NULL OR json_extract(usage_json,'$.state')='RESERVED') "
            "AND json_extract(outcome_json,'$.state') IN ('QUEUED','RUNNING') "
            "AND state IN ('QUEUED','RUNNING','UNKNOWN','FAILED') ORDER BY rowid LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        http = SemanticHTTP(self.window, client, check=self._owner_check)
        value = await CaptionHTTP(http, self.catalog).get(row[1])
        retain_usage(self.catalog, row[0], value)
        outcome = parse(CaptionOutcome, value)
        if outcome.state in {"QUEUED", "RUNNING"}:
            return None
        with self.catalog.atomic():
            self.db.execute(
                "UPDATE meme_caption_bindings SET state=?,outcome_json=CASE WHEN ? "
                "THEN ? ELSE outcome_json END WHERE request_sha=?",
                (outcome.state if outcome.state != "SUCCEEDED" else "FAILED",
                 outcome.state != "SUCCEEDED", outcome.model_dump_json(), row[0]))
            self.db.execute("UPDATE meme_semantic_assets SET state='QUARANTINED' "
                            "WHERE binding_request=?", (row[0],))
        return {"status": "CAPTION_QUARANTINED"}

    async def _semantic_once(self, client):
        semantics = await self._ensure_semantics(client, self._owner_check)
        rows = self.db.execute(
            "SELECT s.sha,s.attempt_key,s.claim_json,r.state FROM meme_runtime_sources s "
            "JOIN meme_runtime_results r ON r.attempt_key=s.attempt_key "
            "WHERE s.state='PENDING' ORDER BY s.rowid LIMIT ?", (self.catalog.max_bindings,),
        ).fetchall()
        for sha, attempt, raw, settlement in rows:
            claim = json.loads(raw)
            try:
                self._event_check(claim["event_id"])
                semantics._current(sha)
            except ValueError:
                with self.db:
                    self.db.execute(
                        "UPDATE meme_runtime_sources SET state='CANCELLED' "
                        "WHERE sha=? AND attempt_key=?", (sha, attempt),
                    )
                continue
            if settlement != "SETTLED":
                continue  # An uncertain download never starts another paid operation.
            context = CaptionContext(
                event_id=claim["event_id"], reply_revision=claim["reply_revision"],
                discovery_job_id=claim["job_id"], attempt_key=attempt,
            )

            def fence(context=context, attempt=attempt):
                self._event_check(context.event_id)
                self.spool.capacity(65536)
                result = self.db.execute(
                    "SELECT state FROM meme_runtime_results WHERE attempt_key=?", (attempt,),
                ).fetchone()
                if not result or result[0] != "SETTLED":
                    raise ValueError("MEME_DISCOVERY_UNSETTLED")

            async def check():
                fence()
                await self.window.check(client, purpose="meme")
                fence()

            http = SemanticHTTP(self.window, client, check=fence)
            status = semantics._status(sha)["state"]
            if status == "PENDING":
                value = await semantics.advance(
                    sha, CaptionHTTP(http, self.catalog), context, check=check, commit_check=fence,
                )
                return {"status": "CAPTION_" + value["state"]}
            if status == "READY":
                binding = self.db.execute(
                    "SELECT b.job_id FROM meme_semantic_assets a "
                    "JOIN meme_caption_bindings b ON b.request_sha=a.binding_request "
                    "WHERE a.sha=? AND b.state='SUCCEEDED'", (sha,),
                ).fetchone()
                embedder = CaptionEmbeddingHTTP(
                    http, job_id=binding[0], context=context,
                    caption=semantics.caption(sha)[0],
                )
                await semantics.index(sha, embedder, check=check, commit_check=fence)
            with self.catalog.atomic(fence):
                self.db.execute(
                    "UPDATE meme_runtime_sources SET state=? WHERE sha=? AND attempt_key=?",
                    ("INDEXED" if status == "READY" else "QUARANTINED", sha, attempt),
                )
            return {"status": "SEMANTIC_" + status}
        return None

    def prepare(self, item, *, check):
        if self.semantics_enabled and self.semantics is None:
            raise ValueError("MEME_SEMANTIC_PROFILE_REQUIRED")
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
        if self.semantics_enabled and self.semantics is None:
            raise ValueError("MEME_SEMANTIC_PROFILE_REQUIRED")
        event_id, image = self._binding(item)

        def fence():
            check()
            self._event_check(event_id)
            self._binding(item)

        self.catalog.mark_sending(image["offer_key"], check=fence)

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
            result = await self._core_post(client, BASE + "/submit", body,
                                           check=lambda: self._event_check(event_id))
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
        if until.utcoffset() is None or not self.window.clock() < until:
            raise ValueError("MEME_LEASE_EXPIRED")
        return value

    def _claim_check(self, claim):
        self._event_check(claim["event_id"])
        if self.window.clock() >= datetime.fromisoformat(claim["lease_until"]):
            raise ValueError("MEME_LEASE_EXPIRED")

    async def _flush_result(self, client, row):
        # Original incurred liability can settle after STOP; never authorize new I/O here.
        self.window.verify(active=False)
        body = json.loads(row[2])
        response = await bounded_post(client, BASE + "/" + row[1] + "/result", body)
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
            if not self.library.enabled:
                return {"status": "DISABLED"}
            self._owner_check()
            if self.semantics_enabled:
                semantic = await self._semantic_once(client)
                if semantic:
                    return semantic
            if not getattr(self.config, "meme_discovery_enabled", False):
                return {"status": "DISABLED"}
            # The original result must have room before claiming/reserving any
            # new work. Existing PENDING results above always reconcile first.
            tables = ["meme_runtime_results"]
            if self.semantics_enabled:
                tables.append("meme_runtime_sources")
            if any(self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                   >= self.catalog.max_bindings for table in tables):
                return {"status": "DEFERRED", "code": "MEME_BINDING_LIMIT"}
            response = await self._core_post(
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
                self._claim_check(claim)

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
                if (self.semantics_enabled and isinstance(value, ReviewApproval)
                    and value.evidence_sha256 == evidence.sha256):
                    with self.catalog.atomic(lambda: self._claim_check(claim)):
                        if self.db.execute(
                            "SELECT COUNT(*) FROM meme_runtime_sources"
                        ).fetchone()[0] >= self.catalog.max_bindings:
                            raise ValueError("MEME_BINDING_LIMIT")
                        self.db.execute(
                            "INSERT OR IGNORE INTO meme_runtime_sources VALUES (?,?,?,'PENDING')",
                            (image.sha256, claim["attempt_key"], encode(claim)),
                        )
                return value

            result = await run_discovery(
                claim["attempt_key"],
                claim["intent"],
                provider,
                self.catalog,
                budget=budget,
                check=check,
                review=review,
                commit_check=lambda: self._claim_check(claim),
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
            code = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 else None
            refused = (
                isinstance(error, httpx.HTTPStatusError)
                and error.response.status_code in {403, 409, 429, 503}
            ) or (isinstance(code, str) and code in {
                "OWNER_RUNTIME_STOPPED", "OWNER_AUTHORITY_EXPIRED",
                "OWNER_AUTHORITY_BINDING_CHANGED", "MEME_INPUT_CANCELLED"})
            if self.semantics_enabled and refused:
                try:
                    receipt = await self._caption_receipt(client)
                except (ValueError, httpx.HTTPError):
                    receipt = None
                if receipt is not None:
                    return receipt
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
            if isinstance(code, str) and code in {
                "MEME_INPUT_CANCELLED",
                "MEME_LEASE_EXPIRED",
                "OWNER_RUNTIME_STOPPED",
                "OWNER_AUTHORITY_EXPIRED",
                "OWNER_AUTHORITY_BINDING_CHANGED",
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
        self.runtime._claim_check(self.claim)
        body = {**self.witness, "limits": asdict(limits)}
        self.state = "RESERVING"
        result = await self.runtime._core_post(
            self.client,
            BASE + "/" + self.claim["job_id"] + "/reserve",
            body,
            check=lambda: self.runtime._claim_check(self.claim),
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
        result = await self.runtime._core_post(
            self.client,
            BASE + "/" + self.claim["job_id"] + "/check",
            self.witness,
            check=lambda: self.runtime._claim_check(self.claim),
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
        # The Core ACK can arrive after expiry. Recheck locally before returning
        # to the final request-header hook, with no later await before dispatch.
        self.runtime._claim_check(self.claim)

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
