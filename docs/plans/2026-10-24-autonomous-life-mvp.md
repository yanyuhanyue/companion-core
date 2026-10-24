# Autonomous Life Loop MVP: reuse audit and proposed contract

Status: recommended integration approved by the owner on 2026-10-24; implemented in the
independent Life branch with targeted synthetic validation. Live acceptance remains separate.
The implemented contract and operation guide are in `docs/qq-beta/autonomous-life.md`.
Baseline: `85c98ec85c1b86929fdb5e8525234f64bb6d43bb` (2026-10-24).

The requested behavior is a bounded continuation of real private conversation and authorized
reading. An unresolved bug can be considered the next day, explicit closure ends consideration,
and an actual reading result can supply an evidence-backed sharing proposal. Model proposals
never grant permission. The initial deliverable is OFF by default, with read-only inspection and
SHADOW text generation that cannot create a QQ deliverable.

## Existing implementation audit

| Capability | Existing implementation to reuse | Actual limit / necessary extension |
| --- | --- | --- |
| Context and provenance | `Core.context`, `MemoryContext.continuation`, `MemorySources.event`, tracked job sources and privacy proofs | Continuation exposes up to three recent episode excerpts; it is not a topic lifecycle or permission to remind |
| Topic evidence | `episodes`, `episode_events`, original events and `event_dependencies` | No durable WAITING/DUE/CLOSED/EXPIRED topic record or one-shot consumption marker exists |
| Goals and execution | `GoalEngine`, `goal_steps`, exploration requests and existing Reader | Goals currently express read_content/investigate, with self/commitment kinds; a conversational follow-up is not a reading goal or accepted commitment |
| Wake cadence | `WakePlanner.evaluate`, `wake_decisions`, `life_worker` | Persistent decision keys, quiet hours, pause and daily limits exist; decision enum currently SKIP/EXPLORE/OBSERVE |
| Interest | `interest.profile`, `record_exploration` | Actual completed goal and FETCHED/WATCHED evidence; neither namespace grants sharing authority |
| Reflection | `ReflectionService`, cited suggestions and rejection cleanup | Hypotheses remain hypotheses. A next_question is not a user request or continuing consent |
| Expression | `ExpressionLearning.snapshot`, existing Affect and expression integration | Communication preferences affect expression only; no initiative or relationship counters |
| Sharing | `SocialPlanner.preview`, `share_source`, existing candidates and common proactive counts | Preview is already owner-only and read-only, checks real reading provenance and duplicate content; it does not generate model prose |
| Wording and delivery | `notices.schedule/validate/messages/parts/record`, original activity jobs and Outbox | Only share/commitment are accepted. FOLLOW_UP requires its own scope and source validation |
| Admission and recovery | `private_life`, live window guards, `budget.complete`, model admission/usage, job leases and reply revision | Reuse chat preemption, actual cost and UNKNOWN semantics; no second model accounting or execution loop |
| Erasure | Existing independent erasure ledger and derived closure | Any new stored topic/proposal text needs integration with invalidation and backup replay before it is usable |

### Primary implementation comparisons

Reviewed 2026-10-24:

