# Original caption receipt closure

An independently completed caption can leave an UNKNOWN budget receipt. The native
current-authority check then correctly refuses further execution. Previously the
Bridge also applied that check to fetching the already submitted caption's outcome,
so its local binding could never retain the original usage.

## Inspected sources and reused mechanisms

- [DSH QQ vision tool, pinned c8b9158](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/media/vision-tool.ts)
  separates image references and the model service. Its existing attachment/service
  separation is retained; it does not supply this project's accounting authority.
- [Celery AsyncResult](https://docs.celeryq.dev/en/stable/reference/celery.result.html)
  retrieves an existing task's result by its original ID separately from submitting
  work. This distinction is reused without adding Celery or another job store.
- Existing `meme_caption_bindings` retains the original job, asset, source, request,
  context and accepted outcome identity. Existing `OwnerRuntime.verify(active=False)`
  verifies original signed custody after STOP, as in discovery settlement.
- Existing private destination pinning, final `CheckedTransport` checks,
  `seal_protocol_content` and bounded JSON reading bind the actual result request.
  The original `SemanticLibrary.accept` usage validation is shared with receipt
  closure; `receipt_follows` continues to forbid usage regression.

## Behavior

`CaptionHTTP.get` only reads a retained caption job through its fixed result URL and
empty JSON body. The retained outcome must agree with the original job, asset,
source and request hashes; its input capture must still have valid signed custody.
The final HTTP check rejects a changed path, body, credential, cookie or destination.
The returned receipt must preserve the original identity, including model and prompt.

The read does not grant permission to submit a caption, refresh profiles, query,
embed, discover, or commit caption content. Those paths retain current authority and
budget gates. Usage is retained before the post-read content check. A refused runtime
can close one known pending receipt using existing bindings and quarantine the asset
without writing a caption cache or vector. There is no new schema, queue or DTO.

After restart, a refused profile refresh leaves the saved profile and original usage
unchanged and returns DEFERRED. Neither submission's `after=False` nor that flag on any
other call grants access to the result-reading path. Synthetic checks use real Core
HTTP and independent workers; provider and public-source transport remain mocked.
