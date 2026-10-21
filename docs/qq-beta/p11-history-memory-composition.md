# B1: approved recent dialogue and readonly facts

This is an offline, default-closed contract. It does not approve deployment, QQ,
paid requests, memory writes, or a real M1/B1 acceptance window.

`LiveTextWindowApproval` and Bridge's existing WindowPlan can explicitly combine
`history_scope="approved_window"` with `memory_scope="approved_facts"`.
The combination requires both `history_approval_sha256` and the existing separate
R5 `memory_approval_sha256`. One human authorization can explicitly approve both;
neither reference is inferred from the other or from `memory_enabled`.
With history `none`, its approval reference must be null. History-only plans keep
their original whole-plan R3 approval and may omit the new reference; historical
plan bytes, hashes, STOP and retirement records are preserved without backfilling
new authorization. This does not reopen stopped authority.

The original plan/config bind the owner/private identity, window and expiry.
The opened binding, current privacy epoch, latest ErasureLedger and independent
Budget/Window histories remain authoritative. The combination never includes
earlier windows or another conversation. Only original signed-capture inputs and
complete SENT replies with valid original receipts are eligible. A reply can have
one settled chat attempt, or the existing ordered memory-review/chat attempts;
the actual request IDs, original frozen context and sources must still agree.
The receiver attestation is not a platform signature.

All approved-facts windows, including memory-only windows, compare the latest
external Ledger identity and complete sequence with applied DB erasures and pin
its digest in the opened binding. A fsynced erasure with a rolled-back DB, missing
Ledger, changed identity or incomplete replay blocks further use without automatic
replay, repair or budget adjustment.

History is deterministically bounded to the approved whole-turn and UTF-8 limits
before retrieval. Only the at-most-six pinned CONFIRMED, private owner/project
facts with `needs_review=0` can become SQL/FTS/BGE candidates. Actual Memory.search
uses the current query and approved recent dialogue for indirect references.
The existing memory_review receives these candidates and the bounded dialogue as
untrusted reference data. No traits, Reader data, automatic extraction, slash
control commands or live memory mutations are enabled.

Review freezes history references and hashes alongside its candidate/selection
and actual payload hashes in the same Job metadata. Chat retains those same
history turns and all selected facts. Its soft trimming does not silently remove
a conflicting reference between review and chat; fixed rules and the complete
current input also remain intact. If the complete payload exceeds actual paid
admission limits, the original engine refuses it. These local byte/soft limits
are not provider-exact token counts. Conflicting history and facts remain labeled
references; the instruction requires uncertainty and clarification, without
voting by similarity, frequency or recency or mutating an authoritative fact.

Reservation, final HTTP dispatch, response acceptance and delivery re-read the
original sources, signatures, content, scope, expiry and epoch. A changed source
is not silently replaced. Existing request/admission payload hashes, stage order,
UNKNOWN/uncertainty fences, one Outbox reply, at most five total model attempts,
amount limits, concurrency and restart accounting all remain in force. Two
stages for a reply consume two attempts. No queue or provider path is added.

Local tests use real native retrieval/BGE and authenticated localhost captures
with Mock model/send transports. They validate source and prompt construction,
not real model conflict resolution, QQ delivery, billing or long-term quality.
Real acceptance still needs a new precise dual-scope authorization and separate
human observations. Memory changes remain separate approved native actions while
business is stopped; epoch changes require fresh authority and handoff/open.
