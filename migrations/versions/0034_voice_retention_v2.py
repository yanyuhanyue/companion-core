"""Bound voice claim replay and retire content without releasing uncertain custody."""

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import op
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import CheckConstraint

revision = "0034_voice_retention_v2"
down_revision = "0033_model_usage_job_index"
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
        ("json_valid(config_json)", "ck_voice_config_json"),
        ("lease_json IS NULL OR json_valid(lease_json)", "ck_voice_lease_json"),
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
        return _account_binding(store, proof, history, row, authority, recorded)
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
    identity = store.one("SELECT value FROM runtime_meta WHERE key='model_budget_journal_id'")
    lifecycle, seen, managed, heads, resolutions = None, set(), False, set(), {}
    account = empty_state()
    for index, line in enumerate(journal.read_text(encoding="utf-8").splitlines()):
        record = json.loads(line)
        checksum = record.pop("sha256")
        if record.get("previous") != previous or digest(record) != checksum:
            raise ValueError("original resolution journal chain changed")
        previous = checksum
        heads.add(checksum)
        if index == 0:
            if (record.get("kind") != "header" or not identity
                or record.get("journal_id") != identity["value"]):
                raise ValueError("original journal identity changed")
        else:
            if record.get("kind") == KIND:
                validate_journal_record(store, record, checksum, lifecycle, identity["value"])
                mid = record["delivery"]["message_id"]
                if mid in resolutions:
                    raise ValueError("duplicate original owner resolution")
                resolutions[mid] = record | {"sha256": checksum}
            fold(account, record)
            lifecycle = _live_record(record, checksum, lifecycle, seen)
            managed |= record.get("kind") in {"window_open", "window_handoff"}
        if checksum == projection:
            if (found is not None or record.get("kind") != KIND
                or record.get("delivery", {}).get("message_id") != message_id
                or record.get("journal_id") != history["binding"]["budget_journal_id"]
                or proof is not None and proof != record.get("quiescence")):
                raise ValueError("original resolution record changed")
            found = record
    saved = _meta(store, "model_budget_cumulative")
    witness = saved.pop("control_head_sha256", None) if saved else None
    expected = binding(account, journal)
    if saved != expected or expected and witness not in heads:
        raise ValueError("original cumulative projection changed")
    for stop in store.rows("SELECT key,value FROM runtime_meta "
                           "WHERE key LIKE 'model_budget_stop:%'"):
        if (not identity or stop["value"] != identity["value"]
            or stop["key"].removeprefix("model_budget_stop:") not in heads):
            raise ValueError("original cumulative stop missing")
    stored_lifecycle = _meta(store, "model_budget_live_lifecycle")
    if (managed or stored_lifecycle) and stored_lifecycle != lifecycle:
        raise ValueError("original lifecycle projection changed")
    validate_projection(store, resolutions)
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


# Frozen e26c4ad receipt, journal lifecycle and cumulative-liability rules.













