# Local voice cleanup operations

The voice v2 cleanup command performs one local retention pass using the existing node
configuration and NodeState exclusive lock. It makes no network, Core, provider, model, QQ
or GPU call. It removes expired result-cache entries and temporary paths only when ownership
is exact and the owning process is confirmed exited, and scrubs retired node content.
QUARANTINED execution state remains quarantined. This command does not acknowledge cleanup
to Core or settle delivery uncertainty.

## Manual pass

Use the deployed voice v2 checkout and its existing runtime/configuration:

```powershell
pwsh -NoProfile -File .\scripts\cleanup-voice-node.ps1 `
  -RuntimeRoot '.\.local\voice-runtime' `
  -NodeConfig '.\.local\voice-runtime\node.json'
```

Replace the example paths with the actual private runtime paths. The config must be inside
RuntimeRoot. The launcher reuses `voice-runtime-env.ps1` and the runtime's
`venv/Scripts/python.exe`, then synchronously invokes exactly:

```text
python -B -m companion_voice_node.worker --config <existing node config> --cleanup-only
```

It does not invoke `--print-manifest`, `local_manifest`, a model warmup or a service launcher.
The worker owns deletion eligibility and locking; the PowerShell wrapper does not implement
a second cleanup policy. Deploy it together with the v2 worker that implements this flag.

The worker prints JSON with `pending_temp`, `unowned_temp`, `pending_ack` and
`core_cleanup_confirmed=false`. Exit 0 means this scan
completed, **not** that pending work reached zero. A nonzero status means lock contention or
an error; inspect stderr and the report before concluding anything was removed. The wrapper
preserves the native exit code. A running node may hold the exclusive lock: leave its process
and state intact and retry after normal shutdown. Pending ownership/exit proof and execution
quarantine require their existing resolution procedure; repeated scans do not clear them.

## Optional Windows task

`deploy/voice-node-cleanup.task.xml.example` is an opt-in template, with the task disabled.
Preparing this file does not register or enable a task. Before a deliberate deployment:

1. Replace the owner SID, checkout, runtime and node-config placeholders; verify the PowerShell
   7 executable path and choose a start time with the intended timezone. XML-escape any `&`
   in paths as `&amp;`. Keep the arguments' path quotes.
2. Use the same ordinary Windows account that owns the runtime, with permission to its private
   configuration and state. `InteractiveToken` requires that account to be logged on; this
   template does not store a password or run as SYSTEM.
3. Review the disabled definition in Task Scheduler. Enable only when intentionally opting in.
   No task registration or enablement is part of this repository change.

The repetition interval is `PT1M` (60 seconds), with no repetition end. `IgnoreNew` skips a
trigger while the prior scan is still running instead of queueing or terminating it. NodeState
still arbitrates against the regular worker. The task does not require network availability or
idle state, wake the machine, or elevate privileges. It runs while logged on and awake; 60
seconds is a scheduling cadence, not a cleanup completion deadline. A scan is not forcibly
terminated by an execution-time limit.

Task Scheduler's last result reports the process exit status; it does not preserve the JSON
stdout report. Run the manual command to inspect pending counts, or explicitly redirect its
stdout into the existing private runtime logs directory when collecting an operator report.
Neither a zero last result nor an idle Windows session proves Core cleanup acknowledgment.

## Coordinated v2 upgrade and recovery

Upgrade Core, the outbound node and the Bridge together while their existing execution and
delivery loops are stopped. The node boundary is `/internal/v2/voice-node`; v1 requests
return `410 VOICE_UPGRADE_REQUIRED`. Core migration validates the original signed stop and
delivery evidence before changing the database. Missing or invalid proof stops the migration;
retain the original database and journals and complete the existing stop/reconciliation
procedure first. An upgrade never settles an UNKNOWN execution or delivery.

Each node keeps a monotonic claim sequence and Core retains at most one replay response per
node. Losing or rolling back node state, or restoring Core, requires explicit owner recovery.
First confirm the original provider process has exited and use the existing resource recovery
procedure for any quarantine. Then run the node's `--recover-claims` command with its existing
config, `--control-token-file`, `--operator`, `--service-instance` and
`--confirmation inflight-execution-stopped`. Keep the owner token in the private token file.
Recovery holds the local state lock, removes exactly owned content and reconciles the Core
session. A job absent from a restored Core is retired locally only after the explicit owner
operation and a second matching session read; its audit records no Core cleanup confirmation.
An ordinary worker 404 does not release that reference. Never edit the cursor or delete the
state directory to make recovery succeed.

For an existing job, Core reports cleanup pending until it receives the authenticated STOP
receipt for the current cleanup epoch. The configured node can send this terminal receipt
even when voice execution is disabled. Removing its credential before acknowledgment leaves
cleanup unresolved. Owner control or that node's credential can read
`GET /internal/v2/voice-node/nodes/{node_id}/cleanup` for the pending count. The local-only
command above never contacts this endpoint or turns its local report into a Core receipt.

## Reused mechanisms and sources

- Existing `scripts/start-voice-node.ps1` supplies the runtime/config resolution pattern;
  `scripts/voice-runtime-env.ps1` confines local caches and temp paths to that runtime.
- Existing `scripts/stop-voice-runtime.ps1` checks process identity and confirmed exit while
  preserving quarantine. The cleanup launcher does not call it or infer exit from idle status.
- The inspected DSH Python SDK `0.2.1rc2`, `deepseek_harness/client.py:HarnessClient.close`,
  requests shutdown, waits for exit and uses terminate/kill with a final wait if needed. This
  supports the distinction between a stop request and confirmed process exit; no DSH runtime
  is invoked by cleanup. See the [SDK release](https://pypi.org/project/deepseek-harness-sdk/0.2.1rc2/).
- Microsoft's [Task Scheduler schema](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-schema),
  [repetition definition](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-repetition-triggerbasetype-element),
  [instance policy](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-multipleinstancespolicy-settingstype-element)
  and [enabled setting](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-enabled-settingstype-element)
  define the native scheduler behavior used by the template.
