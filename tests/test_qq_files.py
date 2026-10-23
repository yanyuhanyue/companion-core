import hashlib
import json
from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from companion import live_text, qq_files
from companion.config import LiveTextWindowApproval, ObservationAdapterConfig
from companion.contracts import CoreError, InboundEvent
from companion.file_evidence import PrivateFilesConfig
from companion.qq_rich import input_digest
from companion.store import encode
from companion.transport import digest
from tests.conftest import event

IMAGE = "sha256:" + "a" * 64


def prepared(core, monkeypatch, *, data=b"first\n\nsecond", filename="notes.txt", count=1,
             config=None):
    """Native-shaped signed source; only the already tested window guard is substituted."""
    key = Ed25519PrivateKey.from_private_bytes(b"k" * 32)
    approval = LiveTextWindowApproval(
        live_approved=True, automatic_reply=True, companion_scope="owner_private_v1",
        companion_approval_sha256="a" * 64, memory_scope="owner_private",
        memory_approval_sha256="b" * 64, history_scope="owner_private",
        history_approval_sha256="c" * 64, max_history_turns=5, max_history_utf8_bytes=8000,
        plan_sha256="d" * 64, window_id="win_" + "e" * 32,
        starts_at=core.clock.now - timedelta(seconds=1),
        ends_at=core.clock.now + timedelta(hours=1), core_config_sha256="f" * 64,
        core_code_sha256="0" * 64, bridge_code_sha256="1" * 64, candidate_sha="2" * 40,
        paid_contract_sha256="3" * 64, budget_journal_id="budget_" + "4" * 32,
        custodian_sha256="5" * 64, approval_sha256="6" * 64,
        capture_public_key=key.public_key().public_bytes_raw().hex(),
        rich_input_scope="references_v1", file_scope="owner_file_v1",
    )
    originals = [{"index": slot, "type": "file", "actor": "UNVERIFIED", "coverage": "UNREAD",
                  "slot": slot, "file_id": f"native-{slot}", "filename": filename,
                  "size": len(data),
                  "source_sha256": hashlib.sha256(str(slot).encode()).hexdigest()}
                 for slot in range(count)]
    source_id = (f"{core.settings.bot_qq_id}:onebot-direct:FriendMessage:"
                 f"{core.settings.owner_qq_id}:epoch:test-file-epoch:1")
    event_id = "evt_" + hashlib.sha256(source_id.encode()).hexdigest()
    body = event(core, text="").model_dump(mode="json")
    body.update(event_id=event_id, source_event_id=source_id)
    body["conversation"]["platform_session_key"] = (
        "onebot-direct:FriendMessage:" + core.settings.owner_qq_id)
    body["payload"].update(
        shared_content=[{"kind": "file", "slot": slot} for slot in range(count)],
        rich={"version": "references_v1", "coverage": "PARTIAL", "segments": originals,
              "segment_count": count, "owner_text_positions": []},
    )
    core.settings.rich_input_scope = "references_v1"
    core.ingest(InboundEvent.model_validate(body), "file-input")
    config = config or PrivateFilesConfig(enabled=True, parser_image=IMAGE)
    core.settings.private_files = config
    core.settings.live_text_window = approval
    core.settings.content_enabled = core.settings.observation_enabled = True
    core.settings.observation_adapters = {config.adapter_id: ObservationAdapterConfig(
        source="qq_file", token="synthetic-file-adapter-token-12345",
    )}
    capture = {"kind": "authenticated_onebot_ws", "window_sha256": approval.plan_sha256,
               "event_id": event_id, "source_event_id": source_id,
               "input_sha256": input_digest(body["payload"]),
               "identity_sha256": digest([core.settings.owner_qq_id, core.settings.bot_qq_id]),
               "protocol_epoch": "test-file-epoch", "received_at": core.clock.now.isoformat(),
               "occurred_at": datetime.fromisoformat(body["occurred_at"]).isoformat()}
    capture["signature"] = key.sign(
        live_text.CAPTURE_SIGNATURE_DOMAIN + encode(capture).encode(),
    ).hex()
    core.store.insert("runtime_meta", key="live_text_input:" + event_id, value=encode({
        "event_id": event_id, "window_sha256": approval.plan_sha256, "privacy_epoch": 0,
        "input_sha256": capture["input_sha256"], "capture": capture,
    }))
    monkeypatch.setattr(live_text, "guard", lambda *args, **kwargs: (
        core.settings.live_text_window, None, 0,
    ))
    return config, approval, key, event_id, originals


