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

`spine-status.json` now includes a `running_nodes` array naming each executing node and its kind (`agent`, `operation`, `gate`, `control`). The `wait_reason` field names the running node(s) and their kinds — e.g. `"waiting for worker delivery: research_director (agent)"`.

Each usage event in `.refactory/events.jsonl` carries `tokens_delta` (the token count of the single request that triggered this event) alongside the cumulative `tokens` total. Summing `tokens_delta` across events for one worker gives its total; summing `tokens` across events overcounts.

The authoring status includes `step_tokens`: per-step token totals computed by summing worker receipts by `node_id`. A step with retries sums to one entry. `total_tokens` is the sum across all steps.

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

### Steering note kinds

Steering notes added with `refactory steer --kind info` record context that agents
see but the audit does not require evidence for. Use `--kind correction` or
`--kind constraint` for asks the audit must cover.

## Apply a control at its boundary

Optimizer notes, keep/reject and pause/resume/stop apply at generation boundaries. Graph controls apply at the next graph boundary and can wait for admitted work to settle. External workers accept new instructions at a subsequent node boundary, not as an in-flight prompt edit. A queued control receipt is a request, not confirmation of application.

The native panel's **Graph controls** offers pause/resume/stop. The console exposes the same operations:

```bash
uv run refactory graph-control pause --run /absolute/run
uv run refactory graph-control resume --run /absolute/run
uv run refactory graph-control stop --run /absolute/run
```

Process cancellation is separate: `refactory stop --run RUN_DIR` requests interruption of the run's current owner process (from the console launch record, or from the spine checkpoint when the launch record is absent or stale, matching how `refactory status` determines `owner_alive`). Inspect the receipt and subsequent activity for confirmation; sending a signal is not proof that every worker terminated. Stopping a reader does not issue either kind of stop.

A control that cannot be applied — a stale revision, an unsupported action, a node that is not safely replayable — is recorded with `status: failed` in the receipt. The failure reason is also journaled as a run warning, visible in `refactory status` and in the receipt shown by `refactory graph-control`'s own output. The owner removes the control file once it applies or refuses the control. A deferred rerun stays queued until concurrent work joins.

To change completed work, choose a completed node and prompt correction, press **Preview affected results**, inspect the invalidated nodes, then **Confirm correction and rerun**. The console equivalent is `graph-control preview --node NODE`, followed by `graph-control rerun --node NODE --text "CORRECTION" --revision REVISION` with the returned revision and the same `--run`. The owner refuses a stale preview. Prior evidence is archived and dependent results invalidated before execution; a note alone does not silently rewrite completed work. Unsafe replay plans, including unsettled concurrent work, are refused.

## Recover a known boundary

The run's status includes whether its owner process is alive. A failed run with a live owner may still apply queued controls and does not need recovery — wait for it or send another control. A failed run whose owner is not alive cannot apply pending controls; explicit recovery (resume) is needed to start a fresh owner from the saved checkpoint.

A live owner's resume control releases its boundary pause. If the old graph owner is gone, explicit resume can start a fresh owned process from the saved checkpoint. When the run's status is terminal (done, halted, failed, refused, or cancelled) and the recorded owner is still alive, resume waits briefly for it to exit. If the owner exits during the wait, a fresh owner is started automatically. If the owner is still alive after the wait, the resume is filed as a pending resume (`.refactory/resume-pending.json`) and honoured automatically when the old owner finishes exiting — no second command is needed. It preserves the run identity, completed work, route configuration and consumed resources. Larger resource limits are explicit allocations, not a reset of usage. `refactory graph-control resume --run <dir>` is the recovery command:

```bash
uv run refactory graph-control resume --run /absolute/run
```

It internally runs the interactive entry point with `--resume`.

Recovery requires a recognized checkpoint, a stopped owner, matching prepared configuration and unchanged fingerprinted inputs/outputs. It refuses a dispatch whose outcome is unknown — a pending marker with no written receipt; a refused step whose receipt was written is not unknown. It also refuses a failed or unknown last-node outcome. In-flight external product sessions are not resumed. Python executor partial-fork recovery is also refused because successful sibling facts cannot yet be restored safely.

A process killed by a signal during a check or environment install is retried once; if it dies again, the run reports it as an infrastructure failure naming the signal and halts without spending an attempt.

An emission that changes nothing is refused back to emit, including when materialise is replayed on an emit.txt the last attempt already holds. The refusal says the emission changed nothing, not that an edit's text is missing, and the branch holds one attempt per answer that changed something.

Inspect side effects and outputs when continuation is refused. Use explicit invalidation where a valid replay plan exists, or start a new run deliberately. Reopening the mode is not an implicit retry. No mechanism here can infer whether an unacknowledged external side effect happened before a crash.

### Supervision

When an owner process exits and the run's spine status is neither terminal nor deliberately paused, the supervisor automatically restarts the owner from the saved checkpoint. This eliminates the need for manual `graph-control resume` after a model outage, a crash, or a killed process.

**Terminal states** (no respawn): `done`, `cancelled`, `failed`.
**Deliberately paused states** (no respawn): `halted`, `paused`, `waiting_user`, `refused`.
**Supervisable states** (respawn if owner is dead): `ready`, `executing`.