def _validate_cumulative_policy(policy):
    """Frozen persisted JSON shape of the v1 CumulativeApproval models."""
    hashes = {"account_ref", "approval_sha256", "account_evidence_sha256", "credential_sha256",
              "key_ownership_sha256", "owner_identity_sha256", "expected_journal_head_sha256"}
    optional = {"predecessor_policy_sha256", "legacy_liabilities_sha256",
                "legacy_adoption_evidence_sha256"}
    periods = {"request", "window", "day", "month", "account"}
    fields = hashes | optional | periods | {"version", "policy_id", "provider", "currency",
        "timezone", "starts_at", "expires_at", "legacy_window_inventory", "concurrency"}
    if (not isinstance(policy, dict) or set(policy) != fields
        or type(policy["version"]) is not int or policy["version"] != 1
        or not identifier(policy["policy_id"], r"quota_[a-f0-9]{32}")
        or any(not hash_value(policy[k]) for k in hashes)
        or any(policy[k] is not None and not hash_value(policy[k]) for k in optional)
        or not isinstance(policy["provider"], str) or not 1 <= len(policy["provider"]) <= 64
        or policy["currency"] != "USD" or not isinstance(policy["timezone"], str)
        or type(policy["concurrency"]) is not int or not 1 <= policy["concurrency"] <= 32
        or policy["predecessor_policy_sha256"] is None
            and policy["legacy_liabilities_sha256"] is None):
        raise ValueError("original cumulative policy invalid")
    ZoneInfo(policy["timezone"])
    if timestamp(policy["starts_at"]) >= timestamp(policy["expires_at"]):
        raise ValueError("original cumulative interval invalid")
    for name in periods:
        caps = policy[name]
        dimensions = set(DIMENSIONS) - ({"owner_inputs", "send_attempts"} if name == "request"
                                       else set())
        if (not isinstance(caps, dict) or set(caps) != dimensions
            or any(type(v) is not int or not 0 <= v <= (2**31 - 1 if k in {
                "owner_inputs", "send_attempts", "model_attempts"} else 2**63 - 1)
                   for k, v in caps.items())):
            raise ValueError("original cumulative caps invalid")
    inventory = policy["legacy_window_inventory"]
    if inventory is not None:
        if (not isinstance(inventory, dict) or set(inventory) != {
                "kind", "identity_sha256", "budget_journal_id", "reservations", "signature"}
            or inventory["kind"] != "cumulative_window_inventory_v1"
            or not hash_value(inventory["identity_sha256"])
            or not identifier(inventory["budget_journal_id"], r"budget_[a-f0-9]{32}")
            or not identifier(inventory["signature"], r"[a-f0-9]{128}")
            or not isinstance(inventory["reservations"], list)
            or len(inventory["reservations"]) > 20000):
            raise ValueError("original cumulative inventory invalid")
        for item in inventory["reservations"]:
            if (not isinstance(item, dict) or set(item) != {"kind", "source_sha256", "window_id",
                    "at", "record_sha256", "journal_head_sha256"}
                or item["kind"] not in {"input", "send"}
                or not isinstance(item["window_id"], str) or not 1 <= len(item["window_id"]) <= 128
                or any(not hash_value(item[k]) for k in (
                    "source_sha256", "record_sha256", "journal_head_sha256"))):
                raise ValueError("original cumulative reservation invalid")
            timestamp(item["at"])
    # Match the historical DTO's persisted JSON time normalization before fold
    # compares the policy and its hash with the original journal record.
    normalized = json.loads(json.dumps(policy))
    for name in ("starts_at", "expires_at"):
        normalized[name] = timestamp(policy[name]).isoformat().replace("+00:00", "Z")
    if inventory is not None:
        for item in normalized["legacy_window_inventory"]["reservations"]:
            item["at"] = timestamp(item["at"]).isoformat().replace("+00:00", "Z")
    return normalized


RECEIPT_KIND = "owner_delivery_resolution_v1"


MAX_INPUT_DELIVERIES = 100


MAX_RECEIPT_BYTES = 60 * 1024


JOB_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


DELIVERY_TERMINAL = {"SENT", "FAILED", "CANCELLED"}


RECEIPT_FIELDS = {
    "kind", "version", "state", "outcome", "at", "journal_id", "record_sha256",
    "account_ref", "policy_sha256", "owner_operation_id", "owner_operation_sha256",
    "source_sha256", "prior_liability_sha256", "prior_quota_state", "core_stop_sha256",
    "delivery", "quiescence", "paused", "receipt_sha256",
    "input_closure",
}


INPUT_CLOSURE_FIELDS = {
    "version", "event_id", "source_sha256", "input_sha256", "job", "work_sha256", "complete",
    "unsettled", "deliveries", "terminal",
}


CLOSURE_DELIVERY_FIELDS = {
    "message_id", "state", "attempt_count", "content_sha256", "authority_sha256",
    "owner_resolution_sha256", "provider_receipt_sha256",
}


def owner_operation(operation_id, operation_sha256):
    if (not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128
        or not operation_id.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in operation_id)
        or not hash_value(operation_sha256)):
        raise ValueError("DELIVERY_OWNER_OPERATION_INVALID")


