"""Real owned HTTP transport cleanup on the fixture's Windows Proactor scope."""

import asyncio

import httpx
import pytest
from uvicorn.protocols.http.auto import AutoHTTPProtocol

from tests.test_onebot_capture import _native_http_proactor_scope, real_core_http


@pytest.mark.skipif(not _native_http_proactor_scope(),
                    reason="Windows CPython 3.12 Proactor cleanup compatibility")
@pytest.mark.parametrize("failure", [None, "reset", "other"])
def test_native_http_closes_real_socket_and_preserves_callback_error(core, monkeypatch, failure):
    transports, servers, sockets, errors, lost = [], [], [], [], []
    made, connection_lost = AutoHTTPProtocol.connection_made, AutoHTTPProtocol.connection_lost

    def observe(self, transport):
        transports.append(transport)
        servers.append(transport._server)
        sockets.append(transport._sock)
        return made(self, transport)

    def observe_lost(self, error):
        lost.append(self)
        return connection_lost(self, error)

    monkeypatch.setattr(AutoHTTPProtocol, "connection_made", observe)
    monkeypatch.setattr(AutoHTTPProtocol, "connection_lost", observe_lost)
    expected = None
    if failure == "reset":
        expected = ConnectionResetError("synthetic shutdown reset")
        expected.winerror = 10054
    elif failure == "other":
        expected = OSError("synthetic shutdown failure")

    class FailedShutdown:
        def __init__(self, sock):
            self.sock = sock

        def shutdown(self, how):
            raise expected

        def __getattr__(self, name):
            return getattr(self.sock, name)

    async def scenario():
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()

        def handler(loop, context):
            errors.append(context.get("exception"))
            if previous is None:
                loop.default_exception_handler(context)
            else:
                previous(loop, context)

        loop.set_exception_handler(handler)
        try:
            async with real_core_http(core) as port:
                async with httpx.AsyncClient(trust_env=False) as client:
                    assert client._transport._pool._keepalive_expiry == 5
                    response = await client.get(
                        f"http://127.0.0.1:{port}/internal/v1/health/live")
                    assert response.status_code == 200 and len(transports) == 1
                    assert _native_http_proactor_scope(transports[0], servers)
                    assert not _native_http_proactor_scope(transports[0], ())
                    assert sockets[0].fileno() >= 0
                    if expected is not None:
                        transports[0]._sock = FailedShutdown(sockets[0])
            assert len(lost) == 1
            assert errors == ([] if expected is None else [expected])
            assert sockets[0].fileno() == -1
            assert transports[0]._sock is None and transports[0]._server is None
            assert transports[0]._called_connection_lost
            assert servers[0]._active_count == 0
        finally:
            for sock in sockets:
                sock.close()
            loop.set_exception_handler(previous)

    asyncio.run(scenario())
