"""Real HTTP/independent worker acceptance; native host sends are synthetic."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from astrbot_plugin_companion_bridge.bridge import Spool, normalize
from astrbot_plugin_companion_bridge.delivery import HostDriver, host_driver
from companion_onebot_bridge.delivery_receipts import DeliveryReceipts
from companion_onebot_bridge.outbound import deliver_tick
from companion_onebot_bridge.spool import Spool as OneBotSpool
from tests.conftest import run
from tests.test_content_bridge import config, event
from tests.test_expression import MemeLibrary, picture
from tests.test_onebot_capture import real_core_http

ROOT = Path(__file__).resolve().parents[1]


class NativeHost:
    def __init__(self, outcome=None):
        self.sends, self.outcome = [], outcome

    async def call_action(self, action, **args):
        assert args["self_id"] == "10002"
        if action == "get_login_info":
            return {"user_id": 10002}
        if action == "get_status":
            return {"online": True, "good": True}
        assert action == "send_private_msg"
        assert args["user_id"] == 10001 and args["auto_escape"] is True
        self.sends.append(args["message"])
        if self.outcome == "ack_lost":
            raise httpx.ReadTimeout("synthetic lost native acknowledgement")
        return True if self.outcome == "boolean" else {"message_id": 42}


def driver(spool, native, library=None):
    platform = SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp"),
                               get_client=lambda: native)
    context = SimpleNamespace(get_platform_inst=lambda name: platform if name == "qq" else None)
    return host_driver(context, config(), spool, library)


@pytest.mark.parametrize("failure", [None, "image", "before_begin", "ack_lost",
                                     "report_lost", "boolean", "wrong_epoch"])
def test_real_http_independent_worker_host_delivery(core, tmp_path, failure):
    core.settings.expression_enabled = failure == "image"
    native = NativeHost(failure)
    path = str(tmp_path / "inbox.sqlite3")
    library = MemeLibrary(tmp_path / "memes", enabled=True)
    if failure == "image":
        picture(tmp_path / "memes")

    async def scenario():
        spool = Spool(path)
        spool.bind_backend("napcat", "")
        key, payload = normalize(event([], text="hello"), "10001", "10002")
        spool.put(key, payload)
        try:
            async with real_core_http(core) as port, httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": "Bearer " +
                         core.settings.bridge_token.get_secret_value()}, trust_env=False,
            ) as client:
                async def verify_lease_identity(request):
                    if request.url.path.endswith("deliveries/lease"):
                        assert json.loads(request.content) == {"worker_id": "astrbot", "limit": 1}

                client.event_hooks["request"].append(verify_lease_identity)
                await spool.flush_one(client)
                settings = core.settings.model_dump(mode="json")
                settings["bridge_token"] = core.settings.bridge_token.get_secret_value()
                child = await asyncio.create_subprocess_exec(
                    sys.executable, "-B", "-m", "tests.test_astrbot_delivery_convergence",
                    "--worker", cwd=str(ROOT),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, errors = await asyncio.wait_for(child.communicate(json.dumps({
                        "settings": settings, "clock": core.clock(),
                    }).encode()), 30)
                    assert child.returncode == 0, errors.decode(errors="replace")
                finally:
                    if child.returncode is None:
                        child.kill()
                        await child.wait()
                sender = driver(spool, native, library)
                if failure == "before_begin":
                    async def forget_before_begin(request):
                        if request.url.path.endswith("begin-send"):
                            with core.store.transaction():
                                core.ledger.expire_events(core.store, [payload["event_id"]],
                                                          core.clock())
                    client.event_hooks["request"].append(forget_before_begin)
                if failure == "report_lost":
                    async def lose_report(response):
                        if response.request.url.path.endswith("/result"):
                            await response.aread()
                            raise httpx.ReadTimeout("synthetic lost result receipt")
                    client.event_hooks["response"].append(lose_report)
                if failure == "wrong_epoch":
                    spool.bind_backend("snowluma", "different-epoch")
                if failure in {"before_begin", "report_lost", "wrong_epoch"}:
                    with pytest.raises((httpx.HTTPStatusError, httpx.ReadTimeout, ValueError)):
                        await deliver_tick(client, sender, spool)
                else:
                    assert await deliver_tick(client, sender, spool)
                assert len(native.sends) == int(failure not in {"before_begin", "wrong_epoch"})
                if native.sends:
                    assert [part["type"] for part in native.sends[0]] == (
                        ["text", "image"] if failure == "image" else ["text"])
                # A new process-local spool must report the original result, never send again.
                spool.db.close()
                spool = Spool(path)
                client.event_hooks["response"].clear()
                if failure == "report_lost":
                    assert await spool.flush_result(client)
                assert not await spool.flush_result(client)
                state = core.store.one("SELECT state FROM outbox")["state"]
                assert state == ({"before_begin": "CANCELLED", "wrong_epoch": "LEASED",
                                  "ack_lost": "DELIVERY_UNCERTAIN",
                                  "boolean": "DELIVERY_UNCERTAIN"}.get(failure, "SENT"))
                if failure != "wrong_epoch":
                    assert not await deliver_tick(client, driver(spool, native, library), spool)
                assert len(native.sends) == int(failure not in {"before_begin", "wrong_epoch"})
        finally:
            spool.db.close()

    run(scenario())


@pytest.mark.parametrize("field", ["voice", "reply_segment", "window_origin_event_id"])
def test_host_rejects_features_without_final_transport_hook(tmp_path, field):
    spool = Spool(str(tmp_path / "inbox.sqlite3"))
    try:
        spool.bind_backend("napcat", "")
        key, payload = normalize(event([]), "10001", "10002")
        spool.put(key, payload)
        host = NativeHost()
        with pytest.raises(ValueError, match="HOST_FINAL_DISPATCH_HOOK_REQUIRED"):
            run(driver(spool, host).prepare_delivery({field: {}, "content": {"text": "hello"}}))
        assert host.sends == []
    finally:
        spool.db.close()


def test_original_inbox_migration_preserves_identity_epoch_and_idempotency(tmp_path):
    path = str(tmp_path / "inbox.sqlite3")
    key, payload = normalize(event([]), "10001", "10002", protocol_epoch="old-epoch")
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE inbox(key TEXT PRIMARY KEY,body TEXT,state TEXT);"
                         "CREATE TABLE protocol_generations(epoch TEXT PRIMARY KEY,backend TEXT);")
        db.execute("INSERT INTO inbox VALUES (?,?,'pending')", (key, body))
        db.execute("INSERT INTO protocol_generations VALUES ('old-epoch','napcat')")
    spool = Spool(path)
    try:
        assert spool.delivery_identity("10001", "10002") == "qq:FriendMessage:10001"
        assert spool.db.execute("SELECT key,body,state FROM inbox").fetchone() == (
            key, body, "pending")
        assert spool.db.execute("SELECT * FROM results").fetchall() == []
        with pytest.raises(ValueError):
            spool.delivery_identity("99999", "10002")
        with pytest.raises(ValueError):
            spool.bind_backend("snowluma", "new-epoch")
        with pytest.raises(ValueError):
            spool.check_backend("snowluma", "old-epoch")
    finally:
        spool.db.close()


def test_both_hosts_share_the_same_receipt_implementation():
    assert Spool.start_delivery is OneBotSpool.start_delivery is DeliveryReceipts.start_delivery
    assert Spool.flush_result is OneBotSpool.flush_result is DeliveryReceipts.flush_result
    assert not hasattr(HostDriver, "deliver")


def test_plugin_zip_contains_shared_dispatch_without_core(tmp_path):
    from scripts.package_bridge import package_bridge

    archive = package_bridge(tmp_path / "bridge.zip")
    target = tmp_path / "isolated_plugin"
    with zipfile.ZipFile(archive) as package:
        package.extractall(target)
        assert not any(name.startswith("companion/") for name in package.namelist())
    script = '''
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'companion', 'companion_onebot_bridge',
                'companion_delivery_protocol', 'companion_meme_protocol'}:
            raise AssertionError('Unbundled runtime import: ' + fullname)
sys.meta_path.insert(0, Guard())
from isolated_plugin.bridge import Spool
from isolated_plugin.delivery import HostDriver
from isolated_plugin._transport.delivery_receipts import DeliveryReceipts
from isolated_plugin._transport.outbound import deliver_item
assert Spool.flush_result is DeliveryReceipts.flush_result
assert callable(deliver_item) and HostDriver.image_scope
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    from companion.config import Settings
    from companion.core import Core
    from companion.model import ModelResponse

    packet = json.loads(sys.stdin.buffer.read())

    class Model:
        async def complete(self, messages, scenario):
            return ModelResponse("reply⬡happy⬡", 20, 10, 0)

    settings = Settings.model_validate(packet["settings"])
    with Core(settings, Model(), lambda: packet["clock"]) as core:
        assert run(core.run_once("independent-host-acceptance"))