def validate_input_closure(value):
    if (not isinstance(value, dict) or set(value) != INPUT_CLOSURE_FIELDS
        or type(value["version"]) is not int or value["version"] != 1
        or not identifier(value["event_id"], r"[A-Za-z0-9_:-]{8,128}")
        or not hash_value(value["source_sha256"]) or not hash_value(value["work_sha256"])
        or value["input_sha256"] is not None and not hash_value(value["input_sha256"])
        or type(value["complete"]) is not bool or type(value["terminal"]) is not bool
        or not isinstance(value["unsettled"], dict)
        or set(value["unsettled"]) != {"jobs", "models", "reads", "deliveries"}
        or any(type(v) is not bool for v in value["unsettled"].values())
        or not isinstance(value["deliveries"], list)
        or not 1 <= len(value["deliveries"]) <= MAX_INPUT_DELIVERIES):
        raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
    job = value["job"]
    if job is not None and (
        not isinstance(job, dict) or set(job) != {"job_id", "kind", "state", "record_sha256"}
        or not identifier(job["job_id"], r"job_[a-f0-9]{32}")
        or job["kind"] not in {"chat", "poke"}
        or job["state"] not in JOB_TERMINAL | {"QUEUED", "RUNNING", "RETRY_WAIT"}
        or not hash_value(job["record_sha256"])
    ):
        raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
    ids = []
    for item in value["deliveries"]:
        if (not isinstance(item, dict) or set(item) != CLOSURE_DELIVERY_FIELDS
            or not identifier(item["message_id"], r"msg_[a-f0-9]{32}")
            or item["state"] not in DELIVERY_TERMINAL | {
                "PENDING", "LEASED", "SENDING", "RETRY_WAIT", "DELIVERY_UNCERTAIN"}
            or type(item["attempt_count"]) is not int or not 0 <= item["attempt_count"] < 2**31
            or not hash_value(item["provider_receipt_sha256"])
            or any(item[k] is not None and not hash_value(item[k]) for k in (
                "content_sha256", "authority_sha256", "owner_resolution_sha256"))):
            raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
        ids.append(item["message_id"])
    terminal = bool(value["complete"] and value["input_sha256"] and job
        and job["state"] in JOB_TERMINAL and not any(value["unsettled"].values())
        and all(item["state"] in DELIVERY_TERMINAL for item in value["deliveries"])
        and all(item["authority_sha256"] or not item["attempt_count"]
                for item in value["deliveries"]))
    if ids != sorted(set(ids)) or value["terminal"] != terminal:
        raise ValueError("DELIVERY_INPUT_CLOSURE_INVALID")
    return dict(value)


def validate_receipt(value):
    """Validate a receipt obtained directly from Core; never authorize from its hash."""
    if (not isinstance(value, dict) or set(value) != RECEIPT_FIELDS
        or value["kind"] != RECEIPT_KIND or type(value["version"]) is not int
        or value["version"] != 1 or value["state"] != "OWNER_RESOLVED"
        or value["outcome"] not in {"SENT", "CANCELLED"} or value["paused"] is not True
        or not identifier(value["journal_id"], r"budget_[a-f0-9]{32}")
        or any(not hash_value(value[name]) for name in (
            "record_sha256", "owner_operation_sha256", "source_sha256", "core_stop_sha256",
            "receipt_sha256"))
        or any(value[name] is not None and not hash_value(value[name]) for name in (
            "account_ref", "policy_sha256", "prior_liability_sha256"))
        or value["prior_quota_state"] not in {None, "RESERVED", "DELIVERY_UNCERTAIN", "UNKNOWN"}):
        raise ValueError("DELIVERY_RESOLUTION_RECEIPT_INVALID")
    owner_operation(value["owner_operation_id"], value["owner_operation_sha256"])
    proof = validate_quiescence(value["quiescence"])
    delivery = validate_delivery(value["delivery"])
    account = proof["action_type"] in ACCOUNT_ACTIONS
    closure = None if account else validate_input_closure(value["input_closure"])
    current = next((item for item in closure["deliveries"]
                    if item["message_id"] == delivery["message_id"]), None) if closure else None
    shared = ("message_id", "action_type", "account_plan_id", "authorization_sha256",
              "payload_sha256") if account else ("message_id", "event_id", "action_type")
    if (timestamp(value["at"]) < timestamp(proof["quiesced_at"])
        or value["journal_id"] != proof["budget_journal_id"]
        or value["source_sha256"] != proof["send_check_sha256"]
        or any(delivery[name] != proof[name] for name in shared)
        or (proof["content_sha256"] is not None
            and delivery["content_sha256"] != proof["content_sha256"])
        or (value["account_ref"] is None) != (value["policy_sha256"] is None)
        or (value["account_ref"] is None) != (value["prior_liability_sha256"] is None)
        or (value["account_ref"] is None) != (value["prior_quota_state"] is None)
        or account and value["input_closure"] is not None
        or not account and (
            closure["event_id"] != delivery["event_id"] or not current
            or current["state"] != value["outcome"]
            or current["owner_resolution_sha256"] is not None
            or any(current[key] != delivery[key] for key in (
                "attempt_count", "content_sha256", "authority_sha256")))
        or value["receipt_sha256"] != digest({k: v for k, v in value.items()
                                               if k != "receipt_sha256"})
        or len(canonical(value)) > MAX_RECEIPT_BYTES):
        raise ValueError("DELIVERY_RESOLUTION_RECEIPT_INVALID")
    return dict(value)


KIND = "quota_send_resolution"


PROJECTION = "delivery_resolution:"


