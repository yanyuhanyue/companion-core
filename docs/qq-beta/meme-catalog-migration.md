# Core catalog staging migration

B1a adds an offline, staged catalog. Bridge remains the running catalog owner until
the separately reviewed B1b cutover. Importing never creates a model job, admission,
usage, Outbox item, execution permission or replacement transport receipt.

## Records and identity

Migration `0047_meme_assets` follows `0046_capability_delegation`.

- `meme_assets` has `asset_id` (primary key), `custody_sha256`, `asset_sha256`,
  `source_sha256`, `record_sha256`, `state`, nullable `mime`, `data` and
  `metadata_json`. The unique key is `(custody_sha256, source_sha256, asset_sha256)`.
  `asset_id` hashes those three values. Custody hashes the original spool identity
  `[owner, bot, origin, protocol_epoch]` and capture public key; it excludes mutable
  journal heads. Equal bytes from different custody or source/license remain distinct.
- `state` is `STAGED`, `QUARANTINED`, `REMOVED` or `ERASED`. None is executable in
  B1a. Unattributed records retain hashes only, with no bytes, metadata or captions.
  `REMOVED` and `ERASED` never return to `STAGED`. `record_sha256` preserves the
  original immutable bytes/source/metadata digest after erasure; the active flag
  is separate so a later removal can only narrow availability. Changing the same
  key's metadata or bytes is refused.
- `meme_asset_sources` relates an asset to existing `events` and original
  `meme_captions` jobs. It records every source of the caption reuse ancestry.
  It has no nullable/invented event or job and creates neither. An unattributed
  asset has no source rows. Sources survive as minimal erasure attribution.

Core verifies the original owner capture against its original authority revision,
event payload, existing job/source relation and discovery attempt. The manifest's
identity, checksum and claimed STOP are transfer evidence only. They cannot grant
permission. Content must match Core's retained original asset/source proof; missing
provenance is quarantined rather than copied. Public static assets without existing
Core provenance require a later onboarding slice.

## Transfer format

One private JSON file has `schema_version=1`, `custody`, `assets`, and `sha256`.
`custody` contains `identity`, `capture_public_key`, original `authority` and
`journal_head`. Each asset has `asset_sha256`, `source_sha256`, `active`,
`metadata`, `mime`, `data_base64` and `sha256`. Item and envelope hashes use the
existing shared canonical JSON/digest, omitting only their own `sha256` field.
Removed items have null bytes and MIME. There are no filesystem member paths,
transport tables, capture bodies, model credentials, captions or usage copies.

Export uses the original instance lock and existing OwnerRuntime read-only custody
verification, requires STOPPED, and opens SQLite with `mode=ro`/`query_only`.
It does not construct a writable Spool or stop/recover a running instance. Legacy
custody lacking the native proof is refused. Original limits apply: 4096 records,
64 MiB aggregate original bytes, 4 MiB per PNG/JPEG/GIF and the shared decoded-media
limits. The complete JSON file is bounded to 100 MiB; metadata stays individually
bounded. Both item and envelope hashes are checked before any import.

Core dry-run and reconcile operate on an in-memory SQLite backup of the target.
They replay the supplied existing independent ErasureLedger there, leaving actual
source/target business data unchanged. Import requires the original database's
matching Ledger identity, replays it before insertion and rechecks the Ledger and
privacy epoch before commit. No missing Ledger is initialized. The operator must
supply the latest independently retained Ledger; existing truncation checks remain.
Prepare the target through Core's normal Alembic initialization first; the CLI
refuses a pre-0047 schema rather than mutating it during a dry-run.

The new `meme-assets` derived cleanup handler uses the existing registry signature
to repair older COMPLETE snapshots. It removes bytes, MIME and metadata and retains
only hashes, state and source attribution. Existing restore already migrates before
replaying that registry, so `backup.py` needs no new restore mechanism. Original
model fees, ACK timestamps, attempts and UNKNOWN holds are never modified.
Read revocation/expiry stops future acquisition; explicit historical withdrawal
and existing retention erasure follow their original Ledger semantics.

## Rehearsal and rollback

Export: `python -m companion_onebot_bridge.meme_export --config <private.toml>
--output <new-private.json>`.
Core: `python -m companion.meme_asset_migration --config <private-core.toml>
<dry-run|import|reconcile> <private.json>`.

Commands print only hashes, states and counts; errors use fixed codes, never raw
metadata or configuration. Use synthetic copies first. Import twice must reconcile
without adding financial or delivery work. A changed item under the same key fails
the entire transaction. Pausing or abandoning staging leaves Bridge's original
authority and transport custody in place. Rollback never restores an old catalog
over the latest erasure history, clears UNKNOWN, resends an ACK, or refunds charges.
Production migration and ownership cutover require separate release review.

