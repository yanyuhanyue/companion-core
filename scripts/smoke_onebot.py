"""Offline nonroot/fresh-volume image smoke; no sockets, QQ, or Core dependency."""

import importlib.util
import os
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from companion_onebot_bridge.capture import SIGNATURE_DOMAIN
from companion_onebot_bridge.config import BridgeConfig
from companion_onebot_bridge.spool import Spool

assert importlib.util.find_spec("astrbot") is None
assert importlib.util.find_spec("companion") is None
assert os.geteuid() == 10004
capture_key = Ed25519PrivateKey.generate()
config = BridgeConfig(
    owner_qq_id="10001",
    bot_qq_id="10002",
    bridge_token="SYNTHETIC_CORE_TOKEN_32_CHARACTERS",
    onebot_token="SYNTHETIC_ONEBOT_TOKEN_32_CHARACTERS",
    protocol_epoch="offline-image-smoke-20261020",
    capture_signing_key=capture_key.private_bytes_raw().hex(),
)
signed = SIGNATURE_DOMAIN + b"synthetic-image-smoke"
Ed25519PublicKey.from_public_bytes(bytes.fromhex(config.capture_public_key)).verify(
    capture_key.sign(signed), signed,
)
payload = {
    "event_id": "evt_offline_image_smoke",
    "trace_id": "trace_" + "a" * 64,
    "payload": {"text": "Synthetic image smoke only"},
}
if "--wrong-owner" in sys.argv:
    assert Path(config.data_dir).stat().st_uid == 0
    assert stat.S_IMODE(Path(config.data_dir).stat().st_mode) == 0o700
    try:
        with Spool(config):
            raise AssertionError("wrong-owner volume must be rejected")
    except PermissionError:
        print("PASS OFFLINE_FIXTURE: wrong-owner volume refused by UID 10004")
    raise SystemExit(0)
with Spool(config) as spool:
    assert spool.put(payload)
    directory = Path(config.data_dir)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for file in directory.iterdir():
        assert file.stat().st_uid == os.geteuid()
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
with Spool(config) as spool:
    assert not spool.put(payload)
    assert spool.pending_count == 1
print("PASS OFFLINE_FIXTURE: isolated nonroot image, fresh volume and durable restart")
