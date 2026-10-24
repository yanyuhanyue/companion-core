"""Bound voice claim replay and retire content without releasing uncertain custody."""

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from alembic import op
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import CheckConstraint

revision = "0035_voice_retention_v2"
down_revision = "0034_social_plan_jobs"
branch_labels = None
depends_on = None


def upgrade():
    # Preflight is deliberately before the first PRAGMA/DDL/write. Missing native
    # proof leaves the old revision and bytes untouched for a coordinated drain.
    class Reader:
        def rows(self, sql, args=()):
            return [dict(row._mapping) for row in op.get_bind().exec_driver_sql(sql, args)]

        def one(self, sql, args=()):
            rows = self.rows(sql, args)
            return rows[0] if rows else None

    retired = preflight_voice_v1_retirement(Reader())
    op.execute("PRAGMA secure_delete=ON")
    constraints = (
        ("operation IN ('tts','asr')", "ck_voice_operation"),
        ("generation>=1", "ck_voice_generation"),
        ("state IN ('QUEUED','LEASED','RUNNING','READY','TRANSCRIBED',"
         "'CANCELLED','EXPIRED','UNKNOWN','FAILED')", "ck_voice_state"),
        ("input_mime IN ('text/plain','audio/wav')", "ck_voice_mime"),
        ("input_bytes>0", "ck_voice_input_bytes"),
        ("source_partial IN (0,1)", "ck_voice_partial"),
        ("(operation='tts' AND message_id IS NOT NULL AND source_segment_index IS NULL) OR "
         "(operation='asr' AND message_id IS NULL AND source_segment_index IS NOT NULL "
         "AND source_segment_index BETWEEN 0 AND 63)", "ck_voice_source"),
    )
    with op.batch_alter_table("voice_jobs", table_args=tuple(
        CheckConstraint(sql, name=name) for sql, name in constraints)) as batch:
        for column in ("source_sha256", "identity_sha256", "window_sha256", "manifest_sha256",
                       "config_sha256", "input_sha256", "input_bytes"):
            batch.alter_column(column, nullable=True)
    for definition in (
        "stop_token_sha256 TEXT",
        "incarnation TEXT",
        "content_retired INTEGER NOT NULL DEFAULT 0 CHECK(content_retired IN (0,1))",
        "cleanup_state TEXT NOT NULL DEFAULT 'NONE' "
        "CHECK(cleanup_state IN ('NONE','PENDING','CONFIRMED'))",
        "cleanup_epoch INTEGER NOT NULL DEFAULT 0",
        "cleanup_confirmed_at TEXT",
    ):
        op.execute("ALTER TABLE voice_jobs ADD COLUMN " + definition)
    for definition in (
        "last_claim_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_claim_seq>=0)",
        "claim_request_sha256 TEXT", "claim_response_json TEXT", "claim_expires_at TEXT",
        "claim_job_id TEXT", "recovery_required INTEGER NOT NULL DEFAULT 0",
        "recovered_incarnation TEXT",
    ):
        op.execute("ALTER TABLE voice_node_state ADD COLUMN " + definition)
    op.execute(
        "INSERT INTO runtime_meta(key,value) VALUES ('voice_incarnation','"
        + uuid.uuid4().hex
        + "')"
    )
    op.execute(
        "UPDATE voice_jobs SET stop_token_sha256=lease_token_sha256,"
        "state=CASE WHEN started_at IS NOT NULL AND state IN ('LEASED','RUNNING','UNKNOWN') "
        "THEN 'UNKNOWN' ELSE 'CANCELLED' END,"
        "input_blob=NULL,result_blob=NULL,input_sha256=NULL,result_sha256=NULL,"
        "source_sha256=NULL,source_input_sha256=NULL,source_proof_sha256=NULL,"
        "outbox_content_sha256=NULL,identity_sha256=NULL,window_sha256=NULL,"
        "manifest_sha256=NULL,config_sha256=NULL,config_json='{}',input_bytes=NULL,"
        "lease_json=NULL,lease_token_sha256=NULL,content_retired=1,"
        "cleanup_state=CASE WHEN lease_token_sha256 IS NULL THEN 'CONFIRMED' ELSE 'PENDING' END,"
        "cleanup_epoch=captured_privacy_epoch,last_error_code='VOICE_UPGRADE_REQUIRED'"
    )
    op.execute(
        "UPDATE audit_log SET safe_detail_json=json_remove(safe_detail_json,"
        "'$.input_sha256','$.result_sha256') WHERE action IN ('voice.created','voice.committed')"
    )
    op.execute("UPDATE voice_node_state SET recovery_required=1,resource_state='QUARANTINED'")
    for key, value in retired.items():
        op.get_bind().exec_driver_sql(
            "UPDATE runtime_meta SET value=? WHERE key=?",
            (json.dumps(value, sort_keys=True, separators=(",", ":")), key),
        )
    op.get_bind().exec_driver_sql("UPDATE runtime_meta SET value=? WHERE key LIKE 'voice_input:%'",
        (json.dumps({"state": "FENCED", "reason": "VOICE_UPGRADE_REQUIRED"}),))
    op.execute("DROP TABLE voice_claims")
    op.execute("CREATE INDEX idx_voice_cleanup ON voice_jobs(node_id,cleanup_state,job_id)")
    op.execute("CREATE INDEX idx_voice_retention ON voice_jobs(content_retired,deadline_at)")


