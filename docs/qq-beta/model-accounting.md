# Model billing evidence

The operator commands `preview-model-accounting` and `record-model-accounting`
record an owner-approved estimate or an independently checked provider statement
for a model attempt whose local execution already has verified closure. They work
across the original model providers and scenarios, rather than relying on the old
video-rate defect. The owner approved this scope on 2026-10-29, including use of
DeepSeek rates as a temporary reference for two historical transport failures.

## Meaning and boundaries

`ESTIMATED` uses the original admission's reserved input/output token bounds and
the explicitly supplied reference prices, with no assumed cache discount.
These counts are not observed usage. A reference provider may differ from the
original provider; that difference is retained in the receipt.

`PROVIDER_CONFIRMED` means an operator checked a provider statement and explicitly
bound it to this exact original attempt. The statement's provider and model must
match the original route. The local tool verifies the file digest and native
binding, not the provider's authenticity or an inferred timestamp-only match.
The operator must retain the original private statement and verify that mapping
before choosing this basis. Unsupported currencies are rejected rather than
converted without a rate receipt.

Both operations preserve the model outcome `UNKNOWN`, original token fields,
`is_estimate`, job/tool/outbox state and original settlement record. A known bill
does not recover a missing response or authorize retry. `billing_status` describes
the separate accounting evidence. The existing carry/closure contract alone
determines whether another independently authorized input can proceed.

The booked liability is the maximum of the prior booked amount and the new
estimate or statement amount. Neither operation refunds the retained amount or
clears STOP, restoration barriers, unrelated budget blocks or source authority.
A later statement may follow an estimate; an estimate cannot replace a confirmed
statement. Each step retains its predecessor, original admission/settlement,
closure proof, owner authorization digest and private evidence digest.

A confirmed statement is checked against the original native attempt, window,
request and period limits. A violation is retained in `budget_limit_codes` and
sets a persistent budget block. Further confirmed statements can still increase
the recorded liability, but cannot clear that block or enable another call.

This version uses the existing native `local_active_attempts_v2` custody. It
requires an original dispatched UNKNOWN with a validated closure record and a
matching owner identity. It retains the carry contract's rejection of cumulative
v1; it does not add cumulative v2 or claim to reconcile unfinished transports,
unbound legacy rows, ASR charges or tool executions.

## Operator workflow

Use the deployed Core environment and its original configured DB, independent
budget journal and cloud cost snapshots. No credentials or raw billing statements
are printed, sent to a model, or stored in the journal. The CLI has no provider or
message-delivery client. Keep input and evidence files inside the operator's
private workspace and preserve them with the verification receipt.

A proposal has `version: 1`, a unique `operation_id`, `request_id`,
`authorization_sha256`, `evidence_sha256`, and one of these `basis` objects:

```json
{
  "kind": "owner_estimate",
  "reference_provider": "deepseek",
  "reference_model": "deepseek-flash",
  "currency": "USD",
  "input_rate": 300000,
  "output_rate": 1200000
}
```

Rates are integer micro-USD per million tokens. Those example rates are peak
reference prices, not a standing pricing configuration. Select and retain the
appropriate observed price version and time band before preparing a real entry.

```json
{
  "kind": "provider_statement",
  "provider": "original-provider",
  "model": "original-model",
  "currency": "USD",
  "actual_micro_usd": 123,
  "provider_record_sha256": "<SHA256 of the matched original billing record>"
}
```

Run `python -m companion preview-model-accounting --request proposal.json
--request-sha256 <file SHA256> --evidence statement-or-pricing-evidence`.
The preview is read-only. Save its `prepared_request` as a separate JSON file,
review the displayed basis and retained amount, then run
`python -m companion record-model-accounting --request prepared.json
--request-sha256 <prepared-file SHA256> --evidence statement-or-pricing-evidence`.

The prepared request binds the journal head, original admission, native settlement,
closure, preceding accounting receipt, prior amount and budget-limit findings. Changed state requires a
new preview. Repeating the identical completed operation returns its original
receipt without another charge or journal append. Reusing an operation ID with
different material or reusing evidence in another operation is rejected.

## Durability and deployment

The implementation reuses `Store.transaction`, the original hash-chained Budget
Journal, `runtime_meta` projection witnesses and native cloud-cost synchronization.
An accounting record is an additional lower-bound receipt; it does not replace the
original execution settlement used by historical carry proofs. The journal is
fsynced before projecting the maximum amount and receipt witness into SQLite.
If a fully validated provider statement cannot finish its journal append or
durability confirmation, the command returns `PENDING_JOURNAL`. SQLite retains
the higher known liability, the exact prepared request and a pending witness;
native cloud costs are synchronized. Ordinary custody checks reject this pending
state, so new model admissions cannot spend through the unrecorded liability.
An estimate does not create a confirmed-liability hold on append failure.

When no bytes were appended, or a complete valid record was written but fsync or
DB projection was interrupted, retry the identical prepared request and evidence
to finish durability and projection. A partial or corrupt journal still fails
closed: this command never truncates or rewrites the old chain. Preserve the
known-liability hold and original evidence for verified repair before retrying.
Successful recovery removes only its pending witness; explicit restore barriers,
STOP and permanent budget blocks remain effective.
Old journal prefixes, missing projection witnesses, changed admission identity or
a DB amount below a recorded liability are rejected.

Deploy the compatible journal reader to every Core executor before recording the
first accounting entry. Older binaries cannot interpret the new record kind.
Rollback must preserve the latest DB, journal, erasure ledger and private evidence;
use a compatible forward repair instead of an old journal prefix. Source erasure
retains only the original accounting identity and digest evidence, never restores
message bodies, and never regenerates a model response.

## Reuse and verification

The native carry/closure and journal replay implementations provide the execution
proof and crash handling. The distinction between estimates and returned usage
follows the previously inspected [DSH token-meter types at 22ddee3](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/llm/token-meter/src/types.ts)
and [DeepSeek's usage contract](https://api-docs.deepseek.com/quick_start/token_usage/).
The [official price table](https://api-docs.deepseek.com/quick_start/pricing/), checked
on 2026-10-29, is only a reference for estimates; it is not evidence of a particular
request's actual usage or a different provider's invoice.

Regression coverage includes immutable previews, increasing retained liability,
provider confirmation after estimates, zero-price statements without refunds,
idempotence, changed evidence/route/cutoff, unclosed transports, crash recovery,
old journal/projection rollback, STOP, source erasure, subsequent same-policy
activation, cloud cost synchronization, and the real CLI in an independent process.
Confirmed over-limit statements, additional statements after a block, missing
block witnesses and known-liability retention during append/fsync failures are
also covered, including rejection of a corrupt journal during ordinary recovery.
