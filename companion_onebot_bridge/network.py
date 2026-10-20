import asyncio
import logging
import socket
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .config import private_address
from .onebot_client import http_client

QUIET_LOG = logging.Logger("onebot-private-transport", level=logging.CRITICAL + 1)


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
        address = await resolve_private(parsed.hostname, parsed.port)
        request.headers["Host"] = parsed.netloc
        request.extensions["sni_hostname"] = parsed.hostname
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