| Aspect | Detail |
|--------|--------|
| Respawn limit | `REFACTORY_MAX_RESPAWNS` environment variable (default 3, 0 disables). |
| History | Each respawn is logged to `.refactory/supervision-history.json` and to `.refactory/events.jsonl`. |
| Detached process | The supervisor (`python -m refactory_dsh.supervisor`) outlives the CLI or GUI that launched it. One per run, held by `.refactory/supervisor.lock`. |
| Transient vs deterministic | A `recovery.prepare()` refusal (e.g. "last node outcome is unknown or failed") is deterministic — recorded as a start failure and not retried. Only transient exits (crash, kill, model outage) are respawned. |
| Start-failure recording | An owner that exits before advancing the spine status writes `.refactory/start-failure.json` with the reason. `refactory status` reports `state: failed` with the reason, instead of the stale spine state. |
| Single-resume | A resume requested while the old owner is still exiting is filed as `.refactory/resume-pending.json` and honoured automatically when the old owner finishes exiting. No second command needed (dm-81). |
| Headless inline | A fresh headless run (`refactory ceo --headless`, `interactive.run(headless=True)`) applies the same supervision loop inline after the owner exits. |
| Supervisor killed | If the supervisor itself is killed (`SIGKILL`), the owner child continues in its own session. The run is then unsupervised — exactly as today. A manual `refactory graph-control resume` is the fallback. |

### Replay a failed run

When a run's owner has exited after a failure, `--resume` refuses the
checkpoint. To replay a specific node, preview and confirm the rerun with the
failure acknowledged:

```bash
uv run refactory graph-control preview --run /absolute/run --node NODE
uv run refactory graph-control rerun --run /absolute/run \
  --node NODE --text "CORRECTION" --revision REVISION --acknowledge-failure
```

The `--acknowledge-failure` flag confirms that the person has inspected the
failed node's side effects. The command queues the rerun control file, then
starts a new owner process. The owner restores the failed checkpoint, applies
the queued rerun at its first boundary, and continues the run.

`--resume` continues to refuse failed checkpoints. Only `graph-control rerun`
with `--acknowledge-failure` starts an owner for a failed run.

### Incomplete cursor drain

A run whose cursor empties before reaching a graph sink — for example after
a redo that dropped the work due, or a skip that bypassed the remaining
path — halts with the names of the unreached sinks. The halt is visible in
`refactory status` (state `halted`, finished) and makes `refactory ceo`
exit non-zero. A sink is a node the scheduler has nowhere to go after: not a
node with an outgoing edge, not a fork branch or a director's child (they
continue at the join or the director's successor), and not a data node's
per-item exit (it returns to its data node).

### Forward-only invalidation

Replaying a node invalidates only its forward path. Nodes reached exclusively
through a gate's reloop back-edge are not invalidated or archived. Example:
replaying `overwatch` invalidates `{overwatch, gate_overwatch, report,
deliver}` but not `{emit, materialise, check, reviews, synth}`.

The forward path of a node inside a fork branch, or of a director's child,
continues where the scheduler would take it: the fork's join, or the
director's successor. Replaying `code_reviewer` invalidates
`{code_reviewer, join_qa, synthesize_qa, gate_qa, ...}` and leaves the other
reviewers' answers in place; once `code_reviewer` runs again, the join and the
gate after it run again too.

A verdict (proceed, reloop, or any forward outcome) inside a fork branch
replaces only the gate's own cursor position; sibling positions and their
container barriers remain intact. A reloop whose target lies outside its
gate's innermost container (fork or expanded director) is refused at graph
load time and at verdict time with a descriptive error naming the gate, its
container, and the external target.

### Scoped corrections

The `--text` on a rerun sets per-visit feedback, not a permanent prompt
amendment. The feedback is shown to the driver, cleared when the agent
completes, and superseded when a gate reloops with its own findings. A
one-shot correction ("emit these two files and change nothing else") applies
to that visit only and does not override a later review's repair.

### Gate refusal reason persistence

When a gate refused work and sent it back (reloop), or halted at its
iteration limit, the gate's last refusal reason is preserved beside its
verdicts. On a rerun of the node the gate sent work back to, that reason
is shown as the node's feedback — the node sees what the gate said, even
when the person reruns without `--text`. When the person supplies `--text`,
the gate's reason appears first and the person's text follows it. A gate
that proceeded clears its stored reason, so a rerun after a passed gate
shows no stale refusal.

### CEO redo and attempt limits

When the CEO redoes a completed node through `wf_redo`, the redo preserves
the run's attempt counters and gate iteration counts. A gate that was on
iteration 2 of 3 stays there; the check gate's `attempts_used` is not reset.
This prevents an infinite redo loop where the CEO redoes emit after the check
gate exhausts its attempts, resetting the counter each time.

Once a gate's iteration limit has halted the run, or a node has failed,
`wf_redo` is refused. The refusal names
`refactory graph-control rerun --acknowledge-failure` as the way a person
spends more attempts. Any `graph-control rerun` by a person resets all
counters — both gate iteration counts and attempt facts — as their decision
to spend more attempts.

### Deferred replay

When concurrent work (a fork's parallel branches) blocks a replay, preview
returns status `deferred` with the invalidation plan. Confirm the deferred
preview to queue the replay. It stays queued until concurrent work joins,
then applies if the invalidation plan still matches, or fails with "plan
changed" if it does not.

### Gate replay

Gate replay is not needed. Forward-only invalidation limits the blast radius:
replaying the upstream agent (e.g. `overwatch` for `gate_overwatch`) re-runs
only that agent, this gate and what follows, not the loop body. The refusal
message names this alternative.

Release checks exercised live CLI observation and native gate answers, pause/resume, reviewed replay and owned stop. Separate registered dead-owner recovery checks passed through public CLI resume and the native Resume control, preserving completed artifacts, saved configuration and accumulated resources while executing only the remaining node. An unavailable-provider guard prevented model execution. See the [validation record](release-validation-20260926.md).

[Profiles and candidate evidence](optimization-profiles.md) · [Fast modes and resource recovery](budgets-fast-modes.md) · [Worker capabilities](models.md#model-provider-versus-coding-agent)
