import asyncio
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from companion_onebot_bridge.network import private_http_client, resolve_private, websocket
from companion_onebot_bridge.onebot_client import OneBotClient
from tests.conftest import run
from tests.test_onebot_delivery import provider
from tests.test_onebot_direct import config


@contextmanager
def onebot_http(sends, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer " + token
            body = self.rfile.read(int(self.headers["Content-Length"]))
            response = provider(
                httpx.Request("POST", "http://mock" + self.path, content=body), sends
            )
            self.send_response(response.status_code)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_loopback_http_probe_and_owner_send_are_bounded_and_authenticated():
    sends = []
    with onebot_http(sends, config().onebot_token) as port:
        cfg = config(
            onebot_http_url=f"http://127.0.0.1:{port}",
            onebot_ws_url="ws://127.0.0.1:3001",
            delivery_enabled=True,
        )

        async def scenario():
            async with private_http_client(cfg.onebot_http_url, cfg.onebot_token) as client:
                onebot = OneBotClient(cfg, client)
                assert (await onebot.probe())["connection_ready"]
                assert await onebot.send_text("literal [CQ:image,file=untrusted]") == "123456"

        run(scenario())
    assert len(sends) == 1
    assert sends[0]["message"] == [
        {"type": "text", "data": {"text": "literal [CQ:image,file=untrusted]"}},
    ]


def test_public_dns_answer_is_rejected_before_any_authenticated_io(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()

        async def public_answer(*args, **kwargs):
            return [(2, 1, 6, "", ("8.8.8.8", 3000))]

        monkeypatch.setattr(loop, "getaddrinfo", public_answer)
        with pytest.raises(ValueError, match="PUBLIC_DNS"):
            await resolve_private("snowluma", 3000)

    run(scenario())


def test_websocket_redirect_is_not_followed():
    calls = []

    async def scenario():
        async def redirect(connection, request):
            calls.append(request.path)
            response = connection.respond(302, "redirect")
            response.headers["Location"] = "ws://public.example:3001/"
            return response

        async def unused(ws):
            pytest.fail("redirect must not reach application")

        async with serve(unused, "127.0.0.1", 0, process_request=redirect) as server:
            port = server.sockets[0].getsockname()[1]
            cfg = config(
                onebot_ws_url=f"ws://127.0.0.1:{port}", onebot_http_url="http://127.0.0.1:3000"
            )
            with pytest.raises(InvalidStatus):
                async with await websocket(cfg):
                    pytest.fail("redirect accepted")

    run(scenario())
    assert calls == ["/"]
