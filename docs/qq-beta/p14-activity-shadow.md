# Initial chat activity diagnostics

The native private-chat checkpoint can record a bounded activity diagnostic without
changing the model request. This is a local SHADOW capability. It does not provide
runtime ENABLED context, new tools, execution permission, Life state or device presence.

## Configuration and lifetime

The optional configuration is omitted from serialized settings when unset, preserving
the previous unconfigured shape. The only supported modes are OFF and SHADOW:

```toml
[activity_context]
mode = "SHADOW"
```

OFF creates no diagnostic. ENABLED is rejected by configuration validation. SHADOW runs
once after the original initial native chat context and planner have succeeded. It
adds an `activity_shadow` entry to the original `agent_conversations.snapshots_json`.
It never adds messages, tool schemas, provider calls or `job_sources`.

Restoring a Conversation retains its saved diagnostic. It does not sample again when
activity changes or the operator switches OFF/SHADOW. This avoids turning an old
observation into a mutable execution dependency. Original source, permission, budget,
STOP, lease and delivery checks still run; diagnostic errors cannot swallow errors
from the original context construction or checks.

## Scope and bounds

Read authority is established before intersecting the original registered sources:

- The authenticated current owner input and the exact originally selected history.
- Selected Memory/continuation proofs under the current memory purpose and scope.
  This initial diagnostic excludes roots from other conversations, shorter current
  retention violations, and internal correction containers it cannot qualify.
- Already admitted chat Reader proofs, using their original result/grant checker.
  Result IDs must already belong to the same job's consumed sources. Goal-driven
  Reader results do not qualify through the chat Reader path.

`job_sources` alone never grants access. In particular, the existing global Goal
reference can register roots which are absent from the selected history or memory;
those registrations cannot bootstrap SHADOW permission. Candidates with missing
purposes or incomplete source qualification are skipped before their content is read.
Omission diagnostics contain bounded counts, not excluded IDs or text.

The complete encoded diagnostic is at most 2048 UTF-8 bytes, with at most three work
records, three selected attention records and 100 distinct source IDs. Oversized
records are omitted whole. It contains native IDs/states, limited commitment/deadline
and feedback state metadata, and selected Memory IDs/status. It contains no Goal topic,
Memory content, Observation body, URL, credential or lease token.

Goal metadata has `execution=NOT_EVALUATED`; a stored DONE label is not proof of
completion. Already verified chat Reader metadata retains the original checker.
Presence is UNKNOWN, progress and interruptibility are NOT_RECORDED. A lease or
WATCHED analysis receipt does not establish physical presence or playback.

## Existing custody and proof semantics

The diagnostic is explicitly `used_in_request=false`. The context planner excludes
only `context_plan` and `activity_shadow` from its source-proof list. Every original
memory/history/Reader/image proof remains in its original count/hash and execution
checker. A future `activity_context` proof is not excluded. Renaming or modifying an
already bound real proof still fails the original planner binding.

The diagnostic uses the original checkpoint's erasure coverage and only already
registered sources; it creates no erasure graph or storage table. Forgetting a used
source clears the original Conversation, snapshots and output, including diagnostics.
Restoring an older database still replays the independent ErasureLedger. Sampling
never repairs a stale ledger or rolls back the caller's transaction. Unavailable
sampling evidence yields an empty diagnostic while original error behavior remains
authoritative.

The existing final delivery record hashes the saved checkpoint for integrity. SHADOW
does not refresh that checkpoint in response to live activity or mode changes, and
does not add a current-activity execution/send checker. The original settled usage,
UNKNOWN, Outbox and ACK contracts continue to own delivery.

## Verification and remaining work

Verification compares the same actually signed ingress and initial database/journal
through independent Core workers with mocked provider transport. OFF and SHADOW must
produce identical actual provider wire/tools, source-proof bindings, source sets,
accounting and delivery outcomes for settled, cancelled and UNKNOWN attempts. Synthetic
ACKs and mocked outputs are engineering evidence, not human naturalness or live account
acceptance.

The original `Core.context -> life.context/goals.recent` Goal reference remains unchanged
in this slice. SHADOW does not fix its global selection or add runtime state proof.
Future native-chat ENABLED work must replace that superseded assembly, qualify Goal
and Observation evidence, and connect its proof to both request and final delivery
checks. Life consumption and A/B experiment changes remain separate. Free-text willingness
to play is not a formal commitment; new game commitment admission and actual execution
remain unsupported.

This follows the existing [context planner](p13-context-inspector.md),
[owner inspection](../cognition-read.md) and native checkpoint/erasure contracts.
The implementation research reuses DSH's pinned
[session-owned read-only job views](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/jobs/jobs-local/src/index.ts);
that in-memory registry does not replace Core's durable execution, billing or ACK authority.

## C2b-b1 native evidence helpers

The independent helper slice reuses the original Goal execution projection, scope/target
predicates and Reading/Observation integrity checks. Its internal SourceAdmissions comes
from actual current-input custody and selected history/Memory/Reader checkpoint proofs.
Each call borrows the caller transaction and checks the latest Ledger before and after
reading. Goal roots, request inputs and derived observations must close inside that admitted
scope before their bodies are read; job_sources and an old global Goal reference grant no scope.

Historical text DONE requires the original context, captured source grant, native observation
identity, content/append fingerprints and full original Reading result hash. An expired
historical lease is not a new execution permission. UNKNOWN remains uncertain. Media protocol
receipts, missing legacy evidence and content that cannot be reconstructed are unavailable
as a whole candidate. The existing chat-only Reader entry keeps its original Goal-row rejection.

These are pure evidence helpers. Core prompt assembly, final owner delivery, the activity
materializer and ENABLED configuration remain at their existing behavior. This slice does
not establish ENABLED or E4 acceptance.
