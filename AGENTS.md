# Working in this repository

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
