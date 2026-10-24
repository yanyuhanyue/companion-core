# Cloud budget synchronization without repeated writes

CUSTODY-BUDGET-01 is a narrow repair on main `85c98ec`. It changes only
`companion/cloud_budget.py`, regression tests and this record. It preserves native
liability, UNKNOWN, frozen pricing, dispatch timing and erasure semantics.

## Sources and reuse

`_native` already distinguishes cancelled tools with native completion or proven
no-dispatch evidence from uncertain cancelled work. The candidate selector now
uses the same predicate; existing tool UNKNOWN remains sticky. `_liability`
remains authoritative. `sync_reference` compares the six computed persisted fields
before UPDATE/audit; changed reconciliation receipts and dispatch dates still persist.

- [SQLite transactions](https://www.sqlite.org/lang_transaction.html): IMMEDIATE
  holds the writer transaction, so redundant historical writes extend writer work.
  The audit does not establish a production latency incident.
- [SQLite query plans](https://www.sqlite.org/eqp.html) and
  [Python instrumentation](https://docs.python.org/3/library/sqlite3.html): trace,
  progress callbacks and EXPLAIN measure SQL work without timing thresholds.
- [DSH agent-loop source](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts):
  `cancel` aborts owned activity; `whenIdle` waits for completion. The local
  `executor_gateway.py` kills and waits for its disposable process group. These
  were inspected for the custody audit; this SQL repair reuses native completion
  evidence and does not execute DSH or introduce a new completion protocol.

## Synchronization responsibilities

| Boundary | Existing responsibility |
| --- | --- |
| `budget.reserve` | Atomically reserve cloud snapshot with native admission. |
| `budget.settle` | Full sync captures legacy entries, then sync native settlement. |
| `budget._persist_accounting` | Paid settlement, cancellation drain, no-dispatch proof and journal uncertainty converge before reference sync. |
| `budget` dispatch fences | Mark actual I/O day. |
| `content` | Reserve/dispatch/settle and erase hooks; lost-worker recovery uses budget settlement. |
| `media` | Reserve, mark dispatch, sync SETTLED/UNKNOWN outcomes. |
| `Executive.begin/run_one/finish` | Reserve, mark adapter I/O, sync completion or uncertainty. |
| `erase_executive` | Record valid no-dispatch evidence when applicable, redact native tasks, sync retained liability. |
| `Executive.recover/revoke`, `restore_executive` | Bulk state changes lack per-reference cloud hooks; later reconciliation must find them. |
| Core recovery/revocation, diary/diary worker, poke, reflection | Direct or bulk UNKNOWN changes require the full reconciliation fallback. |
| `video_accounting` | Native correction changes fee/state with journal/audit evidence; later sync independently validates the receipt. |
| `backup.restore` | Mark pending native usage uncertain and set the cloud restore barrier; old backups cannot prove complete accounting. |
| First binding, legacy and missing snapshots | Backfill all known native liability under the original policy. |

The normal hooks do not establish a complete incremental boundary. Historical
joins and missing-snapshot scans remain necessary. Eliminating them requires a
separate review of all mutation/recovery boundaries; this repair adds no queue,
cursor, watermark, retention rule or schema change.

## Measured effect and verification

The same probe seeds one native erased in-flight task and clones its schema-valid
history to 100 tasks. It repeats sync twice and compares every snapshot field.

| Probe | Before | After |
| --- | --- | --- |
| 100 cancelled tasks per repeated sync | 406 SQL, 100 unchanged updates | 6 SQL, zero updates |
| Accounting values during repeated sync | Unchanged | Unchanged |
| 1,000 settled model rows | 6 SQL, about 24,000 VM instructions, zero writes | Historical scan retained |

67 focused checks pass: 46 budget/restore/regression cases, 18 combinations of
snapshot state/completion proof/native amount comparing selected reconciliation
with direct native reconciliation, one larger-hold/legacy-dispatch import case,
and two original audit probes. Coverage
includes UNKNOWN monetary floors, invalid completion proofs, frozen FX/timezone,
cross-day dispatch, native video correction, erasure and restore barriers.
Receipts are under `.local/verification`; the shared custody status links the
precise candidate evidence. No provider, QQ or GPU execution was used.
