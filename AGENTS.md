# Working in this repository

- Optimize for long-term stability and extensibility rather than preserving legacy API,
  configuration, storage, or code shapes. Replace superseded structures with mature mechanisms
  and update callers, migrations, and documentation together; avoid permanent dual stacks.
  Preserve security, evidence, accounting, and delivery semantics. Discuss genuinely new
  project-specific mechanisms first as required below.
- Inspect relevant DSH implementations and primary sources when introducing a dependency,
  external integration, or shared mechanism. Reuse recorded research for routine fixes and
  unchanged versions; repeat it only when the version, assumptions, or evidence changes.
  Record new sources in the PR or existing documentation rather than a separate plan per change.
- Prefer established implementations and existing project mechanisms. If a feature still needs
  a newly designed mechanism or protocol, present the gap, reason, alternatives, and proposed
  scope to the user and discuss it before implementing that part. Calling new design an adapter
  does not remove this requirement; ordinary research and confirmed reuse may continue.
- Generate all local source, test, cache, and temporary files inside this workspace.
- Keep AstrBot transport independent from the Core package; HTTP v1 is their boundary.
- Keep model adapters free of storage, permission, and message-delivery responsibilities.
- M0/M1 must not execute DSH, Android, Windows MCP, QZone, or public comments.
- Preserve owner/private authentication, idempotency, delivery uncertainty, evidence provenance,
  budget reservations, and erasure replay when simplifying code.
- Run relevant tests after changes; use `.local/test-temp` for pytest temporary data.
- Clean temporary files after each verified change round. Preserve source, docs, tests,
  lockfiles, intended deliverables, and user data. Never use broad `git clean`.
- PowerShell verification: `./scripts/check.ps1`.
- During implementation use `./scripts/check.ps1 -TestFiles <related tests>`; for documentation
  only use `./scripts/check.ps1 -Docs`. Freeze the reviewed candidate before full PR CI.
- Reuse successful verification for the same candidate and environment. Repeat or expand checks
  for relevant changes, failures, or unresolved risks; the CI tree-reuse policy remains authoritative.
- For changes across API/config/worker boundaries, include a synthetic check through the real
  entry point and independent worker before live acceptance. Mock external provider transport.
- When work proceeds in parallel, agree on file ownership and one integration owner for shared
  contracts. Keep one final verification receipt; link its CI summary instead of copying identities.
- Deliver independently reviewable vertical slices, with at most two active PRs. Preserve deferred
  work on branches and reopen its PR when dependencies and a review slot are ready. Keep unrelated
  product repairs, test infrastructure, and architecture changes out of the same delivery slice.
