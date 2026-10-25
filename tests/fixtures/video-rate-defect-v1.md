# Synthetic historical video accounting evidence

`video-rate-defect-v1.json` was generated from commit
`85c98ec85c1b86929fdb5e8525234f64bb6d43bb`, using its existing `failed_video`
fixture in `tests/test_video_accounting.py`. That fixture deliberately injects the
historical video/chat rate-comparison defect at `_persist_accounting`, while running
the real authenticated capture, image intake, model admission and journal machinery.
All provider and QQ transports, identifiers and credentials are synthetic.

The artifact retains the actual schema revision `0033_model_usage_job_index`, original
rows, independent budget journal, erasure ledger and settings snapshot. Secret fields
are masked; the loader supplies the known synthetic model keys required to verify the
original credential hashes. Adapter tokens are fresh local test tokens and cannot
grant execution. Operational database, journal and erasure paths are relocated to the
current pytest directory; original receipt and evidence bytes are retained.

The loader creates that historical schema, restores its rows, and performs normal
forward migrations. It does not downgrade a populated current database, execute a v1
capture on the native runtime, or renew an expired grant. Tests exercise correction,
custody conflicts, crash replay and the offline CLI against these original records.
