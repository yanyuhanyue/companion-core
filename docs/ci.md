# CI validation profiles

Every run reports a `preflight` result and a final `ci` result. No whole-workflow
path filter hides a required result. Configure branch protection to require `ci`;
this change does not modify repository protection settings.

| Change / event | Validation |
|---|---|
| Default branch, release tag, merge queue, manual run | Full validation |
| PR containing code or configuration | Full validation against GitHub's merge preview |
| Ordinary Python changes on a development branch | Fast contracts and repository lint |
| Only README.md or Markdown under docs/ | CI policy and existing readiness/evidence contracts, plus lint |
| Dependency locks, protocols, schema, privacy/budget/transport policy, build/deployment or CI configuration | Full validation even on a development push |
| Unknown files, unavailable comparison, malformed event, empty diff | Full validation |

Full validation retains the complete Ubuntu and Windows pytest suites, replay,
smoke, package checks, all container isolation tests, the Windows browser reader,
and the pinned upstream native-plugin checks. A small preflight runs first so
identity, delivery uncertainty, spool idempotency and isolated Reader imports fail
before expensive jobs start. New worker-composition tests are also included once
their separate feature change is present.

The final `ci` job rejects failed, cancelled, missing or unexpectedly skipped
required jobs. A fast/docs run requires every full-only job to be skipped; it
cannot hide a failed attempted job. PR checks retain GitHub's default merge-preview
checkout. Development push checks are not a substitute for PR/main integration.

New pushes cancel older in-progress runs for the same development branch. New PR
updates cancel older runs for that PR. Default-branch, tag and merge-queue runs do
not cancel in-progress integration runs. Event types use separate concurrency
groups so a branch push cannot cancel its PR merge-preview validation.

The selector uses the complete local Git diff with renames disabled, including
deletions and both names of a rename. New branches compare with the default-branch
merge base. Missing history and unsupported paths select full validation. It does
not depend on a truncated API file list or use filenames as shell commands.

For local policy changes, use this PowerShell command:

```powershell
./scripts/check.ps1 -TestFiles @('tests/test_ci_policy.py', 'tests/test_source_reader_image.py')
```

`python scripts/ci_policy.py test --profile fast`
runs the CI preflight selection using the active environment. Run the full
workflow manually when integration validation is needed without a PR.

Full pytest runs report their 15 slowest cases to guide later sharding. Dependency
and browser-cache changes are deferred until measured benefits justify them;
the existing embedding cache remains enabled. No runtime feature, spending or
delivery permission is changed by CI selection.
