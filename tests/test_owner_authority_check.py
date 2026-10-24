"""Bridge may check an exact grant purpose, but cannot create or widen authority."""

import pytest
from fastapi.testclient import TestClient

from companion import owner_authority
from companion.api import create_app
from companion.owner_authority_protocol import AuthorityStop
from tests.test_owner_input import captured


@pytest.mark.parametrize("boundary", ["active", "stop", "restore", "purpose", "revision"])
def test_bridge_current_check_is_exact_and_does_not_mint_an_execution_lease(
    core, tmp_path, boundary,
):
    capture, _ = captured(core, tmp_path)
    authority = dict(capture.authority)
    if boundary == "stop":
        owner_authority.stop(core.store, core.settings, core.clock, AuthorityStop(
            operation_id="check-stop-authority", authority=authority))
    elif boundary == "restore":
        owner_authority.restore(core.store)
    elif boundary == "revision":
        authority["revision"] += 1
    app = create_app(core.settings)
    app.state.core = core
    client = TestClient(app)
    body = {"authority": authority, "purpose": "memory" if boundary == "purpose" else "chat"}
    path = "/internal/v1/owner-authority/check"
    assert client.post(path, json=body).status_code == 401
    response = client.post(path, json=body, headers={
        "Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()})
    if boundary == "active":
        assert response.status_code == 200, response.text
        assert response.json() == {"approved": True, **body, "expires_at": None}
        assert response.headers["Cache-Control"] == "no-store"
    else:
        assert response.status_code in {403, 409}, response.text
    assert not core.store.rows("SELECT * FROM jobs")
    assert not core.store.rows("SELECT * FROM model_usage")
    assert not core.store.rows("SELECT * FROM outbox")
