# Console reliability audit — 2026-10-24

Scope: independent console-reliability-audit branch from main85c98ec. Root console_static
changes and PR102 remain untouched. No deployment, paid/QQ call, new full CI or schedule.

## Confirmed and repaired

1. Reverse-proxy login attempts shared the proxy IP because serve forced proxy_headers=False.
   The entrypoint now passes explicit fixed proxy IPs to native Uvicorn. Default trust is empty,
   even when the environment supplies FORWARDED_ALLOW_IPS=*. Actual CLI/TCP checks cover source
   isolation, direct-client spoofing and a spoofed leftmost XFF value. No new auth or global backoff.
2. ConsoleConfig contained one deployment's origin as a default. It is now empty while disabled;
   enabling requires an explicit HTTPS origin. The Nginx example publishes only /console/ and
   /v1/console/. It is a deployment template, not a tested/live proxy or certificate configuration.
3. Write-only null updates could retain a credential when changing its destination. Operator
   updates require explicit credential selection when an existing key's endpoint changes and
   reject moving the same old key across hosts. Edited URLs require HTTPS. This is configuration
   error prevention, not proof of past credential disclosure. PaidModelApproval already requires
   HTTPS and paid_budget.contract pins the endpoint; ModelClient binds URL/Host/Authorization and
   disables redirects. Real TCP configuration plus an independent native worker proved zero
   provider calls under the unchanged old paid contract (PAID_PROVIDER_CONTRACT_MISMATCH).
4. Mutable GitHub Action tags are pinned to their current upstream commits, without a major
   upgrade: checkout v4=11d5960a326750d5838078e36cf38b85af677262,
   setup-python v5=a26af69be951a213d495a4c3e4e4022e16d87065,
   cache v4=0057852bfaa89a56745cba8c7296529d2fc39830,
   upload-artifact v4=ea165f8d65b6e75b540449e92b4886f43607fa02.

## Confirmed receipt gap — approved finite repair

The original HTTP probe reproduced MEMORY_NOT_FOUND/404 followed by a same-key
CONSOLE_OPERATION_RECONCILIATION_REQUIRED/409: STARTED had been retained for a definite refusal.
The user approved the limited durable refusal extension. Existing console_operations now records
REJECTED with only code/status, and the same fingerprint replays that error without native effects.
The existing state column is unconstrained TEXT, so no schema migration is needed.

Caller-specific reviewed errors:

| Native call | Definite pre-effect errors | Proof boundary |
|---|---|---|
| Memory.decide | MEMORY_NOT_FOUND, MEMORY_NOT_ACTIVE, CORRECTION_REQUIRED, CREDENTIAL_MEMORY_DENIED | Before correction writes/ledger append; native transaction unwinds before receipt handling |
| DiaryService.correct | INVALID_DIARY_CORRECTION | Before expire_evidence or its write transaction |
| DiaryService.set_mode | DIARY_DISABLED_IN_CONFIG | Before the setter transaction |
| Proactive mode setter | PROACTIVE_DISABLED_IN_CONFIG | Before its transaction |

DIARY_NOT_FOUND is deliberately excluded because expiration cleanup can already have happened.
Every unlisted CoreError, other exception, and process-death gap stays STARTED; no timer deletes it
or infers safe replay. Delivery reconciliation retains its native receipt recovery callback.
Tests verify each rejection leaves native state unchanged, replay never calls the service again,
and an actual child-process exit after a native commit remains fenced after restart. Real serve/TCP
checks verify terminal rejection behavior alongside trusted proxy attribution.

## Engineering evidence and limits

The main Dockerfile copies sources before uv sync, invalidating the dependency layer on source
changes. The source-reader image already separates dependencies. The official uv remedy is
--no-install-project before source COPY, followed by the original project sync. Local Docker
Desktop Linux engine is unavailable. The user subsequently supplied a remote Docker host with
running services. A dedicated buildx docker-container builder, limited to one CPU and 2 GiB RAM,
compared the tracked source at 89a71d3 with the single dependency-layer addition. Each variant
received a cache-only seed, then a source-only comment change and an image-load build. Baseline
source-change build took 155.202 seconds; the layered version took 134.270 seconds (13.5% less).
The final sync/user setup step fell from 28.4 to 6.2 seconds; the intermediate dependency step
was cached. Image export still dominated both runs. Seed times (63.982 and 42.947 seconds) are
not a cold-build comparison because the later variant shared the Python/pip layers.

Both images passed network-disabled imports of all six packaged modules and UID 10001 checks.
Installed package/version inventories were compared for equality. The measured dependency-layer
addition is now in Dockerfile. This single-host, single-run result is scoped to source-only
rebuilds; fresh CI without persisted layers would not automatically gain this benefit. The
dedicated builder and two uniquely tagged images were removed successfully; existing services
were not changed. Local benchmark logs and inventories are linked from the audit receipt.

The session migrated_database fixture and SQLite backup already exist in tests/conftest.py.
PR102 already measured full disjoint file shards and receipt reuse; neither is reimplemented.
No xdist, scheduler redesign, blanket complexity gate or document purge is introduced. Counts
of functions, old designs or evidence JSON do not themselves establish defects.

A periodic full run costs about47.3 summed job-minutes using PR102's single-run measurement:
weekly about3.4 job-hours/month; daily about23.6. This is not a billing quote or timing guarantee.
No recurring workflow is added until the user selects frequency and cost.

## Primary sources and reused mechanisms

- [Uvicorn settings](https://uvicorn.dev/settings/) and installed0.54.0 proxy_headers.py:
  native trusted peers and rightmost untrusted client selection;
  [Starlette Requests](https://www.starlette.io/requests/) supplies client.host from ASGI scope.
- [Nginx proxy headers](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)
  supports explicit peer forwarding for the one-proxy template.
- DSH [browser-auth.ts](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/client/connection/src/browser-auth.ts)
  and [authentication decision](https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/architecture/2026-08-24-browser-token-authentication.md)
  were inspected: loopback/routing are not authentication. Existing owner/session/CSRF remains
  independent of forwarding. No second DSH authentication stack is imported.
- [HTTPX clients](https://www.python-httpx.org/advanced/clients/) plus actual local ModelClient and
  paid_budget source: configured credentials are distinct from admitted final transport.
- [uv Docker](https://docs.astral.sh/uv/guides/integration/docker/) and
  [Docker cache](https://docs.docker.com/build/cache/optimize/): dependency layer ordering and reuse.
- [GitHub secure use](https://docs.github.com/en/actions/reference/security/secure-use): full
  commit pinning. DSH CI was inspected; its runner fleet/version tags are not copied.

One local audit receipt records XML identities and limits. Batch counts overlap and are not
summed. No full local suite or new GitHub CI run was started for this audit.
