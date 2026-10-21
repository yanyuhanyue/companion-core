# Evidence-backed growth foundations

This is offline P11 B3/C1/D1/D2 preparation. It enables no QQ, Reader, paid model,
autonomous worker or outbound sharing. Existing feature defaults stay disabled.
Synthetic fixtures verify contracts; they are not human feedback or elapsed-day
quality evidence.

## Private read views

All HTTP routes below require the existing owner control token. A Bridge token
does not authorize them. Views return the current privacy epoch and rebuild from
SQLite. They create no queue, budget reservation, model request or retained copy.
Responses use `Cache-Control: no-store`, including empty views after forgetting.

| Route | Result |
| --- | --- |
| `GET /internal/v2/interests` | `items` contains `self.interest`; separate `owner.preference` contains confirmed, unambiguous private user preferences and their original evidence. |
| `GET /internal/v2/timeline?topic=…&days=7&limit=40` | Matching original events, episode references, actual reply delivery states, and related outstanding goals. |
| `GET /internal/v2/affect/{event_id}/preview` | A `SHADOW` proposal from explicit private owner wording, even when Affect is disabled; it changes no expression state. |
| `GET /internal/v2/social/{event_id}/preview` | A private `SHADOW` draft from a valid accepted `FETCHED` source, with expiry, duplicate indication and skip reason. `auto_share` is always false. |

`interest.profile` exposes the two subject namespaces explicitly. User preferences
never become evidence that the companion likes something. Self interests require
completed self goals and their actual unredacted `FETCHED`/`WATCHED` outcomes.
Distinct content hashes **and** distinct canonical targets are required for
independent reads. Repeating one article on later days adds no independent day.
Status and counts are recomputed from retained evidence instead of trusting an
old `traits.status`. Evidence expiry or native erasure removes the projection
even with every worker stopped. This statistical status is not approval to set
a permanent personality.

Core's ordinary memory context uses only the short verified `self.interest`
projection. Full evidence stays in the private inspection view. Approved live
text retains its existing scope and excludes these extra trait references.
The existing preference-explanation command uses the same verified projection.

The timeline requires an explicit 2–80 character topic, 1–7 local calendar days,
and a limit of 1–40 items. It scans at most 200 recent eligible events and reports
`truncated` when that scan or output limit is reached. It includes outstanding
goals separately; completed goals must fall in the requested date interval.
`DISCOVERED` is discovery, `FETCHED` is acquired content and `WATCHED` retains its
original analysis coverage meaning. Simulation sources are visibly prefixed
`SIMULATED_`. Replies include text only for `SENT`; pending or uncertain delivery
never becomes a completed conversation. No model-generated diary or summary is
promoted to independent event evidence. This view is requested explicitly and
is not automatically inserted into chat prompts.
Explicit Memory/Life/Diary management commands are excluded from both the
timeline and current/prior diary sources, including retained events without an
old `control_command` marker. Unknown slash text remains ordinary conversation.

## Expression and source lifetime

Affect recognizes only the existing fixed communication-feedback rules. It
rechecks event type, private conversation, owner subject, unredacted source,
retention, expiry and exact feedback provenance before projecting a state.
Quoted directives, control messages, group/public sources, arbitrary stored
state strings and expired feedback do not affect expression. Enabled feedback
can affect only the existing tone/engagement/pace hints, not facts, commitments,
permissions, budgets or send limits. PAD and durable personality inference are
not added.

Revoking or expiring a Source Grant stops future reads. It does not erase accepted
history. Explicit source forgetting uses the existing Ledger closure and removes
dependent history and projections, including after an older backup is restored.
The shadow draft does not imply approval to fetch or share; semantic deduplication,
daily limits, cooldown and any future real-send approval remain separate gates.

## Private daily pipeline

The existing DiaryService, NightlyConsolidator and ReflectionService remain the
only daily pipeline. Their timezone/natural-day idempotency, source hashes,
leases, epoch checks, owner corrections, native erasure closure and latest-Ledger
restore behavior are retained. No additional timer or diary store is introduced.

Only actual owner events and acquired `FETCHED`/`WATCHED` observations count toward
the diary's minimum-source threshold. Discovered titles may be labeled background
when a real source justifies generation, but a list of titles alone yields
`SKIPPED` without a model call. Sent-reply provenance still requires an actual
same-day `SENT` reply. Diaries stay private; `diary_auto_share` remains unsupported.

Actual owner review, repeated independent calendar days, stable interest quality,
private sharing quality and a real chat-day → diary → correction/deletion → next-day
continuity cycle remain to be validated under separate real scopes. These offline
checks do not sign off P11 or authorize any new live window.
