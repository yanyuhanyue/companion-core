# P8 evidence index

P8-A repository baseline was read at `2026-10-20T08:46:24.724237+00:00`; current read-only host metadata at
`2026-10-20T08:47:09.778623+00:00`. This is additional metadata, not a new QQ input or phone acceptance.

| Evidence column | Fixed source | What it proves |
|---|---|---|
| Static repository | main `7bd47a659f60b4904b87508a3dbf0a4d6934d87d`; PR heads in [machine gates](p8-gates.json) | Topology, source contracts and exact-head job results |
| Prior P7 live evidence | report `904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf` | Prior five phone-visible Demo replies, not new P8 samples |
| Current host observation | anonymous counters in p8-gates.json; private reviewed metadata manifest | schema0015, observations0, events5, SENT5/attempts5, no grant tables, delivery false, original service metadata unchanged |
| New offline execution | [regressions](p8-offline-regressions.md), local logs and final CI | Stub/context/budget/fault/restore checks, never LIVE_PASS |
| Independent G1 review | [review](p8-g1-independent-review.md), original `907d62c1491e0a3613a7653f2d4eb0275ec4a357` | Separate findings/repair verification; no Reader activation |

P7 immutable sources: [gates](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-gates.json),
[release](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-release-review.md),
[protocol probe](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-onebot-live.json),
[human receipts](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-qq-human-acceptance.md),
[host recovery](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-host-recovery.json).
P8 does not rewrite those records. All current QQ counters agree with the prior 5/5 samples;
strict first-five and strict20 remain0/5 and0/20, actual paired E2E remainsnull.

Baseline CI per head (five jobs per run, all checked individually):

- [PR #18](https://github.com/yanyuhanyue/companion-core/pull/18), `33dff1186899d3a91f4c2023ab0d594024761971`: [push](https://github.com/yanyuhanyue/companion-core/actions/runs/39373376544), [pull_request](https://github.com/yanyuhanyue/companion-core/actions/runs/39373401280).
- [PR #19](https://github.com/yanyuhanyue/companion-core/pull/19), `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`: [push](https://github.com/yanyuhanyue/companion-core/actions/runs/39377694338), [pull_request](https://github.com/yanyuhanyue/companion-core/actions/runs/39377697730).
- [PR #20](https://github.com/yanyuhanyue/companion-core/pull/20), `904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf`: [push](https://github.com/yanyuhanyue/companion-core/actions/runs/39393452614), [pull_request](https://github.com/yanyuhanyue/companion-core/actions/runs/39393462843).
- [PR #21](https://github.com/yanyuhanyue/companion-core/pull/21), `907d62c1491e0a3613a7653f2d4eb0275ec4a357`: [push](https://github.com/yanyuhanyue/companion-core/actions/runs/39393222818), [pull_request](https://github.com/yanyuhanyue/companion-core/actions/runs/39393232459).

`SYNTHETIC` means manufactured inputs; `OFFLINE` means local execution including stub HTTP,
loopback WS, copied synthetic DBs and observed-ID replay. Existing `OFFLINE_FIXTURE` telemetry
maps to OFFLINE synthetic evidence. `ONEBOT_PROTOCOL` covers identity/status or send API ACK;
`LIVE_QQ_HUMAN` requires separately recorded phone observation. Old `LIVE_ONEBOT` component
spans do not become human E2E. No observed platform same-ID redelivery was fabricated.

Same-process `monotonic` intervals can summarize one component. E2E requires paired client
send/phone-visible times with clock provenance and error bound; absent pairs remain null.
Never subtract unrelated process clocks or sum ACK spans to invent phone RTT.

Private validation manifests retain command exit codes, logs, artifact hashes and exact run
IDs. Public outputs omit private identities, topology, credentials, raw event IDs and bodies.
The current host config label is from pinned P7 provenance; this observation only rechecked
allowlisted runtime metadata and delivery state, not every private setting or real provider bill.
Final report/P8 commit SHA is its actual PR head, not a guessed self-reference. Merge results
are recorded by final GitHub PR state and the separate private integration manifest.
