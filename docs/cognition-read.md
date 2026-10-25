# Cognition metadata projection

`companion.cognition_api.register_cognition_routes(app, control_auth)` provides
`POST /internal/v2/cognition`. The composition root must pass its existing owner
control dependencies. STOP and disabled runtime features do not prevent an
authenticated owner from inspecting metadata. Responses use `Cache-Control: no-store`.

Request: `{"kind":"goal","limit":30,"before":null}`. Supported kinds are wake,
life, goal, diary, reflection, nightly, model and rem. Limits are 1–50. Pagination uses
the native primary-key cursor with SQLite append order, not lexical UUID order.
Optional `record_id` selects an exact native record; a missing or unavailable record
is `NOT_RECORDED`. It cannot be combined with `before`. A missing cursor returns `COGNITION_CURSOR_UNAVAILABLE`; it does not silently
restart. Pages bound scanned records, so privacy filtering can produce an empty
page with a continuation. `truncated` means more native rows exist.

The projection reads existing records and never creates a history ledger, runs a
model, or recalculates a historical measurement. Source checks reuse
`MemorySources` and `MemoryProjections`; custody and privacy versions are checked
before and after reads. Invalid, expired or erased source chains are suppressed.
Goal execution metadata reuses the existing source-backed completion and execution-receipt
checks. Native UNKNOWN and execution uncertainty remain visible.

Goal records expose an ordered `steps` array instead of a single `step`. Each of
at most three steps carries its native ID, position, state, attempt counts,
operation, original source IDs and bounded execution receipts. Raw scope URLs
remain excluded. `WAITING_DECISION` is a recorded step state, not an execution
permission. Each step is limited to 20 original receipts, prioritizing UNKNOWN;
the parent includes their bounded aggregate. `verified_completed_steps` counts
only DONE steps with live, related FETCHED/WATCHED evidence. The parent is `done`
only when its own state is DONE, every step is verified, the original final
result matches, and no step or Reader attempt remains uncertain. A completed
first step with later waiting steps leaves the parent planned.
Receipt `truncated` also indicates native receipts withheld by the existing
provenance/receipt-source bound; an empty filtered list never clears a recorded
UNKNOWN liability. Completion reuses the Life domain's per-step completion
validator, including its complete original step evidence.

The reader checks complete step-source sets up to 256 IDs and withholds a plan
when a step's source set is missing, excessive or withdrawn. It uses read-only
native records and the existing MemorySources lineage validators, not
`GoalEngine.plan`, whose current-authority check serves execution controls.
STOP therefore does not erase valid historical visibility. Migration 0045 and
the matching Life domain/projection implementation are integration prerequisites.

Prompt bodies, topics, diary text, reflection suggestions,
credentials, model endpoints and raw reasoning are excluded. Reason codes and
states use explicit allowlists. Measurement exposes the recorded count kind and
numeric input, reservation margin and output allowance. Missing measurements are
`NOT_RECORDED`. This view does not attest that an unbound usage row has source
evidence; it labels such evidence `NOT_RECORDED`.

## Reused sources

- DSH schedule `delivery-history.ts`, revision
  `22ddee3792402c3e70ec5f86a63c3e3e895a2f60`: inspected the retained delivery-history
  implementation supplied in the Social lane's pinned source snapshot. Reused
  native identity pagination, append order, explicit unavailable history, and
  reading saved receipts without reconstructing Session history.