def downgrade():
    raise RuntimeError("VOICE_V2_DOWNGRADE_REQUIRES_EXPLICIT_CUSTODY_RECONCILIATION")


# Frozen from e26c4ad: v1 wire and original delivery-binding validation.
# Historical migrations must not import evolving Core DTOs or services.
# https://alembic.sqlalchemy.org/en/latest/cookbook.html#data-migrations-general-techniques

QUIESCENCE_DOMAIN = b"companion-core/delivery-quiescence/v1\x00"


QUIESCENCE_KIND = "delivery_quiescence_v1"


PRIVATE_ACTIONS = {"qq.private.reply", "qq.private.proactive", "qq.private.poke"}


ACCOUNT_ACTIONS = {
    "qzone.publish", "qq.profile.nickname", "qq.profile.signature", "qq.profile.avatar",
}


ACTIONS = PRIVATE_ACTIONS | ACCOUNT_ACTIONS


SHA = re.compile(r"[a-f0-9]{64}")


PROOF_FIELDS = {
    "kind", "version", "window_id", "plan_sha256", "budget_journal_id", "message_id",
    "event_id", "action_type", "fence", "item_sha256", "content_sha256", "payload_sha256",
    "payload_kind", "lease_sha256", "send_check_sha256", "reply_revision", "attempt_state",
    "attempt_record_sha256", "reserved_record_sha256", "stop_record_sha256",
    "core_stop_record_sha256", "bridge_journal_head_sha256", "original_result_sha256",
    "stopped_at", "quiesced_at", "signature",
}


DELIVERY_FIELDS = {
    "message_id", "event_id", "action_type", "content_sha256", "delivery_key_sha256",
    "authority_sha256", "attempt_count", "created_at", "lease_until", "original_state",
}


ACCOUNT_PROOF_FIELDS = (PROOF_FIELDS - {"event_id", "reply_revision"}) | {
    "account_plan_id", "authorization_sha256",
}


ACCOUNT_DELIVERY_FIELDS = DELIVERY_FIELDS | {
    "account_plan_id", "authorization_sha256", "payload_sha256", "reservation_sha256",
    "account_receipt_sha256",
}


VOICE_CHECK_FIELDS = {
    "binding_sha256", "job_id", "generation", "result_sha256", "audio_sha256", "audio_bytes",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value):
    if not isinstance(value, str) or len(value) > 48:
        raise ValueError("DELIVERY_RESOLUTION_TIMESTAMP_INVALID")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("DELIVERY_RESOLUTION_TIMESTAMP_INVALID")
    return parsed


