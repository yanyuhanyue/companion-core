# P8-A independent G1 review

Reviewed comparison: `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a...907d62c1491e0a3613a7653f2d4eb0275ec4a357` (PR21).
The checkout at the isolated PR21 worktree resolved to the exact head above and remained unchanged by this review.

Verdict for original head: **BLOCKED — two independently reproduced P1 defects. Runtime G1 remains BLOCKED.**

## Findings at the reviewed head

### P1 — Revalidate Core authority before each account GET

Location: `source_reader/worker.py:51-64`, particularly the transition from the returned lease to `read_result`; related account GET entry points are `source_reader/bili_tools.py:40-53` and `source_reader/heybox_source.py:72-113`.

Trigger: Core issues a valid account lease. Before the Reader receives that response, the owner revokes the grant in Core. The isolated Reader file remains locally valid. `tick` checks only that the response metadata matches its old local snapshot, then performs the credentialed upstream GET. Neither account source revalidates Core's current authority at that boundary.

Consequence: future-read revocation can complete before a new credentialed GET starts, yet that GET still runs. Core correctly rejects the later result, so the reproduction commits no observations; that final guard does not prevent acquisition after revocation. The same missing boundary affects grant rotation and Core expiry when the Reader clock lags, and subsequent account GETs within one Bilibili read.

Reproduction: private synthetic test `.local/p8/test_g1_independent_red.py::test_core_revoke_after_lease_before_public_get_must_prevent_get`. An ASGI transport revokes immediately after producing the lease response; the mock upstream records one GET and the zero-GET assertion fails. No external traffic or real account file is involved.

### P1 — Grant withdrawal misses explicitly superseding corrected memories

Location: `companion/privacy.py:383-394`, reached from the new source-grant erasure path at `companion/privacy.py:362-381`.

Trigger: create a memory supported by grant-attributed evidence, then use the supported owner correction operation. `Memory.decide` creates a successor with `supersedes_memory_id` and a separate `owner.decision` / `memory.corrected` evidence event (`companion/memory.py:315-346`). Forget the source grant.

Consequence: source-grant erasure selects memories only by evidence on its event dependency closure. It forgets the predecessor but leaves the explicitly linked corrected successor `CONFIRMED` with private content and independent correction evidence, available to future memory use. Further descendants and diary/observation projections supported by those correction events also escape this closure. This violates the requested attributable derived erasure contract.

Reproduction: private synthetic test `.local/p8/test_g1_independent_red.py::test_grant_forget_clears_memory_superseding_attributable_memory`. It uses `core.memory.decide(..., "correct", ...)` and observes `{"status":"CONFIRMED","content":"Corrected private source fixture"}` after grant forgetting.

The old COMPLETE shape also matters: the original source-grant record stores only root event IDs and `memory_ids=[]`, while application deletes the predecessor's memory-evidence association. With the grant tombstone and COMPLETE projection present and the root already redacted, `replay` (`companion/privacy.py:340-351`) sees no remaining grant content and skips application even though the corrected successor is active. The independent patch reviewer reproduced this exact legacy shape by temporarily restoring the original root-only closure behavior, forgetting, restoring the new function, and replaying: **one expected failure**, successor still `CONFIRMED`. Its private diagnostic is `the isolated G1 patch worktree/.local/p8/independent-legacy-complete-red.log`. Merely recording closure for new erasures does not repair this old COMPLETE case; any candidate must handle it explicitly or fail closed for review.

## Evidence and checks

Read the raw changed implementation and relevant unchanged account I/O, API authentication, transaction, memory correction, erasure dependency, encrypted restore, and migration code. The review did not rely on the PR's self-review document. Relevant contracts were the owner semantics in `docs/source-grants.md`, its ADR, the observation contract, and repository AGENTS instructions.

Commands below ran with the workspace Python 3.12 virtual environment, using distinct pytest leaves beneath `.local/test-temp/p8-g1-audit` and workspace-local pytest caches.

At original head:

