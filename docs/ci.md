# CI validation profiles

Every selected run reports a `preflight` result and a final `ci` result. Configure
branch protection to require `ci`; repository protection settings are managed
separately. Integration evidence must identify the exact PR workflow or main
commit and all of its required jobs.

| Change / event | Validation |
|---|---|
| Default branch | Full validation, or exact-tree reuse of successful merged-PR CI |
| Release tag, merge queue, manual run | Full validation |
| PR containing code or configuration | Full or conservatively path-selected validation against GitHub's merge preview |
| Development branch push | No workflow; open a PR or manually dispatch full validation |
| Only README.md, Markdown under docs/, or explicitly registered evidence JSON | Privacy, CI policy and registered evidence checks |
| Dependency locks, protocols, schema, privacy/budget/transport policy, build/deployment or CI configuration in a PR | Full validation |
| Unknown files, unavailable comparison, malformed event, empty diff | Full validation |

Full validation retains the complete Ubuntu and Windows pytest suites, replay,
smoke, package checks, all container isolation tests, the Windows browser reader,
and the pinned upstream native-plugin checks. Preflight only selects the profile
and checks existing verification receipts; it does not install dependencies or
repeat product tests before the parallel jobs. Documentation validation owns a
fresh `ci-docs-*` directory under `.local/test-temp`, removed on success or failure.
Unrelated retained diagnostics are preserved.

## Windows path selection

Only pull requests can use the `selected` profile. Ubuntu's complete four-file-shard suite,
container checks and native-plugin checks remain required. Windows is selected independently
for the product suite and the browser-reader job:

| Complete non-document diff | Windows product suite/package checks | Windows browser |
|---|---|---|
| Only the three existing Console static assets in `STATIC_UI` | Omitted | Required |
| Only existing backend files/tests explicitly listed in `NON_BROWSER` | Required | Omitted |
| Mixed surfaces, shared dependencies, packaging/deployment, PowerShell, process/path/lock, voice/bridge, new tests/files or any unknown path | Required | Required |

The exact small allowlists live in `scripts/ci_policy.py`. Generic Python changes are **not**
assumed portable or browser-independent; they keep Windows unless explicitly listed. Even
the listed backend Python changes retain the complete Windows product suite. New filenames,
unknown evidence JSON, missing Git history and malformed events run both surfaces. Existing
backend test files listed alongside their modules can omit only the browser job; new test
files default to both. An allowlisted file gaining browser/platform responsibilities requires
review of this policy in the same change. No source grep, test deletion or timing heuristic
decides coverage.

Main pushes, tags, merge queues and manual runs keep full platform coverage (or the existing
documentation-only profile). Main can reuse only independently verified **full** PR evidence;
a `selected` PR must run full validation after merge. Missing Windows work never counts as
full coverage. The full reuse artifact is version 2 and explicitly records Windows product
and browser coverage in addition to checking all actual job names/results. Older receipts
and selected receipts are rejected.

Preflight emits the native GitHub Actions JSON matrix. Each selected platform still gets all
four shards, plus its replay/smoke/package checks once. The gate validates the browser skip
against the selection and rejects failed, cancelled or unexpected job results. Every final
gate writes `.local/ci/validation.json` with selection reasons, the actual scheduled matrix,
expected and observed job results, checkout identity and reuse reference. The always-uploaded
`ci-validation-*` artifact preserves failures as well as successes. It is a scoped report,
not reusable full-tree proof; only a successful `full` run emits `ci-full-*`.
The gate also reads this run attempt's native Actions job timestamps with read-only permission
and adds per-job elapsed seconds to the report/summary. Unavailable or incomplete timing data
is explicitly marked unavailable; the running gate has no invented completion time. These
measurements do not replace JUnit timings or change which jobs must pass.

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

Use `./scripts/check.ps1 -Docs` for local documentation validation. For code,
pass related tests to `check.ps1` during implementation, then freeze the reviewed
candidate before opening/updating its PR for full CI. Run the workflow manually
only when a full integration run is needed without a PR. The final job's
Validation receipt is the one handoff record; link it instead of replaying the
same candidate's checks or duplicating its commit/tree identifiers.

## File shards and timing maintenance

The ordinary suite runs as four disjoint file shards on each selected platform.
Full runs include both Ubuntu and Windows. Every collected file is assigned, including new files, and a file's
parametrized cases and module fixtures stay together. Replay, smoke, build and
package checks run once per platform, on the existing designated shard. A failed
shard fails the final gate; full-tree reuse requires all eight test jobs as well as the
remaining full-validation jobs. An old two-shard receipt is insufficient.

`scripts/ci_weights.json` records file durations from a successful full run.
Weights use the slower platform's per-file JUnit total, including setup and
teardown. The existing longest-first/lightest-shard algorithm balances those
weights; unmeasured files retain a two-second estimate. No live cache, network
lookup or mutable timing service changes collection. To refresh after substantial
suite growth, download the successful run's complete `pytest-*` artifacts into
an owned `.local` directory, then run:

```powershell
$reports = Get-ChildItem .local/ci-timings -Recurse -Filter pytest.xml | Select-Object -ExpandProperty FullName
python -B scripts/ci_shards.py --source-run <successful-run-url> @reports
./scripts/check.ps1 -TestFiles tests/test_ci_shards.py
```

Review the resulting weights with their source run, then include them in the next
frozen candidate. Do not refresh weights after every CI run or trigger a second
full run just to update timings. Compare first-job-to-gate wall time separately
from summed job/case time and queue delays. Four shards add runner startup and
session-fixture overhead; if runner concurrency or costs dominate, reverting the
matrix, shard count and reuse job set together restores the previous two shards.

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


## October 24 measurement and reused mechanisms

The source [full CI run](https://github.com/yanyuhanyue/companion-core/actions/runs/40062163855)
took 672 seconds from its first job starting to the final gate ending. Windows
shard 0 took 658 seconds (621.899 seconds reported by pytest); Windows shard 1
finished in 513 seconds. The previous weights described only seven files.
Repartitioning the same JUnit case totals with the updated 270-file weights gives
these estimates, excluding collection, session setup, packaging and runner queues:

| Assignment | Ubuntu maximum case-time sum | Windows maximum case-time sum |
|---|---:|---:|
| Previous two shards and weights | 592.12 s | 609.90 s |
| Updated weights, two shards | 578.57 s | 537.36 s |
| Updated weights, four shards | 291.95 s | 271.41 s |

These are historical-load simulations, not measured candidate CI wall times.
Candidate measurements belong in its PR and final CI receipt. Baseline and
candidate use the same Python, dependency lock, hosted OS labels, full test
coverage, cache configuration, native/browser/container and packaging checks;
hosted runner variability and doubled session initialization remain limitations.

The implementation reuses the existing file partitioner and native
[GitHub Actions matrix](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations),
with standard [pytest JUnit total durations](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files).
The DSH plugin's [selfcheck](https://github.com/c1overzz/dsh-bili-scroll/blob/main/selfcheck.mjs)
was inspected: it exercises real host/client entry functions with synthetic host
services. That supports retaining our existing native-plugin and entrypoint
checks; it supplies no Python sharding facility to replace. There is no new test
executor, validation service or business protocol.