def identifier(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def hash_value(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def validate_quiescence(value, *, signed=True):
    """Validate shape only; the Core verifies the frozen Bridge Ed25519 key."""
    account = isinstance(value, dict) and value.get("action_type") in ACCOUNT_ACTIONS
    fields = ACCOUNT_PROOF_FIELDS if account else PROOF_FIELDS
    voice = isinstance(value, dict) and "voice_check" in value
    if voice and not account:
        fields = fields | {"voice_check"}
    fields = fields if signed else fields - {"signature"}
    if (not isinstance(value, dict) or set(value) != fields
        or value["kind"] != QUIESCENCE_KIND or type(value["version"]) is not int
        or value["version"] != 1 or value["action_type"] not in ACTIONS
        or not identifier(value["window_id"], r"win_[a-f0-9]{32}")
        or not identifier(value["budget_journal_id"], r"budget_[a-f0-9]{32}")
        or not identifier(value["message_id"], r"msg_[a-f0-9]{32}")
        or not identifier(value["account_plan_id"] if account else value["event_id"],
                          r"[A-Za-z0-9_:-]{8,128}")
        or type(value["fence"]) is not int or not 1 <= value["fence"] <= 2**53 - 1
        or value["payload_kind"] not in {"reply_sha256", "content_sha256", "account_payload_sha256"}
        or value["attempt_state"] not in {"RESERVED", "IO_STARTED", "UNKNOWN"}
        or (not account and value["reply_revision"] is not None and (
            type(value["reply_revision"]) is not int or not 0 <= value["reply_revision"] < 2**53))
        or any(not hash_value(value[name]) for name in fields if name.endswith("_sha256")
               and name not in {"original_result_sha256", "content_sha256"})
        or any(value[name] is not None and not hash_value(value[name])
               for name in ("original_result_sha256", "content_sha256"))
        or (signed and not identifier(value["signature"], r"[a-f0-9]{128}"))):
        raise ValueError("DELIVERY_QUIESCENCE_INVALID")
    if (account != (value["payload_kind"] == "account_payload_sha256")
        or account and value["content_sha256"] is None
        or (value["action_type"] == "qq.private.poke")
        != (value["payload_kind"] == "content_sha256")
        or timestamp(value["quiesced_at"]) < timestamp(value["stopped_at"])):
        raise ValueError("DELIVERY_QUIESCENCE_INVALID")
    if value["payload_kind"] == "content_sha256" and (
        value["payload_sha256"] != value["content_sha256"] or value["reply_revision"] is not None
    ):
        raise ValueError("DELIVERY_QUIESCENCE_INVALID")
    if voice:
        if account or value["payload_kind"] != "reply_sha256" or value["reply_revision"] is None:
            raise ValueError("DELIVERY_QUIESCENCE_INVALID")
        validate_voice_check(value["voice_check"])
    return dict(value)


def validate_voice_check(value):
    """The six exact voice delivery check fields, without importing runtime services."""
    if (not isinstance(value, dict) or set(value) != VOICE_CHECK_FIELDS
        or not identifier(value["job_id"], r"voice_[a-f0-9]{32}")
        or type(value["generation"]) is not int or not 1 <= value["generation"] <= 2**63 - 1
        or type(value["audio_bytes"]) is not int or not 44 <= value["audio_bytes"] <= 4_000_000
        or any(not hash_value(value[k]) for k in VOICE_CHECK_FIELDS if k.endswith("_sha256"))):
        raise ValueError("DELIVERY_VOICE_CHECK_INVALID")
    return dict(value)


def signing_payload(value):
    payload = {k: v for k, v in value.items() if k != "signature"}
    validate_quiescence(payload, signed=False)
    return QUIESCENCE_DOMAIN + canonical(payload)


def validate_delivery(value):
    account = isinstance(value, dict) and value.get("action_type") in ACCOUNT_ACTIONS
    fields = ACCOUNT_DELIVERY_FIELDS if account else DELIVERY_FIELDS
    if (not isinstance(value, dict) or set(value) != fields
        or not identifier(value["message_id"], r"msg_[a-f0-9]{32}")
        or not (account and value["event_id"] is None)
            and not identifier(value["event_id"], r"[A-Za-z0-9_:-]{8,128}")
        or account and not identifier(value["account_plan_id"], r"[A-Za-z0-9_:-]{8,128}")
        or value["action_type"] not in ACTIONS
        or value["original_state"] not in {"SENDING", "DELIVERY_UNCERTAIN"}
        or type(value["attempt_count"]) is not int or not 1 <= value["attempt_count"] < 2**31
        or any(not hash_value(value[name]) for name in fields if name.endswith("_sha256")
               and name not in {"content_sha256", "account_receipt_sha256"})
        or account and value["account_receipt_sha256"] is not None
            and not hash_value(value["account_receipt_sha256"])
        or (value["content_sha256"] is not None and not hash_value(value["content_sha256"]))
        or (value["action_type"] == "qq.private.poke" or account)
            and value["content_sha256"] is None):
        raise ValueError("DELIVERY_RESOLUTION_BINDING_INVALID")
    timestamp(value["created_at"])
    if value["lease_until"] is not None:
        timestamp(value["lease_until"])
    return dict(value)


def _meta(store, key):
    row = store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))
    return json.loads(row["value"]) if row else None


