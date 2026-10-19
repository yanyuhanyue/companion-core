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