- `python -m pytest tests/test_source_grants.py tests/test_plugin_readonly.py tests/test_source_reader.py tests/test_heybox_reader.py tests/test_exploration.py tests/test_release_migration.py -q`: **173 passed**, one Starlette/httpx deprecation warning, 56.97 seconds.
- `python -m pytest .local/p8/test_g1_independent_red.py -q --tb=short`: **2 failed as expected** at the assertions described above, one identical warning, 4.57 seconds. Preserved diagnostic: `.local/p8/g1-original-red.log`.
- An initial baseline invocation had a missing pytest basetemp parent (102 tests passed; 71 fixture setup errors). After creating the parent, the complete baseline ran once in a new leaf and passed. Those setup errors are not product findings.

Other reviewed guards are present: exact adapter/source/revision binding, grant expiry at lease and result commit, private owner-only lifecycle APIs, immutable revisions, pre-commit lease/privacy checks, local full-file checks before/after GET, no account cookies or whole-file integrity digest in Core metadata, retained valid history on expiry/read revocation, source-grant tombstones, encrypted restore revoking restored authority, and legacy inventory without invented attribution or automatic deletion. The passing original tests exercise those paths, including restoration before registration and after evidence and rebuilding a missing tombstone under a COMPLETE projection. They do not cover the two reproduced gaps.

## Separate repair candidate

The parent explicitly authorized repairs on a separate branch. Candidate workspace: `the isolated G1 patch worktree`; branch `codex/p8-g1-pre-read-revalidation`; base `907d62c1491e0a3613a7653f2d4eb0275ec4a357`. The patch is **pending completion and final verification** at this report snapshot. Its final commit, checks and resolution belong in the later PR/validation manifest; this report does not guess them. No repair was made on PR21's branch.

The repair is intended to add adapter-authenticated revalidation before each account GET and explicit memory correction/supersession erasure. It also needs regressions for legacy COMPLETE recovery and strict validation-response typing. A separate independently reviewed and verified committed patch is required before either finding is considered resolved. This document is frozen as the original-head findings snapshot.

## Limits and runtime status

No real credentials/account files were read; only synthetic private fixtures and mocked upstream/Core HTTP were used. No public account probe, QQ send, paid model call, deployment, push, merge, runtime migration, or enablement occurred.

Core validation and an upstream GET cannot be one atomic distributed operation. Revocation after successful validation can race a permitted/in-flight GET; the candidate documents this limit, blocks the next GET, and retains the final commit rejection. It does not promise recall of traffic already permitted or in flight.

An old record may have lost its grant-to-memory evidence association. An existing `FORGOTTEN` predecessor plus an explicit successor relationship is still evidence of a prior erasure decision and can support careful descendant reconciliation; it must not be represented as newly proved grant attribution. Any unresolved real historical inventory needs explicit owner review and the documented freeze procedure.

Original PR21 is not eligible on its own while the findings remain. A green candidate requires the parent's independent diff review, exact committed-head checks and integration decision. **Runtime G1 remains BLOCKED regardless of synthetic test success.**

## 后继修复与完整组合验证

独立 [G1 后继补丁](https://github.com/yanyuhanyue/companion-core/pull/23) 固定 `6d200378aaeed5a13e2aa76a676cb0c7d8e594c4`，保留原始审查结论，
并分别修复每次账号 GET 前的 Core 授权复核与显式纠正后继的遗忘闭包。
最终定向81项、原两条失败复现和第二位审查者独立7项均通过。
已丢失必要归属的旧 COMPLETE 以明确错误阻止启动/恢复发布；没有推断归属或追加恢复期 Ledger。
真实 Reader/G1 仍未部署、未授权启用。

完整组合验证提交 `fbb5861f336d3cd6da74b13ae67454cfd715aad9`：**771 passed、2 skipped、
1 warning，605.83s**，Chromium 执行、ruff通过。
它包含本P8回归、#21原实现、上述G1补丁和固定P7报告，额外验证组合后的源码，
不把P8单支线测试冒充已验证G1组合。最终报告提交仅追加文档；代码/测试哈希由私密清单核对。
精确最终 P8 与 G1 补丁 HEAD 的 push/PR CI 须实际全部成功，最终 run ID 与 SHA
在对应 PR 和最终 manifest 中关联；本条不预填尚未发生的 CI 结果。
