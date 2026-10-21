# P9 recent delivered dialogue (offline)

Base: `8bd28718003b7ab087bf5dc01759fe18beeee3f3`.

`companion/dialogue_history.py` owns recent-turn selection and soft-bound trimming.
An eligible turn contains one nonempty owner input and one nonempty SENT
`qq.private.reply`, bound to the same event and conversation. Inputs must be private
QQ message events before the current input, unredacted, outside diary/control
commands, and without SUPERSEDED/FORGOTTEN memory evidence. Multiple eligible
Outbox rows for one input select the last inserted SENT reply once. Selecting history
does not change delivery state or retry an uncertain send.

The existing `max_recent_messages` cap permits at most `floor(cap / 2)` complete
turns; zero or one permits no history. Optional goal/affect/memory references remain
separate blocks. Trimming removes those blocks in their existing order, followed by
oldest whole turns. Fixed rules, the complete current input and its bounded current
shared-content reference survive. Required input may exceed the soft bound; the
original budget engine still admits or rejects the full prompt. This local UTF-8 byte
bound is not provider token measurement, price proof or a paid reservation.

The fixed-base regression run reproduced 16 failures with 16 passing cases in 6.00s:
non-SENT inputs entered prompts, partial turns survived trimming, non-reply actions
completed turns, and duplicate SENT rows repeated inputs. The first repaired round
passed all 32 cases in 6.29s. Six further cases cover source/privacy eligibility,
future/current event exclusion, optional reference blocks and current content priority.

Final targeted verification used `scripts/check.ps1` with `PYTHONPATH` set to this
worktree and `HF_HUB_OFFLINE=1`: repository lint passed, **55 tests passed in 16.94s**
with one existing Starlette/httpx deprecation warning. This includes all 38 P9 cases,
the existing 100-message replay, bounded memory recall, correction/forgetting and
old-backup erasure, content-reference priority, the live history-approval refusal,
and paid missing-approval zero-I/O checks. The P8 synthetic uncertain-delivery
expectation now excludes both sides of that turn; its uncertainty state stays intact.
The frozen P8-B 7/8 benchmark and its examples were not changed or rerun.

These are synthetic prompt and privacy checks. At this initial P9 validation,
`live_text.guard` required `max_recent_messages == 0`; no approval switch, provider
contract, memory retrieval policy, model admission, delivery queue or deployment was changed. Real provider
HTTP, QQ I/O, private-data access, billing and continuous-dialogue acceptance remain
unverified and require their separate approvals.

The separately approved-window extension is described in
`p9-approved-window-continuity.md`; its history scope remains closed by default.