ROW_FIELDS = {
    "kind", "version", "decision_source", "at", "journal_id", "account_ref", "policy_sha256",
    "key", "source_sha256", "owner_operation_id", "owner_operation_sha256", "outcome",
    "prior_liability_sha256", "prior_quota_state", "core_stop_sha256", "delivery", "quiescence",
    "input_closure", "previous",
}


def _account_binding(store, proof, history, row, authority, recorded):
    reservation = store.one("SELECT * FROM social_autonomy_reservations WHERE message_id=?",
                            (row["message_id"],))
    plan = store.one("SELECT * FROM social_autonomy_plans WHERE plan_id=?",
                    (proof["account_plan_id"],))
    approval = history["approval"]
    if (not reservation or not plan or not authority
        or reservation["plan_id"] != proof["account_plan_id"]
        or reservation["action_type"] != proof["action_type"]
        or reservation["released_at"] is not None or reservation["dispatched_at"] is None
        or row["action_type"] != proof["action_type"] or row["attempt_count"] < 1
        or row["provider_message_id"] is not None or row["native_ack_json"] is not None
        or approval.get("account_actions_scope") != "qq_social_v1"
        or approval.get("account_actions_authorization_sha256") != proof["authorization_sha256"]
        or any(authority.get(k) != proof[k] for k in (
            "message_id", "plan_sha256", "content_sha256", "payload_sha256",
            "authorization_sha256"))
        or any(plan[k] != proof[k] for k in ("payload_sha256", "authorization_sha256"))):
        raise ValueError("original account authority changed")
    native_receipt = json.loads(reservation["receipt_json"])
    if (reservation["receipt_sha256"] != (digest(native_receipt) if native_receipt else None)
        or native_receipt and (native_receipt.get("result") != "UNKNOWN"
            or any(native_receipt.get(k) != proof[k] for k in (
                "message_id", "action_type", "payload_sha256")))):
        raise ValueError("original account receipt changed")
    if row["content_sha256"]:
        content = json.loads(row["content_json"])
        if (row["content_sha256"] != proof["content_sha256"]
            or digest(content) != proof["content_sha256"]
            or content != json.loads(plan["plan_json"])
            or digest({k: v for k, v in content.items() if k != "payload_sha256"})
                != proof["payload_sha256"]
            or any(content.get(k) != v for k, v in {
                "plan_id": proof["account_plan_id"], "message_id": row["message_id"],
                "action_type": row["action_type"], "delivery_key": row["delivery_key"],
                "policy_sha256": plan["policy_sha256"],
                "source_snapshot_sha256": plan["source_snapshot_sha256"],
                "authorization_sha256": proof["authorization_sha256"],
            }.items())):
            raise ValueError("original account payload changed")
    elif not (plan["redacted"] and plan["state"] == "CANCELLED"
              and plan["last_error_code"] == "SOCIAL_SOURCE_ERASED"
              and plan["plan_json"] == plan["input_snapshot_json"] == "{}"):
        raise ValueError("unproved erased account delivery")
    token = row["lease_token"]
    if token:
        check = {"window_sha256": proof["plan_sha256"], "message_id": row["message_id"],
                 "lease_token": token, "content_sha256": proof["content_sha256"],
                 "payload_sha256": proof["payload_sha256"]}
        if (digest(token) != proof["lease_sha256"]
            or digest(check) != proof["send_check_sha256"]
            or native_receipt and native_receipt.get("lease_token") != token):
            raise ValueError("original account send fence changed")
    elif recorded is None:
        raise ValueError("original account send lease missing")
    delivery = {"message_id": row["message_id"], "event_id": row["origin_event_id"],
        "action_type": row["action_type"], "content_sha256": proof["content_sha256"],
        "delivery_key_sha256": digest(row["delivery_key"]),
        "authority_sha256": digest(authority), "attempt_count": row["attempt_count"],
        "created_at": row["created_at"], "lease_until": row["lease_until"],
        "original_state": recorded["original_state"] if recorded else row["state"],
        "account_plan_id": proof["account_plan_id"],
        "authorization_sha256": proof["authorization_sha256"],
        "payload_sha256": proof["payload_sha256"],
        "reservation_sha256": digest({k: v for k, v in reservation.items()
                                           if k not in {"receipt_json", "receipt_sha256"}}),
        "account_receipt_sha256": reservation["receipt_sha256"]}
    validate_delivery(delivery)
    if recorded is not None and recorded != delivery:
        raise ValueError("retained account binding changed")
    return row, history, delivery


