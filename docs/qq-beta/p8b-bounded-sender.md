# P8-B bounded QQ test-window sender

Status: **offline synthetic implementation; live window NOT APPROVED**. This change
does not deploy the current main, read QQ, send real messages, or reopen the P7
window. Historical P7 phone samples remain 5/5; strict acceptance remains 0/5 and
0/20, with live E2E P50/P95 null. `production_ready=false`.

## Reproduced gap and boundary

At base `a33d3cb77f348828b17e4bb48c621c53cfcdc017`, public Bridge CLI help
exposed only `--config`; it had no durable bounded once-send entry. A behavioral
CLI test failed on the missing command before implementation (P1). The existing
delivery state machine already preserved uncertain sends; it is reused here.

The new commands are separate from `daemon`:

| Command | Local behavior and external boundary |
|---|---|
| `window-open` | Validate the immutable plan, independent private inventory/authorization, confirmed Spool and authenticated Core `/internal/v1/ops/status`; register one window. |
| `window-ingest` | Validate exactly one individually approved owner/friend text frame from a restricted file; record its unique input allowance in Spool, then reuse `Spool.flush_one()` to Core HTTP v1. |
| `window-send-once` | Check queues, lease at most one existing Core Outbox item, require its exact individual reply approval, reserve one attempt durably, then reuse `deliver_tick()` / `begin-send` / Spool uncertain fallback / existing OneBot client / Core result reporting. |
| `window-status` | Read only local receipt counters and journal consistency; no HTTP, no new Spool/window if absent. |
| `window-stop` | Persist an irreversible local stop when no other process holds the Spool lock; no HTTP, no new Spool/window if absent. |

The file-based frame staging interface is intended for FakeOneBot/local synthetic
tests in this phase. It does not attest that an operator-supplied frame came from
QQ. A future approved live run must separately specify an authenticated capture
method and private provenance; a fabricated fixture never counts as phone evidence.
The normal receiver cannot run against a Spool registered for controlled windows.
One input is completed before another distinct input can enter Core. Previously
accepted identical inputs are no-ops; any source-ID/content/time conflict stops.

## Private artifacts and immutable scope

All JSON files must be regular non-symlink files, at most 64 KiB, with POSIX mode
0600. Windows requires an operator-managed restricted ACL. The private plan binds:

- a new `win_` plus 32 hexadecimal digits, `timezone=Asia/Shanghai`, and explicit
  absolute `starts_at` / `ends_at` with `+08:00`; the end is exclusive;
- the existing stable `protocol_epoch`, owner/bot identity digest, canonical Bridge
  configuration digest, private configuration version and exact Core/Bridge/protocol
  `image@sha256:` digests;
- independent inventory and new-window authorization hashes;
- fixed limits: at most 5 new inputs, 5 attempt reservations, 5 possible visible
  replies, concurrency exactly 1; approved text length bounds at most 500 characters;
- `execution=local_synthetic` by default and an absolute independent journal path
  outside the Spool directory, in an existing private directory.

`digest(value)` in `companion_onebot_bridge.window` is SHA-256 of UTF-8 canonical
JSON (`spool.encode`, sorted keys, compact separators, unescaped Unicode). The
configuration digest is of `dataclasses.asdict(BridgeConfig)`, including the
unmodified persistent `delivery_enabled=false`; it is not a hash of TOML whitespace.
The identity digest is of the private two-item owner/bot list. Do not publish the
private JSON, configuration, raw IDs, message bodies, paths or authorization evidence.

The independent inventory is exactly the plan's `identity_sha256`,
`protocol_epoch`, `config_sha256`, `config_version`, and `image_digests`. Its canonical
digest must equal `runtime_attestation_sha256`. Bridge has no Docker socket and
does not independently discover image identity: a trusted deployment operator must
attest the actual running images/configuration. Matching a self-written inventory
does not prove deployment provenance. That operational check remains BLOCKED here.

`local_synthetic` rejects any configured Core/OneBot HTTP/WS hostname other than
`localhost`, `127.0.0.1`, or `::1`; normal private-address DNS pinning still applies.
`approved_live` additionally requires a separate private authorization record:

```json
{
  "kind": "owner_qq_window",
  "approved": true,
  "scope_sha256": "<canonical plan hash excluding authorization_sha256>",
  "evidence_sha256": "<hash of the newly approved written window record>"
}
```

Its canonical hash must equal the plan's `authorization_sha256`. This private file
records an already obtained human authorization; software does not authenticate
mail or grant permission by creating a JSON file. No such live authorization was
created or used by P8-B. Old P7 authorization does not match a new exact scope.
This candidate additionally requires Core `mode=demo` with all reported features
disabled; separate paid-model/M1/Reader approval does not silently change that gate.

An individual input approval contains exactly `window_sha256`, `event_id`,
`input_sha256` (normalized redacted text), and `evidence_sha256`. An individual reply
approval contains exactly `window_sha256`, `event_id`, `message_id`,
`reply_sha256` (the exact Outbox text), and `evidence_sha256`. Both evidence hashes
refer to private operator records of actual individual approval, not public text.
Group/stranger/bot mismatch, non-text segments, old timestamps, changed hashes,
unrelated historical delivery keys, and a second distinct pending input are refused.

