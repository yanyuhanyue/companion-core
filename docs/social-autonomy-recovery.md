# Social planning recovery and historical quota reads

Social planning keeps the existing plan, model request, source snapshot, native budget and
Outbox identities. Recovery does not reconstruct an unstored model response or repeat an
admitted request. No executor, grant store, table or provider retry contract is added.

## Recovery boundaries

| Retained state | Background action |
| --- | --- |
| Expired PLANNING, no model usage, no active native admission lease | Mark CANCELLED with SOCIAL_PLAN_EXPIRED_UNADMITTED; retain candidate/source/audit identities |
| Expired PLANNING, SETTLED social usage, no active admission lease | Mark CANCELLED with SOCIAL_PLAN_RESULT_LOST; retain actual cost and do not request replacement text |
| PLANNING with RESERVED or UNKNOWN usage | Preserve the original liability and reconciliation block |
| PLANNING with an unexpired plan or native admission lease | Leave the other worker's state intact |
| REJECTED by an allowlisted pre-admission quota or foreground-yield refusal | Reconsider the same request only while its original plan/source/authority remain valid and no usage, admission, cloud cost or action reservation exists |
| Paid/uncertain/interrupted/skipped/delivered candidate | Never regenerate it as a retry |

`budget.reserve` revalidates social scope, original messages, source/authority and expiry inside
its existing transaction. A successful readmission changes REJECTED to PLANNING and reserves
the original request together. A still-refused admission rolls back that transition and its
audit, so worker idle polling neither accumulates new plans nor repeats refusal logs. Concurrent
callers cannot both reserve the same request, and a losing caller cannot reject the winner's plan.

The original expiry is never extended. If an equivalent Reader refetch receives a new event ID,
the background worker retries the original frozen references, not the newer observation. Each
reference is checked again; refetch cannot repair revoked authority or erased original evidence.
Source deletion keeps using the existing erasure ledger. No model adapter owns storage or delivery.

## Quota reads and local dates

Poke claims use indexed conversation/date COUNT and latest claimed-at MAX, rather than loading
the complete claim history into Python. Local-day boundaries use the configured IANA timezone
and convert to UTC for the persisted timestamp range, including DST-length days.

Social capacity loads the relevant action pool and the current-day/cooldown horizon, plus every
potentially unresolved reservation regardless of age. Original owner-reconciliation proof still
determines whether an ambiguous cancelled action is resolved. Known completed history outside
that horizon stays stored but is not materialized for each check. This is not permission to
truncate unknown liabilities, delete receipts, reset cooldowns or discard model charges.

Social's persisted timezone must match the current configured budget timezone. A mismatch now
fails before consuming a candidate with SOCIAL_TIMEZONE_BINDING_CHANGED. This prevents silent
calendar divergence; it does not migrate historic quotas. An intentional timezone transition
still requires a reviewed migration policy preserving the original budget/accounting semantics.

## Reused implementations and evidence

- DSH `0.2.1-rc.3`, fixed `22ddee3792402c3e70ec5f86a63c3e3e895a2f60`:
  [jobs-local](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/jobs/jobs-local/README.md)
  is explicitly process-local. Its terminal-result ownership informs fencing; Core continues
  to use its own durable native ledger rather than copying that executor.
- [Temporal activity lifecycle](https://docs.temporal.io/activity-execution) separates task
  timeouts from retry policy; at-most-one attempt can time out without replay.
- [AWS idempotent API guidance](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
  motivates atomic identity/admission and preserving ambiguous results, not blind retries.
- [Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html) supplies the existing aware
  local-calendar conversion; no fixed-offset approximation is introduced.

Regression checks cover actual child-process death before reservation and after settlement,
valid native admission leases, a stale process resuming after expiry, same-request concurrent
readmission, native budget refusal, midnight/refetch recovery, source/authority invalidation,
old UNKNOWN responsibility and indexed poke day boundaries. Existing real HTTP and independent
worker tests cover the original account action lifecycle with mocked provider transport.
These are synthetic evidence, not real QQ acceptance or permission for paid calls/deployment.

Public content remains untrusted model input. The field/authority checks bound what an authorized
profile decision may do; they do not prove resistance to all prompt injection. This repair does
not expand public-source authority or replace existing authorized autonomy with mandatory reviews.