def _row(store, message_id):
    row = store.one("SELECT o.*,c.scope,c.owner_actor_id FROM outbox o "
                    "JOIN conversations c USING(conversation_id) WHERE message_id=?", (message_id,))
    if (not row or row["action_type"] not in ACTIONS or row["scope"] != "private"
        or row["owner_actor_id"] != "owner"
        or (row["action_type"] in ACCOUNT_ACTIONS) != (row["delivery_lane"] == "qq_account")):
        raise ValueError("DELIVERY_RECONCILIATION_SOURCE_REQUIRED")
    return row


def _authority(store, message_id):
    values = [_meta(store, prefix + message_id) for prefix in (
        "private_life_delivery:", "live_text_reply:", "account_action_delivery:",
    )]
    present = [value for value in values if value is not None]
    if len(present) > 1:
        raise ValueError("ambiguous delivery authority")
    return present[0] if present else None


def _frozen(store, proof):
    history = _meta(store, "live_text_window:" + proof["plan_sha256"])
    if not history or history.get("state") not in {"OPEN", "STOPPED", "RETIRED"}:
        raise ValueError("original approved window missing")
    approval, binding = history["approval"], history["binding"]
    if (approval.get("live_approved") is not True
        or binding["approval_sha256"] != digest(approval)
        or approval["budget_journal_id"] != proof["budget_journal_id"]
        or approval["plan_sha256"] != proof["plan_sha256"]
        or approval["window_id"] != proof["window_id"]
        or binding["plan_sha256"] != proof["plan_sha256"]
        or binding["window_id"] != proof["window_id"]
        or binding["budget_journal_id"] != proof["budget_journal_id"]):
        raise ValueError("original approved window changed")
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(approval["capture_public_key"])).verify(
        bytes.fromhex(proof["signature"]), signing_payload(proof),
    )
    return history


def _binding(store, proof, *, recorded=None):
    """Use retained authority hashes; erasure never requires reconstructing text."""
    validate_quiescence(proof)
    history = _frozen(store, proof)
    row = _row(store, proof["message_id"])
    authority = _authority(store, row["message_id"])
    if proof["action_type"] in ACCOUNT_ACTIONS:
        raise ValueError("voice migration requires private delivery")
    if (not authority or any(authority.get(k) != proof[k] for k in (
        "message_id", "event_id", "plan_sha256"))
        or row["origin_event_id"] != proof["event_id"]
        or row["action_type"] != proof["action_type"] or row["attempt_count"] < 1):
        raise ValueError("original delivery authority changed")
    content_hash = authority.get("content_sha256") or row["content_sha256"]
    if not hash_value(content_hash):
        # A completed resolution may retain the former content-object hash.
        content_hash = recorded["content_sha256"] if recorded else None
    if not hash_value(content_hash) and not (
        content_hash is None and proof["payload_kind"] == "reply_sha256"
        and hash_value(authority.get("reply_sha256"))
    ):
        raise ValueError("retained delivery content hash required")
    if (proof["content_sha256"] is not None and proof["content_sha256"] != content_hash
        or authority.get("action_type", row["action_type"]) != row["action_type"]
        or authority.get("delivery_key", row["delivery_key"]) != row["delivery_key"]
        or authority.get("reply_sha256", proof["payload_sha256"]) != proof["payload_sha256"]):
        raise ValueError("original delivery hashes changed")
    if row["content_sha256"]:
        content = json.loads(row["content_json"])
        if (row["content_sha256"] != content_hash or digest(content) != content_hash
            or digest(content if proof["payload_kind"] == "content_sha256"
                               else content["text"]) != proof["payload_sha256"]):
            raise ValueError("original delivery content changed")
    elif not store.one("SELECT 1 FROM events e JOIN runtime_meta r "
                       "ON r.key='erased_event:'||e.event_id WHERE e.event_id=? AND e.redacted=1",
                       (row["origin_event_id"],)):
        raise ValueError("unproved erased delivery")
    _voice_binding(store, proof, history, row, content_hash)
    token = row["lease_token"]
    if token:
        check = {"window_sha256": proof["plan_sha256"], "message_id": proof["message_id"],
                 "event_id": proof["event_id"], proof["payload_kind"]: proof["payload_sha256"],
                 "lease_token": token}
        if proof["reply_revision"] is not None:
            check["reply_revision"] = proof["reply_revision"]
        if "voice_check" in proof:
            check["voice"] = proof["voice_check"]
        if (digest(token) != proof["lease_sha256"]
            or digest(check) != proof["send_check_sha256"]):
            raise ValueError("original send fence changed")
    elif recorded is None:
        raise ValueError("original send lease missing")
    delivery = {"message_id": row["message_id"], "event_id": row["origin_event_id"],
        "action_type": row["action_type"], "content_sha256": content_hash,
        "delivery_key_sha256": digest(row["delivery_key"]),
        "authority_sha256": digest(authority), "attempt_count": row["attempt_count"],
        "created_at": row["created_at"], "lease_until": row["lease_until"],
        "original_state": recorded["original_state"] if recorded else row["state"]}
    validate_delivery(delivery)
    if recorded is not None and recorded != delivery:
        raise ValueError("retained delivery binding changed")
    return row, history, delivery


