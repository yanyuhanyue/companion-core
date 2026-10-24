# Cognition metadata projection

`companion.cognition_api.register_cognition_routes(app, control_auth)` provides
`POST /internal/v2/cognition`. The composition root must pass its existing owner
control dependencies. STOP and disabled runtime features do not prevent an
authenticated owner from inspecting metadata. Responses use `Cache-Control: no-store`.

Request: `{"kind":"goal","limit":30,"before":null}`. Supported kinds are wake,
life, goal, diary, reflection, nightly and model. Limits are 1–50. Pagination uses
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

## Integration handoff and limits

The shared `companion/api.py` is owned by the integration lane. Add alongside the
existing memory projection registration inside `create_app`:

```python
from companion.cognition_api import register_cognition_routes
register_cognition_routes(app, control_auth)
```

The module test registers this route explicitly; it does **not** claim that the
real application entry point already exposes it. Before acceptance, integration
must test the registered `create_app` with its real control token, STOP state and
an independent worker using mocked provider transport. No live acceptance was run.

This is the initial native-record projection. Historical candidate alternatives
are `NOT_RECORDED`; Life's saved structured reason is returned as `decision_reason` with
`kind=decision_reason`, distinct from raw reasoning. It must agree with the saved
proposal, is capped at 160 characters, and applies the existing secret-redaction
and sensitive-content rules. Missing reasons are `NOT_RECORDED`, disagreement is
`UNKNOWN`, and sensitive reasons are `REDACTED` without their text. N1/N2/N3 are read directly from the stable `steps_json` fields inspected at
`7b8def23`; missing fields remain `NOT_RECORDED`. REM waits for the final Life
interface. Life exposes the native topic/revision candidate reference, destination
and job state. It does not reconstruct unrecorded alternative candidates. Model usage
is separately paginated; a full cross-kind execution graph and UI remain pending.

Verification: `scripts/check.ps1 -TestFiles tests/test_cognition_read.py` passed
24 cases, covering empty kinds without execution, native wake decisions and
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

The reason/epoch follow-up passed 30 focused cases across `test_cognition_read.py`
and `test_cognition_reason.py`. Native Life decisions and a generated Diary remain
observable after an unrelated real Memory addition; source erasure retracts them.
Saved credential-like and sensitive reasons do not appear in responses. Pending
records do not acquire an inferred reason. These are module-level checks; real
entry registration remains the integration owner's responsibility.