## Durable accounting, crash recovery and limits

The existing Spool SQLite database gains a singleton window receipt, approved-input
receipts, and attempt receipts. These are counters/bindings, not another message
queue: Core Outbox and existing Spool inbox/results remain the delivery authority.
The existing cross-process OS lock covers the whole invocation. A local busy fence
also rejects concurrent tasks using the same object. Restart preserves the same
identity, limits, absolute deadline, attempt count and fencing number.

The independent fsynced hash-chained journal records OPEN, INPUT, RESERVED,
IO_STARTED, ACK/UNKNOWN and STOP. Its current head is mirrored in SQLite.
Each journal append is flushed/fsynced **before** the corresponding SQLite state;
every action checks the latest head. A missing, truncated or older Spool, or a
crash between journal append and SQLite commit, blocks reconciliation rather than
recreating quota. The journal's exclusive-create registration prevents reopening
the same window after clearing Spool. An interrupted RESERVED/IO_STARTED attempt
consumes quota even if no send reached the platform. It is not refunded or retried.

Before any send, Core active states LEASED/SENDING/RETRY_WAIT/DELIVERY_UNCERTAIN,
unconfirmed Spool inputs/results, unattended inputs, unexpected terminal-count
changes and more than one pending Outbox item block the window. Initial active
states, including PENDING and outstanding jobs, must all be zero. Confirmed old
history can remain; no command drains old work. A leased item's exact origin,
delivery key, message ID and text hash must match its new approved input/reply.

An attempt is persisted before Core begin-send and protocol probes. Immediately
before external send I/O, a separate IO_STARTED journal/fence and deadline check
are persisted. A final per-request guard repeats the current journal/fence/clock
check after asynchronous HTTP network preflight (including private DNS pinning),
immediately before transport dispatch. The guard is removed after that one call.
An in-memory one-call permission then uses the existing text sender;
the stored daemon delivery switch remains false. Timeouts, malformed/lost ACK,
connection errors, process cancellation/death and missing Core result ACK never
restore quota. The fifth acknowledged attempt automatically stops the window.
Every failure stops further work; interrupted state requires review.

The independent journal and Spool must be preserved together with current Core
Outbox/DB and the latest ErasureLedger. Restoring or deleting **both** the journal
and Spool to an older coherent snapshot cannot be detected by software on that
same snapshot. Stronger recovery/anti-rollback assurance needs an independently
retained latest journal and an approved recovery procedure; it is not a PASS here.
Changing window ID/epoch/plan against an existing registered Spool is refused;
creating another independently approved window is an explicit new operator action.

## Invocation and stop procedure

The following templates require already approved private artifacts. They were
not run against a live server. `$BridgeConfig`, `$WindowPlan`, `$Inventory`,
`$Authorization`, `$RawFrame` and `$ItemApproval` are private operator-selected paths.
For local fixtures omit `--authorization`; approved live mode requires that argument.

```powershell
python -m companion_onebot_bridge --config $BridgeConfig window-open --window $WindowPlan --inventory $Inventory
python -m companion_onebot_bridge --config $BridgeConfig window-ingest --window $WindowPlan --inventory $Inventory --event $RawFrame --approval $ItemApproval
python -m companion_onebot_bridge --config $BridgeConfig window-send-once --window $WindowPlan --inventory $Inventory --approval $ItemApproval
python -m companion_onebot_bridge --config $BridgeConfig window-status
python -m companion_onebot_bridge --config $BridgeConfig window-stop
```

For an active foreground once-command, first use its own terminal Ctrl+C and wait
for process exit; if required, stop only that exact approved process. Then run
`window-status` / `window-stop`. A concurrent stop command cannot acquire the same
OS lock; it never silently creates a replacement state. SIGKILL/process death
releases the lock but leaves RESERVED/IO_STARTED counted and blocks the next send.
Keep Core/spool/journal evidence for review. Do not reset epoch, remove queues,
change uncertain items to PENDING, rerun an old window or start the daemon on this
controlled Spool. These commands do not stop SnowLuma, 1Panel/OpenResty/redroid,
delete volumes, log out, or revoke messages already accepted by the platform.

CLI output contains only reason codes, hashes, fencing and anonymous counts.
`ACK` means protocol/Core acknowledgement, **not** a phone receipt. The visible reply
upper bound counts all reservations, including uncertain/no-I/O reservations.
`phone_receipts`, `e2e_p50_ms` and `e2e_p95_ms` remain null. Actual paired client
timestamps/errors and naturalness scores require future independent phone evidence.

## Synthetic verification

`tests/test_onebot_window.py` uses the actual Core HTTP v1/Outbox state machine and
FakeOneBot/MockTransport. It covers five attempts across process-state reopen,
duplicate/conflicting IDs, exact input/output approvals, wrong owner/bot, media,
full/pending/unconfirmed queues, lost protocol/Core ACK, deadline before lease and
immediately before I/O, in-flight completion after deadline, independent-journal
rollback detection, two real processes contending for the OS lock, and a subprocess
calling `os._exit()` after durable reservation but before external send I/O.
Private diagnostic red/green and full-check logs are retained outside Git.
Only same-head executed checks/CI may be reported as current PASS by the final report.
