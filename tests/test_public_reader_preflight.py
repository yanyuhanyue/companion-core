"""The real worker/HTTPCore request boundary rechecks public card read authority."""

import ssl

import httpcore
import httpx
import pytest

from companion import live_text
from companion.api import create_app
from source_reader.heybox import HeyboxReader
from source_reader.network import PublicHTTP, PublicNetwork
from source_reader.worker import tick
from tests.conftest import run
from tests.test_live_card_content import TOKEN, captured
from tests.test_source_reader import RecordingStream, Responses, response


@pytest.mark.parametrize("stop_at", [None, "after_lease", "dns", "tls"])
def test_public_card_worker_rechecks_core_after_network_wait_before_get(core, tmp_path, stop_at):
    async def scenario():
        async with captured(core, tmp_path) as (window, _, _, _):
            def stop():
                live_text.stop_window(core.store, live_text.WindowReference(
                    window_sha256=window.plan.sha256,
                ), core.clock)

            class Stream(RecordingStream):
                async def start_tls(self, *args, **kwargs):
                    if stop_at == "tls":
                        stop()
                    return await super().start_tls(*args, **kwargs)

            class Backend(Responses):
                async def connect_tcp(self, host, port, **kwargs):
                    self.hosts.append(host)
                    return Stream(self.rows.pop(0), self)

            async def resolver(host, port):
                assert (host, port) == ("www.xiaoheihe.cn", 443)
                if stop_at == "dns":
                    stop()
                return ["1.1.1.1"]

            validations = []

            async def after_core(response):
                await response.aread()
                if response.request.url.path.endswith("/lease") and stop_at == "after_lease":
                    stop()
                if response.request.url.path.endswith("/validate"):
                    validations.append(response.status_code)

            backend = Backend(response(
                "<title>公开耳机评测</title><article><p>作者比较两款耳机的佩戴与隔音体验。</p></article>"
                .encode(),
            ))
            pool = httpcore.AsyncConnectionPool(
                ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                network_backend=PublicNetwork(resolver, backend), max_keepalive_connections=0,
            )
            api = create_app(core.settings)
            api.state.core = core
            async with PublicHTTP(pool) as public, httpx.AsyncClient(
                base_url="http://core", transport=httpx.ASGITransport(api),
                headers={"Authorization": "Bearer " + TOKEN},
                event_hooks={"response": [after_core]},
            ) as client:
                if stop_at:
                    with pytest.raises(httpx.HTTPStatusError):
                        await tick(client, HeyboxReader(public, clock=core.clock), "reader")
                    assert len(validations) == 1 and validations[0] != 200
                    assert backend.requests == []
                    assert not core.store.one("SELECT 1 FROM observations")
                else:
                    assert await tick(client, HeyboxReader(public, clock=core.clock), "reader")
                    assert validations == [200] and len(backend.requests) == 1
                    saved = core.store.one("SELECT state FROM exploration_requests")
                    assert saved["state"] == "DONE"
                # Connections go through the existing public DNS pinning backend.
                assert backend.hosts == ["1.1.1.1"]
                assert not core.store.one("SELECT 1 FROM model_usage")

    run(scenario())
