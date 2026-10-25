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

Native conversation history also keeps a digest of each actual selected input and delivered
reply prefix in the existing Conversation snapshot. Model hooks revalidate those original
sources, and the Outbox's original delivery material pins the same history digest for the
final send check. History omitted by the context bound creates no dependency. Invalidated
pending replies are cancelled without refunding settled calls or replaying uncertain work.
This reuses the original signed input, `dialogue_history` projection and SDK persistence,
following the inspected DSH pre-step context boundary and Pydantic Conversation storage
references above; it introduces no additional history store or execution authority.

Native model failures retain the original job error and `job.failed` audit receipt with all
reserved or unknown accounting. They do not enqueue the retired generic fallback, which lacks
an original native delivery binding. Only a source-validated, authority-bound Outbox item can
enter the native send path; the existing owner job view still exposes the recorded failure.


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
Nested attachment capture and both video capabilities have separate remaining acceptance work.

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
The native queue now reuses `automatic_media`, its original stage journal, and finite Content
claims. Private descriptors bind the native authority and capture digest in the v2 local
manifest; v1 manifests retain their original verification domain. Queue writes belong to the
same capture transaction. Tuple-based OwnerRuntime connections use per-cursor named rows;
the shared connection's row factory is unchanged.

Recovery from DOWNLOADING reuses the original lease. Recovery from UPLOADING only queries
the existing Content status, and discards private descriptors after confirmed acceptance.
An uncertain CLAIMING result remains fenced without a replacement claim. Native daemon
patches add the existing pump after admission and pass prepared descriptors into capture
persistence. A real authenticated loopback WebSocket, actual HTTP Core, queued transfer and
independent SDK process exercise this entry; provider/CDN transport remains synthetic.
The queue/daemon slice adds six native checks and preserves three frozen queue regression
checks. Nested attachments, video and voice integration remain separate work.


### Native private files

Private files use the existing Content rows, bounded traffic reservation, source manifest,
original upload lease, short-lived spool, parser image digest and DocumentEvidence. The native
branch checks the current `file` purpose and original OwnerCapture without tying file processing
to the latest reply revision. A new owner turn cancels the previous reply but does not delete a
queued native file before its existing deadline. Recall, erasure, expiry and STOP still fence
execution. Frozen v1 upload proofs keep their original verifier and are never relabelled v2.

The dependency-free v2 file codec binds authority, event, slot, source hash, original lease,
file ID, filename, actual byte count, media hash and MIME. Bridge reuses `fetch_file` for the
native private-file URL lookup and one bounded GET. Images and files share the existing exact
checked Core HTTP boundary; the file queue retains DOWNLOADING recovery and read-only
UPLOADING reconciliation. A lost upload response does not trigger a second upload.

