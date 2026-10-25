"""Finish owned fixture HTTP cleanup on Windows CPython 3.12 Proactor transports."""

import socket
import sys

from uvicorn.protocols.http.auto import AutoHTTPProtocol


def native_http_proactor_scope(transport=None, servers=()):
    if (sys.platform != "win32" or sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)):
        return False
    if transport is None:
        return True
    from asyncio.proactor_events import _ProactorBasePipeTransport

    return (
        isinstance(transport, _ProactorBasePipeTransport)
        and all(hasattr(transport, name) for name in (
            "_call_connection_lost", "_sock", "_server", "_called_connection_lost"))
        and callable(transport._call_connection_lost)
        and isinstance(transport._sock, socket.socket)
        and any(transport._server is server for server in servers)
        and callable(getattr(transport._server, "_detach", None))
        and type(getattr(transport._server, "_active_count", None)) is int
    )


def owned_http_protocol(servers):
    """Resolve this fixture's asyncio servers only after Uvicorn starts accepting."""
    class HTTPProtocol(AutoHTTPProtocol):
        def connection_made(self, transport):
            super().connection_made(transport)
            if not native_http_proactor_scope(transport, servers()):
                return
            original = transport._call_connection_lost

            def connection_lost(exc):
                # CPython 3.12 can raise in shutdown() before native close/detach.
                # Complete this owned transport's cleanup and preserve the exception.
                try:
                    original(exc)
                finally:
                    if transport._sock is not None:
                        transport._sock.close()
                        transport._sock = None
                    if transport._server is not None:
                        transport._server._detach()
                        transport._server = None
                    transport._called_connection_lost = True

            transport._call_connection_lost = connection_lost

    return HTTPProtocol
