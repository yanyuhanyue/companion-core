"""Offline nonroot/fresh-volume image smoke; no sockets, QQ, or Core dependency."""

import importlib.util

from companion_onebot_bridge.config import BridgeConfig
from companion_onebot_bridge.spool import Spool

assert importlib.util.find_spec("astrbot") is None
assert importlib.util.find_spec("companion") is None
config = BridgeConfig(
    owner_qq_id="10001",
    bot_qq_id="10002",
    bridge_token="SYNTHETIC_CORE_TOKEN_32_CHARACTERS",
    onebot_token="SYNTHETIC_ONEBOT_TOKEN_32_CHARACTERS",
    protocol_epoch="offline-image-smoke-20261020",
)
payload = {
    "event_id": "evt_offline_image_smoke",
    "trace_id": "trace_" + "a" * 64,
    "payload": {"text": "Synthetic image smoke only"},
}
with Spool(config) as spool:
    assert spool.put(payload)
with Spool(config) as spool:
    assert not spool.put(payload)
    assert spool.pending_count == 1
print("PASS OFFLINE_FIXTURE: isolated nonroot image, fresh volume and durable restart")
