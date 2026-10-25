"""Original signed input remains inspectable without reviving stopped work."""

import pytest

from companion import owner_authority, owner_input, owner_input_state
from companion.contracts import CoreError
from companion.owner_authority_protocol import AuthorityStop
from companion.store import encode
from companion_onebot_bridge.owner_handoff import terminal
from tests.test_owner_input import captured, inbound


def test_native_input_state_requires_original_capture_and_terminal_work(core, tmp_path):
    capture, _ = captured(core, tmp_path)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    source = core.ingest(inbound(core, capture), "input-state-source")
    pending = owner_input_state.read(core.store, core.settings, capture)
    assert not pending["terminal"] and pending["unsettled_work"]
    assert pending["reply_revision"] == 1
    core.store.execute("UPDATE events SET reply_revision=3 WHERE event_id=?", (capture.event_id,))
    revised = owner_input_state.read(core.store, core.settings, capture)
    assert revised["reply_revision"] == 3
    assert revised["work_sha256"] == pending["work_sha256"]
    owner_authority.stop(core.store, core.settings, core.clock,
        AuthorityStop(operation_id="stop-input-state", authority=capture.authority))
    core.store.execute("UPDATE jobs SET state='CANCELLED' WHERE job_id=?", (source["job_id"],))
    result = owner_input_state.read(core.store, core.settings, capture)
    assert result["terminal"] and result["authority"] == capture.authority
    assert result["input_sha256"] == capture.input_sha256
    terminal(result, capture.model_dump(mode="json"))
    historical = {key: value for key, value in result.items() if key != "reply_revision"}
    terminal(historical, capture.model_dump(mode="json"))
    for revision in (-1, True, "3", None):
        with pytest.raises(ValueError, match="OWNER_INPUT_TERMINAL_REQUIRED"):
            terminal({**result, "reply_revision": revision}, capture.model_dump(mode="json"))
    changed = capture.model_copy(update={"input_sha256": "f" * 64})
    with pytest.raises(CoreError, match="OWNER_CAPTURE_INVALID"):
        owner_input_state.read(core.store, core.settings, changed)


def test_unadmitted_stopped_input_has_zero_revision_without_making_work(core, tmp_path):
    capture, _ = captured(core, tmp_path)
    owner_authority.stop(core.store, core.settings, core.clock,
        AuthorityStop(operation_id="stop-unadmitted-input", authority=capture.authority))
    before = list(core.store.db.iterdump())
    result = owner_input_state.read(core.store, core.settings, capture)
    assert result["reply_revision"] == 0 and result["state"] == "CANCELLED"
    terminal(result, capture.model_dump(mode="json"))
    assert list(core.store.db.iterdump()) == before


def test_native_input_state_never_treats_unknown_delivery_as_complete(core, tmp_path):
    from tests.test_owner_delivery_reconciliation import pending

    proof, _ = pending(core, tmp_path)
    import json

    from companion.owner_authority_protocol import OwnerCapture

    saved = core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                           (owner_input.INPUT + proof["event_id"],))
    capture = OwnerCapture.model_validate(json.loads(saved["value"])["capture"])
    result = owner_input_state.read(core.store, core.settings, capture)
    assert not result["terminal"] and result["unsettled_delivery"]


@pytest.mark.parametrize("kind", ["content", "reader", "video", "voice", "cleanup"])
def test_unknown_or_unconfirmed_source_work_cannot_authorize_handoff(core, tmp_path, kind):
    capture, _ = captured(core, tmp_path)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    accepted = core.ingest(inbound(core, capture), "unknown-source-work")
    core.store.execute("UPDATE jobs SET state='CANCELLED' WHERE job_id=?", (accepted["job_id"],))
    event = core.store.one("SELECT * FROM events WHERE event_id=?", (capture.event_id,))
    now = core.clock()
    if kind in {"content", "reader", "video"}:
        core.store.insert("exploration_requests",
            request_id="read-original", request_key="read-key",
            adapter_id="bili", operation="read", url="https://example.com/source",
            state="UNKNOWN" if kind == "reader" else "DONE", captured_privacy_epoch=0,
            created_at=now, updated_at=now, reserved_bytes=100, used_bytes=40)
        core.store.insert("content_items", item_id="content-original",
            owner_event_id=capture.event_id,
            kind="link", slot=0, read_request_id="read-original", target_url="https://example.com/source",
            state="UNKNOWN" if kind == "content" else "SUCCEEDED", captured_privacy_epoch=0,
            deadline=now, created_at=now, updated_at=now, reserved_bytes=100)
        if kind == "video":
            core.store.insert("bili_video_items",
                item_id="content-original", request_id="read-original",
                source_event_id=capture.event_id, owner_event_id=capture.event_id,
                job_id=accepted["job_id"], adapter_id="bili", source_sha256="a" * 64,
                source_snapshot_sha256="b" * 64, metadata_json="{}", source_grant_sha256="c" * 64,
                policy_sha256="d" * 64, window_sha256="", binding_sha256="e" * 64,
                captured_privacy_epoch=0, deadline=now, state="UNKNOWN",
                created_at=now, updated_at=now)
    else:
        core.store.insert("voice_jobs", job_id="voice-original", operation="asr",
            event_id=capture.event_id, native_source_event_id=event["source_event_id"],
            conversation_id=event["conversation_id"], source_segment_index=0,
            captured_privacy_epoch=0, captured_reply_revision=event["reply_revision"],
            node_id="local", config_json="{}", state="UNKNOWN" if kind == "voice" else "CANCELLED",
            input_mime="audio/wav", source_partial=0,
            created_at=now, updated_at=now, deadline_at=now,
            content_retired=1, cleanup_state="CONFIRMED" if kind == "voice" else "PENDING",
            authority_json=encode(capture.authority))
    before = {table: core.store.rows(f"SELECT * FROM {table}") for table in (
        "content_items", "exploration_requests", "bili_video_items", "voice_jobs", "model_usage")}
    result = owner_input_state.read(core.store, core.settings, capture)
    assert result["unsettled_work"] and not result["terminal"]
    with pytest.raises(ValueError, match="OWNER_INPUT_TERMINAL_REQUIRED"):
        terminal(result, capture.model_dump(mode="json"))
    assert before == {table: core.store.rows(f"SELECT * FROM {table}") for table in before}
    if kind == "cleanup":
        core.store.execute("UPDATE voice_jobs SET cleanup_state='CONFIRMED'")
        assert owner_input_state.read(core.store, core.settings, capture)["terminal"]
