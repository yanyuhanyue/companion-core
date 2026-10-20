# Source grant security review

Scope: the independent G1 change relative to reviewed code
`940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`, including new untracked implementation,
migration, schema and tests. This is an implementation self-review with executable
regressions; it is not a formal third-party approval or an account activation record.

The implemented boundary keeps credentials in Reader storage, makes owner control the
only authority for grant registration/withdrawal, and binds accepted source events to an
immutable revision. No unresolved reproducible P0/P1/P2 defect was identified in this
bounded review. Real deployment and historical review remain separate gates.

| Surface | Evidence and conclusion |
|---|---|
| Owner/adapter separation | `api.py` source-grant lifecycle routes use existing `control_auth`; Reader and Bridge tokens receive 401. The lease/result routes retain adapter-specific auth. |
| Request/property validation | `SourceGrantRegistration`/`SourceGrantSnapshot` reject extra fields, including cookies, and constrain IDs/epoch/fingerprint/aware expiry. SQL uses bound values. Invalid bodies retain the existing generic contract error response. |
| Credential boundary | Core tables and response SELECTs contain only allowlisted metadata. Reader keeps cookies/account identifiers and the whole-file integrity digest locally. The transported fingerprint is separately issued non-credential metadata. |
| Cross-adapter/revision substitution | `SourceGrantService.require` checks registry adapter/source, highest revision, expiry, revocation and tombstone. Captured request revision and result must match. |
| Before public I/O | Account workers require metadata before contacting Core and reject a dropped/replaced Core lease binding before the source GET. Existing URL/GET/cookie-to-CDN restrictions remain. |
| Result race | Reader revalidates local authorization after reading. Core revalidates authorization plus lease/privacy epoch in the same serialized write transaction before event attribution and acceptance. Late revoked/rotated/expired output is discarded. |
| Read revocation vs erasure | Natural expiry and future-read revocation retain existing observations and privacy epoch. Explicit `forget` uses fsynced ErasureLedger records, root attribution and existing dependency cleanup. No account inference or automatic history deletion occurs. |
| Restore before grant/event creation | The independent ledger carries the grant ID and root IDs. Replay records a grant tombstone, resolves roots in the restored snapshot and cannot be bypassed by a prior COMPLETE projection. New registration under a forgotten ID fails. |
| Derived projections | Regression fixtures cover transitive observation dependencies, memory evidence, diary evidence and social candidates. Existing erasure semantics retain SENDING uncertainty and conservative clearing of shared projections. |
| Restore of read-only revocation | Supported backup restoration revokes restored read authority even when an older snapshot predates a DB-only read revocation; authorized history is retained. |
| Unknown traffic and delivery | Source-grant cancellation does not refund started read reservations or retry I/O. No QQ transport, model adapter, OneBot retry or delivery reconciliation behavior is added. |
| Historical migration | Migration inventories account/opaque old evidence without modifying bodies or inventing mappings. The owner-only paged inventory excludes content. An unresolved inventory keeps the release gate closed. |
| Rollback | Populated grant registries refuse downgrade; current independent ledger must be preserved. Old binaries reject the new ledger record type. Use a compatible isolated recovery environment, never an older ledger. |

The legacy review procedure in [source-grants.md](source-grants.md) is a material limit:
the migration does not automatically quarantine all prior derived projections. An operator
must keep Reader and dependent processing disabled while reviewing an isolated historical
copy. Unknown legacy ownership is not evidence for a new authorization and cannot be
resolved by guessed URLs, text matching or blanket deletion. A nonempty review inventory
cannot be reported as G1 complete.

The new code has not been deployed to the QQ host. The reviewed runtime remains the fixed
Demo candidate with Reader/M1/autonomous processing disabled, and no third-party account
traffic or paid model calls were made during these regressions. Final local/package/CI
results and commit provenance are reported in the G1 draft PR and validation manifest.
