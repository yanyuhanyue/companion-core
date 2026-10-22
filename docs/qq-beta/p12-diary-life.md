# P12-D: private diary and bounded life preparation

Baseline: `c6493d481676e1e6684fb49564c2557224950d20`. This is an independent
offline package. It enables no live diary worker, Reader, exploration, goal
execution or proactive delivery. Real natural days, human diary review, Reader
receipts, proactive messages and phone confirmation remain `NOT_RUN`.

## Native pipeline and repaired boundaries

DiaryService, diary_worker, NightlyConsolidator and ReflectionService remain the
only daily pipeline. The new integration regression runs a synthetic closed day
through consolidation and private diary, owner correction, next-day continuity
from the original received events, deletion, and restoration of an older encrypted
backup with the latest ErasureLedger. The deleted diary and its revision remain
absent; the independently grounded next-day diary remains available. No diary
text becomes independent fact evidence and no automatic share is queued.

Goals now use the same transitive source-lifetime check as Diary. A recent result
derived from an expired older event cannot authorize acceptance, execution or a
retained goal view. Expiry writes the existing Ledger, including outcome sources.

The existing owner-only `GET /internal/v2/goals` adds:

| Field | Meaning |
| --- | --- |
| `mode=SHADOW` | Unaccepted candidate, without a read or send action |
| `source_evidence` | Original event IDs, timestamps, source and acquisition level |
| `execution` | Actual step state, attempts, last error, outcome ID and last update; `null` before a step exists |
| `result` | Retained observed result, source/hash/time and `FETCHED`/`WATCHED`; `null` without completion evidence |
| `delivery` | Native Outbox state and actual `sent_at`; completion alone is not delivery |
| `phone_confirmation` | Always `null`; an Outbox ACK does not prove the owner saw the reply |

Completed and running goals are `READ_ONLY` projections. Simulation sources keep
their `observation.simulation` identity. These fields create no read permission,
model request, schedule or message.

Social source lifetime is checked while considering a draft, before queuing,
before leasing the native Outbox and immediately before `begin-send`. The final
checks also revalidate candidate state/expiry, the social switch, accepted source
hash/target and the exact outbound text. Source or content changes cancel work
that has not begun I/O, preserving zero send attempts. Already `SENDING` or
`DELIVERY_UNCERTAIN` records keep their uncertainty and are never treated as
successfully recalled or made available for replay. Ordinary owner replies keep
their existing path.

## Verification and limits

`tests/test_p12_diary_life.py` exercises the native diary loop, independent-day
preservation during deletion/restore, goal intention/running/pause/resume/failure,
completion versus Outbox ACK, upstream expiry and restore, all four share
boundaries, final-dispatch source/candidate/text/switch changes, restart,
post-I/O uncertainty, ordinary replies, and Reader rejection without a required
grant. Existing Diary, Consolidation, Goals, Exploration and Source Grant suites
retain budget, idempotency, deadline, quiet-hours, cooldown, duplicate suppression,
grant revoke versus processing withdrawal and source-forgetting regressions.

These are synthetic fixtures. Clock advances do not establish real calendar-day
growth, genuine reading, phone receipt or quality. New paid model calls and real
QQ sends from this package are both zero. Deployed image/config, live usage and
provider billing evidence are `null`; public and third-party delivery stay closed.

## Separate real-use scopes to prepare

1. **D1 closed-day diary:** bind a stopped-chat source set to one actual local day,
   exact owner, current epoch/Ledger, candidate image/config, original source IDs
   and source hash. Approve diary/optional reflection budget separately from chat.
   Keep `diary_auto_share=false` and all proactive delivery disabled. Owner review
   records false experience, uncertain claim and useful continuity individually.
2. **D1 revision and deletion:** separately bind the day/entry ID and requested
   correction or deletion. Confirm revision provenance and current epoch, then
   restore an approved pre-deletion backup to a network-isolated new target with
   the latest Ledger. The live DB, budget and window journals are never replaced.
3. **D2/D3 read candidate:** inspect the goal's `SHADOW` scope and sources first.
   Bind the exact read target, R6 grant/adapter/account identity, expiry, request
   and byte ceilings, and stop owner. Configure the adapter to require its grant;
   no grant means no lease or external read, even with a goal or interest.
4. **D3 first share:** inspect `GET /internal/v2/social/{event_id}/preview` before
   separately approving an exact owner recipient, text/source, expiry and one
   native Outbox send. Recheck quiet hours, cooldown, frequency, duplicate status
   and cancellation. Record phone confirmation separately from `SENT`.

All four scopes need an actual candidate identity and explicit owner decision;
this document supplies neither. Begin with one approved day and one reviewed
candidate, then collect genuine 3/7/14/30-day samples without backfilling dates.
Stop expansion on UNKNOWN, uncertain delivery, source drift, wrong recipient,
failed erasure or broken journals. Disable the relevant feature and retain the
latest independent records; rollback must not recreate expired permissions,
allowance or messages.
