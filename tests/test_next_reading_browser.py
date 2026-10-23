import asyncio
import os

import pytest

from source_reader.browser import BrowserRenderer
from source_reader.network import ReadFailure
from tests.conftest import run

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPANION_TEST_BROWSER") != "1", reason="Explicit sandboxed Chromium test run"
)
URL = "https://articles.example/story"


def test_generic_html_renders_inline_dom_with_no_subresource_or_network_authority():
    async def scenario():
        renderer = BrowserRenderer(timeout=8)
        document = b"""<html><body><div id="root"></div><script>
          document.getElementById('root').innerHTML = '<article>Inline article body</article>';
          fetch('http://127.0.0.1:1/private').then(() => document.body.innerHTML = 'leaked');
          new WebSocket('ws://127.0.0.1:1/');
          window.open('https://evil.example/');
        </script><script src="https://evil.example/malware.js"></script></body></html>"""
        result = await renderer.html(URL, document)
        assert b"<article>Inline article body</article>" in result
        assert b"<body>leaked" not in result
        assert renderer.active_contexts == 0

    run(scenario())


def test_generic_html_timeout_and_cancellation_close_context():
    async def scenario():
        renderer = BrowserRenderer(timeout=1)
        with pytest.raises((ReadFailure, TimeoutError)):
            await renderer.html(URL, b"<script>while(true){}</script>")
        assert renderer.active_contexts == 0
        renderer.timeout = 8
        task = asyncio.create_task(renderer.html(URL, b"<script>while(true){}</script>"))
        for _ in range(100):
            if renderer.active_contexts:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert renderer.active_contexts == 0
        assert b"healthy" in await renderer.html(URL, b"<article>healthy</article>")

    run(scenario())


def test_rtc_is_locked_before_inline_code_and_in_child_realms():
    async def scenario():
        packets = []

        class Receiver(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                packets.append(data)

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            Receiver, local_addr=("127.0.0.1", 0),
        )
        port = transport.get_extra_info("sockname")[1]
        document = f"""<html><body><iframe id="early"></iframe><script>
          const probe = window.__probe = {{exposed:[], locked:false}};
          const desc = Object.getOwnPropertyDescriptor(window, 'RTCPeerConnection');
          probe.locked = !!desc && desc.value === undefined && !desc.writable && !desc.configurable;
          try {{
            Object.defineProperty(window, 'RTCPeerConnection', {{value:Function}});
          }} catch {{}}
          function inspect(realm, name) {{
            if (!realm) return;
            try {{
              const names = ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCIceTransport'];
              for (const key of names) {{
                if (typeof realm[key] !== 'function') continue;
                probe.exposed.push(name + ':' + key);
                if (key.endsWith('RTCPeerConnection')) {{
                  const pc = new realm[key]({{iceServers:[{{urls:'stun:127.0.0.1:{port}'}}]}});
                  pc.createDataChannel('probe');
                  pc.createOffer().then(o => pc.setLocalDescription(o)).catch(() => {{}});
                }}
              }}
            }} catch {{}}
          }}
          inspect(window, 'main');
          inspect(document.getElementById('early').contentWindow, 'parser-frame');
          const child = document.createElement('iframe');
          document.body.append(child);
          inspect(child.contentWindow, 'dynamic-frame');
          inspect(window.frames[1], 'indexed-frame');
          inspect(window.open('about:blank'), 'popup');
          document.body.insertAdjacentHTML('beforeend',
            '<article>Inline DOM still works</article>');
        </script></body></html>""".encode()
        renderer = BrowserRenderer(timeout=8)
        try:
            # Let asynchronous ICE work run; a busy JS getter can hide the UDP leak by
            # closing Chromium before the createOffer continuation gets a turn.
            result = await renderer._render(URL, document, """async () => {
                await new Promise(resolve => setTimeout(resolve, 1200));
                return {...window.__probe, body:document.querySelector('article')?.textContent};
            }""")
            assert not packets
            assert result == {"locked": True, "exposed": [], "body": "Inline DOM still works"}
            assert renderer.active_contexts == 0
        finally:
            transport.close()

    run(scenario())
