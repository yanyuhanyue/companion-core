# P12-C: feedback, interest and reflection

Baseline: `c6493d481676e1e6684fb49564c2557224950d20`. This package is offline
development and synthetic verification. It does not approve a live window or
enable Affect, exploration, reflection, model calls or outbound delivery.

## Reused behavior and repaired gaps

| Scope | Existing behavior retained | P12 change |
| --- | --- | --- |
| C1 | Owner control authentication; exact private owner text; `SHADOW` preview; event ID/reason/TTL; native source erasure; neutral state while disabled | Recognize the explicit standalone phrases 更简短, 更详细, 目前不想聊, 欢迎继续. Quoted or reported phrases still do not match. |
| C2 | `owner.preference` and `self.interest` remain separate; only completed self goals with retained `FETCHED`/`WATCHED` outcomes count; both content hash and canonical target must be independent | Evidence now includes its source and `SIMULATED_` coverage label. Retained demo outcomes are excluded when mode changes to live. |
| C3 | Native ReflectionService produces cited hypotheses, with owner accept-as-hypothesis/reject/need-more-evidence decisions | Read and decision paths now apply native transitive source expiry. A recent observation cannot retain a reflection after an older upstream source expires. Expiry uses the existing ErasureLedger and survives old-backup restoration. |

`STABLE` remains a statistical projection of retained evidence. It is not a
permanent personality, a real-world elapsed-day claim, or execution authority.
Simulation clock advances verify date counting only. No PAD model, extra database,
schedule, wallet, permission or personality framework is introduced.

## Verification and evidence levels

`tests/test_p12_growth_shadow.py` checks the four direct feedback phrases and TTL,
quoted feedback rejection, duplicate content at different URLs, demo-to-live
isolation, transitive reflection expiry before either reading or deciding, latest
Ledger restoration, control authentication, veto/revision, and unchanged facts,
traits, permissions, goals, outbox and usage after decisions. Existing Affect,
growth, Goals and Consolidation suites cover erasure, repeat URLs, local-calendar
day counts, source correction, reservation uncertainty and no hidden calls.

| Evidence | Status |
| --- | --- |
| Implementation and synthetic regression | See this PR's exact HEAD/check results |
| New authenticated QQ feedback sample | `NOT_RUN` |
| Real independent reading sessions and natural days | `NOT_RUN` |
| 1–5 human quality scores | `null` |
| Model/QQ attempts caused by this package | 0 / 0 |
| Deployed image, new live config and billing receipts | `null` |
| Automatic expression change / exploration / sharing | `DISABLED` |

## Concrete private-use preparation

Start with a separate read-only observation scope, after the current window is
stopped. Bind the candidate code/image/config, exact owner and conversation,
permitted event IDs and a short expiry; retain raw text only in the private receipt.
Owner control preview is `GET /internal/v2/affect/{event_id}/preview`. Record the
reason, TTL and whether the owner meant that feedback. The preview adds no model
or send attempt and must leave the disabled snapshot neutral. A later expression
trial requires its own approval; feedback is not that approval.

For C2, record independently authorized read results and their source IDs, hashes,
canonical targets, completion states and actual local dates. Use
`GET /internal/v2/interests`; repeated opens and model diary claims add no evidence.
Human reports distinguish useful continuity from incorrect inferred preference.
The 3/7/14/30-day fields remain empty until those real dates occur.

For C3, separately approve the closed day/source set and reflection model budget.
Read `GET /internal/v2/reflections`, then use the existing owner-only decision
endpoint for `REJECT` or `NEED_MORE_EVIDENCE`. Even `ACCEPT_AS_HYPOTHESIS` changes
only the suggestion state. Verify source citations and no facts/traits/permission
mutation. Erasure uses the native source-forgetting scope and current Ledger;
backup rehearsal is isolated and never restores an old live allowance.

Stop on unapproved source, private-scope drift, unavailable provenance, UNKNOWN
usage, stale privacy epoch or failed erasure. Keep all C flags disabled during
rollback; preserve the latest independent logs. Real samples and quality remain
unverified until their separate scopes are executed and reviewed.