The pinned [DSH attachment implementation](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/attachment.ts)
was inspected for attachment custody, path metadata, bounds and DNS checks. The project's
existing stronger pinned-transport and streaming bounds are retained. Parser processes reuse
[Python subprocess](https://docs.python.org/3/library/subprocess.html#subprocess.Popen) and
its existing offline container controls. Final callbacks check the original source, current
permission, exact staged bytes and proof, frozen parser configuration and RUNNING lease before
process creation and stdin writes, or before socket connect, metadata and file-byte writes.
[run_coroutine_threadsafe](https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe)
returns those checks to the owning event loop; parser threads do not access SQLite directly.
Cancellation closes this check path, and failed input dispatch uses the existing cleanup path.

Acceptance uses authenticated loopback WebSocket receive, actual Core HTTP, the persistent
Bridge queue, and an independent parser/SDK worker. Only external OneBot, CDN, Docker and model
transports are synthetic. Native file content reaches SDK context with filename, bounded
excerpts, source locations and the existing external-untrusted label, then one Outbox reply.
The shared Core patch describes an empty-text capture as a received non-text message; it does
not supply a request on the owner's behalf. Shared app and codec edits remain narrow patches
for the integration owner alongside the prior native queue patches.


### SDK animation analysis

The SDK exposes `understand_video(question)` only with the configured video model and native
content/image purposes. It consumes the original `agent_image_inputs`; model arguments cannot
select a URL, path, account or model. Animated sources have a first-frame preview and an
explicit temporal limitation in chat context. A selected tool reuses the existing bounded
PyAV/H.264 conversion, preserving frame composition and timing for one animation cycle.
Static images remain image parts. No selected animation means no video-provider call.

The existing `agent_tool_calls` STARTED/RETURNED lifecycle owns the tool outcome. Its metadata
binds the original paid request, authority, source snapshot and encoded payload. The Reader
foreign-key column is untouched. The existing budget reserve/complete path keeps the original
chat logical request and all limits, and rechecks source, job, permission and tool binding at
actual model dispatch. An unknown outcome keeps its charge and cannot restart the tool.
Historical v1 video evidence retains its original verification functions; native execution is
owned by the SDK tool, including the final chat response and original Outbox transaction.

The inspected [DSH vision tool](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/media/vision-tool.ts)
registers auxiliary analysis as a tool and keeps image bytes out of session logs. This port
reuses that separation through [Pydantic AI function tools](https://pydantic.dev/docs/ai/tools-toolsets/tools/)
and existing source references. Encoding uses the existing [PyAV encode/mux pattern](https://pyav.org/docs/stable/cookbook/numpy.html),
without adding a model loop or media downloader. Synthetic checks decode the actual MP4
received by the video model, verify changing frames, and exercise GIF/APNG through an
independent SDK process. The authenticated WebSocket daemon also carries a GIF through the
same queue, Core upload, independent SDK tool and one final reply.


### Original account send-check evidence

`account_original_proof.validate_original_account_send_check` is a pure validator for the
existing account receipt, Outbox row, reservation and frozen social plan. The caller authenticates
the historical OwnerGrantPolicy, signed reconciliation proof and quiescence first. The helper
has no Store, settings, clock, permission lookup, reservation or send capability. It returns the
exact original `SendCheck` fields: authority, message_id, lease_token, content_sha256 and
payload_sha256. Account proofs use `account_payload_sha256` and the original account_plan_id.

The retained `account_action_delivery:<message_id>` receipt binds the old lease and both hashes.
An intact plan is checked against its original JSON and policy; an erased plan uses the original
retained hashes and matching erased projections. STOP, elapsed time and current configuration
changes do not relabel the original evidence. Seven checks use real native planning and HTTP
leasing, then verify STOP, expiry/configuration drift, five binding mutations and source erasure;
the validator leaves the database unchanged. This reuses the project's existing historical-voice
validator separation. The inspected [DSH file-send tool](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/media/send-file-tool.ts)
returns native send receipts; the project's original custody, uncertainty and accounting remain
necessary for historical reconciliation.

### SDK selection of Bili media

`analyze_shared_video(url)` reuses the existing Reader deferred-tool binding and Bili
prepare/preview/clip phases. The model first selects `read_content`; only its original
metadata observation can supply the video target. Native reading completion does not
automatically start media analysis. Current `bili_video` permission, the original job's
authority, captured owner input and original Reader grant remain mandatory at each phase
and at model dispatch. The existing Bili row, paid reservation and per-phase receipts own
idempotency and uncertainty; no replacement Reader request or media protocol is introduced.

This reuses the inspected [DSH auxiliary vision tool](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/media/vision-tool.ts)
separation and [Pydantic AI deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/).
Original tool-call IDs resume the SDK conversation after the independently running Reader
returns its result. Existing bounded PyAV extraction and provenance remain unchanged.

The synthetic acceptance runs real Core HTTP, independent SDK and isolated Reader processes,
and actual local media extraction. Its short pytest temporary-directory name keeps the
Windows child TEMP below the platform [GetTempPath limit](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-gettemppathw).
A minimal launch probe reproduced a startup timeout with a 277-character TEMP and successful
startup with a 74-character TEMP; the native extraction timeout was not relaxed.

Reader completion passes `execution_uncertain` to Life for UNKNOWN results, UNKNOWN
accounting outcomes, and cancellations whose settled usage is unavailable. The original
request, observed counters and reservations remain intact; duplicate receipts do not call
Life again. Native goal/Reader checks cover UNKNOWN, uncertain cancellation and a known
failure against the shared Goal continuation contract.


### Captured reference media in the native owner runtime

Reference images and files reuse the existing bounded OneBot traversal and its
[get_msg/get_forward_msg APIs](https://github.com/botuniverse/onebot-11/blob/master/api/public.md).
The inspected [DSH attachment implementation](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/attachment.ts)
provides the download-once pattern with native tools consuming the retained bytes.
The authenticated receive path resolves references once, records each original leaf's
path, origin and source hash in the signed projection, and keeps transport descriptors
private. Queue reconstruction compares those descriptors with the captured projection;
it does not resolve references again. Quoted authors remain unverified content.

Image and file purposes are checked before capture persistence. Native media workers
reuse the original lease, upload proof and receipt recovery. Later conversation turns
reuse that capture's original expiry and recall boundary: expired or recalled image
bytes are removed and original-upload replay remains denied. This does not establish
new authority or provenance merely because an embedded message ID matches another event.

Synthetic checks cover authenticated WebSocket reply/forward ingress for images and
files, real Core HTTP and independent SDK/file workers, one reference lookup and one
original download, signed descriptor tampering, later-turn reuse, expiry, recall and
missing media permission. External protocol/CDN/provider transport is mocked. The queue
integration patch targets the frozen native Voice host baseline and preserves its record
handling and owner-history verification.
