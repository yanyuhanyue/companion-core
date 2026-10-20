# CI validation profiles

Every selected run reports a `preflight` result and a final `ci` result. Configure
branch protection to require `ci`; repository protection settings are managed
separately. Integration evidence must identify the exact PR workflow or main
commit and all of its required jobs.

| Change / event | Validation |
|---|---|
| Default branch, release tag, merge queue, manual run | Full validation |
| PR containing code or configuration | Full validation against GitHub's merge preview |
| Development branch push | No workflow; open a PR or manually dispatch full validation |
| PR changing only README.md or Markdown under docs/ | CI policy and existing readiness/evidence contracts, plus lint |
| Dependency locks, protocols, schema, privacy/budget/transport policy, build/deployment or CI configuration in a PR | Full validation |
| Unknown files, unavailable comparison, malformed event, empty diff | Full validation |

Full validation retains the complete Ubuntu and Windows pytest suites, replay,
smoke, package checks, all container isolation tests, the Windows browser reader,
and the pinned upstream native-plugin checks. A small preflight runs first so
identity, delivery uncertainty, spool idempotency and isolated Reader imports fail
before expensive jobs start. New worker-composition tests are also included once
their separate feature change is present.

Each preflight invocation initializes its scratch parent and owns one fresh
`ci-preflight-*` directory under `.local/test-temp`. Success and failure both
remove only that invocation's temporary data; unrelated retained diagnostics are
preserved. A fresh runner does not depend on directories left by an earlier job.

The final `ci` job rejects failed, cancelled, missing or unexpectedly skipped
required jobs. A docs run requires every full-only job to be skipped; it cannot
hide a failed attempted job. PR checks retain GitHub's default merge-preview
checkout, including for forks, with read-only contents permission. No
`pull_request_target` event or additional token permissions are used.

The push filter selects `main` and tags. A development commit is validated once
through its PR, rather than starting duplicate push and PR full suites. A branch
without a PR has no remote check. New PR updates cancel older runs for that PR.
Main, tag and merge-queue runs do not cancel in-progress integration runs. Event
types use separate concurrency groups so a main push cannot cancel a PR merge
preview. If the default branch is renamed, update the explicit push branch filter.

The selector uses the complete local Git diff with renames disabled, including
deletions and both names of a rename. New branches compare with the default-branch
merge base. Missing history and unsupported paths select full validation. It does
not depend on a truncated API file list or use filenames as shell commands.

For local policy changes, use this PowerShell command:

```powershell
./scripts/check.ps1 -TestFiles @('tests/test_ci_policy.py', 'tests/test_source_reader_image.py')
```

`python scripts/ci_policy.py test --profile full`
runs the CI preflight selection using the active environment. Run the full
workflow manually when integration validation is needed without a PR.

Full pytest runs report their 15 slowest cases to guide later work. The generic
`core` fixture migrates an empty SQLite database once per pytest session, before
any Core, Runtime or ErasureLedger initialization. SQLite backup then creates
each test's separate database. Runtime pragmas, identity initialization, ledger
creation, real embedding inference and all business assertions still execute.
Migration, historical upgrade and recovery tests continue calling real Alembic.

Measured on the same Windows worktree, interpreter and offline model-cache
configuration with cProfile enabled for both runs:

| `tests/test_m0.py`, 20 passing tests | Baseline `12a3f5b` | Empty-schema fixture |
|---|---:|---:|
| Pytest elapsed | 32.38 s | 5.12 s |
| Real `migrate()` calls | 23 | 4 |
| Cumulative migration time | 27.969 s | 1.330 s |

This is an 84.2% reduction for this measured scope. It is not a full-suite speedup
claim. The remaining four migrations are the session template and tests that
explicitly exercise repeat migration or recovery. The encoder already had a
process-local cache; no embedding stub or global model replacement was added.

To repeat the comparison, run the same command at the baseline and candidate
using separate owned directories under `.local/test-temp` and the same cached
model. Keep `PYTHONPATH` bound to that checkout and use `HF_HUB_OFFLINE=1` when
reusing an existing model cache:

```powershell
.venv/Scripts/python.exe -m cProfile -o .local/performance/m0.pstats -m pytest -q tests/test_m0.py --durations=15 --basetemp=.local/test-temp/m0 -o cache_dir=.local/test-temp/m0-cache
```

The existing embedding cache remains enabled. No runtime feature, spending or
delivery permission is changed by test setup or CI selection.
