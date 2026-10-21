# Bounded readonly M1 recall

This is a default-closed offline implementation contract. It does not authorize a
real R5 window, account access, paid calls, or native memory changes.

`LiveTextWindowApproval` and Bridge's exact plan schema add:

- `memory_scope`: `none` by default, or `approved_facts`.
- `memory_approval_sha256`: a separate explicit R5 evidence reference; required for
  `approved_facts`. An existing R3/R4 approval cannot implicitly enable memory.
- `memory_facts`: at most six native fact pins: `memory_id`, `subject` (`user` or
  `project`), `status=CONFIRMED`, `content_sha256`, `memory_sha256` (the complete
  memory row), and `evidence_sha256`. With scope `none`, this list and R5 reference
  must be empty/null. These pins never supply prompt text.

Approved facts must remain private, confirmed, supported by unredacted owner QQ
or owner-decision evidence, and have `needs_review=0`. Reader/source-grant evidence
is excluded. Evidence must remain within native raw retention throughout the
window. Any changed fact/evidence/epoch invalidates the window.

The original memory-only scope keeps history `none`/zero limits. An explicit
[B1 dual-scope contract](p11-history-memory-composition.md) additionally permits
approved-window history with its separate approval reference. `Memory.search` restricts its
SQL candidates and missing-evidence correction before FTS/BGE; vector-cache reads
also restrict to the actual candidate IDs. Traits and unapproved facts are absent
from both review and chat. Semantic retrieval writes the native vector index.

One Job permits `memory_review` then `chat`, or only `chat` when retrieval has no
candidates. Review failure/invalid selection cannot fall back to chat or retry a
stage. R5 requires `per_request_calls=2`; ordinary M0 still requires one. The same
budget engine, logical Job, at-most-five total attempts, amounts, concurrency,
UNKNOWN accounting, and independent Budget Journal remain authoritative.

Core freezes candidate/selection references and hashes, the actual review payload,
and the memory references retained after chat trimming with its actual payload.
Metadata contains no extra fact plaintext. Reservation, final model dispatch,
response acceptance, and delivery recheck the stages, sources, epoch and admission
payloads. One Outbox reply binds all ordered SETTLED request IDs; no synthetic usage
or second provider/Spool/Outbox path is introduced.

QQ slash commands remain rejected. Idle extraction and Core's automatic memory
writes stay disabled under a live-text binding. Generic memory search/decision
HTTP routes reject access under that binding, including STOP; a Bridge token is
not R5 authorization. Explicitly approved native changes must happen separately
while business is stopped, followed by a fresh explicit handoff/open. Epoch
changes never automatically rebind the old window or create an ACK exception.

Actual acceptance still needs a fixed new R5 scope and human evidence over the
approved facts, correction/forgetting, and newest-Ledger recovery. The independent
temporary native Memory pilot and these localhost/Mock tests do not establish
that business acceptance, QQ command control, automatic extraction, or full M1.
