# Native capability execution

Existing capabilities bind their work to the original OwnerAuthority revision in
`jobs.authority_json`. Each execution boundary checks that reference and its
independent purpose. Old source receipts and unsettled resource/model liabilities
remain evidence; they cannot confer current execution permission.

## References and reuse

The inspected [DSH agent loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts)
resolves a tool set before a step, uses that same admission for dispatch, and closes
it after the step. Core applies its existing durable authorization and native job
leases around the same bounded execution lifecycle. Model adapters retain only
request preparation and transport responsibilities.

## Meme discovery and captions

Discovery and caption requests use the existing jobs, discovery reservations,
caption cache, source manifests and model usage ledger. Their `meme` purpose binds
the concrete discovery/caption policy. Discovery alone does not require caption
generation or a paid image-input policy. Captions retain their separate paid sample
limits and per-scenario cap. New native records do not write an execution window.

Queued work checks the original grant before acquiring a lease. Network checks and
model dispatch recheck it; an approved pending owner input preempts background
caption dispatch. The controlled transport's exact admission proof distinguishes
zero provider I/O from an unknown attempted call. Original discovery accounting can
still settle after STOP or source withdrawal. Cached caption reuse preserves the
original usage and erasure lineage.

The native acceptance fixture uses signed HTTP ingress and a separate Core worker
process with synthetic provider transport. It covers discovery-only authorization,
caption idempotency, final-boundary STOP/source withdrawal, and an unknown provider
result. Live QQ or provider acceptance remains a separate stage.