def signed_upload(approval, key, event_id, original, claim, data, mime="text/plain"):
    value = {"plan_sha256": approval.plan_sha256, "event_id": event_id,
             "slot": original["slot"], "source_sha256": original["source_sha256"],
             "lease_token": claim["lease_token"], "file_id": original["file_id"],
             "filename": original["filename"], "media_sha256": hashlib.sha256(data).hexdigest(),
             "mime": mime, "bytes": len(data)}
    return value | {"signature": key.sign(qq_files.UPLOAD_DOMAIN + encode(value).encode()).hex()}


def test_file_source_requires_real_capture_and_current_owner(core, monkeypatch):
    _, approval, _, event_id, files = prepared(core, monkeypatch)
    assert qq_files.source(core.store, core.settings, approval, event_id)[2] == files
    receipt = core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                             ("live_text_input:" + event_id,))
    broken = json.loads(receipt["value"])
    broken["capture"]["signature"] = "0" * 128
    core.store.execute("UPDATE runtime_meta SET value=? WHERE key=?",
                       (encode(broken), "live_text_input:" + event_id))
    with pytest.raises(CoreError, match="LIVE_SOURCE_CAPTURE_CHANGED"):
        qq_files.source(core.store, core.settings, approval, event_id)


def test_image_permission_does_not_authorize_files(core, monkeypatch):
    _, approval, _, event_id, _ = prepared(core, monkeypatch)
    with pytest.raises(CoreError, match="PRIVATE_FILE_SCOPE_REQUIRED"):
        qq_files.source(core.store, core.settings,
                        approval.model_copy(update={"file_scope": "none"}), event_id)
    core.store.execute("UPDATE conversations SET reply_revision=reply_revision+1")
    with pytest.raises(CoreError, match="REPLY_SUPERSEDED"):
        qq_files.source(core.store, core.settings, approval, event_id)


@pytest.mark.parametrize("change", [
    {"filename": "../outside.docx"}, {"filename": " "}, {"file_id": "id\x00"},
    {"slot": True}, {"size": 0}, {"size": True}, {"source_sha256": "not-a-hash"},
])
def test_file_manifest_rejects_ambiguous_metadata(change):
    original = {"index": 0, "type": "file", "actor": "UNVERIFIED", "coverage": "UNREAD",
                "slot": 0, "file_id": "native", "filename": "a.txt", "size": None,
                "source_sha256": "a" * 64} | change
    with pytest.raises(CoreError, match="PRIVATE_FILE_MANIFEST_INVALID"):
        qq_files.manifest({"rich": {"segments": [original]},
                           "shared_content": [{"kind": "file", "slot": 0}]})


@pytest.mark.parametrize("field", ["file_id", "filename", "media_sha256", "lease_token",
                                   "source_sha256", "mime", "bytes", "slot"])
def test_upload_signature_binds_source_metadata_and_actual_bytes(core, monkeypatch, field):
    _, approval, key, event_id, files = prepared(core, monkeypatch)
    data, claim = b"payload", {"lease_token": "content_lease_" + "1" * 32}
    proof = signed_upload(approval, key, event_id, files[0], claim, data)
    assert qq_files.upload_proof(approval, event_id, files[0], claim["lease_token"],
                                 hashlib.sha256(data).hexdigest(), "text/plain", len(data), proof)
    proof[field] = 1 if field == "bytes" else True if field == "slot" else "changed"
    with pytest.raises(CoreError, match="PRIVATE_FILE_UPLOAD_SIGNATURE_REQUIRED"):
        qq_files.upload_proof(approval, event_id, files[0], claim["lease_token"],
                              hashlib.sha256(data).hexdigest(), "text/plain", len(data), proof)
