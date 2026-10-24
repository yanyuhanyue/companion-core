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
to the captured input. CONTINUE must refer to an existing source-linked goal. In SHADOW,
CONTINUE/EXPLORE record reviewable intentions without starting a new read. Existing authorized
Goal/Reader tasks keep using their original execution path.

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

Live-mode model generation requires `LiveTextWindowApproval.life_shadow`, an extension of the
existing owner/private window: exact conversation, allowed source IDs and private purposes,
authorization hash, expiry within the window and finite item cap. This is additional to the
original paid admission. It grants no delivery. A changed or expired approval fences commit and
readback. Source invalidation is checked before model I/O and before commit.

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

Lane C can validate this DTO offline. A future authorized publication must revalidate current
grants and exact content/source snapshot through the existing account-action path. Current code
does not create a publication task. Visibility expansion beyond owner-only is outside this MVP.

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
