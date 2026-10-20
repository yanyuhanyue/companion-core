"""A standalone Reader's two-GET source, with explicitly supplied account authority.

No upstream Agent, login, cookie discovery, repository, or sending code is imported.
Endpoint/parameter compatibility: astrbot_plugin_xiaoheihe_adapter@90d3c9d4.
"""

import json
import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpcore

from source_reader.accounts import HEYBOX_COMMIT, account_grant
from source_reader.heybox_signing import generate_hkey
from source_reader.network import PublicNetwork, ReadFailure, post_url, response_body

UPSTREAM_COMMIT = HEYBOX_COMMIT
WEB_PARAMS = {
    "os_type": "web",
    "app": "web",
    "client_type": "web",
    "version": "999.0.4",
    "web_version": "2.5",
    "x_client_type": "web",
    "x_app": "heybox_website",
    "x_os_type": "Windows",
    "device_info": "Chrome",
    "_notip": "true",
}


def source_status(raw):
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ReadFailure("UPSTREAM_RESPONSE_INVALID")
    status = str(payload.get("status", "")).lower()
    hint = str(payload.get("msg", "")).lower()
    if status in {"relogin", "login", "need_login"}:
        raise ReadFailure("SOURCE_CREDENTIAL_REVOKED")
    if status == "show_captcha" or "captcha" in hint or "验证码" in hint:
        raise ReadFailure("UPSTREAM_CAPTCHA_REQUIRED")
    if status == "forbidden":
        raise ReadFailure("UPSTREAM_FORBIDDEN")
    if status != "ok":
        raise ReadFailure(
            "UPSTREAM_SIGNATURE_CHANGED" if "hkey" in hint else "UPSTREAM_RESPONSE_INVALID"
        )


class NativeHeyboxSource:
    """One upstream GET per lease, accounted in the existing Core reservation."""

    def __init__(self, account_file=None, pool=None, clock=lambda: datetime.now(UTC)):
        self.account_file, self.clock, self.blocked_grant = account_file, clock, None
        self.pool = pool or httpcore.AsyncConnectionPool(
            network_backend=PublicNetwork(),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.pool.aclose()

    async def get(self, item, budget):
        if item["operation"] == "discover":
            path = "/bbs/app/feeds"
            params = {
                "app": "heybox",
                "pull": 0,
                "offset": 0,
                "limit": min(40, int(item["max_items"])),
            }
        elif item["operation"] == "read":
            path = "/bbs/app/link/tree"
            params = {"link_id": post_url(item["url"]).rsplit("/", 1)[1], "h_src": ""}
        else:
            raise ReadFailure("READER_OPERATION_INVALID")
        grant, fingerprint = account_grant(self.account_file, self.clock())
        if fingerprint == self.blocked_grant:
            raise ReadFailure("SOURCE_DISABLED")
        timestamp, nonce = int(self.clock().timestamp()), secrets.token_hex(16).upper()
        params = {
            **WEB_PARAMS,
            **params,
            "heybox_id": grant["account_id"],
            "device_id": grant["device_id"],
            "_time": timestamp,
            "nonce": nonce,
            "hkey": generate_hkey(path, timestamp, nonce),
        }
        budget.reserve()
        try:
            async with self.pool.stream(
                "GET",
                "https://api.xiaoheihe.cn" + path + "?" + urlencode(params),
                headers={
                    "Accept-Encoding": "identity",
                    "Referer": "https://www.xiaoheihe.cn/",
                    "User-Agent": "CompanionSourceReader/0.6",
                    "Cookie": "; ".join(f"{k}={v}" for k, v in grant["cookies"].items()),
                },
                extensions={"timeout": {key: 8.0 for key in ("connect", "read", "write", "pool")}},
            ) as response:
                if response.status in {401, 403, 429}:
                    raise ReadFailure(
                        {
                            401: "SOURCE_CREDENTIAL_REVOKED",
                            403: "UPSTREAM_FORBIDDEN",
                            429: "UPSTREAM_RATE_LIMITED",
                        }[response.status]
                    )
                raw = await response_body(response, budget)
            _, current = account_grant(self.account_file, self.clock())
            if current != fingerprint:
                raise ReadFailure("SOURCE_CREDENTIAL_REVOKED")
            source_status(raw)
            return raw
        except ReadFailure:
            # A rejected grant/contract is latched until the operator changes the grant.
            # Network uncertainty is handled by the existing worker and is never retried.
            self.blocked_grant = fingerprint
            raise
