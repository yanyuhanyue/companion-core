# Owner-authorized SDK runtime

The text conversation slice uses Pydantic AI Slim **2.60.0** for its native model/tool loop,
argument validation, deferred tool requests, and Conversation checkpoints. Core keeps the
original job lease, authority reference, source checks, paid reservation/settlement journal,
Reader queue, and Outbox transaction. Model mappings contain no storage, authority or delivery.

The reviewed dependency base is `7b8def2`. Shared changes are delivered separately for the
integration owners: model/budget/token-measurement, Core hooks, storage/erasure/diary/capability
hooks, and the package/lock/migration patch. The reserved Alembic revision is
`0041_agent_conversations`, whose parent is `0040_owner_authority`. These patches must be
integrated together before the new runtime entry can execute.

## Reused contracts and primary references

- [DSH agent loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts)
  informed using one resolved tool set for prompting and execution; no second custom tool loop.
- [Pydantic AI persistence](https://pydantic.dev/docs/ai/core-concepts/persistence/)
  and the installed 2.60.0 Conversation/Hooks API supply SDK serialization and hook points.
  Core records pending requests before admission and settled responses after the original ledger.
- The inspected DSH agent-loop README blob `28b862ff9dd47f4db9fae29e821687486ab61084`
  distinguishes started calls with unknown outcomes from calls that never started. The existing
  Core accounting journal and native Reader identities retain those distinctions on SDK resume.
- [DSH QQ inbound, pinned c8b9158](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/inbound.ts)
  assembles current and quoted attachments separately into the scoped conversation. Its text
  media references do not establish Core pixel provenance or permission.
- [Pydantic AI multimodal input](https://pydantic.dev/docs/ai/core-concepts/input/)
  supplies BinaryContent/ImageUrl conversion. [Cryptography Ed25519](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)
  supplies signature verification; the native image proof codec is a separate pending shared patch.

## Verification and scope

`tests/test_agent_sdk_entry.py` and `tests/agent_sdk_worker.py` use production modules. They cover
complete-payload tool measurement, the original paid ledger, settled-response recovery without
repeated paid I/O, unknown-effect recovery refusal, erasure replay, tool-purpose revalidation,
Core/Outbox atomic completion, and actual HTTP ingress followed by an independent Python worker.
The same HTTP scenario exercises the original send-check before begin-send, after begin-send,
after content tampering, and after STOP; it never contacts QQ or a paid provider.
`tests/test_agent_owner_authority.py` checks native STOP/revision/lease/source fences with real
empty image-binding tables, without replacing image lookup.

Image read/custody helpers are present for the subsequent media integration, but native image
upload/claim/cleanup/context hooks and nested reference capture are **not completed by this
slice**. The new image-proof shared codec is not in the three SDK/Core dependency patches.
No native image or nested-media acceptance is claimed. This slice's independent-worker evidence
covers the unbound-image text path. The earlier overlay-only tests remain historical evidence;
production-module tests are the acceptance tests for integration.

`0041_agent_conversations` preserves existing accounting and grants during upgrade. Downgrade
requires all four new custody tables to be empty, including images uploaded before the first
conversation. `tests/test_agent_migration.py` exercises actual Alembic upgrade/downgrade and
retention of the original accounting rows. The model payload's tool catalog, calls and results
share the existing measurement and final transport check; see
[the request-counting contract](../qq-beta/p10-provider-evidence-delta.md).

The integration owner retains the single final CI receipt. This lane does not run full PR CI,
activate services, make paid calls, send QQ messages, push a PR, or merge branches.


## Reader selection and continuation

The SDK's `read_content` and `discover_content` tools select existing Reader work.
Owner ingestion records shared-link identities without starting network reads. Neither
browsing words nor continuation phrases dispatch tools. The persisted SDK tool call binds
the original Reader request, and a later authorized turn reuses that request, including a
FAILED or UNKNOWN outcome. Reader reservations, source receipts, and result provenance remain
in the original services. A newer reply revision does not cancel an independently leased read;
source recall, privacy epoch changes, authority STOP and finite Reader expiry still fence it.

The [DSH resolved tool set](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts)
provides the reference for using the same declarations in context and dispatch. The
[Pydantic AI deferred tools API](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)
provides `CallDeferred` and correlated deferred results; the existing `ConversationStore`
persists those bindings. No additional intent parser, worker queue, or retry protocol is added.

The Core integration owner removes the retired owner-exploration import and schedule/wait/context
calls, and the old `Content.followup` call. Reader regression fixtures use real signed v2 input
approval and the HTTP inbox, actual SDK calls, independent Core connections, and mocked external
provider transport. The v1 capture helper remains only for frozen video-contract tests.


## Native capture preparation

`capture.prepare_owner_input(runtime, core_client, onebot, frame, received_at, peer)`
returns `event`, the exact `frame_sha256`, and private `media.images` / `media.files`
descriptors using the existing OriginalImage/OriginalFile fields. It reuses normalization,
bounded reference enrichment, checked HTTP dispatch, and explicit current purpose checks.
The caller supplies the authenticated WebSocket frame and original OneBotClient; OwnerRuntime
owns capture signing, Spool/journal persistence and HTTP admission. The helper does not acquire
attachment bytes or upload them. In particular, the returned descriptors are not Core events.
The pinned DSH inbound reference above separates current and quoted attachments; this slice
prepares direct image/file descriptors, while nested attachment expansion and native upload
acceptance remain pending. The native reference-read transport rejects STOP and modified
request bodies at its final HTTP boundary and rechecks authority after each enrichment batch.


## Native original-image custody

The existing image claim/check/upload routes can dispatch to the native `agent_images`
handlers. Claims reuse Content's byte reservation and finite upload lease. A signed v2 proof
binds authority, event, slot, source digest, lease, original-byte digest and MIME. A known
identical upload returns its existing receipt without extending retention; a lost or uncertain
claim/upload is not permission to create a replacement lease. Upload stages bytes and does not
run a separate vision model. The ordinary SDK chat consumes originals under the existing paid
ledger. GIF/APNG inputs retain original bytes and use the existing bounded Pillow frame-sheet
preparation for the image context; temporal video analysis is a separate capability.

A later owner message may consume the original upload under explicit history and image scopes.
Content initialization and worker recovery retain those originals only until their recorded
expiry. Source recall and erasure remove access and bytes. Source replacement, STOP and expired
upload leases reject before model work. Unknown model calls preserve the original liability and
are not replayed; the existing honest failure notice remains available.

The 0041 positive migration tests create a v1 upload proof with the frozen signing domain and
validate it through the original verifier. The migration preserves canonical proof JSON,
expiry, raw-file bytes and the budget journal, while creating no authority or image-consumer
binding. A current native grant still rejects that v1 proof. Expiry and source withdrawal remove
the retained bytes. Copying evidence therefore does not upgrade its permission.

The Core/API/shared-codec and budget changes are delivered as narrow patches to R1. The image
HTTP and independent SDK-process tests use only synthetic images and mocked provider transport.
Native Bridge queue integration, nested attachment capture, file processing and both video
capabilities have separate remaining acceptance work.

`images.upload_original` now transfers originals using the native purpose check, original
Content claim/check lease, and the shared v2 image signature. It reuses the bounded public-CDN
fetcher and the existing final checked HTTP transport. Request hooks run before exact Core
destination, header and byte checks; source and lease checks run again before upload dispatch.
The [HTTPX transport API](https://www.python-httpx.org/advanced/transports/) supplies the
existing transport composition and external MockTransport test seam. The caller retains
CLAIMING/DOWNLOADING/UPLOADING transitions and must reconcile uncertain effects without replay.
Three real HTTP plus independent SDK-worker cases cover PNG/GIF/APNG through this transfer;
three additional cases cover STOP after DNS, changed upload proof, and lost upload receipt.
The lost-receipt case retains the accepted original and reports failure to the queue owner.
Durable queue wiring remains separate from this verified byte-transfer helper.
