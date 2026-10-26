"""Staged assets and their original evidence; never an execution authority."""

import hashlib
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from companion import owner_input
from companion.contracts import CoreError
from companion.meme_core import _proof
from companion.qq_rich import input_digest
from companion_meme_protocol import CaptionContext, digest, parse, source_evidence, strict_json

INTENTS = {"celebrate", "comfort", "like", "surprised", "morning", "sleep", "playful"}


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    description: str = Field(min_length=1, max_length=2000)
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(max_length=32)
    suitable: list[str] = Field(max_length=7)
    avoid: list[str] = Field(max_length=7)
    source_page: str = Field(min_length=1, max_length=2048)
    asset_url: str = Field(min_length=1, max_length=2048)
    license: str = Field(min_length=1, max_length=256)
    usage_basis: str = Field(min_length=1, max_length=512)
    fetched_at: str = Field(min_length=1, max_length=64)
    revision: str = Field(max_length=200)
    acquisition_json: str = Field(max_length=32768)


def metadata(value):
    parsed = Metadata.model_validate(value).model_dump()
    if (any(v not in INTENTS for v in parsed["suitable"] + parsed["avoid"])
        or any(not parsed[k].strip() for k in (
            "description", "source_page", "asset_url", "license", "usage_basis", "fetched_at"
        ))):
        raise ValueError("MEME_ASSET_METADATA_INVALID")
    return parsed


def source_digest(value):
    return digest(source_evidence(SimpleNamespace(**value)))


def custody_digest(custody):
    return digest({key: custody[key] for key in ("identity", "capture_public_key")})


def asset_id(custody, item):
    return digest([custody_digest(custody), item["source_sha256"], item["asset_sha256"]])


def erased(store, event_id):
    event = store.one("SELECT redacted FROM events WHERE event_id=?", (event_id,))
    return bool(not event or event["redacted"] or store.one(
        "SELECT 1 FROM runtime_meta WHERE key=?", ("erased_event:" + event_id,)
    ))


def _capture(store, settings, event_id, custody, authority):
    event = store.one(
        "SELECT e.*,c.scope,c.owner_actor_id,c.platform_session_key FROM events e "
        "JOIN conversations c USING(conversation_id) WHERE event_id=?", (event_id,),
    )
    saved = store.one("SELECT value FROM runtime_meta WHERE key=?", ("owner_input:" + event_id,))
    value = strict_json(saved["value"]) if saved else {}
    if not store.one("SELECT 1 FROM runtime_meta WHERE key='model_budget_journal_id'"):
        raise ValueError("MEME_ASSET_SOURCE_UNATTRIBUTED")
    capture, view = owner_input.verify(store, settings, value.get("capture", {}))
    identity = custody["identity"]
    if (not event or event["redacted"] or event["source"] != "qq.onebot"
        or event["actor_id"] != "owner" or event["scope"] != "private"
        or event["owner_actor_id"] != "owner" or event["privacy_scope"] != "private"
        or event["event_type"] != "message.received"
        or capture.event_id != event_id or capture.source_event_id != event["source_event_id"]
        or capture.occurred_at != datetime.fromisoformat(event["occurred_at"])
        or capture.input_sha256 != input_digest(strict_json(event["body_json"]))
        or value.get("input_sha256") != capture.input_sha256
        or value.get("authority") != capture.authority or capture.authority != authority
        or identity != [settings.owner_qq_id, settings.bot_qq_id,
                        event["platform_session_key"], capture.protocol_epoch]
        or custody["capture_public_key"] != view.policy.capture.public_key):
        raise ValueError("MEME_ASSET_SOURCE_UNATTRIBUTED")


