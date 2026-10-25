"""Reuse native Core STOP and exclusive reconciliation custody in recovery tests."""

from types import SimpleNamespace

from companion_onebot_bridge.owner_runtime import OwnerRuntime
from companion_onebot_bridge.spool import Spool

CONTROL_TOKEN = "synthetic-native-recovery-control"


async def stop_host(active):
    result = await active.client.post("/internal/v1/owner-authority/stop", json={
        "authority": active.runtime.authority, "operation_id": "native-recovery-stop"},
        headers={"Authorization": "Bearer " + CONTROL_TOKEN})
    assert result.status_code == 200, result.text
    active.runtime.stop()


def reopen_host(active, monkeypatch):
    assert not active.tasks
    active.spool.close()
    monkeypatch.setattr("companion_onebot_bridge.spool.datetime",
                       SimpleNamespace(now=lambda _: active.core.clock.now))
    spool = Spool(active.config, reconciliation_only=True)
    return OwnerRuntime(spool, clock=lambda: active.core.clock.now)
