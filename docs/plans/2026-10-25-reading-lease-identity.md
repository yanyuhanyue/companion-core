# Reading v3 original lease identity

Native consumer migration reproduced two missing checks after a real signed input,
SDK selection and Reader lease: changing the queued URL or changing the original
owner event's `source` did not make `/validate` reject the request.

The repair reuses `reading_attempts.original_url_sha256` at each validation and
the retained `owner_input:` receipt to keep an original owner source subject to
`owner_input.source`. Changing an event projection cannot remove that check.
There is no new protocol, storage table, executor or permission layer.

The existing `ReadingService.complete` boundary still authenticates the original
lease before accepting late metering. A denied source yields no observation;
known usage becomes CANCELLED and uncertain usage remains UNKNOWN. Reservations,
observed usage and duplicate receipt identity remain intact.

Inspected implementations and reuse:

- [DSH agent loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts)
  derives requests from retained session history and keeps prepared tool admission
  tied to its dispatch boundary. Core retains its existing signed-source and lease
  custody rather than deriving permission from mutable event labels.
- [DSH QQ vision plugin](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/media/vision-tool.ts)
  keeps external attachment loading separate from the model service. Reader
  transport remains separate from Core authorization and accounting.
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html#validate-the-permissions-on-every-request)
  requires permission checks on every request. The repair closes the two missed
  checks in the existing common Reader validation method.

Regression coverage uses real HTTP intake and an independent SDK worker, mutates
each original identity after lease, and checks both known and UNKNOWN late usage,
duplicate settlement, retained reservations and the absence of observations.
