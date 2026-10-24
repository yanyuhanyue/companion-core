# Native Voice owner authority migration

This candidate replaces live-window execution checks in `VoiceFlow` and
`VoiceJobs` with the existing OwnerAuthority, signed owner-input and native
Outbox checks. It is not runtime acceptance and must not be activated before the
shared migration and Bridge consumer integration below are complete.

## Binding and deadlines

New voice jobs retain the exact original `AuthorityRef` in `authority_json`.
Current policy must still approve `voice` and its exact existing VoiceConfig
capability hash. ASR checks the original signed input and current reply revision;
TTS checks the original owner delivery proof, settled source task and exact text.
No window or replacement execution grant is created.

Job TTL, ASR wait and TTS wait are capped by the original grant's expiry when
present. Existing node leases and result retention remain bounded by the native
job deadline. STOP, revision changes, erased sources, configuration drift and
expired grants prevent further input/result use. Chat-only grants do not enable
optional ASR; the authenticated chat can still retain its unsupported record tag.

The local voice-node execution lease remains protocol v2. ASR capture proofs use
the v2 voice-upload signing domain and the original owner reference instead of a
window hash. Outgoing delivery bindings use schema v3 with that reference.
Historical schema-v2 delivery bindings are accepted only by the retirement codec;
they cannot construct a new send envelope. Retired capability verification,
UNKNOWN outcomes, cleanup acknowledgment and node quarantine remain independent
of active execution permission.

## Required shared integration

R1 owns migration `0044_voice_owner_authority`, after the reserved Reader 0043
revision, and the Store schema-capability declaration. The registered request is
`voice-owner-authority-interface.json` in the shared next-features handoff folder.
The requested additive column is:

```sql
authority_json TEXT NOT NULL DEFAULT '{}'
  CHECK(json_valid(authority_json) AND json_type(authority_json)='object')
```

Historical rows are not backfilled. The existing nullable `window_sha256` is NULL
for new owner jobs; its historical values are retained until native retirement.
Dropping nonempty authority custody must require explicit reconciliation.

The current Core APIs import the Voice ASR proof DTO directly. Their external
Bridge caller must submit the new proof, compare the exact authority in input
state, and validate schema-v3 voice delivery bindings against its original owner
reference. The native Bridge host interface is being migrated by its owner;
these leaf consumers must be integrated with that frozen host, not a fabricated
WindowPlan. Shared Core/API/config/storage/migration files are not edited here.

## Verification so far

`voice_original_proof.validate_original_voice_binding` is a pure historical
validator for UNKNOWN reconciliation. Its keyword arguments are `receipt`, `job`,
`proof`, `outbox`, `content_sha256`, `original_scope`, and `voice_policy_sha256`.
The caller loads the `voice_reply:<message_id>` receipt and the voice job named
by `proof.voice_check.job_id`. Original scope is exactly `{"authority": ref}`
for schema v3 or `{"window_sha256": digest}` for historical schema v2.
The policy hash must come from authenticated original policy evidence.

The caller remains responsible for original grant/policy and accounting custody,
proof signature, delivery attempt, lease, send-check digest, and quiescence.
The validator has no storage, settings, clock, or current-authority access and
does not permit execution. Full bindings require retained content, matching job
material and original result expiry; expiry equality is checked without a
current-time test. Retired custody checks its original identity and capability
verifier (or historical signature verifier) without requiring erased job
authority or payload. Neither path reconstructs a new grant.

Twenty pure evidence tests cover both schema versions, full and retired custody,
expired records, original scope/policy/job/token substitution, missing voice
proof, content/byte/result/expiry changes and historical signature retirement.
These tests do not claim that the shared reconciliation entry point is integrated.

Four native-source checks pass through real signed owner capture and Core ingest:
window-free ASR waiting, original-expiry bound, STOP rejection and chat-only
permission separation. No guard replacement or real voice node is used.

ASR/TTS task creation, actual API plus independent voice worker, byte/hash and
transcript preservation, cleanup/UNKNOWN recovery and Bridge delivery acceptance
remain pending the shared schema and consumer integration. Existing window-based
voice fixtures also require migration before they can validate the new entry.

## Reuse

- Existing `owner_authority.current/evidence`, `owner_input.source`,
  `owner_delivery.check_delivery` and native voice lease/retirement code own the
  permission, source and cleanup semantics.
- Inspected DSH schedule runtime and delivery-history source at
  `22ddee3792402c3e70ec5f86a63c3e3e895a2f60`: reuse serialized permission-sensitive
  operations and durable receipt identities, without treating a stop request as
  confirmed completion.
- [RFC 7662](https://www.rfc-editor.org/rfc/rfc7662.html), sections 2 and 4:
  current active scope/expiry is checked at the protected resource. This migration
  uses the existing Core authority service; it does not introduce OAuth or a
  second authorization system.
