# Autonomous Life: source-bound SHADOW

The feature defaults to OFF. It can inspect actual owner/private input and real Reader results,
record WAITING/DUE/CLOSED/EXPIRED topics, and generate a bounded decision through the original
activity job, model budget and Life worker. There is no new executor, factual memory store or
QQ sender. Real sending, paid experimentation and production deployment require their own
approval; this candidate is for offline verification and a Draft PR.

## Configuration and inspection

`Settings.life_loop` accepts `mode = OFF | PREVIEW | SHADOW`, at most eight sources per topic,
a topic lifetime up to seven days, follow-up delay (default one day), at most two daily decisions
by default and a one-hour decision interval. `wake_enabled` must also be on to schedule SHADOW.
Quiet hours, owner pause, current input, idle time, existing budget and uncertain outcomes gate
generation. No eligible source means no Life model call. PREVIEW makes no new tasks or model calls.

- `GET /internal/v2/life/topics` uses the existing control token and `Cache-Control: no-store`.
  It returns currently valid source previews, topic states, and eligible private SHADOW results.
  Preview does not mutate state. Source fingerprints allow a reviewer to bind an exact source.
- `POST /internal/v2/life/topics/{topic_id}/close`, with `source_event_id` of an owner event in
  that conversation, explicitly closes the topic. The control token is required.
- Owner commands `/结束话题 [topic_id]` and `/不再跟进 [topic_id]` close matching topics and
  withdraw their current drafts; omission applies to the current conversation. The command itself
  is not a candidate.

Topic candidacy means there is an actual experience to consider, not that a model has established
an unresolved fact. One active conversational topic groups up to eight actual inputs. A newer
input changes the snapshot and postpones consideration; it cannot commit an older in-flight
proposal. If the latest owner input cannot fit within the topic source bound, generation stops
rather than considering an older incomplete context. A read result binds to an owner conversation
through its source ancestry. Model-derived
memory, fabricated daily routines, quoted/rich-only inputs, private-group crossovers and management
commands are not new Life sources. Model selection can always SKIP.

The new pipeline replaces generic proactive scheduling and new SocialPlanner share scheduling
while PREVIEW/SHADOW is selected. OFF restores those existing configured paths. Already accepted
commitments, existing delivered/uncertain actions and their accounting retain their original
meaning; changing mode does not erase or replay them.

## Actions and destinations

The strict proposal uses SKIP, CONTINUE, EXPLORE, FOLLOW_UP, SHARE or DEFER. Source IDs must belong
to the captured input. CONTINUE must refer to an existing source-linked self goal. EXPLORE can
carry the native `GoalProposal` (`topic`, `source_event_ids`, `scope`, optional `priority`).
The default-false `life_loop.read_goals_enabled` switch submits these choices to the existing
GoalEngine only in SHADOW. It grants no read permission: existing goal, adapter/source, budget,
pause and window checks still apply. Without a concrete goal or with the switch disabled, the
result remains a reviewable intention. Terminal deduplicated goals are not executed again.

Submission happens in the original activity commit transaction. The Life worker schedules the
native goal; the independent Reader v3 worker returns its ordinary evidence and usage receipt.
Migration `0038_life_goal_binding` adds only the nullable native goal ID to the activity result.
Readback exposes the current native goal state, result event and error code. No extra execution
queue or retry policy is introduced. Wake's automatic new-goal selection yields while Life
read-goal submission is enabled; accepted goals and commitments retain their original paths.

Only a completed native goal result linked to a successful, settled Life choice under the same
private approval can supply the next private Life decision. Its original goal evidence must
remain in that approval's exact source set, with unchanged fingerprints and current validity.
Unrelated reads and arbitrary descendant events cannot inherit this purpose. QZone draft
permission still requires exact per-result grants. A failed or uncertain read supplies no SHARE
evidence; native recovery and accounting remain authoritative.

FOLLOW_UP targets only a conversational topic and PRIVATE_MESSAGE_DRAFT. SHARE can choose
PRIVATE_MESSAGE_DRAFT or PRIVATE_DIARY; QZONE_DRAFT additionally needs explicit per-source purpose
grants. A private diary result reuses the original cited reflection validation and is a private
SHADOW draft, not a new canonical daily diary entry. Ordinary closed-day diary generation remains
with DiaryService and its existing worker. SKIP creates no prose. DEFER is bounded by source expiry
and permits at most one further decision; it does not loop on an unanswered topic.

One completed decision consumes its topic. Explicit closure, expiry, mode changes or restart
cannot reopen that candidate. New independent owner input can form a new candidate. Direct pressure
wording is rejected and the prompt excludes guilt, abandonment and demands for replies. Semantic
appropriateness still needs human evaluation before any live Beta; synthetic outputs do not prove
real model expression quality.

## Authority and QZone draft handoff

Model generation with live or retained owner-authority custody requires the current
`OwnerAuthorityView.policy.life_shadow`: exact conversation, allowed source IDs and private
purposes, authorization hash, expiry and finite item cap. Native activity jobs pin the complete
owner authority reference in their admission transaction. Current permission and the original
job lease are checked independently; a revision never rebinds an old job. This is additional to
the original paid admission and grants no delivery. STOP, restore, changed sources and expired
approval fence commit and readback even if runtime settings are later switched to mock/OFF.