1. [DSH bili-scroll scheduler](https://github.com/c1overzz/dsh-bili-scroll/blob/main/index.js),
   `tick` around lines 1393–1420: checks current settings, daily limits, window membership and
   persisted window keys before starting work. Reuse the bounded wake pattern through Core's
   existing transactional WakePlanner; do not import its child-process runner or public actions.
2. [DSH agent-loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md),
   Persistence integration and Unanswered calls after step failure: stable session identity,
   recorded progress and distinct not-started/unknown outcomes. Core already has durable jobs,
   leases and UNKNOWN; use those rather than add a separate DSH session runtime to Life.
3. [Temporal workflow execution](https://docs.temporal.io/workflow-execution): durable execution
   and replay are mature alternatives for a general workflow system. Introducing its service and
   execution history would duplicate Core's current worker/accounting ownership and does not
   supply this application's source erasure or private follow-up consent. It is not selected.

These sources motivate reuse; none establishes authority to execute DSH, spend, send QQ, or
retain withdrawn source material. Repository ADR 0001 also distinguishes revoking future reads
from withdrawing historical processing. A read grant expiry must stop new acquisition without
silently treating all retained history as erased. A new follow-up grant revocation must stop the
corresponding future follow-up even if historical evidence remains valid.

## Integration decision and alternatives considered

The owner approved the recommended lightweight topic metadata, original activity SHADOW results,
existing private-life authority extension and erasure integration. The comparison below records
why these extensions were selected. Exact field names and module boundaries are implementation
details; they do not require another approval.

### A. Topic lifecycle and candidate extraction

Gap: neither episode excerpts nor reading goals record an unresolved conversational topic,
closure, due time or one-shot consumption. A rebuilt projection alone cannot reliably remember
that a proposal was already consumed after restart or source cleanup.

Alternatives:

- Reuse GoalEngine storage by adding a conversational goal kind. This mixes task completion
  and conversation status and requires excluding it from every reading goal claimant.
- Keep a read-only projection with no durable state. Suitable for inspection, but insufficient
  for restart-safe closure and one-shot follow-up.
- Proposed: a small source-bound topic record, separate from facts and execution. It contains
  identity, conversation, source references, candidate proposal key, bounded label, due/expiry,
  terminal reason and consumed decision reference. Evidence links use the existing event model.
  It has no executor, tool queue, inferred personality or independent long-term fact content.
  This is the selected option.

Proposed state semantics: WAITING before due, DUE at due, CLOSED after explicit owner closure,
owner dismissal or one-shot consumption, EXPIRED when source/TTL is unavailable. Pause is a
scheduling gate, not closure. Deletion clears derived text through the existing erasure closure.
Do not resurrect a terminal candidate on a later model replay; a new independent owner input is
required for a new candidate. Two candidate records cannot independently reserve the same
topic episode's follow-up. The implementation uses a unique original source key, one active
conversational topic per conversation, and unique topic-revision/activity results. New independent
inputs after consumption may create a fresh candidate; closed source keys are never recreated.

Candidate selection uses bounded structured proposals with original event IDs and validated
source fingerprints. Only actual owner/private input may establish unresolved context or
closure; model text, received web instructions, quotes from other speakers, public messages and
management commands cannot. Exact quotation proves provenance, not semantic correctness.
Explicit owner closure/dismissal takes precedence over model suggestions, including in flight.
Authenticated closure and owner commands are deterministic; understanding ambiguous natural
closure still relies on model SKIP and needs human expression evaluation before any live use.

### B. Follow-up authorization

Gap: share authorizes a real reading result; commitment authorizes a requested task outcome.
Neither authorizes revisiting an unresolved private topic.

Proposed extension of the existing private-life approval: an explicit versioned follow-up scope
bound to the owner/private conversation, source/topic scope, expiry, authorization receipt and
finite item limit. At most one item per candidate; an unanswered message cannot schedule another.
Core validates this at admission, commit and delivery against the current source and approval.
SHARE retains its existing evidence checks and needs the applicable sharing authorization.
This should extend the existing authority object and revoke path, not create a parallel grant
registry. `LiveTextWindowApproval.life_shadow` owns the implemented scope.

### C. Structured decisions and SHADOW persistence

Proposed strict action union: SKIP, CONTINUE, EXPLORE, FOLLOW_UP, SHARE, DEFER. A proposal contains
only bounded source/topic/goal references and action-specific arguments. Core resolves references
and decides admissibility. CONTINUE/EXPLORE delegate to existing Goal/Reader paths; SHARE delegates
to existing SocialPlanner. DEFER is bounded by expiry and cannot erase a consumption marker.

OFF performs no new scheduling or model calls. Read-only preview reconstructs evidence without
creating topics, jobs or send reservations. SHADOW generates reviewable prose through the existing
model admission and usage path, records it as a source-bound derived result, and never inserts
Outbox rows. That result storage is new and needs an explicit erasure hook. An audit log or
runtime_meta blob is not a way around this design decision.

Prefer extending original activity job commit with a SHADOW sink and decision reference rather
than implementing another model worker. Job identity, lease, source hash, privacy epoch and
model request identity must agree in the final transaction. A settled call whose response was
lost is not regenerated for free; UNKNOWN or unrecoverable settled output stops that attempt.
Existing pending sends preserve their original delivery uncertainty when the feature is disabled.

## Planned data flow and file ownership

Owner/private event -> existing episode/source validation -> bounded topic proposal -> validated
topic lifecycle -> Wake/Life tick -> bounded action proposal -> Core policy/source/budget check ->
existing Goal/Reader or activity job -> SHADOW derived result / separately authorized Outbox.
Every later stage revalidates current evidence and authority. New owner input invalidates the
captured conversation revision before an unattended result can commit.

Life owns the independent branch and, once discussed, topic/decision module, migration,
configuration, API, Life worker, lifecycle cleanup and related tests. Changes in shared Core,
notices, private_life and budget require integration coordination with the tools lane. The runtime
lane's durable tool conversation work is not a prerequisite for read-only topic inspection and
must not be copied into this branch. CI scripts belong to the workflow lane.

Expected implementation locations after contract approval: `companion/life.py`,
`companion/life_worker.py`, `companion/wake.py`, `companion/notices.py`, Core/runtime wiring,
settings/API modules, the existing source cleanup registry, and one dedicated topic module plus
an Alembic migration. Precise final files depend on the selected storage and authority alternatives.

## Acceptance cases and proof boundaries

1. Ingest an unresolved synthetic bug through authenticated owner HTTP; advance to the next
   local day; run the independent native Life worker with mocked provider transport; verify one
   eligible SHADOW follow-up and zero Outbox messages. Close through owner input before due and
   during generation; both cases prevent a late reminder. Reopen requires new independent input.
2. Authorize a synthetic read through the real existing goal/Reader entry; submit actual mock
   FETCHED evidence through the existing observation contract; verify a SHARE proposal cites the
   result and preserves reading coverage. DISCOVERED, failed, expired and withdrawn results do not
   qualify. This proves protocol behavior, not real-world reading quality.

| Failure or race | Required observable result |
| --- | --- |
| Pause / STOP / OFF | No new unattended work or QQ items; original obligations remain accounted |
| Source delete / expiry / historical withdrawal | No new derived disclosure; text erased; stale backup cannot revive it |
| Follow-up grant revocation | No late commit or delivery from the old scope |
| New owner input | Background proposal yields; stale conversational revision cannot publish |
| Restart / duplicate workers | One decision/job/item; same terminal lifecycle after restart |
| Budget denial | No provider call; bounded postponement only within expiry |
| UNKNOWN usage or delivery | No automatic repeated paid call or QQ resend |
| Unanswered follow-up | No recurring nag; one-shot consumption survives restart |
| Group/private crossover | Private evidence and actions rejected outside owner/private scope |
| Manipulative draft | Reject fear, abandonment, guilt or coercive dependence wording in the bounded review |

After approved implementation, run only related `check.ps1 -TestFiles` checks and the real TCP
entrypoint/independent worker synthetic cases. Freeze once before full PR CI; record one receipt
and link the CI summary. No unchanged test reruns merely for handoff. This document-only candidate
uses `check.ps1 -Docs`; no product or synthetic acceptance claim follows from that check.

Finite live Beta requires separate explicit approval of owner/conversation, actions, dates and
expiry, source scope, model request/cost and outbound item caps, revocation/STOP controls and
operator observation. Offline implementation and a Draft PR do not authorize paid calls, QQ,
production changes, deployment or merge.

## Supplement: experience, intention and destination

The owner's supplemental request is included in A (bounded decisions) and B (real experience
to private diary/private SHADOW prose). It adds result destinations; the original fault matrix
and two native-worker E2E cases above remain required. Lane C owns the QZone consumer and its
existing SocialAutonomy/Outbox/Bridge lifecycle. R1 supplies expression evaluation, not a second
Life product pipeline. Root coordinates the QZONE_DRAFT interface.

### Additional primary-source verification, 2026-10-24

| Source actually inspected | Verified mechanism | Application to Core |
| --- | --- | --- |
| [DSH Schedule types](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/schedule.md) | Host-owned durable tasks bind the original session, preserve active/inactive state and delivery records; deleted records are not recreated | Original conversation identity, durable cancellation and due state; retain existing Wake/Life execution |
| [DSH Jobs](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/jobs/README.md) | Owner-scoped background work with completion notification; jobs-local is explicitly process-local | Completion-driven coordination is reusable; do not claim its local registry supplies restart persistence |
| [OpenClaw current heartbeat migration](https://github.com/openclaw/openclaw/blob/main/docs/gateway/heartbeat.md) | Heartbeats now use ordinary automation jobs; supports idleOnly, explicit owner delivery, empty-scratch short circuit, NO_REPLY and no_change; deletion survives restart | Reuse original jobs, pre-model no-candidate exit and explicit SKIP. Do not import a second heartbeat subsystem or assume older duplicate suppression remains |
| [Letta memory and dreaming](https://docs.letta.com/configuration/memory) | Background review/consolidation with step-count or compaction triggers; optional second model review costs additional tokens | Existing NightlyConsolidator/Reflection own organization; no additional memory store or claim that inactivity alone is the documented trigger |
| [InitiativeDialogue core](https://github.com/advent259141/astrbot_plugin_InitiativeDialogue/blob/master/core/initiative_dialogue_core.py) and [configuration](https://github.com/advent259141/astrbot_plugin_InitiativeDialogue/blob/master/_conf_schema.json) | Default 7200-second inactivity and 3600-second random response window; prompt pool varies with unanswered attempts | Only borrow idle/active-window gating; Core must use actual evidence, one-shot follow-up and no escalating pressure or random prompt pool |

The supplement's generic scheduler description is not evidence that DSH Jobs survives restart.
OpenClaw's inspected main branch differs from older HEARTBEAT_OK examples: ordinary automation
delivery and explicit quiet outcomes are the current reference. These are source observations,
not installed dependencies or compatibility claims about the production environment.

### Consolidated recommendation for discussion

Topic states and SHADOW are already requested. The remaining decision is a single cohesive
storage/authority integration choice: **lightweight topic metadata plus extensions to original
activity results and the existing private-life approval object**. Prefer this over generalizing
reading goals, introducing another planner, or adopting another workflow engine. Field naming
and migration numbering are routine implementation details and do not need owner selection.

Keep SKIP/CONTINUE/EXPLORE/FOLLOW_UP/SHARE/DEFER. Treat destination as a typed parameter of an
expressive action rather than add WRITE_DIARY or PRIVATE_MESSAGE_DRAFT action types:

- CONTINUE and EXPLORE refer to existing goals and actual Reader execution.
- FOLLOW_UP targets a source-bound private topic, with private draft output initially.
- SHARE proposes expressing supported experience; its destination is PRIVATE_DIARY,
  PRIVATE_MESSAGE_DRAFT or QZONE_DRAFT. PRIVATE_DIARY means retaining a private reflection,
  not asserting a delivery. It goes through the existing cited diary validation and closed-day
  rules. An open-day proposal can remain a private draft until the original diary worker is
  eligible; it must not force a daily entry or overwrite a normal saved diary.
- SKIP records a bounded reason without prose or send; DEFER cannot reopen CLOSED/EXPIRED.

One bounded proposal may choose one destination. A model-selected destination does not extend
source permissions. QZONE_DRAFT remains an unconnected consumer until C's source/audience
contract is agreed; private raw text and private diary do not enter its prompt by default.
The published destination is never inferred from the private draft destination.

Before model admission, evaluate current eligible evidence, terminal/consumed topic state,
due time, pause, idle priority and existing scope/resource gates. An empty eligible set exits
without a model call. A real source event, due goal, read completion or explicit owner feedback
can make an item eligible; a timer alone does not invent a new experience. Existing discovery
behavior outside the new OFF-by-default feature retains its original configured semantics.

Persist only source references, bounded candidate rationale, decision/outcome and relevant
feedback references; no chain-of-thought log or second factual history. All derived text uses
the same invalidation and erasure path. Explicit close or 'do not follow up' stays terminal
across restart and feature toggles. Independent new owner input may create a new candidate,
but cannot reset the closed candidate or recover erased text.

The owner explicitly accepted this recommendation. Topic metadata, activity SHADOW results and
the `life_shadow` addition to the existing approval are implemented. There is no remaining design
approval gate for these contracts. QZone review artifacts use the same SHADOW storage; C owns
downstream validation and any separately authorized future publishing work.

### Independent completed checks

`tests/test_life_source_reuse.py` adds four passing synthetic checks against existing APIs:
private experience is available to diary/episode consumers but not reading sharing; authenticated
uncached HTTP sharing preview and diary use the same actual reading evidence; transitive expiry
and explicit source withdrawal remove it from both; restart reconstructs the same preview.
The checks assert no model calls, Outbox, social candidate, diary run or usage writes. These
are source-reuse checks, not proof of the unimplemented decision/SHADOW pipeline or real TCP
independent-worker acceptance. Ruff and all four tests passed in the targeted check; one existing
Starlette/httpx deprecation warning remains. The JUnit receipt is local to this branch.