def _voice_binding(store, proof, history, row, content_hash):
    value = _meta(store, "voice_reply:" + row["message_id"])
    given = proof.get("voice_check")
    if given is None:
        if value and value.get("binding"):
            raise ValueError("original voice send check missing")
        return
    given = validate_voice_check(given)
    if not value or value.get("state") not in {"AUDIO", "FENCED"}:
        raise ValueError("original voice binding missing")
    binding = _validate_voice_binding(value["binding"])
    job = store.one("SELECT * FROM voice_jobs WHERE job_id=?", (given["job_id"],))
    approval = history["approval"]
    if (not job or job["operation"] != "tts"
        or job["state"] not in {"READY", "CANCELLED", "EXPIRED"}
        or approval.get("voice_scope") != "owner_voice_v1"
        or binding["voice_policy_sha256"] != approval.get("voice_policy_sha256")
        or value.get("job_id") != job["job_id"]
        or value.get("binding_sha256") != digest(binding)
        or given["binding_sha256"] != digest(binding)
        or any(given[k] != binding[k] for k in VOICE_CHECK_FIELDS - {"binding_sha256"})
        or any(binding[k] != v for k, v in {
            "message_id": row["message_id"], "delivery_key": row["delivery_key"],
            "event_id": proof["event_id"], "window_sha256": proof["plan_sha256"],
            "content_sha256": content_hash, "reply_sha256": proof["payload_sha256"],
            "reply_revision": proof["reply_revision"],
        }.items())
        or any(job[k] != v for k, v in {
            "job_id": binding["job_id"], "message_id": row["message_id"],
            "generation": binding["generation"], "result_sha256": binding["result_sha256"],
            "outbox_content_sha256": content_hash, "outbox_delivery_key": row["delivery_key"],
            "event_id": proof["event_id"], "window_sha256": proof["plan_sha256"],
            "source_sha256": binding["source_sha256"],
            "identity_sha256": binding["identity_sha256"],
            "captured_privacy_epoch": binding["privacy_epoch"],
            "captured_reply_revision": binding["reply_revision"],
            "node_id": binding["node_id"], "manifest_sha256": binding["manifest_sha256"],
            "config_sha256": binding["node_config_sha256"],
        }.items())
        or timestamp(job["result_expires_at"]) != timestamp(binding["expires_at"])):
        raise ValueError("original voice delivery changed")


