"""One authenticated current-owner image; source and result fences, never downloads."""

import hashlib
import json
import re
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from companion import paid_budget
from companion.contracts import CoreError
from companion.privacy import epoch
from companion.qq_rich import input_digest
from companion.store import encode

FROZEN = "live_text_image:"
UPLOAD_DOMAIN = b"companion-core/onebot-owner-image/v1\x00"


def upload_proof(approval, event_id, image, lease_token, media_sha256, mime, proof):
    expected = {
        "plan_sha256": approval.plan_sha256,
        "event_id": event_id,
        "source_sha256": image["source_sha256"],
        "lease_token": lease_token,
        "media_sha256": media_sha256,
        "mime": mime,
    }
    try:
        if (
            not isinstance(proof, dict)
            or set(proof) != {*expected, "signature"}
            or any(proof[key] != value for key, value in expected.items())
        ):
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval.capture_public_key)).verify(
            bytes.fromhex(proof["signature"]), UPLOAD_DOMAIN + encode(expected).encode()
        )
    except (ValueError, TypeError, InvalidSignature):
        raise CoreError("LIVE_IMAGE_UPLOAD_SIGNATURE_REQUIRED", 403) from None
    return proof


def enabled(settings):
    return bool(
        settings.live_text_window and settings.live_text_window.image_scope == "owner_image_v1"
    )


def manifest(body):
    images = [
        item
        for item in (body.get("rich") or {}).get("segments", [])
        if item.get("type") == "image" and item.get("coverage") == "UNREAD"
    ]
    if not images:
        if body.get("shared_content"):
            raise CoreError("LIVE_IMAGE_SOURCE_REQUIRED", 403)
        return None
    if (
        len(images) != 1
        or images[0].get("slot") != 0
        or not isinstance(images[0].get("source_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", images[0]["source_sha256"])
        or body.get("shared_content") != [{"kind": "image", "slot": 0}]
    ):
        raise CoreError("LIVE_IMAGE_SOURCE_REQUIRED", 403)
    return images[0]


def policy(settings, approval):
    paid = settings.paid_model
    image = paid.image_input if paid else None
    if (
        approval.image_scope != "owner_image_v1"
        or image is None
        or approval.image_approval_sha256 != image.approval_sha256
        or approval.rich_input_scope != "references_v1"
        or approval.history_scope != "none"
        or approval.memory_scope != "none"
        or not approval.capture_public_key
        or not settings.content_enabled
        or not settings.observation_enabled
        or not settings.content.use_chat_model_for_vision
    ):
        raise CoreError("LIVE_IMAGE_APPROVAL_REQUIRED", 403)
    adapters = settings.observation_adapters
    adapter = adapters.get(settings.content.image_adapter)
    if (
        len(adapters) != 1
        or adapter is None
        or adapter.source != "qq_image"
        or not adapter.media_analysis
    ):
        raise CoreError("LIVE_IMAGE_ADAPTER_REQUIRED", 403)
    return image


def source(store, settings, approval, event_id):
    """Pure DB proof after the caller's live-window guard; no recursive guard calls."""
    policy(settings, approval)
    event = store.one(
        "SELECT e.*,c.scope,c.owner_actor_id FROM events e "
        "JOIN conversations c USING(conversation_id) WHERE e.event_id=?",
        (event_id,),
    )
    receipt = store.one(
        "SELECT value FROM runtime_meta WHERE key=?", ("live_text_input:" + event_id,)
    )
    value = json.loads(receipt["value"]) if receipt else {}
    body = json.loads(event["body_json"]) if event else {}
    image = manifest(body)
    if (
        not event
        or event["redacted"]
        or not image
        or (event["source"], event["actor_id"], event["scope"], event["owner_actor_id"])
        != ("qq.onebot", "owner", "private", "owner")
        or body.get("control_command")
        or body.get("diary_command")
        or body.get("media")
        or body.get("text", "").strip().startswith("/")
        or value.get("window_sha256") != approval.plan_sha256
        or value.get("privacy_epoch") != epoch(store)
        or value.get("input_sha256") != input_digest(body)
        or not value.get("capture")
        or value["capture"]["source_event_id"] != event["source_event_id"]
    ):
        raise CoreError("LIVE_IMAGE_SOURCE_CHANGED", 403)
    capture = value["capture"]
    try:
        signed = {key: item for key, item in capture.items() if key != "signature"}
        for key in ("received_at", "occurred_at"):
            signed[key] = datetime.fromisoformat(capture[key]).isoformat()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval.capture_public_key)).verify(
            bytes.fromhex(capture["signature"]),
            b"companion-core/onebot-capture/v1\x00" + encode(signed).encode(),
        )
        received, occurred = (
            datetime.fromisoformat(capture[key]) for key in ("received_at", "occurred_at")
        )
        if (
            capture["kind"] != "authenticated_onebot_ws"
            or capture["window_sha256"] != approval.plan_sha256
            or capture["event_id"] != event_id
            or capture["input_sha256"] != input_digest(body)
            or capture["identity_sha256"]
            != paid_budget.sha([settings.owner_qq_id, settings.bot_qq_id])
            or event_id != "evt_" + hashlib.sha256(capture["source_event_id"].encode()).hexdigest()
            or occurred != datetime.fromisoformat(event["occurred_at"])
            or not approval.starts_at <= occurred <= received < approval.ends_at
            or not 0 <= (received - occurred).total_seconds() <= 30
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError, InvalidSignature):
        raise CoreError("LIVE_IMAGE_CAPTURE_CHANGED", 403) from None
    jobs = store.rows("SELECT * FROM jobs WHERE event_id=? AND kind='chat'", (event_id,))
    if len(jobs) != 1:
        raise CoreError("LIVE_IMAGE_CHAT_JOB_REQUIRED", 403)
    return event, body, image, jobs[0]


