# P9 approved-window continuity (local synthetic verification)

Recent dialogue remains closed by default. `LiveTextWindowApproval` now requires
an explicit `history_scope="approved_window"`, `max_history_turns` from 1 to 4,
and `max_history_utf8_bytes` from 1 to 16000 to allow it. The default is
`history_scope="none"` with both limits zero. `Settings.max_recent_messages`
must equal twice the approved turn count. Bridge's original WindowPlan binds
these fields in the existing plan/scope digest; it does not infer approval.

Only the same conversation's earlier complete SENT private replies can enter.
Each input needs this window's original input approval and an independently
approved receiver's valid Ed25519 capture attestation; each assistant reply needs
the original settled reply receipt (one chat attempt, or the ordered stages in the
explicit [B1 dual-scope contract](p11-history-memory-composition.md)). This attests a fixed receiver and
key holder, not a platform signature. Local loopback tests are synthetic evidence.
Window-before history, other identities/conversations/windows, unsigned captures,
redacted/corrected/forgotten sources, unfinished replies and UNKNOWN or uncertain
work never acquire eligibility. Reader, devices and other closed features stay
disabled; readonly M1 needs its own explicit scope. Global UNKNOWN/DELIVERY_UNCERTAIN
still stops the original window.

Turn and UTF-8 content-byte caps remove oldest whole pairs. The existing context
soft bound may remove more whole turns; fixed rules and the complete current input
remain intact even if they alone exceed that soft bound. Paid admission still
applies to the entire actual prompt and does not relax amount reservations.
These local byte limits are not provider-exact token measurement or billing proof.

After trimming, Core freezes the actual selected event/message references, source
and receipt hashes, privacy epoch, window/conversation and final model-payload hash
in the existing runtime_meta. It stores no duplicate dialogue plaintext there.
Existing check_job/check_model hooks re-read eligibility and compare the frozen
sources, including in final model preflight and before reply delivery. Admission's
payload hash must match the frozen context; a changed source stops the attempt
instead of replacing or silently removing its prompt. A fresh process reads the
same binding and attempts, with no allowance reset. No queue, fact database or
general context registry is added.

Erasure keeps the original conservative reply cleanup and newest independent
Ledger replay, including replies generated using recent dialogue. Original owner
inputs are not relabeled as derived events. Context hash metadata cannot restore
erased plaintext, and the changed epoch fences its reuse.

No deployment, real QQ, paid provider call, private-data read or continuous-dialogue
production acceptance is authorized or established by these local checks.

Verification on base `9eb673747ccdf05372426926370d018e05665e3d`: repository lint
passed and 147 focused tests passed in 76.37 seconds (one existing Starlette/httpx
deprecation warning). This covers continuity, the existing live text window and
command suite, prior P9 context cases and both original real-localhost five-capture
HTTP/WS cases. Earlier failures repaired a missing conversation lookup in the new
snapshot check and two invalid test fixtures; no failed run is counted as a pass.
No full suite, embedding benchmark or frozen P8-B benchmark was run.
The final test-only budget negative case uses `max_input_tokens=1` instead of the
removed tokenizer-proof field, retaining all zero-I/O/zero-reservation assertions;
its five-parameter group and repository lint passed separately in 3.71 seconds.
