"""Erasure keeps only frozen non-content authority needed to resolve an attempted send."""

import json

import pytest

from companion.contracts import CoreError
from companion.store import encode
from companion.voice_jobs import erase_voice_jobs, fence_conversation, restore_voice_jobs
from tests.test_voice_flow import finish_node, flow_fixture, prepare


@pytest.mark.parametrize("action", ["erase", "restore", "revision"])
def test_voice_fence_removes_audio_but_retains_original_delivery_binding(core, monkeypatch, action):
    flow, jobs, message_id, event_id, _ = flow_fixture(core, monkeypatch)
    prepare(core, flow, message_id)
    finish_node(jobs)
    prepare(core, flow, message_id)
    key = "voice_reply:" + message_id
    original = json.loads(
        core.store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))["value"]
    )
    with core.store.transaction():
        if action == "erase":
            erase_voice_jobs(core.store, [event_id])
        elif action == "restore":
            restore_voice_jobs(core.store)
        else:
            conversation = core.store.one(
                "SELECT conversation_id FROM events WHERE event_id=?", (event_id,)
            )["conversation_id"]
            fence_conversation(core.store, conversation)
    retained = json.loads(
        core.store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))["value"]
    )
    assert set(retained) == {"state", "reason", "job_id", "binding", "binding_sha256"}
    assert retained["state"] == "FENCED"
    assert {name: retained[name] for name in ("job_id", "binding", "binding_sha256")} == {
        name: original[name] for name in ("job_id", "binding", "binding_sha256")
    }
    row = core.store.one("SELECT * FROM voice_jobs WHERE job_id=?", (retained["job_id"],))
    assert row["input_blob"] is None and row["result_blob"] is None
    assert row["result_sha256"] == original["binding"]["result_sha256"]
    with pytest.raises(CoreError, match="VOICE_FLOW_FENCED"):
        prepare(core, flow, message_id)


def test_erasure_does_not_keep_arbitrary_fields_from_corrupt_voice_binding(core, monkeypatch):
    _, _, message_id, event_id, _ = flow_fixture(core, monkeypatch)
    key = "voice_reply:" + message_id
    core.store.insert(
        "runtime_meta",
        key=key,
        value=encode(
            {
                "state": "AUDIO",
                "binding": {"text": "private content"},
                "binding_sha256": "0" * 64,
                "audio_base64": "private audio",
            }
        ),
    )
    with core.store.transaction():
        erase_voice_jobs(core.store, [event_id])
    assert json.loads(
        core.store.one("SELECT value FROM runtime_meta WHERE key=?", (key,))["value"]
    ) == {
        "state": "FENCED",
        "reason": "VOICE_SOURCE_ERASED",
    }
