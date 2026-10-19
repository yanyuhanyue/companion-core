import asyncio
import os

import pytest

from source_reader.browser import BrowserRenderer
from source_reader.network import ReadFailure
from tests.conftest import run
from tests.test_source_reader import URL

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPANION_TEST_BROWSER") != "1", reason="Explicit sandboxed Chromium test run"
)


def test_browser_executes_inline_metadata_offline_without_subresources(tmp_path):
    async def scenario():
        renderer = BrowserRenderer(timeout=8)
        document = b"""<html><script>
          window.__INITIAL_STATE__ = {videoData:{title:'rendered',desc:'real inline description'}};
          fetch('http://127.0.0.1:1/private').then(()=>{window.__INITIAL_STATE__.videoData.desc='leak'});
          new WebSocket('ws://127.0.0.1:1/');
          window.open('https://evil.example/');
          </script><iframe src="file:///etc/passwd"></iframe>
          <script src="https://api.bilibili.com/x/member/info"></script></html>"""
        result = await renderer.metadata(URL, document)
        assert result["desc"] == "real inline description"
        assert renderer.active_contexts == 0
        result = await renderer.metadata(URL, b'<meta property="og:description" content="next">')
        assert result["desc"] == "next" and result["title"] == ""

    run(scenario())


def test_browser_timeout_closes_session_and_next_task_still_works():
    async def scenario():
        renderer = BrowserRenderer(timeout=1)
        with pytest.raises((ReadFailure, TimeoutError)):
            await renderer.metadata(URL, b"<script>while(true){}</script>")
        assert renderer.active_contexts == 0
        renderer.timeout = 8
        result = await renderer.metadata(URL, b'<meta property="og:title" content="healthy">')
        assert result["title"] == "healthy"

    run(scenario())


def test_browser_cancel_closes_session():
    async def scenario():
        renderer = BrowserRenderer(timeout=15)
        task = asyncio.create_task(renderer.metadata(URL, b"<script>while(true){}</script>"))
        for _ in range(100):
            if renderer.active_contexts:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert renderer.active_contexts == 0

    run(scenario())
