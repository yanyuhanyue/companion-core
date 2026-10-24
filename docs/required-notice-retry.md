# Bounded retry for required task feedback

An accepted commitment's outcome requires model-written feedback. Previously a settled `[SKIP]`
failed its one-attempt wording job immediately, leaving no Outbox item and the commitment waiting
for delivery. Optional proactive shares still accept `[SKIP]` as a terminal decision.

Required commitment wording now receives one additional native job attempt only when:

- the response was an explicit empty string/whitespace or `[SKIP]`, with all current job usage
  `SETTLED` and exactly one observed activity call per consumed job attempt;
- the native job still owns its RUNNING lease, has not already exhausted this two-attempt path,
  and neither an Outbox row nor a commitment feedback-message reference exists;
- the original notice material, sources, private-owner scope and applicable live/paid approval
  remain valid.

The job initially retains `max_attempts=1`. Only that proven failure atomically earns a second
attempt, so transport uncertainty cannot gain a retry merely because the job is a commitment.
The same native `RETRY_WAIT`, attempt counter and five-second initial backoff are used. On its
next claim the worker makes a **new reservation/request ID**, under the same job/logical-request
budget. The first call's settled charge is retained. Normal request, daily, window, global quota,
STOP, expiry and source checks still gate the second call. There is no new queue or sender.

The adapter distinguishes a received, size-valid empty string without tool calls as
`MODEL_EMPTY_RESPONSE`; missing content, malformed data and oversized content remain invalid.
This classification never establishes accounting certainty: missing/invalid usage and any
UNKNOWN outcome still prevent this retry. A second empty/SKIP result ends the wording job as
FAILED. Neither failure nor retry fabricates a delivered notification, changes the actual task
outcome, or marks a commitment complete. Only the existing delivery acknowledgements complete
its feedback state. Exhausted or blocked feedback remains visibly undelivered for operator
handling; this change does not revive historical failed jobs or repair production records.

## Reuse and evidence

The mechanism uses `notices.validate/_settled`, native jobs/leases/backoff, original budget
admission/settlement, and the existing Outbox/commitment ACK path. The Core change is a narrow
failure-handler call; budget implementation and delivery semantics are unchanged.

[DSH's agent-loop at 22ddee3](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/core/agent-loop/src/agent.ts)
was inspected for explicit retry decisions and retained request ownership. Core keeps its own
durable job mechanism. [Temporal retry-policy guidance](https://docs.temporal.io/encyclopedia/retry-policies)
provides the mature finite-attempt/backoff/non-retryable distinction; its default unlimited
activity retries are not adopted here.

Synthetic checks cover settled SKIP/empty, malformed and missing usage, unknown transport,
oversized responses, exhausted attempts, request/daily limits, source expiry/revocation, disabled
goals and signed-window STOP/expiry. A real authenticated TCP test accepts the commitment, runs
the native worker in separate OS processes across attempts, verifies distinct reservations and
retained charges, and exercises synthetic SENT/DELIVERY_UNCERTAIN acknowledgements. No actual
provider call or QQ send is made. Exact checks belong to the candidate's single receipt.
