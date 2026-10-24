"""Original signed input remains inspectable without reviving stopped work."""

import pytest

from companion import owner_authority, owner_input, owner_input_state
from companion.contracts import CoreError
from companion.owner_authority_protocol import AuthorityStop
from tests.test_owner_input import captured, inbound


def test_native_input_state_requires_original_capture_and_terminal_work(core, tmp_path):
    capture, _ = captured(core, tmp_path)
    owner_input.approve(core.store, core.settings, core.clock, capture)
    source = core.ingest(inbound(core, capture), "input-state-source")
    pending = owner_input_state.read(core.store, core.settings, capture)
    assert not pending["terminal"] and pending["unsettled_work"]
    owner_authority.stop(core.store, core.settings, core.clock,
        AuthorityStop(operation_id="stop-input-state", authority=capture.authority))
    core.store.execute("UPDATE jobs SET state='CANCELLED' WHERE job_id=?", (source["job_id"],))
    result = owner_input_state.read(core.store, core.settings, capture)
    assert result["terminal"] and result["authority"] == capture.authority
    assert result["input_sha256"] == capture.input_sha256
    changed = capture.model_copy(update={"input_sha256": "f" * 64})
    with pytest.raises(CoreError, match="OWNER_CAPTURE_INVALID"):
        owner_input_state.read(core.store, core.settings, changed)


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