`life_shadow.qzone_draft_sources` supplies per-source `LifeQZoneSourceGrant`: grant identity and
revision, `purpose=qzone_draft`, event ID, current source fingerprint, expiry, `audience=owner_only`
and exact owner QQ ID. Every source in the model context must have a current matching grant before
QZONE_DRAFT is offered. A grant for a subset of sources does not permit a mixed private prompt to
produce a QZone artifact. No diary or private-message purpose implicitly authorizes QZone.

The output `LifeQZoneDraft` contains schema version 1, draft/job ID, purpose, owner-only audience,
owner ID, text, content hash, source snapshot hash, source grants and effective expiry. Hashes use
the project's canonical `paid_budget.sha`: content is `{"text": text}`; source snapshot is the map
`{event_id: source_sha256}`. The DTO lives in `companion/life_protocol.py`. It is an activity SHADOW
artifact; it is never a SocialAutonomy READY plan and cannot be passed to reserve/send.
Readback revalidates the DTO, native successful job and settled usage, retained text, both hashes,
exact current unique source grants, audience/owner and effective expiry. A malformed or changed
artifact is hidden individually without writing during GET or failing the whole view.

`LifeTopics.consume` accepts an exact retained draft and an explicit execution action:
`qq.private.proactive`, `diary.save` or `qzone.publish`. The request binds the original owner
authority and `sha({"text": retained_text})`; it cannot supply replacement prose or sources.
This is separate from generating or viewing a draft and never calls a second model to rewrite it.

For `qzone.publish`, all original model-context sources must pass the existing `public_source`
checks. Exact private-source draft grants permit owner review only: private chat, uploads and
fiction cannot become publication evidence, even with owner-only visibility. The current `social`
and `account_actions` purposes and the independently configured QZone action are required.
The consumer freezes the same text into the native SocialAutonomy plan and reserves its original
Outbox once in one transaction. Source roots, exact draft, current reference, STOP/OFF/DND and
native action quotas remain checked before dispatch. UNKNOWN retains the native reservation.
Visibility expansion beyond owner-only is outside this stage.

Private delivery additionally requires `proactive`, the original conversation revision and
native pause/DND/daily/cooldown limits. Life SHADOW/SKIP results reserve no send slot; the common
counter counts a Life-origin private Outbox group once at enqueue. Feedback affects these checks
only with the independent current `affect` purpose. Source-backed prior modules remain the
consumers; the selector does not create expression candidates from idle time or lack of reply.
While Life is PREVIEW/SHADOW, SocialAutonomy does not independently regenerate public-source
wording. Its separately scoped account-profile handling and retained native work remain native.

Shared HTTP/Outbox integration supplies `owner_delivery.record_life_draft` for the completed
activity job; the ordinary running-job `record_replies` contract is unchanged. Diary owns
`DiaryService.save_life_expression` and the daily evidence/revision lifecycle. Missing native
interfaces fail closed rather than reopening a completed job, relabelling an owner correction,
or paying for another daily generation. REM remains Life-owned and requires explicit fiction,
owner scope and source/erasure identity before any private expression consumer can accept it.
The domain slice alone does not certify these shared integrations or real publication.

## Recovery and erasure

Migration `0037_life_topics` adds only topics, topic source links and activity results keyed by
original job ID. Jobs own leases, attempts and unique execution identity. Model usage and the
original independent journal own all actual costs. Failure, UNKNOWN or a lost response never
automatically pays for the same decision again. Budget refusal before admission can use the
existing bounded notice retry path; source expiry still stops it.

Source fingerprints and conversation input identity pin a decision. Readback suppresses a draft
when its source changes, expires or its purpose grant is revoked. The registered derived cleanup
erases result prose, rationale and JSON and cancels pending work; latest-ledger restore applies
the same erasure to old backups. No cleanup refunds an actual model call or resets UNKNOWN.
Owner input supersession and pause cancel only the stale Life job through the existing reply
lifecycle; they do not stop the live conversation window. Actual UNKNOWN and authority failures
retain the original global accounting/authorization guards.

## Validation boundaries

Related tests cover the source/lifecycle failure matrix, original notices and goals, and existing
social autonomy. The native-worker cases use real loopback HTTP input and Reader v3 routes, an
independent Core/store in the original Life worker, native paid admission/settlement and
`httpx.MockTransport`. Foreground reply jobs are explicitly cancelled to isolate background work;
retained synthetic sources are explicitly admitted to the subsequent SHADOW window. They are not
production capture or live QQ acceptance. No external model or QQ call is made.

The worker cases verify next-day Bug follow-up, reading-result SHARE, explicitly scoped QZone
draft and in-flight grant revocation. Unit/integration checks also cover owner closure, source
withdrawal/expiry, pause/OFF, new input, restart, duplicate claiming, budget denial, UNKNOWN,
bounded DEFER, purpose isolation and erasure replay. Exact candidate checks and eventual CI are
recorded once in the lane receipt. Finite live Beta still needs explicit dates, source/purpose
scope, request/cost/item caps, destination/audience and stop/reconciliation responsibility.

The execution follow-up additionally verifies owner HTTP input → independent Life/model worker →
native Goal → independent Reader v3 with mocked source transport → actual receipt → independent
Life SHARE, including same-approval private-purpose checks and zero outbox items. Model output
is synthetic; this does not establish live model decision quality.