def receipt(row, record_sha256):
    result = {"kind": RECEIPT_KIND, "version": 1, "state": "OWNER_RESOLVED",
        "paused": True, **{key: row[key] for key in RECEIPT_FIELDS - {
            "kind", "version", "state", "paused", "record_sha256", "receipt_sha256"}},
        "record_sha256": record_sha256}
    result["receipt_sha256"] = digest(result)
    return validate_receipt(result)


def _shape(row):
    if (not isinstance(row, dict) or set(row) != ROW_FIELDS or row["kind"] != KIND
        or type(row["version"]) is not int or row["version"] != 1
        or row["decision_source"] != "owner_manual"
        or row["key"] != "send:" + digest(row["delivery"]["message_id"])
        or not hash_value(row["previous"])):
        raise ValueError("invalid owner send resolution record")
    receipt(row, digest(row))


def _provider_receipt(row):
    return digest({"provider_message_id": row["provider_message_id"],
                        "native_ack": json.loads(row["native_ack_json"])
                        if row["native_ack_json"] else None})


def _closure_references(state, row):
    if row["input_closure"] is None:
        return
    for item in row["input_closure"]["deliveries"]:
        previous = state["send_resolutions"].get(item["message_id"])
        if item["owner_resolution_sha256"] is None:
            if previous:
                raise ValueError("input closure omitted a preceding owner decision")
            continue
        if (not previous or digest(previous) != item["owner_resolution_sha256"]
            or previous["delivery"]["event_id"] != row["delivery"]["event_id"]
            or previous["outcome"] != item["state"]
            or any(previous["delivery"][key] != item[key] for key in (
                "attempt_count", "content_sha256", "authority_sha256",
            )) or next(entry for entry in previous["input_closure"]["deliveries"]
                       if entry["message_id"] == item["message_id"])["provider_receipt_sha256"]
                != item["provider_receipt_sha256"]):
            raise ValueError("input closure must reference a preceding owner decision")


def fold_record(state, row):
    """Pure fold: terminal owner responsibility retains every original dimension."""
    _shape(row)
    _closure_references(state, row)
    message_id = row["delivery"]["message_id"]
    prior = state["operations"].get(row["key"])
    if (message_id in state["send_resolutions"]
        or any(value["owner_operation_id"] == row["owner_operation_id"]
               for value in state["send_resolutions"].values())):
        raise ValueError("owner resolution replay")
    if state["policy"]:
        if (row["account_ref"] != state["policy"]["account_ref"]
            or row["policy_sha256"] != state["policy_sha256"] or not prior
            or prior["state"] not in {"RESERVED", "UNKNOWN", "DELIVERY_UNCERTAIN"}
            or row["prior_quota_state"] != prior["state"]
            or row["prior_liability_sha256"] != digest(prior)
            or prior["source_sha256"] != row["source_sha256"]
            or prior["window_id"] != row["quiescence"]["window_id"]
            or prior["logical_request_id"] != _logical_request(row["quiescence"])
            or instant(row["at"]) != row["at"]
            or state["clock"] and row["at"] < state["clock"]):
            raise ValueError("unbound owner send resolution")
        prior["state"] = "OWNER_SENT" if row["outcome"] == "SENT" else "OWNER_CLOSED"
        state.update(paused=True, clock=row["at"],
                     reason=state["reason"] or "OWNER_DELIVERY_RECONCILIATION")
    elif any(row[key] is not None for key in (
        "account_ref", "policy_sha256", "prior_liability_sha256", "prior_quota_state",
    )) or prior:
        raise ValueError("unbound non-cumulative owner resolution")
    state["send_resolutions"][message_id] = dict(row)


def validate_journal_record(store, row, record_sha256, lifecycle, journal_id):
    _shape(row)
    proof = row["quiescence"]
    if (row["journal_id"] != journal_id or not lifecycle or lifecycle["state"] != "STOPPED"
        or lifecycle["plan_sha256"] != proof["plan_sha256"]
        or lifecycle["window_id"] != proof["window_id"]
        or lifecycle["head_sha256"] != row["core_stop_sha256"]):
        raise ValueError("original stopped execution required")
    try:
        original, _, _ = _binding(store, proof, recorded=row["delivery"])
        if row["input_closure"] is not None:
            current = next(item for item in row["input_closure"]["deliveries"]
                           if item["message_id"] == proof["message_id"])
            event = store.one("SELECT fingerprint FROM events WHERE event_id=?",
                              (proof["event_id"],))
            if (not event or event["fingerprint"] != row["input_closure"]["source_sha256"]
                or current["provider_receipt_sha256"] != _provider_receipt(original)):
                raise ValueError("retained input or provider receipt changed")
    except InvalidSignature as error:
        raise ValueError("owner resolution quiescence signature changed") from error
    receipt(row, record_sha256)


