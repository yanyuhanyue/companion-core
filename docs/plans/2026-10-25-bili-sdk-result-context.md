# Original Bili visual result in SDK context

The real Bridge/HTTP/independent worker test completed both paid visual phases,
but the final SDK request omitted their analysis. `exploration_evidence` contains
the Reader's source dependencies; the authoritative v3 output identities are in
`reading_attempts.result_event_ids_json`. SDK result loading now uses those original
output identities, with the same attempt/result fingerprint and retained events.

The result joins the existing Conversation snapshot list as a Reader result digest.
The existing `check_conversation` path rechecks it before the final paid transport
boundary and during Conversation persistence. This reuses the snapshot mechanism
already used for memory, images and source custody; there is no new model loop,
queue, database table or delivery protocol. The changing `as_of` display time is
excluded from the digest; source identities, state, complete evidence and the
original v3 receipt/source bindings are included. Deleting a Reader evidence link
cannot be hidden by the same event still appearing in its result list.

Bili result validation reuses the original source snapshot, retained observation
TTL, phase evidence and native model admissions. Its text must match the original
phase analyses and observation hash. The published phase identities, payload hashes,
settled usage and accepted receipt hashes must still agree. External transport hooks
cannot replace that material after context assembly and before provider I/O.
Known rejected additional phases do not hide already settled partial evidence;
pending or UNKNOWN phases cannot support a completed visual result.

The delivery integration seam is
`revalidate_results(store, settings, clock, job, snapshots, executing=False)` in
`agent_reading`. It supports a completed job and only reads the original custody;
the existing service constructors hold references and open no Core, model, search
or HTTP session. Active conversation checks use the same function with
`executing=True`. Delivery callers retain their existing snapshot hash binding and
pass those saved `reader` snapshots unchanged. Owner delivery integration is owned
by the integration lane and is deliberately separate from this commit.

Inspected references:

- [DSH agent-loop prepared tool admission](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts)
  keeps the prepared tool set through dispatch and appends original tool results to
  session history. The Core implementation keeps its existing result ownership.
- [Pydantic AI deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)
  resumes a Conversation using results correlated with the original deferred calls.
  `DeferredToolRequests.build_results` remains the only SDK continuation mechanism.
- [HTTPX event hooks](https://www.python-httpx.org/advanced/event-hooks/)
  can run after request preparation. The existing paid transport's final source
  check therefore remains responsible for revalidating the persisted result.

The 49 consumer fixtures were frozen separately in `b554ad5`, retaining the missing
visual-body regression. The preceding Reader URL/source repair and its 129 passing
checks remain separate. The shared native busy fix from `f4d8d6a` is applied before
final verification: a newly accepted input cannot start another SDK call while an
old model RPC remains reserved. The in-flight follow-up fixture waits for the old
phase to settle before selecting the existing video request again.