def provenance(store, settings, custody, item, data, *, caption_job_id=None):
    """Resolve originals independently of manifest hints, including reuse ancestors.

    A known erased original yields only a tombstone. Unverifiable legacy material
    yields no content or fabricated attribution; neither path grants execution.
    """
    candidates = store.rows(
        "SELECT c.*,j.event_id,j.authority_json FROM meme_captions c JOIN jobs j USING(job_id) "
        "WHERE c.asset_sha256=? AND c.source_sha256=? AND j.kind='meme_caption' "
        + ("AND c.job_id=? " if caption_job_id else "") + "ORDER BY c.rowid",
        (item["asset_sha256"], item["source_sha256"],
         *((caption_job_id,) if caption_job_id else ())),
    )
    proof = source_evidence(SimpleNamespace(**item["metadata"]))
    for candidate in candidates:
        lineage, pending, seen = [], [candidate], set()
        while pending:
            row = pending.pop()
            if row["job_id"] in seen:
                raise ValueError("MEME_ASSET_LINEAGE_INVALID")
            seen.add(row["job_id"])
            lineage.append(row)
            if row["reuse_job_id"]:
                parent = store.one(
                    "SELECT c.*,j.event_id,j.authority_json FROM meme_captions c "
                    "JOIN jobs j USING(job_id) WHERE c.job_id=? AND j.kind='meme_caption'",
                    (row["reuse_job_id"],),
                )
                if parent is None:
                    raise ValueError("MEME_ASSET_LINEAGE_INVALID")
                pending.append(parent)
        sources = {(r["event_id"], r["job_id"]) for r in lineage}
        for row in lineage:
            sources.update((s["event_id"], row["job_id"]) for s in store.rows(
                "SELECT event_id FROM job_sources WHERE job_id=?", (row["job_id"],),
            ))
        if any(erased(store, event) for event, _ in sources):
            return sorted(sources), True
        try:
            if (strict_json(candidate["source_json"]) != proof
                or item["active"] and candidate["asset_data"] != data):
                continue
            _proof(proof, item["asset_sha256"], item["source_sha256"])
            for row in lineage:
                authority = strict_json(row["authority_json"])
                if not store.one("SELECT 1 FROM job_sources WHERE job_id=? AND event_id=?",
                                 (row["job_id"], row["event_id"])):
                    raise ValueError("MEME_ASSET_SOURCE_UNATTRIBUTED")
                _capture(store, settings, row["event_id"], custody, authority)
                context = parse(CaptionContext, row["context_json"])
                parent = store.one("SELECT * FROM jobs WHERE job_id=? AND kind='meme_discovery'",
                                   (context.discovery_job_id,))
                reservation = store.one("SELECT * FROM meme_discovery_reservations WHERE job_id=?",
                                        (context.discovery_job_id,))
                source = store.one("SELECT value FROM runtime_meta WHERE key=?",
                                   ("meme_discovery:" + context.discovery_job_id,))
                source = strict_json(source["value"]) if source else {}
                if (not parent or not reservation or reservation["state"] != "SETTLED"
                    or parent["state"] != "SUCCEEDED" or parent["event_id"] != row["event_id"]
                    or strict_json(parent["authority_json"]) != authority
                    or reservation["attempt_key"] != context.attempt_key
                    or source.get("event_id") != row["event_id"]
                    or source.get("reply_revision") != context.reply_revision
                    or context.event_id != row["event_id"]
                    or row["asset_sha256"] != item["asset_sha256"]
                    or not row["asset_data"]
                    or hashlib.sha256(row["asset_data"]).hexdigest() != row["asset_sha256"]):
                    raise ValueError("MEME_ASSET_SOURCE_UNATTRIBUTED")
                _proof(strict_json(row["source_json"]), row["asset_sha256"], row["source_sha256"])
            for event, job in sources:
                authority = next(r["authority_json"] for r in lineage if r["job_id"] == job)
                _capture(store, settings, event, custody, strict_json(authority))
            return sorted(sources), False
        except (CoreError, ValueError, KeyError, TypeError):
            continue
    return [], False


def erase(store, event_ids):
    """Idempotent projection cleanup in the original Ledger transaction."""
    if not store.db.in_transaction:
        raise RuntimeError("ERASURE_REQUIRES_TRANSACTION")
    if not event_ids:
        return
    from companion.store import encode

    events = encode(sorted(event_ids))
    store.execute(
        "DELETE FROM meme_vectors WHERE asset_id IN (SELECT asset_id FROM meme_asset_sources "
        "WHERE event_id IN (SELECT value FROM json_each(?))) "
        "OR caption_job_id IN (SELECT job_id FROM job_sources "
        "WHERE event_id IN (SELECT value FROM json_each(?))) "
        "OR caption_job_id IN (SELECT job_id FROM jobs "
        "WHERE event_id IN (SELECT value FROM json_each(?)))", (events, events, events),
    )
    store.execute(
        "UPDATE meme_assets SET state='ERASED',data=NULL,metadata_json=NULL,mime=NULL "
        "WHERE asset_id IN (SELECT asset_id FROM meme_asset_sources "
        "WHERE event_id IN (SELECT value FROM json_each(?)))", (events,),
    )
