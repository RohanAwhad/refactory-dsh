# Live activity, control and recovery

A run directory is the durable identity shared by the terminal, native panel and owning runtime. Attach a viewer using the exact returned directory. Opening, closing or reconnecting a viewer does not execute work or cancel the run.

## Read live work

```bash
uv run refactory status --run /absolute/run --follow
uv run refactory runs
uv run refactory steer --run /absolute/run
```

The native Modes panel offers **Runs and pending questions** and **Existing run directory → Read this run**. Attached views stream persisted changes. Activity includes due/completed nodes, last event time, wait reason, worker receipts, requested and observed routes, available usage, instruction-delivery state, recorded outputs, **run warnings** and **owner process liveness**. No completion percentage is estimated from elapsed time. A missing observation remains unavailable.

The status includes `owner_alive` (true when the checkpoint's owner process is responding, false when it has exited, absent when no owner is recorded) and failed graph control receipts visible in the activity's `control` field. `refactory status` prints whether the owner process is alive and, when the last graph control failed, a line naming the failure.

Graph activity is appended to `.refactory/events.jsonl`; checkpoints, worker receipts and the resource ledger live beside it. Historical runs may lack newer records. An interrupted journal write is reported as a gap rather than fabricated progress. A worker context being prepared, a dispatch being observed, and an output satisfying a correction are different facts.

## Questions and ownership

Pending user gates retain their prompt and accepted verdicts.

**Live mode (no `--headless`).** When a run reaches a user gate, the terminal shows the gate's own prompt (including the delivery line, with the run's facts filled in), the accepted verdicts, and asks the person to pick one. After choosing a verdict, the person may add an optional multi-line note (ending with a line containing only `.`); the note is recorded as steering in the intent ledger — a correction with reloop, a constraint with proceed.

**Headless mode.** A headless run refuses a user gate and prints the exact commands to answer it later:

```
refactory: <gate> is waiting for a person.
  <the gate's prompt>
  Answer it with one of <verdicts> (add --note "..." to say why):
    refactory steer --run <dir> --verdict <gate>=<verdict>
  Then continue the run:
    refactory graph-control resume --run <dir>
```

You can also answer via the native panel, the owning CEO session, or an explicit verdict:

```bash
uv run refactory steer --run /absolute/run --verdict gate_name=proceed
```

The allowed verdict comes from that gate. The answer is scoped to the run and invocation, not to whichever chat is visible. Reading or answering a persisted question does not grant a second session permission to drive the graph. Each CEO session owns its workflow; concurrent runs need distinct work directories. A native question left pending after a disconnect stays pending until a valid answer reaches the owner. A restarted owner must pass the recovery checks below.

## Apply a control at its boundary

Optimizer notes, keep/reject and pause/resume/stop apply at generation boundaries. Graph controls apply at the next graph boundary and can wait for admitted work to settle. External workers accept new instructions at a subsequent node boundary, not as an in-flight prompt edit. A queued control receipt is a request, not confirmation of application.

The native panel's **Graph controls** offers pause/resume/stop. The console exposes the same operations:

```bash
uv run refactory graph-control pause --run /absolute/run
uv run refactory graph-control resume --run /absolute/run
uv run refactory graph-control stop --run /absolute/run
```

Process cancellation is separate: `refactory stop --run RUN_DIR` requests interruption of its recorded owned process. Inspect the receipt and subsequent activity for confirmation; sending a signal is not proof that every worker terminated. Stopping a reader does not issue either kind of stop.

A control that cannot be applied — a stale revision, an unsupported action, a node that is not safely replayable — is recorded with `status: failed` in the receipt. The failure reason is also journaled as a run warning, visible in `refactory status` and in the receipt shown by `refactory graph-control`'s own output. The owner consumes and removes the control file regardless of outcome.

To change completed work, choose a completed node and prompt correction, press **Preview affected results**, inspect the invalidated nodes, then **Confirm correction and rerun**. The console equivalent is `graph-control preview --node NODE`, followed by `graph-control rerun --node NODE --text "CORRECTION" --revision REVISION` with the returned revision and the same `--run`. The owner refuses a stale preview. Prior evidence is archived and dependent results invalidated before execution; a note alone does not silently rewrite completed work. Unsafe replay plans, including unsettled concurrent work, are refused.

## Recover a known boundary

The run's status includes whether its owner process is alive. A failed run with a live owner may still apply queued controls and does not need recovery — wait for it or send another control. A failed run whose owner is not alive cannot apply pending controls; explicit recovery (resume) is needed to start a fresh owner from the saved checkpoint.

A live owner's resume control releases its boundary pause. If the old graph owner is gone, explicit resume can start a fresh owned process from the saved checkpoint. It preserves the run identity, completed work, route configuration and consumed resources. Larger resource limits are explicit allocations, not a reset of usage. The interactive entry point also accepts:

```bash
uv run python -m refactory_dsh.interactive package/mode \
  --workdir /absolute/run --headless --resume
```

Recovery requires a recognized checkpoint, a stopped owner, matching prepared configuration and unchanged fingerprinted inputs/outputs. It refuses a pending dispatched worker with unknown outcome or a failed/unknown last-node outcome. In-flight external product sessions are not resumed. Python executor partial-fork recovery is also refused because successful sibling facts cannot yet be restored safely.

An emission that changes nothing is refused back to emit, including when materialise is replayed on an emit.txt the last attempt already holds. The refusal says the emission changed nothing, not that an edit's text is missing, and the branch holds one attempt per answer that changed something.

Inspect side effects and outputs when continuation is refused. Use explicit invalidation where a valid replay plan exists, or start a new run deliberately. Reopening the mode is not an implicit retry. No mechanism here can infer whether an unacknowledged external side effect happened before a crash.

Release checks exercised live CLI observation and native gate answers, pause/resume, reviewed replay and owned stop. Separate registered dead-owner recovery checks passed through public CLI resume and the native Resume control, preserving completed artifacts, saved configuration and accumulated resources while executing only the remaining node. An unavailable-provider guard prevented model execution. See the [validation record](release-validation-20260926.md).

[Profiles and candidate evidence](optimization-profiles.md) · [Fast modes and resource recovery](budgets-fast-modes.md) · [Worker capabilities](models.md#model-provider-versus-coding-agent)
