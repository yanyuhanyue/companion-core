import asyncio
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect

from .config import private_address
from .onebot_client import http_client

QUIET_LOG = logging.Logger("onebot-private-transport", level=logging.CRITICAL + 1)


@dataclass(frozen=True, repr=False)
class PrivateDestination:
    authority: str
    scheme: str
    hostname: str
    port: int
    address: str


def private_request_matches(request, url, path, *, synthetic=False):
    """Bind original authority/Host/SNI to the checked TCP destination after DNS."""
    original = urlsplit(url)
    expected = httpx.URL(url.rstrip("/") + path)
    pin = request.extensions.get("companion_private_destination")
    if isinstance(pin, PrivateDestination):
        return (
            (pin.authority, pin.scheme, pin.hostname, pin.port)
            == (original.netloc, original.scheme, original.hostname, original.port)
            and private_address(pin.address) and request.url.host == pin.address
            and request.headers.get("Host") == original.netloc
            and request.extensions.get("sni_hostname") == original.hostname
            and request.url.copy_with(host=original.hostname) == expected
        )
    # Pure MockTransport is allowed only for the explicitly loopback synthetic route.
    return (synthetic and original.hostname in {"127.0.0.1", "::1", "localhost"}
            and request.url == expected and request.headers.get("Host") == original.netloc)


async def resolve_private(host, port):
    addresses = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 5
    )
    if not addresses or any(not private_address(item[4][0]) for item in addresses):
        raise ValueError("PUBLIC_DNS_REFUSED")
    return addresses[0][4][0]


def private_http_client(url, token):
    parsed = urlsplit(url)

    async def pin(request):
        # Resolve and validate BEFORE auth leaves this process; pin the actual TCP destination.
        original = httpx.URL(url)
        if (request.url.scheme, request.url.host, request.url.port) != (
            original.scheme, original.host, original.port
        ):
            raise ValueError("PRIVATE_HTTP_AUTHORITY_CHANGED")
        address = await resolve_private(parsed.hostname, parsed.port)
        if not private_address(address):
            raise ValueError("PUBLIC_DNS_REFUSED")
        request.headers["Host"] = parsed.netloc
        request.extensions["sni_hostname"] = parsed.hostname
        request.extensions["companion_private_destination"] = PrivateDestination(
            parsed.netloc, parsed.scheme, parsed.hostname, parsed.port, address
        )
        request.url = request.url.copy_with(host=address)

    client = http_client(url, token)
    client.event_hooks["request"] = [pin]
    return client


class NoRedirectConnect(connect):
    def process_redirect(self, exc):
        return exc


async def websocket(config):
    parsed = urlsplit(config.onebot_ws_url)
    address = await resolve_private(parsed.hostname, parsed.port)
    return NoRedirectConnect(
        config.onebot_ws_url,
        host=address,
        port=parsed.port,
        additional_headers={"Authorization": "Bearer " + config.onebot_token},
        proxy=None,
        max_size=config.max_frame_bytes,
        max_queue=4,
        compression=None,
        open_timeout=10,
        close_timeout=3,
        logger=QUIET_LOG,
    )