def replay_matches(row, selector):
    return bool(selector and set(selector) == {
        "message_id", "operation_id", "operation_sha256", "outcome",
    } and row["delivery"]["message_id"] == selector["message_id"]
        and row["owner_operation_id"] == selector["operation_id"]
        and row["owner_operation_sha256"] == selector["operation_sha256"]
        and row["outcome"] == selector["outcome"])


def validate_projection(store, resolutions, selector=None):
    projected = {row["key"].removeprefix(PROJECTION): json.loads(row["value"])
                 for row in store.rows("SELECT key,value FROM runtime_meta WHERE substr(key,1,?)=?",
                                       (len(PROJECTION), PROJECTION))}
    if set(projected) - set(resolutions):
        raise ValueError("owner resolution journal behind database")
    for message_id, row in resolutions.items():
        current = _row(store, message_id)
        if replay_matches(row, selector):
            if (projected.get(message_id) not in {None, row["sha256"]}
                or current["state"] not in {
                    row["delivery"]["original_state"], "DELIVERY_UNCERTAIN", row["outcome"],
                }):
                raise ValueError("owner resolution recovery conflict")
            continue
        if (projected.get(message_id) != row["sha256"] or current["state"] != row["outcome"]
            or current["lease_token"] is not None):
            raise ValueError("owner resolution database behind journal")


def _logical_request(proof):
    return proof["account_plan_id"] if proof["action_type"] in ACCOUNT_ACTIONS else (
        proof["event_id"])


DIMENSIONS = ("micro_usd", "model_attempts", "input_tokens", "output_tokens",
              "owner_inputs", "send_attempts")


POLICY_RECORDS = {"quota_enroll", "quota_renew"}


