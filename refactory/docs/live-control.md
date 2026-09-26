# Live activity, control and recovery

A run directory is the durable identity shared by the terminal, native panel and owning runtime. Attach a viewer using the exact returned directory. Opening, closing or reconnecting a viewer does not execute work or cancel the run.

## Read live work

```bash
uv run refactory status --run /absolute/run --follow
uv run refactory runs
uv run refactory steer --run /absolute/run
```

The native Modes panel offers **Runs and pending questions** and **Existing run directory → Read this run**. Attached views stream persisted changes. Activity includes due/completed nodes, last event time, wait reason, worker receipts, requested and observed routes, available usage, instruction-delivery state and recorded outputs. No completion percentage is estimated from elapsed time. A missing observation remains unavailable.

Graph activity is appended to `.refactory/events.jsonl`; checkpoints, worker receipts and the resource ledger live beside it. Historical runs may lack newer records. An interrupted journal write is reported as a gap rather than fabricated progress. A worker context being prepared, a dispatch being observed, and an output satisfying a correction are different facts.

## Questions and ownership

Pending user gates retain their prompt and accepted verdicts. Select the run in the native panel and answer its waiting gate, use the owning CEO session, or record an explicit verdict:

```bash
uv run refactory steer --run /absolute/run --verdict gate_name=proceed
```

The allowed verdict comes from that gate. The answer is scoped to the run and invocation, not to whichever chat is visible. Reading or answering a persisted question does not grant a second session permission to drive the graph. Each CEO session owns its workflow; concurrent runs need distinct work directories. A native question left pending after a disconnect stays pending until a valid answer reaches the owner. A restarted owner must pass the recovery checks below.

## Apply a control at its boundary

Optimizer notes, keep/reject and pause/resume/stop apply at generation boundaries. Graph controls apply at the next graph boundary and can wait for admitted work to settle. External workers accept new instructions at a subsequent node boundary, not as an in-flight prompt edit. A queued control receipt is a request, not confirmation of application.

The native panel's **Graph controls** offers pause/resume/stop. The console exposes the same operations:

```bash
uv run python -m refactory_dsh.console graph-control pause --run /absolute/run
uv run python -m refactory_dsh.console graph-control resume --run /absolute/run
uv run python -m refactory_dsh.console graph-control stop --run /absolute/run
```

Process cancellation is separate: `refactory stop --run RUN_DIR` requests interruption of its recorded owned process. Inspect the receipt and subsequent activity for confirmation; sending a signal is not proof that every worker terminated. Stopping a reader does not issue either kind of stop.

To change completed work, choose a completed node and prompt correction, press **Preview affected results**, inspect the invalidated nodes, then **Confirm correction and rerun**. The console equivalent is `graph-control preview --node NODE`, followed by `graph-control rerun --node NODE --text "CORRECTION" --revision REVISION` with the returned revision and the same `--run`. The owner refuses a stale preview. Prior evidence is archived and dependent results invalidated before execution; a note alone does not silently rewrite completed work. Unsafe replay plans, including unsettled concurrent work, are refused.

## Recover a known boundary

A live owner's resume control releases its boundary pause. If the old graph owner is gone, explicit resume can start a fresh owned process from the saved checkpoint. It preserves the run identity, completed work, route configuration and consumed resources. Larger resource limits are explicit allocations, not a reset of usage. The interactive entry point also accepts:

```bash
uv run python -m refactory_dsh.interactive package/mode \
  --workdir /absolute/run --headless --resume
```

Recovery requires a recognized checkpoint, a stopped owner, matching prepared configuration and unchanged fingerprinted inputs/outputs. It refuses a pending dispatched worker with unknown outcome or a failed/unknown last-node outcome. In-flight external product sessions are not resumed. Python executor partial-fork recovery is also refused because successful sibling facts cannot yet be restored safely.

Inspect side effects and outputs when continuation is refused. Use explicit invalidation where a valid replay plan exists, or start a new run deliberately. Reopening the mode is not an implicit retry. No mechanism here can infer whether an unacknowledged external side effect happened before a crash.

[Profiles and candidate evidence](optimization-profiles.md) · [Fast modes and resource recovery](budgets-fast-modes.md) · [Worker capabilities](models.md#model-provider-versus-coding-agent)
