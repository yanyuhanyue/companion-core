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
