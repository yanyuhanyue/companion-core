"""Owner control routes; transport credentials cannot create or widen permission."""

from companion import owner_authority
from companion.owner_authority_protocol import AuthorityChange, AuthorityPreview, AuthorityStop


def register_owner_authority_routes(app, control_auth):
    prefix = "/internal/v1/owner-authority"

    @app.post(prefix + "/preview", dependencies=control_auth)
    async def preview(body: AuthorityPreview):
        core = app.state.core
        return owner_authority.preview(core.store, core.settings, core.clock, body)

    @app.post(prefix + "/activate", dependencies=control_auth)
    @app.post(prefix + "/revisions", dependencies=control_auth)
    async def activate(body: AuthorityChange):
        core = app.state.core
        return owner_authority.activate(core.store, core.settings, core.clock, body)

    @app.post(prefix + "/stop", dependencies=control_auth)
    async def stop(body: AuthorityStop):
        core = app.state.core
        return owner_authority.stop(core.store, core.settings, core.clock, body)

    @app.get(prefix + "/status", dependencies=control_auth)
    async def status():
        core = app.state.core
        return owner_authority.status(core.store, core.settings, core.clock)