def instant(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("aware cumulative time required")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def empty_state():
    return {"policy": None, "policy_sha256": None, "policies": {}, "models": {},
            "operations": {}, "clock": None, "paused": False, "reason": None,
            "send_resolutions": {}}


def binding(state, path, head=None):
    if state["policy"] is None:
        return None
    return {"account_ref": state["policy"]["account_ref"],
            "policy_sha256": state["policy_sha256"], "journal_path": str(Path(path).resolve()),
            **({"control_head_sha256": head} if head else {})}


def _liability(value):
    if set(value) != {"at", "window_id", "logical_request_id", "state", *DIMENSIONS}:
        raise ValueError("invalid cumulative liability")
    if value["at"] != instant(value["at"]) or value["state"] not in {
        "RESERVED", "UNKNOWN", "SETTLED", "ADMITTED", "SENT", "FAILED", "DELIVERY_UNCERTAIN",
    } or any(type(value[name]) is not int or value[name] < 0 for name in DIMENSIONS):
        raise ValueError("invalid cumulative liability value")
    if not isinstance(value["window_id"], str) or not isinstance(value["logical_request_id"], str):
        raise ValueError("invalid cumulative source")
    return dict(value)


def fold(state, row):
    """Pure projection of versioned records in the existing hash-chained journal."""
    kind = row["kind"]
    if kind == "quota_send_resolution":
        fold_record(state, row)
        return
    if kind in POLICY_RECORDS:
        expected = {"kind", "version", "at", "policy", "policy_sha256", "previous"}
        if kind == "quota_enroll":
            expected.add("legacy")
        if set(row) != expected or row["version"] != 1:
            raise ValueError("invalid cumulative policy record")
        policy = _validate_cumulative_policy(row["policy"])
        if policy != row["policy"] or digest(policy) != row["policy_sha256"]:
            raise ValueError("cumulative policy hash")
        at = instant(row["at"])
        if at != row["at"] or state["clock"] and at < state["clock"]:
            raise ValueError("cumulative policy clock")
        if row["previous"] != policy["expected_journal_head_sha256"]:
            raise ValueError("cumulative policy cutoff")
        if kind == "quota_enroll":
            if state["policy"] or policy["predecessor_policy_sha256"] is not None:
                raise ValueError("cumulative account already bound")
            legacy = row["legacy"]
            if set(legacy) != {"models", "operations"} or digest(legacy) != policy[
                "legacy_liabilities_sha256"
            ]:
                raise ValueError("legacy cumulative liabilities")
            if (legacy["models"] or legacy["operations"]) and not policy[
                "legacy_adoption_evidence_sha256"
            ]:
                raise ValueError("legacy account attribution missing")
            for category in ("models", "operations"):
                if not isinstance(legacy[category], dict) or len(legacy[category]) > 10000:
                    raise ValueError("bounded legacy liabilities required")
                state[category] = {
                    key: _liability(value) for key, value in legacy[category].items()
                }
                if any(value["state"] not in {"SETTLED", "ADMITTED", "SENT", "FAILED"}
                       for value in state[category].values()):
                    raise ValueError("unresolved legacy liability")
                if any(value["at"] > at for value in state[category].values()):
                    raise ValueError("legacy responsibility is ahead of the clock")
        else:
            previous = state["policy"]
            if not previous or policy["predecessor_policy_sha256"] != state["policy_sha256"]:
                raise ValueError("cumulative renewal predecessor")
            if any(policy[key] != previous[key] for key in (
                "account_ref", "provider", "currency", "timezone", "owner_identity_sha256",
            )) or policy["policy_id"] in {p["policy_id"] for p in state["policies"].values()}:
                raise ValueError("cumulative account rebind or policy reuse")
            if policy["approval_sha256"] in {
                p["approval_sha256"] for p in state["policies"].values()
            }:
                raise ValueError("new cumulative approval evidence required")
            if policy["credential_sha256"] != previous["credential_sha256"] and (
                policy["key_ownership_sha256"] == previous["key_ownership_sha256"]
            ):
                raise ValueError("key rotation attribution required")
            if any(v["state"] in {"RESERVED", "UNKNOWN", "DELIVERY_UNCERTAIN"}
                   for v in [*state["models"].values(), *state["operations"].values()]):
                raise ValueError("unresolved responsibility cannot renew")
        state.update(policy=policy, policy_sha256=row["policy_sha256"], clock=at,
                     paused=False, reason=None)
        state["policies"][row["policy_sha256"]] = policy
        return
    if kind in {"quota_pause", "quota_operation", "quota_send_result"}:
        if not state["policy"] or row.get("version") != 1 or row.get("account_ref") != (
            state["policy"]["account_ref"]
        ):
            raise ValueError("cumulative operation account")
        if kind == "quota_pause":
            if set(row) != {"kind", "version", "account_ref", "at", "observed_at", "reason",
                            "evidence_sha256", "previous"}:
                raise ValueError("invalid cumulative pause")
            if row["reason"] not in {"OWNER_PAUSE", "WINDOW_STOP", "CLOCK_ROLLBACK", "EXPIRED",
                                     "IDENTITY_CHANGED", "LOCAL_LIMIT_EXCEEDED"}:
                raise ValueError("invalid cumulative pause reason")
            instant(row["observed_at"])
            state.update(paused=True, reason=row["reason"])
        elif kind == "quota_operation":
            if set(row) != {"kind", "version", "account_ref", "at", "policy_sha256", "key",
                            "source_sha256", "liability", "previous"} or row[
                "policy_sha256"
            ] != state["policy_sha256"] or state["paused"]:
                raise ValueError("invalid cumulative operation")
            liability = _liability(row["liability"])
            if (not re.fullmatch(r"(?:input|send):[a-f0-9]{64}", row["key"])
                or not re.fullmatch(SHA, row["source_sha256"])
                or liability["at"] > row["at"]
                or row["key"] in state["operations"]):
                raise ValueError("cumulative operation replay")
            dimension = "owner_inputs" if row["key"].startswith("input:") else "send_attempts"
            if (liability[dimension] != 1
                or liability["state"] != ("ADMITTED" if dimension == "owner_inputs" else "RESERVED")
                or any(liability[k] for k in DIMENSIONS if k != dimension)):
                raise ValueError("invalid cumulative operation dimensions")
            state["operations"][row["key"]] = liability | {"source_sha256": row["source_sha256"]}
        else:
            if set(row) != {"kind", "version", "account_ref", "at", "key", "state", "previous"}:
                raise ValueError("invalid cumulative send result")
            prior = state["operations"].get(row["key"])
            if not prior or not row["key"].startswith("send:") or row["state"] not in {
                "SENT", "FAILED", "DELIVERY_UNCERTAIN",
            }:
                raise ValueError("unbound cumulative send result")
            if prior["state"] == "DELIVERY_UNCERTAIN" and row["state"] != "DELIVERY_UNCERTAIN":
                raise ValueError("uncertain send needs independent reconciliation")
            if prior["state"] in {"OWNER_SENT", "OWNER_CLOSED"}:
                raise ValueError("owner decision cannot be replaced by a late send result")
            prior["state"] = row["state"]
            if row["state"] == "DELIVERY_UNCERTAIN":
                state.update(paused=True, reason="DELIVERY_UNCERTAIN")
        at = instant(row["at"])
        if at != row["at"] or state["clock"] and at < state["clock"]:
            raise ValueError("cumulative clock moved backwards")
        state["clock"] = at
        return
    if not state["policy"]:
        if row.get("quota"):
            raise ValueError("cumulative account not enrolled")
        return
    if kind in {"reserve", "io_started", "settle", "blocked"}:
        key, quota = row["request_id"], row.get("quota")
        prior = state["models"].get(key)
        if quota is None:
            # A previously adopted legacy request may receive a late discrepancy.
            if not prior or kind not in {"settle", "blocked"}:
                raise ValueError("cumulative metadata cannot be removed")
            quota = dict(prior)
        else:
            quota = _liability(quota)
        if quota["micro_usd"] != row["minimum_micro_usd"] or quota["state"] != row["state"]:
            if row.get("quota") is not None:
                raise ValueError("cumulative model amount/state mismatch")
            quota.update(micro_usd=row["minimum_micro_usd"], state=row["state"])
        if quota["model_attempts"] != 1 or quota["owner_inputs"] or quota["send_attempts"]:
            raise ValueError("invalid model attempt dimensions")
        if kind == "reserve" and (prior or state["paused"]):
            raise ValueError("cumulative model reservation replay or pause")
        if prior:
            if any(prior[k] != quota[k] for k in ("at", "window_id", "logical_request_id")):
                raise ValueError("model liability period changed")
            if prior["state"] == "UNKNOWN" and (
                quota["state"] != "UNKNOWN" or any(quota[k] < prior[k] for k in DIMENSIONS)
            ):
                raise ValueError("unknown liability cannot be refunded")
        if kind in {"reserve", "io_started"}:
            checked_at = instant(row.get("io_started_at") or quota["at"])
            if checked_at < state["clock"]:
                raise ValueError("cumulative model clock moved backwards")
            state["clock"] = checked_at
        state["models"][key] = quota
        if row["state"] == "UNKNOWN" or kind == "blocked":
            state.update(paused=True, reason="UNKNOWN_MODEL_LIABILITY")


def liability(at, window, logical, state, **values):
    return {"at": instant(at), "window_id": window, "logical_request_id": logical,
            "state": state, **dict.fromkeys(DIMENSIONS, 0), **values}


def _live_record(row, digest, current, seen):
    kind = row.get("kind")
    if kind not in {"window_open", "window_stop", "window_handoff"}:
        return current
    fields = {"kind", "window_id", "plan_sha256", "previous"}
    if kind != "window_stop":
        fields.add("handoff_sha256")
    if kind == "window_handoff":
        fields |= {"predecessor_window_id", "predecessor_plan_sha256"}
    if set(row) != fields or not re.fullmatch(r"win_[a-f0-9]{32}", row["window_id"]) or (
        not re.fullmatch(r"[a-f0-9]{64}", row["plan_sha256"])
    ):
        raise ValueError("invalid live lifecycle")
    if kind != "window_stop" and row["handoff_sha256"] is not None and not re.fullmatch(
        r"[a-f0-9]{64}", row["handoff_sha256"]
    ):
        raise ValueError("invalid handoff hash")
    same = current and (current["window_id"], current["plan_sha256"]) == (
        row["window_id"], row["plan_sha256"]
    )
    if kind == "window_handoff":
        if (
            not current or current["state"] != "STOPPED" or not row["handoff_sha256"]
            or (current["window_id"], current["plan_sha256"]) != (
                row["predecessor_window_id"], row["predecessor_plan_sha256"]
            ) or row["window_id"] in seen or row["plan_sha256"] in seen
        ):
            raise ValueError("invalid lifecycle predecessor")
    elif kind == "window_open":
        if current and (not same or current["state"] != "PREPARED"):
            raise ValueError("stopped or replaced window")
        if current and row["handoff_sha256"] != current["handoff_sha256"]:
            raise ValueError("handoff changed")
        if not current and row["handoff_sha256"] is not None:
            raise ValueError("missing predecessor")
    elif current and not same:
        raise ValueError("stale window stop")
    seen.update((row["window_id"], row["plan_sha256"]))
    return {"window_id": row["window_id"], "plan_sha256": row["plan_sha256"],
            "state": {"window_open": "OPEN", "window_stop": "STOPPED",
                      "window_handoff": "PREPARED"}[kind],
            "handoff_sha256": row.get("handoff_sha256", (current or {}).get("handoff_sha256")),
            "head_sha256": digest}
