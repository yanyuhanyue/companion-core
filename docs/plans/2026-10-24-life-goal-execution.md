# Life decisions through the original Goal/Reader execution path

Base: PR104 `f8da10d19eeb77c1d948d63c57f10ffb6c4164b7`; PR104 remains frozen.
This follow-up completes the already authorized offline execution loop. It adds no live
provider, DSH or QQ acceptance and no new executor, grant registry or accounting ledger.

## Data flow and selected reuse

1. Original owner/private sources produce a bounded Life topic. Original activity/model admission
   produces CONTINUE with an existing self goal, or EXPLORE with the existing `GoalProposal` type.
2. A separate default-false `life_loop.read_goals_enabled` switch enables submitting that choice
   to the already configured native Goal/Reader gates. It grants no read permission. OFF/PREVIEW
   do not submit anything; expressive outputs remain SHADOW with zero new QQ delivery.
3. In the original activity commit transaction, `GoalEngine.propose` (for a new proposal) and
   `GoalEngine.accept(..., autonomous=True)` establish the native goal/step. The activity result
   retains only the actual goal ID; native goals/steps own execution state and retries.
4. The existing Life worker calls `ExplorationService.schedule_goal`; the original Reader v3
   worker leases/validates/returns evidence through HTTP. Existing adapter/source grants, request
   and byte reservations, idle/STOP/source checks, deadlines and UNKNOWN behavior remain in force.
5. Only an actual accepted FETCHED/WATCHED result completed by `GoalEngine.finish` becomes a new
   Life reading candidate. Its native dependency edges bind it to the originating owner evidence.
6. Private-purpose Life authority follows only results of that same successfully settled Life
   goal decision, whose original goal evidence remains inside the approved private source roots.
   This reuses native goal/result provenance; unrelated observations do not inherit it. QZone
   remains exact per-result purpose grants for every context source, with no inheritance.
7. The next original activity decision can SKIP or produce a source-backed private SHARE draft.
   No SocialAutonomy READY plan, new sender, or automatic publication is introduced.

New self-goal selection must not run in parallel with the old automatic Wake selection. When
read-goal submission is enabled, Wake leaves new self-goal acceptance to the Life decision;
already accepted native goals and commitments continue through their original queue.

## Precise shared interfaces

- `companion/life_protocol.py`: reuse `GoalProposal` inside EXPLORE; retain the existing action
  union and destination split. Add the default-false native read-goal switch.
- `companion/life_topics.py`: source-scoped goal context, validation, native goal submission,
  goal/result readback, and private completed-result provenance checks. No model adapter I/O.
- `companion/core.py`: invoke native goal submission within the existing successful activity
  commit transaction. Failed validation cannot leave an accepted goal without its decision.
- `companion/life_worker.py` / `companion/wake.py`: route selection through one path and schedule
  the accepted native goal promptly. Native Reader remains independently deployed/run.
- Existing activity result storage: one nullable goal foreign key, not another task state machine.
  Native source erasure invalidates the result and goal through the existing registered handlers.
- `GoalEngine`, `ExplorationService`, `ReadingService`, `SourceGrantService` and Reader v3 contracts
  are the existing authorities. Changes to their behavior are not planned merely for Life wiring.

The private-result boundary is deliberately narrower than general descendant traversal: require
a successful Life activity, settled usage, exact native self-goal/result binding and current valid
original goal evidence under the same private approval. A changed grant or unrelated read cannot
become authorized because it has a convenient event dependency. Public draft grants stay exact.

## Primary references already inspected for this feature family

- [DSH agent-loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md):
  model requests and tool outcomes belong to one owned runtime; uncertain outcomes are distinct
  from work that never started. Reuse Core's owned jobs and original Reader receipts.
- [DSH Jobs](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/jobs/README.md):
  ownership and completion notifications, while jobs-local is process-local. Keep Core's durable
  native queue instead of importing another runtime.
- [Temporal workflow execution](https://docs.temporal.io/workflow-execution): durable identity and
  recovery are mature patterns; replacing Core's established persistence is unnecessary here.

## Required offline proof

Real authenticated loopback owner input -> independent Life worker/model MockTransport -> native
Goal acceptance and Reader request -> independent Reader v3 worker with mocked source transport ->
actual typed receipt -> independent Life decision -> evidenced SHARE/SKIP, with no QQ item.
Test CONTINUE and EXPLORE, absent read grants/disabled scope, revoked or deleted source, budget
denial, pause/STOP, restart and duplicate workers, failed/UNKNOWN reads, and source/goal mismatch.
Keep actual reservations/usage and no-op/idempotent outcomes in their original tables.

Final receipt must distinguish PR104's frozen decision/draft proof from this follow-up execution
proof. Real model quality, natural-language closure and finite live Beta remain separate.