The design reuses recorded unchanged DSH attachment/vision separation and this
project's original receipt/erasure contracts in [caption semantics](memes-semantic.md)
and [original receipts](../plans/2026-10-25-meme-original-receipts.md).
Coherent rehearsal copies use the existing
[SQLite backup API](https://www.sqlite.org/backup.html) and transactional writes use
[SQLite transactions](https://www.sqlite.org/lang_transaction.html). Read-only file
connections use SQLite's existing [URI mode=ro](https://www.sqlite.org/uri.html).
Path checks use `lstat()` for every component, reject symbolic links, and on Windows
reject every `FILE_ATTRIBUTE_REPARSE_POINT`, including directory junctions. Missing
Windows file attributes or filesystem errors refuse the operation. This follows the
existing raw-stat approach in voice-node state custody and uses the
[Python 3.11 file attributes](https://docs.python.org/3.11/library/os.html#os.stat_result.st_file_attributes)
and [stat constants](https://docs.python.org/3.11/library/stat.html#stat.FILE_ATTRIBUTE_REPARSE_POINT),
preserving the project's Python 3.11 minimum without `Path.is_junction()`.

## Verification status

Local implementation and synthetic checks cover the independent export/dry-run/
import/reconcile processes, exact original-work preservation, identity/source
isolation, hash conflicts, process death, pending erasure, old COMPLETE repair and
pre-0047 backup restoration followed by reimport. The existing restore migrates
before replay and needs no change. Tests are in
[migration checks](../../tests/test_meme_asset_migration.py) and
[erasure coordination](../../tests/test_erasure_coordination.py).
The B0 baseline's 84 targeted tests remain evidence for unchanged portions.
B1a's [final main validation](https://github.com/yanyuhanyue/companion-core/actions/runs/40224533506/job/121263068243)
reuses the verified identical-tree full PR checks. Ownership cutover, SHADOW,
production migration, real QQ delivery and human acceptance remain separate stages.

## B3a: read-only historical retirement preflight

Run `python -m companion_onebot_bridge.meme_retirement --config <private.toml>`
only against the stopped native Bridge store. The report diagnoses local blockers;
it never authorizes a cutover, clears content, changes a pin, settles fees or changes
the independent Core Ledger. Exit 0 means only that these local checks found no
blocker. Exit 2 means a local blocker. Both report `state=BLOCKED` and
`cutover_authorized=false`: latest independent Core Ledger/live reconciliation,
explicit managed-copy retirement and backup revalidation remain external gates.
No arbitrary JSON report or old hash substitutes for those current checks.

The CLI reuses the export's component-by-component regular-file/reparse refusal,
the original nonempty instance lock and read-only native owner verifier. The
`existing_only` lock option opens that same file without creating, initializing or
chmodding it; default callers retain their original behavior. The original OS lock
is held throughout. Reading can update filesystem access times, which is distinct
from product or custody writes.

SQLite [read-only WAL access](https://www.sqlite.org/wal.html#read_only_databases)
can still create sidecars. This preflight therefore refuses any nonempty `-wal` or
`-journal`, or residual nonempty `-shm` with unproven recovery state, as
`WAL_RECOVERY_REQUIRED`, without checkpointing, repairing, removing
files or copying the database. Only a known clean, stopped baseline is opened with
standard [mode=ro and immutable=1](https://www.sqlite.org/uri.html#uriimmutable)
and `query_only` in one transaction. Immutable skips SQLite's own locking/change
detection; it is limited to this offline tool under the original OS lock. The
database, all known sidecars, owner journal, STOP marker and lock are fingerprinted
before and after, including absence, SHA256, size, modification and change times.
Any observed change rejects the report. This is not a running-reader strategy.

The report contains counts, known schema fingerprints, fixed reasons and necessary
custody hashes. It emits no caption, image bytes, URL, query, private path, credential
or raw authority. Unknown/missing table shapes, unverified source/receipt links,
unsettled caption/discovery usage and unfinished original handoff custody block.
`PREPARED`, `SENDING` and `UNKNOWN` pins block independently even if an owner
resolution exists: that existing resolution does not project the meme pin. No
timeout or elapsed cooldown releases such a pin.

Historical `SENT` pins need their original binding, ACK and trustworthy timestamps.
The original journal does not bind the historical cooldown value to each pin;
current config, a default, maximum, image or ACK time alone does not supply that
missing policy. These rows report `ORIGINAL_COOLDOWN_UNVERIFIED`; `wait_until` stays
null. Neither an old timestamp nor a shorter new config grants release.

For the next cleanup slice, the following content and responsibility columns must
be reviewed separately. This inventory grants no cleanup permission:

| Existing Bridge table | Content to consider retiring | Responsibility to preserve |
| --- | --- | --- |
| `meme_assets` | `data`, `metadata` | physical `sha`; source verification |
| `meme_offers` | `intent`, `category`, `ids` | original offer key |
| `meme_runtime_offers` | `offer_json` | original event/window hashes |
| `meme_runtime_semantic_offers` | `query_binding`, `offer_json` | event/window/revision/state |
| `meme_runtime_sources` | `claim_json` | original SHA/attempt and settlement relation |
| `meme_caption_cache` | `identity_json`, `result_json` | original `usage_json` and cache/asset keys |
| `meme_caption_bindings` | `context_json`, `outcome_json` | original request/source/job/usage/state |
| `meme_semantic_vectors` | `vector_json` | asset/source/cache/caption/model identity |
| `meme_semantic_offers` | `proof_json` | original key/query/profile hashes |
| `meme_pins`, `meme_runtime_bindings` | no cleanup in B3a | original immutable delivery identity/state/time |
| `meme_runtime_results` | no cleanup in B3a | original retry body, state and incurred usage |
| owner journal, attempts, results and resolutions | no cleanup in B3a | original ACK/UNKNOWN/NOT_SENT and fee evidence |

`meme_catalog.remove()` clears original bytes and vectors but leaves other content
listed above. It does not establish full retirement. The B1a manifest itself also
contains private bytes/metadata. Enumerate each managed artifact explicitly with
`--managed-copy <file>` (maximum 32 files, 100 MiB each); only its path hash, content
hash and size are reported. The CLI neither discovers nor traverses an operator's
`meme_directory`. Old Bridge backups/binaries have no automatic anti-resurrection
guarantee: restoration must repeat current STOP/authority validation, latest Core
Ledger/reconcile and the reviewed retirement procedure. B3b cleanup, compaction,
pin receipt projection and production acceptance are separate work.
