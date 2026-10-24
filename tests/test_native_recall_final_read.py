"""The recall journal read cannot move STOP/lease checks away from final dispatch."""

import pytest

from companion_onebot_bridge import journal, owner_notices
from tests.conftest import run
from tests.support.native_dispatch import attempts, dispatch, staged


@pytest.mark.parametrize("change", ["stop", "lease"])
def test_native_recall_final_read_retains_synchronous_send_fence(
    core, tmp_path, monkeypatch, change,
):
    async def scenario():
        async with staged(core, tmp_path) as (runtime, client, protocol, guard, sends):
            original = owner_notices.recalled
            injected = []

            def after_recall_read(owner, event_id):
                result = original(owner, event_id)
                state = owner.db.execute("SELECT state FROM window_attempts").fetchone()
                if not injected and state and state[0] == "IO_STARTED":
                    injected.append(change)
                    if change == "stop":
                        journal.stop_marker(owner.stop_path)
                    else:
                        core.clock.now = guard.lease_until
                return result

            monkeypatch.setattr(owner_notices, "recalled", after_recall_read)
            assert await dispatch(runtime, client, protocol, guard)
            assert injected == [change]
            assert sends == [] and not guard.dispatched
            # Local STOP/expiry has no Core source-cancellation proof. Preserve
            # uncertain custody even though this process demonstrably did not send.
            assert attempts(runtime) == {"UNKNOWN": 1}
            assert runtime.row()["state"] == "STOPPED"
            assert core.store.one("SELECT state FROM outbox")["state"] == "DELIVERY_UNCERTAIN"
            assert core.store.one("SELECT state FROM model_usage")["state"] == "SETTLED"
            runtime.verify(active=False)

    run(scenario())
