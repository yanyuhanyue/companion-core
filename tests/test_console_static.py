"""Console assets stay compatible with the strict same-origin CSP and build DOM safely."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from companion.api import create_app
from companion.retrieval import SemanticIndex

ORIGIN = "https://console.example.test"
STATIC = Path(__file__).resolve().parents[1] / "companion" / "console_static"


@pytest.fixture
def client(core, monkeypatch):
    core.settings.console.enabled = True
    core.settings.console.public_origin = ORIGIN
    core.settings.control_token = SecretStr("owner-console-static-test-" * 2)
    monkeypatch.setattr(SemanticIndex, "warmup", lambda self: None)
    with TestClient(create_app(core.settings), base_url=ORIGIN) as http:
        yield http


def test_console_assets_are_served_with_private_csp(client):
    for path, kind in (("/console/", "text/html"), ("/console/app.js", "text/javascript"),
                       ("/console/app.css", "text/css"), ("/console/icon.svg", "image/svg+xml")):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith(kind)
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"


def test_index_has_no_inline_script_or_style():
    html = (STATIC / "index.html").read_text("utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert "<style" not in html and " style=" not in html
    assert not re.search(r"\son[a-z]+=", html)


def test_app_builds_dom_without_html_injection_or_foreign_hosts():
    script = (STATIC / "app.js").read_text("utf-8")
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                   "new Function", "localStorage", "sessionStorage"):
        assert unsafe not in script, unsafe
    assert re.findall(r"fetch\(([^,]+),", script) == ['"/v1/console" + path']
    assert "Idempotency-Key" in script and "X-CSRF-Token" in script