- [OpenClaw automation management](https://docs.openclaw.ai/automation/cron-jobs/managing-jobs):
  reused the separation of automation state and persisted run-history inspection.
- Existing repository `memory_projection_api.py`, `memory_context.py` and
  `memory_projections.py`: reused owner route registration, source custody,
  erasure/retention checks and actual execution-result semantics.
- Installed `deepseek-harness-sdk` 0.2.1rc2 `api.py::finish_reason` and
  `companion/dsh_job.py::run_job`: inspected root-run termination separately from
  tool-result failures; one successful child result does not establish overall
  success. The Goal reader reuses native Goal/step states rather than DSH events.
- [SQLite SELECT](https://www.sqlite.org/lang_select.html): reuse explicit
  ORDER BY and LIMIT for bounded original rows, with an extra row detecting
  overflow; the query itself does not modify stored state.

## Integration handoff and limits

The shared `companion/api.py` is owned by the integration lane. Add alongside the
existing memory projection registration inside `create_app`:

```python
from companion.cognition_api import register_cognition_routes
register_cognition_routes(app, control_auth)
```

The route is registered by `create_app`. Integration checks exercise its real
owner-control authentication, STOP state and records produced by independent
workers using mocked provider transport. Real environment acceptance is pending.

This is the initial native-record projection. Historical candidate alternatives
are `NOT_RECORDED`; Life's saved structured reason is returned as `decision_reason` with
`kind=decision_reason`, distinct from raw reasoning. It must agree with the saved
proposal, is capped at 160 characters, and applies the existing secret-redaction
and sensitive-content rules. Missing reasons are `NOT_RECORDED`, disagreement is
`UNKNOWN`, and sensitive reasons are `REDACTED` without their text. N1/N2/N3 are
read directly from the native `steps_json` fields; missing fields remain
`NOT_RECORDED`. REM metadata is described below. Life exposes the native topic/revision candidate reference, destination
and job state. It does not reconstruct unrecorded alternative candidates. Model usage
is separately paginated; a full cross-kind execution graph and UI remain pending.

`tests/test_cognition_read.py` covers empty kinds without execution, native wake decisions and
pagination, native goal provenance with an independent Core instance, erasure,
privacy races, measurement filtering, native paid reservation/settlement with MockTransport, UNKNOWN usage,
waiting/cancelled goal steps, missing-record NOT_RECORDED and the explicit module HTTP contract.

Goal pages project the selected native goal directly, reusing execution and result
validators without querying a second truncated same-source goal list. Regression
coverage includes 25 same-source historical goals and timer-triggered wake records
whose selected goal source is subsequently erased.


Historical observations do not require their execution epoch to equal the current
privacy epoch: ordinary Memory additions advance that epoch too. They retain
current source/retention/custody validation plus the request-wide before/after
version check. A committed Diary without valid original evidence is suppressed.
Life uses the decision's saved source fingerprints, not the topic's current source
membership, and verifies them against the native Life source codec. The candidate
reference exposes the original topic/revision, saved source IDs, material hash and
authority hash. The full historical allowed-item list was not persisted and is
explicitly `NOT_RECORDED`; current permissions are not substituted for it.

Checks in `test_cognition_read.py` and `test_cognition_reason.py` verify that
native Life decisions and a generated Diary remain
observable after an unrelated real Memory addition; source erasure retracts them.
Saved credential-like and sensitive reasons do not appear in responses. Pending
records do not acquire an inferred reason. `test_cognition_api.py` covers the
registered application route and its owner-control authentication.


## REM metadata

`kind=rem` uses the same bounded pagination and exact `record_id` selection. The
projection reads frozen `rem_records`, the original job and its `scenario=rem`
usage. It reuses `MemoryContext.revalidate` from the native REM reader, checks that
stored memory/event references agree with that proof, and retains the outer
request-wide custody/version fence. Missing records are `NOT_RECORDED`.

Life-origin creations reuse `rem.life_record` to validate the original settled
Life result, exact content digest, native REM job binding and full event-source
closure. Their source Memory list is empty because saving fiction creates no
fact. Saving the retained text makes no new model call: its REM `usage` is null
and `usage_status=NOT_RECORDED`, while the original Life model usage remains on
that Life job. Known Life source, pause and reconciliation failures are exposed
as bounded error codes.

Every row is labelled `kind=FICTION`, `audience=owner_only`, and
`content_kind=private_fiction`; question metadata is `UNVERIFIED`. No title,
fictional text, question text, source snapshot, or memory body is returned. The
saved decision and question count are metadata, not factual observations or tool
execution receipts. `usage` is read from original accounting, including UNKNOWN;
no count or cost is inferred from the fictional artifact.

Expired, erased or unavailable sources set `source_available=false` and withhold
source IDs and private result metadata while retaining native job/accounting
status. REM disabling is not an execution permission for this owner-control read.
The native `rem.records` reader only supports the newest N rows; this adapter
adds the cognition native-ID cursor without rebuilding a second history store.

`test_cognition_rem.py` uses real Memory evidence and pinned proofs with synthetic
retained rows to verify the registered owner API, paging, missing IDs, expiry,
ledger erasure, native scrub, reference mismatch, version races and unchanged accounting.
`test_rem_life_entrypoint.py` and `test_life_rem_cognition.py` also inspect records
produced through signed HTTP input and independent Life/REM workers. The latter
checks exact retained creation, source/output tampering, recall and queued-save
failure through the real cognition HTTP route, with no body exposure or accounting writes.