def _validate_voice_binding(value):
    hashes = {"window_sha256", "source_sha256", "identity_sha256", "manifest_sha256",
              "node_config_sha256", "voice_policy_sha256", "content_sha256", "reply_sha256",
              "result_sha256", "audio_sha256"}
    fields = hashes | {"schema_version", "job_id", "generation", "message_id", "delivery_key",
                       "event_id", "privacy_epoch", "reply_revision", "node_id", "audio_bytes",
                       "expires_at"}
    if (not isinstance(value, dict) or set(value) != fields
        or type(value["schema_version"]) is not int or value["schema_version"] != 1
        or any(not hash_value(value[k]) for k in hashes)
        or not identifier(value["job_id"], r"voice_[a-f0-9]{32}")
        or not identifier(value["message_id"], r"msg_[a-f0-9]{32}")
        or not identifier(value["node_id"], r"[a-zA-Z0-9_-]{1,64}")
        or any(not isinstance(value[k], str) or not 1 <= len(value[k]) <= maximum
               for k, maximum in (("event_id", 128), ("delivery_key", 512)))
        or any(type(value[k]) is not int or not lower <= value[k] <= upper
               for k, lower, upper in (("generation", 1, 2**63 - 1),
                                       ("audio_bytes", 44, 4_000_000)))
        or any(type(value[k]) is not int or value[k] < 0
               for k in ("privacy_epoch", "reply_revision"))):
        raise ValueError("original v1 voice binding invalid")
    timestamp(value["expires_at"])
    return dict(value)


def _recorded_resolution(store, message_id, proof):
    projection = _meta(store, "delivery_resolution:" + message_id)
    if projection is None:
        return None
    if not hash_value(projection):
        raise ValueError("original resolution projection invalid")
    authority = _authority(store, message_id)
    history = _meta(store, "live_text_window:" + authority["plan_sha256"]) if authority else None
    if not history:
        raise ValueError("original resolution window missing")
    previous, found = "0" * 64, None
    journal = Path(history["binding"]["budget_journal_path"])
    for line in journal.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        checksum = record.pop("sha256")
        if record.get("previous") != previous or digest(record) != checksum:
            raise ValueError("original resolution journal chain changed")
        previous = checksum
        if checksum == projection:
            if (found is not None or record.get("kind") != "quota_send_resolution"
                or record.get("delivery", {}).get("message_id") != message_id
                or record.get("journal_id") != history["binding"]["budget_journal_id"]
                or record.get("outcome") not in {"SENT", "CANCELLED"}
                or proof is not None and proof != record.get("quiescence")):
                raise ValueError("original resolution record changed")
            found = record
    if found is None:
        raise ValueError("original resolution record missing")
    return found


def preflight_voice_v1_retirement(store):
    """Validate the original signed fence and binding before any migration writes."""
    retired = {}
    for entry in store.rows("SELECT key,value FROM runtime_meta WHERE key LIKE 'voice_reply:%'"):
        value = json.loads(entry["value"])
        if not value.get("binding"):
            continue
        message_id = entry["key"].removeprefix("voice_reply:")
        row = _row(store, message_id)
        proof = _meta(store, "delivery_quiescence:" + message_id)
        resolution = _recorded_resolution(store, message_id, proof)
        if resolution:
            proof = resolution["quiescence"]
        required = row["state"] in {"LEASED", "SENDING", "DELIVERY_UNCERTAIN"}
        if not required and proof is None:
            retired[entry["key"]] = {"state": "FENCED", "reason": "VOICE_UPGRADE_REQUIRED"}
            continue
        if proof is None:
            raise RuntimeError("VOICE_MIGRATION_REQUIRES_NATIVE_STOP_PROOF:" + message_id)
        if proof["message_id"] != message_id:
            raise ValueError("original voice proof message changed")
        _binding(store, proof, recorded=resolution["delivery"] if resolution else None)
        binding = value["binding"]
        retired[entry["key"]] = {
            "state": "FENCED", "reason": "VOICE_UPGRADE_REQUIRED", "job_id": value["job_id"],
            "retired": {"kind": "legacy_signature",
                        "verifier": hashlib.sha256(bytes.fromhex(proof["signature"])).hexdigest(),
                        **{k: binding[k] for k in (
                            "job_id", "generation", "message_id", "event_id", "delivery_key",
                            "window_sha256", "voice_policy_sha256", "reply_revision")}},
        }
    return retired
