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


def test_console_assets_delegate_only_after_native_gate(client):
    for path, asset in (("/console/", "index.html"), ("/console/app.js", "app.js"),
                        ("/console/app.css", "app.css"), ("/console/icon.svg", "icon.svg")):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["x-accel-redirect"] == "/_console_assets/" + asset
        assert response.content == b""  # No packaged resource fallback.
        assert "content-security-policy" not in response.headers
        assert "cache-control" not in response.headers


@pytest.mark.parametrize("path", ["/console/", "/console/app.js", "/console/unknown"])
@pytest.mark.parametrize("state,status,error", [
    ("disabled", 404, "CONSOLE_DISABLED"),
    ("no-credential", 503, "CONSOLE_OWNER_CREDENTIAL_REQUIRED"),
])
def test_console_static_refusals_never_delegate(client, core, path, state, status, error):
    if state == "disabled":
        core.settings.console.enabled = False
    else:
        core.settings.control_token = SecretStr("")
    response = client.get(path)
    assert response.status_code == status and response.json() == {"error": error}
    assert "x-accel-redirect" not in response.headers


@pytest.mark.parametrize("path", ["index.html", "unknown", "app.js/extra", "%2e%2e/config.toml"])
def test_console_rejects_non_public_assets(client, path):
    response = client.get("/console/" + path)
    assert response.status_code == 404
    assert "x-accel-redirect" not in response.headers


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