def lease(store, settings, approval, clock, event_id, *, request_id=None):
    event, body, image, job = source(store, settings, approval, event_id)
    rows = store.rows(
        "SELECT * FROM content_items WHERE owner_event_id=? AND redacted=0", (event_id,)
    )
    expected = "RUNNING" if request_id else "FETCHING"
    if (
        job["state"] != "QUEUED"
        or len(rows) != 1
        or rows[0]["kind"] != "qq_image"
        or rows[0]["slot"] != 0
        or rows[0]["state"] != expected
        or rows[0]["captured_privacy_epoch"] != epoch(store)
        or not rows[0]["lease_token"]
        or not rows[0]["lease_until"]
        or rows[0]["lease_until"] <= clock()
        or rows[0]["deadline"] <= clock()
        or (request_id and rows[0]["usage_request_id"] != request_id)
    ):
        raise CoreError("LIVE_IMAGE_LEASE_FENCED", 409)
    return rows[0], job, image


def freeze(store, settings, approval, clock, row, messages, proof):
    from companion.model import model_payload

    _, job, image = lease(
        store, settings, approval, clock, row["owner_event_id"], request_id=row["usage_request_id"]
    )
    key = FROZEN + job["job_id"]
    if store.one("SELECT 1 FROM runtime_meta WHERE key=?", (key,)):
        raise CoreError("LIVE_IMAGE_ALREADY_FROZEN", 409)
    value = {
        "plan_sha256": approval.plan_sha256,
        "privacy_epoch": epoch(store),
        "event_id": row["owner_event_id"],
        "item_id": row["item_id"],
        "source": image,
        "request_id": row["usage_request_id"],
        "media_sha256": row["media_sha256"],
        "fingerprint": row["fingerprint"],
        "payload_sha256": paid_budget.sha(model_payload(settings, messages, "content_vision")),
        "image_approval_sha256": approval.image_approval_sha256,
        "upload_proof": proof,
    }
    store.insert("runtime_meta", key=key, value=encode(value))
    return job


def frozen(store, settings, approval, job):
    _, _, image, _ = source(store, settings, approval, job["event_id"])
    record = store.one("SELECT value FROM runtime_meta WHERE key=?", (FROZEN + job["job_id"],))
    value = json.loads(record["value"]) if record else {}
    row = store.one("SELECT * FROM content_items WHERE item_id=?", (value.get("item_id"),))
    if (
        not row
        or row["redacted"]
        or value.get("privacy_epoch") != epoch(store)
        or value.get("plan_sha256") != approval.plan_sha256
        or value.get("event_id") != job["event_id"]
        or value.get("source") != image
        or value.get("image_approval_sha256") != approval.image_approval_sha256
        or value.get("request_id") != row["usage_request_id"]
        or value.get("media_sha256") != row["media_sha256"]
        or value.get("fingerprint") != row["fingerprint"]
    ):
        raise CoreError("LIVE_IMAGE_SOURCE_CHANGED", 403)
    proof = value.get("upload_proof") or {}
    if row["state"] == "RUNNING" and proof.get("lease_token") != row["lease_token"]:
        raise CoreError("LIVE_IMAGE_LEASE_FENCED", 409)
    upload_proof(
        approval,
        job["event_id"],
        image,
        proof.get("lease_token"),
        row["media_sha256"],
        proof.get("mime"),
        proof,
    )
    return value, row


def result(store, settings, approval, job):
    """Complete native observation + original paid admission, rehashed at every boundary."""
    value, row = frozen(store, settings, approval, job)
    observation = store.one(
        "SELECT o.*,e.body_json,e.redacted AS event_redacted "
        "FROM observations o JOIN events e USING(event_id) WHERE o.event_id=?",
        (row["output_event_id"],),
    )
    admission = store.one(
        "SELECT a.*,m.state FROM model_admissions a JOIN model_usage m "
        "USING(request_id) WHERE a.request_id=?",
        (value["request_id"],),
    )
    dependencies = store.rows(
        "SELECT source_event_id FROM event_dependencies WHERE event_id=? ORDER BY source_event_id",
        (row["output_event_id"],),
    )
    if (
        row["state"] != "SUCCEEDED"
        or not observation
        or observation["redacted"]
        or observation["event_redacted"]
        or observation["source_kind"] != "image_analysis"
        or observation["result_status"] != "FETCHED"
        or not admission
        or admission["state"] != "SETTLED"
        or admission["logical_request_id"] != job["job_id"]
        or admission["payload_sha256"] != value["payload_sha256"]
        or dependencies != [{"source_event_id": job["event_id"]}]
    ):
        raise CoreError("LIVE_IMAGE_RESULT_REQUIRED", 403)
    body = json.loads(observation["body_json"])
    evidence = json.loads(observation["evidence_json"])
    if (
        evidence.get("media_sha256") != value["media_sha256"]
        or observation["captured_privacy_epoch"] != epoch(store)
        or observation["source_sha256"] != hashlib.sha256(body.get("text", "").encode()).hexdigest()
    ):
        raise CoreError("LIVE_IMAGE_RESULT_CHANGED", 403)
    return {
        "image": value,
        "item_sha256": paid_budget.sha(row),
        "observation_sha256": paid_budget.sha(observation),
        "admission_sha256": paid_budget.sha(admission),
    }
