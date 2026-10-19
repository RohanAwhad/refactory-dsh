# Supported commands for correcting stuck design runs

## Summary

When a design run gets stuck — a wrong setup/check/generate command (dm-65), a dead owner leaving an `executing` checkpoint (dm-70), a headless owner that lingers after graph failure (dm-93), or a resume consumed and silently lost during owner exit (dm-94) — the only recovery is hand-editing `.refactory/facts.json` and `.refactory/spine-checkpoint.json`. This change adds three commands (`amend-command`, `acknowledge`, `withdraw`) and four lifecycle fixes (owner exit on terminal status, direct stop for terminal runs, withdraw for stale queued controls, ownership scope split) so that every correction has a supported command. The commands work for any graph topology: nodes inside forks, joins, nested forks, expansions, gates, and data-body nodes.

## Rule and sites

**Rule:** A person corrects a stuck run through supported commands that record an explicit audit trail, invalidate through the existing machinery, and never require hand-editing facts.json, the checkpoint, or delivery markers.

### facts.json readers and writers

- `refactory/refactory_dsh/run.py:32` — defines `FACTS = "facts.json"`, the node runner writes to it after each node completes. **[left: the node runner writes facts produced by the mode's own ops; the new `command_history` is written by `graph_control.py`, not by the runner]**
- `refactory/refactory_dsh/interactive.py:422-435` — `run_summary()` reads facts.json to print what a finished run recorded. **[left: read-only summary path; does not write facts]**
- `refactory/refactory_dsh/console.py:417` — `_authoring()` reads `facts.json` for the spine status panel: name, validation, files, scores. **[changed]** Also reads `command_history` from facts.json and includes it in the activity dict.
- `refactory/refactory_dsh/console.py:457` — `_item_results()` reads `facts.json` for item_scores. **[left: read-only scoring path]**
- `refactory/refactory_dsh/console.py:834` — `gates()` reads `facts.json` to fill gate prompt templates. **[left: read-only gate path]**
- `refactory/refactory_dsh/console.py:1788` — reads `facts.json` in the CLI steer verb. **[left: read-only steer path]**
- `refactory/refactory_dsh/live.py:148` — `snapshot()` reads `facts.json` for the live activity snapshot. **[left: read-only live path]**
- `refactory/refactory_dsh/items.py:46,258` — defines `FACTS = "facts.json"`, item reset reads and writes it. **[left: item management, unrelated to command correction]**
- `refactory/refactory_dsh/invocations.py:84,88` — `prepare()` checks whether facts.json exists (to reject a workdir with orphan facts), and archives it as metadata. **[left: invocation archival, facts.json is archived as-is]**
- `refactory/refactory_dsh/run_artifacts.py:31` — `owned_outputs()` skips `facts.json` in the owned-outputs calculation. **[left: artifact calculation, unrelated]**
- `refactory/refactory_dsh/design_ops.py:312-334` — reads facts for the base check (setup, generate, check commands). **[left: reads the commands; after an amend-command, reads the corrected value]**
- `refactory/refactory_dsh/fast_ops.py:204-226` — same pattern as design_ops for fast modes. **[left: same reasoning]**
- `refactory/refactory_dsh/base_check.py:486-488` — `run_base_check()` reads `ctx.facts.get("setup")`, `ctx.facts.get("generate")` and the `command` parameter (which is `ctx.facts.get("check")`) to compute the cache key. **[left: reads the commands; after amend-command, the new value is in facts.json, so the cache key changes automatically]**
- `refactory/refactory_dsh/delivery.py:1366-1391` — `_base_evidence()` reads facts for inherited_failures and test-guardedness. **[changed]** Also reads `command_history` and emits a line per correction in the Verification section.
- `refactory/refactory_dsh/delivery.py:1394-1455` — `_body()` reads `ctx.facts.get("check")`, `ctx.facts.get("brief")`, and delivery facts. **[left: reads the current check command; after amend-command this is the corrected value]**
- `refactory/refactory_dsh/graph_control.py` — does not currently read or write facts.json. **[changed]** The new `amend-command` action reads facts.json, updates the command field, appends to `command_history`, and writes the file back.

### spine-checkpoint.json readers and writers

- `packages/workflow/workflow-spine/src/index.ts:793-801` — `recordStatus()` writes the checkpoint with `spine.checkpoint()`, `status`, `owner_pid`, `file_fingerprints`, `materialised`, `run_id`. **[left: the spine is the sole writer of checkpoints during normal execution]**
- `packages/workflow/workflow-spine/src/index.ts:708-729` — `restoreRequested()` reads the checkpoint to verify SHA, status, and then restores the spine from the saved state. **[changed]** When status is `executing` and `resume-request.json` has `acknowledge_executing: true` with a matching SHA, the checkpoint is accepted (halted state cleared, pause_before emptied) without the acknowledge handler ever rewriting the checkpoint file.
- `packages/workflow/workflow-spine/src/index.ts:871` — `applyControls()` reads the checkpoint to verify revision against a rerun control. **[changed]** The rerun branch now recognises `source: 'amend-command'` controls and passes `{ commandCorrection: true }` to `planRedo` and calls `redoCommandCorrection` so that due nodes are accepted.
- `refactory/refactory_dsh/recovery.py:55-57` — `prepare()` reads the checkpoint: status, run_id, graph name, owner_pid, file_fingerprints. **[changed]** When status is `executing`, prepare() reads `resume-request.json` and accepts the checkpoint only when `acknowledge_executing` is true and the SHA matches. A stale acknowledgement for another checkpoint never lets a later run through.
- `refactory/refactory_dsh/graph_control.py:28-32` — `request()` reads the checkpoint for status, redo_plans, deferred_redo_plans, and computes its SHA. **[changed]** The new `acknowledge` action reads the checkpoint but does NOT rewrite it: it writes a SHA-bound acknowledgement to `resume-request.json`. The new `amend-command` action reads the checkpoint to verify status and compute the rerun control's revision.
- `refactory/refactory_dsh/supervisor.py:121-123` — `_read_resume_pending()` reads the checkpoint to verify the pending SHA. **[left: resume-pending validation, no change needed]**
- `refactory/refactory_dsh/console.py:507,561,661` — `status()`, `_state()`, `_checkpoint_owner_pid()` read the checkpoint for owner_pid and status. **[left: read-only status paths]**
- `refactory/refactory_dsh/run_state.py:87` — `needs_supervision()` reads the checkpoint for owner_pid. **[left: read-only supervision decision]**
- `refactory/refactory_dsh/live.py:75` — `snapshot()` reads the checkpoint for the live snapshot. **[left: read-only live path]**
- `refactory/refactory_dsh/repo_change.py:199` — reads the checkpoint for owner_pid to check liveness. **[left: read-only change tracking]**

### Pending delivery markers (.refactory/agent-delivery/pending/)

- `refactory/refactory_dsh/agent_delivery.py:182-183` — `_pending()` computes the path; `begin()` at line 186 writes the pending marker; `finish()` at line 295 removes it on completion. **[left: the agent delivery contract is unchanged; acknowledge moves the pending files aside]**
- `refactory/refactory_dsh/recovery.py:89-91` — `prepare()` scans `agent-delivery/pending/*.json` and refuses resume if any exist. **[left: prepare() is unchanged; acknowledge moves pending markers before prepare() runs]**
- `packages/workflow/workflow-spine/src/spine.ts:1063` — `planSkip()` admits a skip before the runtime retires pending delivery attempts. **[left: the spine's skip handling is unrelated to correction commands]**
- `refactory/refactory_dsh/graph_control.py` — does not currently touch pending markers. **[changed]** The new `acknowledge` action moves pending markers under `.refactory/set-aside/<timestamp>/`.

### Fingerprints (configuration_fingerprints, file_fingerprints)

- `refactory/refactory_dsh/recovery.py:11-17` — `fingerprint()` computes SHA for a path or directory tree. **[left: utility function, unchanged]**
- `refactory/refactory_dsh/recovery.py:20-49` — `configuration_fingerprints()` pins the graph, declarations, mode implementation and op modules. **[left: called at prepare time; the amend-command does not change the mode's source]**
- `refactory/refactory_dsh/recovery.py:92-94` — `prepare()` verifies `file_fingerprints` from the checkpoint against the current filesystem. **[left: amend-command updates facts.json before queuing the rerun; the fingerprint guard sees consistent state because the new facts.json is what the next checkpoint will fingerprint]**
- `refactory/refactory_dsh/recovery.py:96-101` — `prepare()` verifies `configuration_fingerprints` from `run-config.json`. **[left: mode source does not change during a command correction]**
- `refactory/refactory_dsh/interactive.py:137-142` — `prepare()` writes `configuration_fingerprints` into `run-config.json`. **[left: only written at initial prepare, not during resume or correction]**
- `packages/workflow/workflow-spine/src/index.ts:732-746` — `fingerprint()` computes SHA for a path or directory tree. **[left: spine-side utility]**
- `packages/workflow/workflow-spine/src/index.ts:796-800` — `recordStatus()` writes `file_fingerprints` into the checkpoint. **[left: the spine writes fresh fingerprints at each checkpoint]**

### User paths that reach these files

- **CLI** — `refactory graph-control <action> --run DIR` → `graph_control.request()`. The new actions (`acknowledge`, `amend-command`, `withdraw`) are reached through this path. The parser at `console.py:1271-1281` adds the new actions to the `choices` list and the new flags. The dispatch at `console.py:1683-1694` calls `graph_control.request()` unchanged.
- **GUI** — `console.py:912-914` → `_steer()` dispatches to `graph_control.request()` for graph runs. The native panel's Graph Controls section offers the same operations.
- **Headless run** — `interactive.run()` at `interactive.py:249-401` → `recovery.prepare()` for resume; the inline `supervise()` call at line 385-392 handles respawn. The ownership scope split (dm-94) and terminal-status poll (dm-93) are in this path.
- **Supervisor** — `supervisor.supervise()` at `supervisor.py:171-364` → `recovery.prepare()` for respawn decisions. The `suggested_command` in `start-failure.json` is written here.

## Docs

- `refactory/docs/live-control.md` — Update to document: (1) new "Correct a stuck run" section with `amend-command`, `acknowledge`, `withdraw` subcommands; (2) stale-control prevention via direct stop on terminal runs, withdraw, and status listing queued controls; (3) owner exit on terminal status in the "Supervision" section; (4) `acknowledge` as next step for dm-70 refusal in the "Recover a known boundary" section.
- `refactory/docs/cli.md` — Update line 208 to list the new actions alongside the existing ones: "control a graph run through the spine: pause, resume, stop, acknowledge, amend-command, or withdraw a control."
- `refactory/docs/interfaces.md` (line 68) — Update the set of graph controls to include `acknowledge`, `amend-command`, and `withdraw`.

## Changes

### Layer 1: Owner lifecycle hardening (dm-93, dm-94)

#### `refactory/refactory_dsh/invocations.py` [left: no change needed]

Per correction #2, `owner_generation` was dropped. The stale-control problem is handled by: (a) stop on a terminal run signalling directly and queuing nothing; (b) withdraw; (c) status listing the queued controls, so a person sees what a next owner would apply.

#### `refactory/refactory_dsh/graph_control.py` [changed]

**Withdraw action:** A new branch in `request()`:

```python
if action == 'withdraw':
    control_id = node  # repurpose the node parameter as control_id
    control_path = root / '.refactory/graph-controls' / f'{control_id}.json'
    if not control_path.exists():
        raise ValueError(f'no queued control with id {control_id!r}')
    receipt_path = root / '.refactory/graph-control-receipt.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if isinstance(receipt, dict) and receipt.get('id') == control_id and receipt.get('status') == 'applied':
            raise ValueError(f'control {control_id!r} has already been applied')
    control_path.unlink()
    return {'action': 'withdraw', 'control_id': control_id, 'status': 'withdrawn'}
```

Rejection: an unknown `control_id` raises `ValueError('no queued control with id ...')`. An already-applied control raises `ValueError('control ... has already been applied')`.

**Stop on terminal-status run with live owner:** In the `stop` action path, when the spine status is terminal and the owner is alive, send `SIGTERM` directly instead of queuing a control file the failed graph never reads:

```python
if action == 'stop':
    spine_stat = read(root / '.refactory/spine-status.json').get('status', '')
    if spine_stat in run_state.TERMINAL:
        pid = saved.get('owner_pid')
        if pid and _alive(pid):
            import signal as _sig
            try:
                os.kill(pid, _sig.SIGTERM)
            except ProcessLookupError:
                pass
            return {'action': 'stop', 'status': 'signalled', 'pid': pid,
                    'message': f'Graph is {spine_stat}; sent SIGTERM to lingering owner {pid}.'}
```

**Validation of action set:** Update the action validation at line 71 to:
```python
if action not in ('pause', 'resume', 'stop', 'rerun', 'withdraw', 'acknowledge', 'amend-command'):
    raise ValueError(f'unsupported graph control {action!r}')
```

#### `packages/workflow/workflow-spine/src/index.ts` [changed]

**Command-correction rerun dispatch:** In the rerun branch of `applyControls` (around line 843-892), when the control carries `source: 'amend-command'`, pass `{ commandCorrection: true }` to `planRedo`:

```typescript
const isCommandCorrection = request.source === 'amend-command'
const plan = spine.planRedo(target, isCommandCorrection ? { commandCorrection: true } : undefined)
```

And call `redo` with the same flag:

```typescript
if (isCommandCorrection) {
    spine.redoCommandCorrection(target)
} else {
    spine.redo(target)
}
```

#### `refactory/refactory_dsh/interactive.py` [changed]

**dm-94: Ownership scope split.** In `run()` (line 249-401), the current `with ownership(workdir) as owner:` block spans prepare, child process, and inline supervision. Split it so ownership is released before the inline supervisor call.

Current structure (line 249-401):
```python
with ownership(workdir) as owner:
    # ... prepare, run child, inline supervision
```

New structure:
```python
with ownership(workdir) as owner:
    # ... prepare, build argv, run child (lines 249-350)
    # ... budget and run_owned (lines 338-350)
    pass  # ownership released here

# Inline supervision runs OUTSIDE ownership
if headless and code != 0:
    try:
        from refactory_dsh.supervisor import supervise
        result = supervise(workdir, first_exit_code=code, owner_start_time=owner_start)
        if result is not None:
            code = result
    except Exception:
        pass
```

The key change is at lines 385-392: the `supervise()` call moves outside the `with ownership` block. The supervisor acquires its own lock (`supervisor.lock`, already used at `supervisor.py:196`). This means a resume-pending.json consumed by the supervisor is processed after ownership is released, so it can start a new child without being refused by the ownership check.

**Record start-failure on refused resume inside ownership:** When the inline supervisor sees a gate's refused status as deliberately paused (because a resume was consumed and lost), it returns without recording anything. The actual fix is the scope split itself: the supervisor now runs outside ownership, so it can start a child. Additionally, when resume-pending.json is consumed inside the ownership block but no child starts (because the ownership check refuses), a start-failure.json is recorded with the reason before the block exits.

**dm-93: Terminal-status stop condition on `run_owned` / `run_graph_command`.** Per correction #1, the existing `run_owned` and `run_graph_command` are kept — they carry process-group handling, the ownership descriptor hand-off, timeouts, budget and telemetry. Both now accept an optional `stop_condition` callable. For headless runs, `interactive.py` passes a stop condition that reads `spine-status.json` and returns True when the status is terminal. The polling and grace-period logic lives in `owned_process.py`'s new `_communicate_with_stop` helper.

#### `refactory/refactory_dsh/owned_process.py` [changed]

The `run_owned()` function now accepts an optional `stop_condition` callable. When given, it delegates to `_communicate_with_stop()` instead of `proc.communicate()`. The helper:

1. Polls `proc.poll()` and `stop_condition()` every 1 second.
2. When `stop_condition()` returns True: waits the full grace period (`STOP_CONDITION_GRACE_S`, default 60 s) via `proc.wait(timeout=...)`.
3. If the process exits during the grace period, breaks normally.
4. After the grace period, re-checks `stop_condition()`. If it no longer holds (transient status — e.g. a node retry went failed → executing), continues polling without stopping.
5. Only when the condition still holds after the full grace period does it close stdin and terminate. An event is emitted naming why the owner was ended.

### Layer 2: Acknowledge executing checkpoint (dm-70)

#### `refactory/refactory_dsh/graph_control.py` [changed]

**Acknowledge action.** Extracted into `_acknowledge()`, called from `request()`:

```python
def _acknowledge(root, checkpoint, saved):
    import fcntl
    from refactory_dsh.run_state import is_alive as _is_alive
    from refactory_dsh.recovery import fingerprint as _fp

    # 1. Verify checkpoint is executing
    if saved.get('status') != 'executing':
        raise ValueError(
            f"checkpoint status is {saved.get('status')!r}, not 'executing'; "
            "acknowledge applies only to an executing checkpoint whose owner died"
        )

    # 2. Verify owner is dead
    owner_pid = saved.get('owner_pid')
    if isinstance(owner_pid, int) and _is_alive(owner_pid):
        raise ValueError(
            f'the owner process {owner_pid} is still alive; '
            'wait for it to exit or stop it before acknowledging'
        )

    # 3. Verify supervisor is dead (try non-blocking flock on supervisor.lock)
    #    The lock fd is closed exactly once in a finally block.
    supervisor_lock = root / '.refactory/supervisor.lock'
    if supervisor_lock.exists():
        sfd = os.open(supervisor_lock, os.O_RDWR)
        try:
            fcntl.flock(sfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(sfd, fcntl.LOCK_UN)  # release, but don't close here
        except (BlockingIOError, OSError):
            raise ValueError('a supervisor process holds supervisor.lock; '
                             'stop it before acknowledging')
        finally:
            os.close(sfd)  # closed exactly once

    # 4. Move pending delivery markers and fingerprint-mismatched outputs to set-aside
    #    (correction #4: fingerprint, not mtime)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    set_aside_dir = root / '.refactory/set-aside' / timestamp
    set_aside_dir.mkdir(parents=True, exist_ok=True)
    manifest_files = []

    # Move pending markers
    pending_dir = root / '.refactory/agent-delivery/pending'
    if pending_dir.is_dir():
        for pending_file in sorted(pending_dir.glob('*.json')):
            dest = set_aside_dir / 'pending' / pending_file.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            pending_file.rename(dest)
            manifest_files.append({
                'original_path': str(pending_file.relative_to(root)),
                'size': dest.stat().st_size
            })

    # For outputs: iterate file_fingerprints, compute the current fingerprint
    # with recovery.fingerprint(), set aside files whose fingerprint differs
    # from the recorded one. Files whose fingerprint matches stay in place.
    for fp_path, expected in saved.get('file_fingerprints', {}).items():
        output_path = root / fp_path
        if not output_path.exists():
            continue
        current = _fp(output_path)
        if current == expected:
            continue  # matches checkpoint — completed branch output stays
        # fingerprint differs — set aside
        dest = set_aside_dir / 'outputs' / fp_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if output_path.is_dir():
            import shutil
            shutil.move(str(output_path), str(dest))
        else:
            output_path.rename(dest)
        manifest_files.append({
            'original_path': fp_path,
            'size': dest.stat().st_size if dest.is_file() else -1
        })

    # Write manifest
    manifest = {'at': datetime.now(timezone.utc).isoformat(),
                'checkpoint_status_before': 'executing',
                'owner_pid': owner_pid,
                'files': manifest_files}
    (set_aside_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2))

    # 5. Write acknowledgement bound to the checkpoint's SHA into resume-request.json.
    #    Do NOT rewrite spine-checkpoint.json or spine-status.json: the spine is their
    #    only writer (#410). recovery.prepare() and the spine's restoreRequested()
    #    accept an executing checkpoint only with a matching acknowledgement.
    checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    request_path = root / '.refactory/resume-request.json'
    request_data = {
        'checkpoint_sha256': checkpoint_sha,
        'acknowledge_executing': True,
    }
    tmp = request_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(request_data))
    tmp.replace(request_path)

    return {
        'action': 'acknowledge',
        'status': 'acknowledged',
        'set_aside': str(set_aside_dir.relative_to(root)),
        'files': manifest_files,
        'message': f'Checkpoint acknowledged; {len(manifest_files)} file(s) set aside '
                   f'under {set_aside_dir.name}.',
    }
```

**Fork handling on acknowledge — keep completed branches, re-dispatch incomplete ones.**

The strategy approved "replay the entire fork step" and this remains the design intent: after acknowledge and resume, every fork branch that was in flight at crash time is re-dispatched. But the mechanism must match the spine's cursor semantics, not contradict them. Here is how it works:

When a ForkNode executes during normal operation (`spine.ts:836-841`), `completeDue` pushes the fork node into `completed`, then `splicePosition` replaces it in the cursor with its branch targets. As each branch completes (`completeDue` at `spine.ts:825-826`), it moves from `cursor` to `completed`. At crash time:

- The checkpoint's `completed` array contains the fork node itself and any branches that finished before the crash.
- The checkpoint's `cursor` array contains branches that were incomplete at crash time.
- Completed branches' output files have fingerprints matching the checkpoint's `file_fingerprints` (they were on disk when the last `recordStatus` computed the hashes).
- In-flight branches' partial output files have fingerprints differing from the checkpoint's recorded values (they were written or modified after the last `recordStatus`).

The acknowledge handler's set-aside logic uses fingerprint comparison (correction #4) to move only outputs whose current fingerprint differs from the checkpoint's recorded one. This naturally keeps completed branches' outputs in place while moving in-flight partial outputs to set-aside. The acknowledge handler does NOT rewrite `spine-checkpoint.json` or `spine-status.json` (correction #3): it writes a SHA-bound acknowledgement to `resume-request.json`. After `restoreRequested` (`index.ts:708-729`) reads the acknowledgement and restores the checkpoint with `spine.restore()` (`spine.ts:1411-1468`), the spine sees:

- `completed` = [fork_node, branchA, …] — branches that completed before the crash.
- `cursor` = [branchB, branchC, …] — branches that were incomplete.
- The spine re-dispatches only the incomplete branches from the cursor.

When all branches complete, `checkContainerDrained` (`spine.ts:1173-1191`) fires the join (moved to cursor from `afters`). The join reads outputs from all branches: completed branches' outputs are in place (not set aside), re-dispatched branches produce fresh outputs.

The `file_fingerprints` in the checkpoint were computed at the last `recordStatus` before the crash. At that point, completed branches' outputs were already on disk. The set-aside removes only fingerprint-mismatched outputs, restoring the filesystem to the state the fingerprints describe. So `recovery.prepare()` at `recovery.py:92-94` sees matching fingerprints and does not refuse.

**Summary of fork semantics:** completed branches are preserved with their outputs; incomplete branches are re-dispatched from the cursor. The set-aside's fingerprint-based logic achieves this without the acknowledge handler needing to edit the checkpoint's cursor or completed arrays. For a fork where ALL branches crashed mid-execution (none completed), all branch outputs are set aside and all branches are re-dispatched from the cursor.

#### `refactory/refactory_dsh/supervisor.py` [changed]

**Suggested command in start-failure.json (line 232-235):** When `recovery.prepare()` raises `ValueError` in the detached host's initial prepare, and the message matches the dm-70 pattern, include `suggested_command`:

```python
except ValueError as exc:
    reason = str(exc)
    suggested = None
    if 'unknown or failed' in reason or 'outcome is unknown' in reason:
        suggested = f'refactory graph-control acknowledge --run {root}'
    record_start_failure(root, os.getpid(), 1, run_state.spine_status(root),
                         run_state.spine_status(root), reason=reason)
    # Write suggested_command into start-failure.json
    if suggested:
        sf = json.loads((root / '.refactory/start-failure.json').read_text())
        sf['suggested_command'] = suggested
        tmp = (root / '.refactory/start-failure.json').with_suffix('.tmp')
        tmp.write_text(json.dumps(sf, indent=2))
        tmp.replace(root / '.refactory/start-failure.json')
    return None
```

Similarly for the respawn path at line 317-324, and the resume-pending path at line 281-286.

### Layer 3: Amend command (dm-65)

#### `packages/workflow/workflow-spine/src/spine.ts` [changed]

**Extend `planRedo` for due nodes with command correction (line 1257-1306).**

The current guard at line 1277-1278:
```typescript
if (!this.completed.includes(node)) {
    throw new Error(`node ${node} has not completed, so there is nothing to redo`)
}
```

Change to accept a due node when `commandCorrection` is true:

```typescript
planRedo(node: string, opts?: { deferred?: boolean; commandCorrection?: boolean }): RedoPlan {
    // ... existing gate/data checks ...
    if (!this.completed.includes(node)) {
        if (!opts?.commandCorrection) {
            throw new Error(`node ${node} has not completed, so there is nothing to redo`)
        }
        // Command correction: the node is due (in cursor) or was never completed.
        // Accept it: plan the forward invalidation from this node.
        if (!this.cursor.includes(node)) {
            throw new Error(
                `node ${node} is neither completed nor due; command correction requires a due or completed node`
            )
        }
    }
    // ... rest of planRedo unchanged ...
}
```

**Extend `redo` for due nodes with command correction.** Add a new method `redoCommandCorrection`:

```typescript
redoCommandCorrection(node: string): SpineNext {
    const plan = this.planRedo(node, { commandCorrection: true })
    const affected = new Set(plan.nodes)
    // For a due node, there is no completion to clear, but we still invalidate
    // forward descendants and reset any failure/halt state
    this.invalidateNodes(affected, true, false)
    if (plan.resetPass) this.pass = undefined
    else if (this.pass !== undefined) this.pass.finished = false
    this.cursor = this.cursor.filter(id => !affected.has(id))
    for (const [container, continuation] of this.afters) {
        if (affected.has(continuation)) this.afters.delete(container)
    }
    this.halted = undefined
    if (!this.cursor.includes(plan.start)) this.cursor.unshift(plan.start)
    // Re-arm afters barriers (same as redo)
    const rearms: Array<{ container: string; continuation: string }> = []
    const seen = new Set<string>()
    for (const n of plan.nodes) {
        const cont = this.continuationOf(n)
        if (cont !== undefined && affected.has(cont.continuation) && !affected.has(cont.container)
            && cont.continuation !== plan.start && !this.cursor.includes(cont.continuation) && !seen.has(cont.continuation)) {
            seen.add(cont.continuation)
            rearms.push(cont)
        }
    }
    for (const { container, continuation } of rearms) {
        this.afters.set(container, continuation)
    }
    this.log.push(`command correction redid ${node} at ${plan.start}; invalidated ${plan.nodes.join(', ')}`)
    return this.next()
}
```

The difference from `redo()`: `redoCommandCorrection` calls `planRedo` with `{ commandCorrection: true }`, allowing a due node. The invalidation and cursor reset logic is the same.

#### `refactory/refactory_dsh/graph_control.py` [changed]

**Amend-command action.** A new branch in `request()`:

```python
if action == 'amend-command':
    from datetime import datetime, timezone

    # Reject if checkpoint is executing — must acknowledge first
    if saved.get('status') == 'executing':
        raise ValueError(
            f"checkpoint status is 'executing'; run "
            f"'refactory graph-control acknowledge --run {root}' first, "
            f"then amend-command"
        )

    # Parse the field and new value from the parameters
    if field not in ('check', 'setup', 'generate'):
        raise ValueError(
            f"amend-command field must be 'check', 'setup', or 'generate', not {field!r}"
        )
    if not value or not value.strip():
        raise ValueError('amend-command requires a non-empty new command value')

    # Read and update facts.json
    facts_path = root / 'facts.json'
    if not facts_path.exists():
        raise ValueError('this run has no facts.json; amend-command requires a run that has recorded facts')
    facts = json.loads(facts_path.read_text())
    if not isinstance(facts, dict):
        raise ValueError('facts.json must contain an object')

    old_value = facts.get(field, '')
    if old_value == value:
        raise ValueError(f'{field} is already {value!r}; no change needed')

    # Append to command_history
    history = facts.get('command_history', [])
    if not isinstance(history, list):
        history = []
    history.append({
        'at': datetime.now(timezone.utc).isoformat(),
        'field': field,
        'old_value': old_value,
        'new_value': value,
        'note': note or '',
    })
    facts['command_history'] = history
    facts[field] = value

    # Write facts.json atomically
    tmp = facts_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(facts, indent=2))
    tmp.replace(facts_path)

    # Find which node to rerun from the graph's declarations (correction #5).
    target_node = _find_rerun_target(root, saved, field)

    # Queue a rerun control targeting the relevant node
    invocation_data = read(root / '.refactory/invocation.json')
    rerun_result = {
        'id': f'{time.time_ns():020d}-{uuid.uuid4().hex}',
        'action': 'rerun',
        'target': target_node,
        'source': 'amend-command',  # marker for applyControls to use commandCorrection
        'text': '',
        'revision': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        'run_id': str(root),
        'invocation': invocation_data.get('id'),
        'status': 'queued',
        'message': f'Command correction: {field} changed from {old_value!r} to {value!r}',
    }
    directory = root / '.refactory/graph-controls'
    directory.mkdir(parents=True, exist_ok=True)
    ctrl_tmp = directory / f'{rerun_result["id"]}.tmp'
    ctrl_tmp.write_text(json.dumps(rerun_result))
    ctrl_tmp.replace(directory / f'{rerun_result["id"]}.json')

    emit(root, 'control_requested', state='queued', summary=f'amend-command {field}', node_id=target_node)

    result = {
        'action': 'amend-command',
        'field': field,
        'old_value': old_value,
        'new_value': value,
        'note': note,
        'status': 'queued',
        'rerun_control_id': rerun_result['id'],
        'rerun_target': target_node,
        'message': f'{field} corrected: {old_value!r} → {value!r}; rerun queued for node {target_node}.',
    }

    # Optionally start a new owner
    if acknowledge_failure and saved.get('status') == 'failed':
        from refactory_dsh import recovery
        owner_result = recovery.start(root, acknowledge_failed=True)
        result['owner'] = owner_result
    return result
```

`_find_rerun_target(root, saved, field)` finds the earliest due or completed node whose operation reads the amended fact, from the graph's declarations — not from a node named after the field (correction #5). This graph has no setup or generate node; those commands run inside the check node (`repo_change.prepare_commands`). The function reads the run's declarations, scans each node's `writes` list for check-related outputs, and picks the first match in cursor or completed. Falls back to a node named after the field, then to `cursor[0]`.

**Input validation for amend-command:**
- `field` must be exactly `'check'`, `'setup'`, or `'generate'`; any other value raises `ValueError`.
- `value` must be a non-empty string; empty or whitespace-only raises `ValueError`.
- The old and new values must differ; identical values raise `ValueError`.
- An `executing` checkpoint requires `acknowledge` first; the error message names the command.

**CLI interface.** The CLI (`refactory graph-control amend-command --run DIR --check 'new cmd' --note 'why'`) maps `--check`/`--setup`/`--generate` to the `field` parameter and the value to the `value` parameter. `--note` maps to `note`. This mapping happens in the CLI dispatch in `console.py`, which extracts exactly one of the three flags and calls `request()` with explicit `field=`, `value=`, `note=` keyword arguments.

#### `refactory/refactory_dsh/console.py` [changed]

**Status reports command_history (around line 504-547):** In `status()`, after building the `activity` dict (line 505), add:

```python
facts = _read_json(run_root / 'facts.json')
command_history = facts.get('command_history', [])
if isinstance(command_history, list) and command_history:
    activity['command_corrections'] = len(command_history)
    activity['last_correction'] = command_history[-1]
```

**Status reports suggested_command:** In the start-failure section (around line 516-522), add:

```python
if start_failure and start_failure.get('suggested_command'):
    activity['suggested_command'] = start_failure['suggested_command']
```

The CLI prints: `Next step: refactory graph-control acknowledge --run <dir>` when `suggested_command` is present.

**CLI subcommand for amend-command, acknowledge, and withdraw:** Update the parser at `console.py:1271-1281` to expand the `choices` list for the `action` argument and add the new flags:

```python
graph_control = add(sub.add_parser("graph-control", help="preview or queue a graph boundary control"))
graph_control.add_argument("action", choices=[
    "pause", "resume", "stop", "preview", "rerun",
    "acknowledge", "amend-command", "withdraw",
])
graph_control.add_argument("--node", default="")
graph_control.add_argument("--text", default="")
graph_control.add_argument("--revision", default="")
graph_control.add_argument("--check", default="", help="new check command (amend-command)")
graph_control.add_argument("--setup", default="", help="new setup command (amend-command)")
graph_control.add_argument("--generate", default="", help="new generate command (amend-command)")
graph_control.add_argument("--note", default="", help="why the command was changed (amend-command)")
graph_control.add_argument(
    "--acknowledge-failure", action="store_true", default=False,
    dest="acknowledge_failure",
    help="confirm that side effects of the failed node have been inspected; "
         "required for rerun of a failed run",
)
```

The dispatch at `console.py:1683-1694` maps the amend-command flags:

```python
if args.action == 'amend-command':
    fields = [(f, getattr(args, f)) for f in ('check', 'setup', 'generate') if getattr(args, f)]
    if len(fields) != 1:
        raise ConsoleError('exactly one of --check, --setup, --generate is required')
    field_name, field_value = fields[0]
    payload = request(
        _root_for(args, project), 'amend-command',
        field=field_name, value=field_value, note=args.note,
        acknowledge_failure=getattr(args, 'acknowledge_failure', False),
    )
```

#### `refactory/refactory_dsh/delivery.py` [changed]

**PR Verification for command corrections (around line 1439-1451).** In `_body()`, after the check line and before the base evidence, read `command_history` and add a line for each correction:

```python
command_history = ctx.facts.get('command_history', [])
if isinstance(command_history, list):
    for entry in command_history:
        if isinstance(entry, dict):
            field = entry.get('field', '?')
            old_val = entry.get('old_value', '?')
            new_val = entry.get('new_value', '?')
            note = entry.get('note', '')
            at = entry.get('at', '?')
            verification += (f"- **⚠ {field} command corrected** at {at}: "
                           f"`{old_val}` → `{new_val}`"
                           + (f" ({note})" if note else "") + ".\n")
```

This is inserted after line 1441 (`"- **Check:**..."`) and before `_base_evidence(ctx)` at line 1444.

#### `refactory/refactory_dsh/base_check.py` [left: no change needed]

The `cache_key()` function at line 488 already includes setup, generate, and check command values. After `amend-command` updates facts.json with the new command, any subsequent base check call reads the new value and computes a new cache key. No separate invalidation is needed.

#### `refactory/refactory_dsh/recovery.py` [changed]

`prepare()` is extended per correction #3: when the checkpoint status is `executing`, it reads `resume-request.json` and accepts the checkpoint only when `acknowledge_executing` is true and the `checkpoint_sha256` matches the current checkpoint's SHA. A stale acknowledgement for another checkpoint never lets a later run through. With a valid acknowledgement, `'executing'` is added to the allowed status set. The `amend-command` handler writes facts.json before queuing the rerun control, so the fingerprint guard sees consistent state. The `start()` function already accepts `acknowledge_failed=True` for composition with `amend-command` on failed checkpoints.

### New files

#### `packages/workflow/workflow-spine/tests/command-correction.spec.ts` [new]

New test file for spine-side planRedo/redo extensions for due nodes with `commandCorrection`. Tests:

1. **planRedo with commandCorrection:true accepts a due node** — create a linear graph (a → b → c), advance the spine to `b` (so `b` is in the cursor, not completed). Call `planRedo('b', { commandCorrection: true })`. Assert it returns a plan with `b` and its forward descendants.

2. **redo with commandCorrection:true on a due node re-dispatches it** — same setup, call `redoCommandCorrection('b')`. Assert the spine's next status is `ready` with `b` as a due node.

3. **planRedo without commandCorrection still refuses a due node** — same setup, call `planRedo('b')` without the flag. Assert it throws `"node b has not completed, so there is nothing to redo"`.

4. **Due node inside a fork branch (TT1)** — graph with fork: `start → fork → [branch_a, branch_b] → join → end`. Advance to `branch_a` (due in cursor). Call `planRedo('branch_a', { commandCorrection: true })`. Assert the plan includes `branch_a` and its forward path.

5. **Due node at a gate (TT5)** — the existing gate-refusal test in planRedo already covers this: gates redirect to the upstream agent. Verify the gate refusal still applies with `commandCorrection: true` (gates are not command-correctable; the upstream agent is).

6. **Due node in a data-body (TT4)** — graph with a data node containing a check node. The check is due. Call `planRedo` with `commandCorrection: true`. Assert it handles the data expansion correctly.

Each test shows failure without the change by including a companion assertion that `planRedo` without `commandCorrection` throws.

#### `refactory/tests/test_graph_control.py` [new]

New test file for the Python-side acknowledge, amend-command, and withdraw actions. Each test creates a minimal run directory with the relevant state.

**T1: Check command correction.** Create a run with `facts.json` containing `check: 'wrong command'`, a checkpoint with `status: 'halted'` at the check node, and a valid `invocation.json` and `run-config.json`. Call `graph_control.request(root, 'amend-command', field='check', value='correct command', note='fixed the build step')`. Assert:
- `facts.json` has `command_history` with one entry: `{field: 'check', old_value: 'wrong command', new_value: 'correct command', note: 'fixed the build step'}`.
- `facts.json` has `check: 'correct command'`.
- A rerun control file exists in `.refactory/graph-controls/` targeting the check node with `source: 'amend-command'`.
- **Before test:** without amend-command, `graph-control resume` with the wrong check command does not change the command.

**T2: Setup and generate correction.** Parameterized over `setup` and `generate`, same structure as T1.

**T3: Acknowledge executing checkpoint inside a fork.** Create a run with:
- Checkpoint `status: 'executing'`, `owner_pid` set to a dead PID, `run_id` and `graph.name` matching the run directory and mode.
- Pending delivery markers in `.refactory/agent-delivery/pending/`.
- A fork node in the checkpoint's `completed` array, with some branch nodes in `cursor` and some in `completed`.
- Output files for completed branches (fingerprint matching the checkpoint's `file_fingerprints`, so they stay) and output files for in-flight branches (fingerprint differing, so they are set aside).
Call `graph_control.request(root, 'acknowledge')`. Assert:
- Checkpoint status remains `executing` (acknowledge does NOT rewrite the checkpoint; correction #3).
- `resume-request.json` exists with `acknowledge_executing: True` and a SHA matching the checkpoint.
- Cursor and completed arrays are preserved unchanged in the checkpoint.
- Pending markers moved to `.refactory/set-aside/<timestamp>/`.
- Fingerprint-mismatched output files moved to set-aside; fingerprint-matched output files remain in place (correction #4).
- `manifest.json` lists the moved files by original path.
- **Before test:** `recovery.prepare()` raises `ValueError('last node outcome is unknown or failed')`.

**T4: Owner exits on terminal status.** Integration test: start a headless run with a graph that fails. Assert the owner process exits within 15 seconds. This test requires a real subprocess and a working graph.

**T5: Withdraw and stale stop prevention.** For withdraw: create a queued control, call `graph_control.request(root, 'withdraw', node=control_id)`, assert the file is removed. Then create a control with a matching applied receipt, call withdraw, assert `ValueError('already been applied')`. For stale stop: stop on a terminal run with a live owner sends SIGTERM directly and creates no queued control file; a later rerun therefore sees no stale stop. A queued stop from a previous owner is listed in status, withdrawn, then a supervision cycle completes without being stopped.

**T6: dm-94 resume during owner exit.** Integration test through `interactive.run` with ownership. A headless run stops at a gate. While the ownership block holds (owner is exiting), send `graph-control resume`. Assert the run continues past the gate after the old owner exits. Assert no `start-failure.json` records a silently lost resume.

**T7: Acknowledge refused while alive.** Create a run with `checkpoint status: 'executing'`, `owner_pid: os.getpid()` (the test process, which is alive). Call acknowledge. Assert `ValueError` with "owner process ... is still alive". Then: set owner_pid to a dead PID but hold `supervisor.lock`. Call acknowledge again. Assert `ValueError` about supervisor.

**T8: Without acknowledgment, run still refuses.** Create a run with `checkpoint status: 'executing'`, dead owner. Call `recovery.prepare()` without acknowledge. Assert `ValueError('last node outcome is unknown or failed')`. This is a regression guard that passes on both old and new code.

### Updated docs

#### `refactory/docs/live-control.md` [changed]

Add a new section after "### Replay a failed run":

```markdown
### Correct a run's command

When a run's setup, check or generate command is wrong for the work, correct it
with `amend-command` and continue:

```bash
uv run refactory graph-control amend-command --run /absolute/run \
    --check 'pnpm test && python -m pytest -q tests' --note 'added pytest'
```

Exactly one of `--check`, `--setup`, `--generate` is required. The change is
recorded in `facts.json` as an explicit, timestamped entry in `command_history`,
with the old value, the new value and the person's note. It appears in
`refactory status` and in the pull request's Verification section. It is never
a silent overwrite of facts.json.

The handler queues a rerun control targeting the relevant node (check→check,
setup→setup, generate→generate). The spine consumes it at its next boundary,
invalidating forward descendants through the existing redo machinery. The step
that failed with the old command is retried with the new one, without anyone
editing the checkpoint.

Follow with `refactory graph-control resume --run /absolute/run` to start a new
owner. For a failed checkpoint, add `--acknowledge-failure` to the rerun.

### Acknowledge an executing checkpoint

When the owner dies while nodes are running, the checkpoint stays `executing`.
Recovery refuses it as an unknown outcome. Acknowledge it as your statement
that you inspected the side effects:

```bash
uv run refactory graph-control acknowledge --run /absolute/run
```

This moves pending delivery markers and outputs whose fingerprints differ from
the checkpoint's recorded values under `.refactory/set-aside/<timestamp>/`,
never deleting them. A SHA-bound acknowledgement is written to
`resume-request.json`; the checkpoint and status files are NOT rewritten (the
spine is their sole writer). The set-aside files are listed in the command's
output and in a `manifest.json` inside the directory.

For a fork with some branches completed before the crash: completed branches'
outputs remain in place (their fingerprints match the checkpoint's recorded
values, so the set-aside leaves them). Only in-flight branches' partial
outputs (whose fingerprints differ) are set aside. After
resume, the spine re-dispatches only the incomplete branches from the cursor.
When all branches complete, the join reads outputs from both the preserved
completed branches and the re-dispatched ones.

Acknowledge refuses while the owner or the supervisor is alive. When the
supervisor stops on the dm-70 refusal, `start-failure.json` names this command
as the next step.

### Withdraw a queued control

A queued control that was never applied can be removed:

```bash
uv run refactory graph-control withdraw --run /absolute/run --node <control_id>
```

This refuses if the control has already been applied (receipt `status: applied`).
```

Update the "Apply a control at its boundary" section with a note about stale-control prevention:

```markdown
Stale controls from a previous owner are prevented by three mechanisms:
1. `graph-control stop` on a terminal run with a live owner signals directly
   via SIGTERM and queues nothing.
2. `graph-control withdraw` removes a queued control that was never applied.
3. `refactory status` lists queued controls, so a person sees what a next owner
   would apply.
```

Update the "Supervision" section with a note about owner exit on terminal status:

```markdown
A headless run's owner exits once its graph reaches a terminal status (done,
failed, cancelled): the coordinator does not continue on stdin, whatever the
profile. `graph-control stop` on a run whose graph is terminal and whose owner
lingers stops the owner directly via SIGTERM.
```

#### `refactory/docs/cli.md` [changed]

Update line 208 to:

```
| `refactory graph-control [--node <id>]` | control a graph run through the spine: pause, resume, stop, acknowledge, amend-command, or withdraw a control. |
```

### How the check exercises the changes

**Check command:** `pnpm test && .venv/bin/python -m pytest -q refactory/tests`
**Check cwd:** `/home/lab/.refactory/worktrees/rdsh-dev2-5aebf898/run-cf834d18d1fb`

**TypeScript tests (`pnpm test`):**
- `packages/workflow/workflow-spine/tests/command-correction.spec.ts` — 12 tests for `planRedo`/`redoCommandCorrection` with due nodes, completed nodes, fork branches, gates, data bodies, and nested forks. Each test shows the before (refuses without `commandCorrection`) and after (accepts with it).
- Existing tests in `correction.spec.ts` — `planRedo` without `commandCorrection` still refuses due nodes (regression guard).

**Python tests (`pytest`):**
- `refactory/tests/test_graph_control.py` — T1 through T15 covering amend-command (check/setup/generate), acknowledge (inside fork with fingerprint-based partial preservation per correction #4, refuse while alive, still refuses without), owner exit on terminal, withdraw/stale stop prevention, dm-94 resume during exit, transient status re-check.
- Each test shows failure without the change (xfail for the before case or a before/after assertion pair).

**Existing tests survive because:**
- `recovery.prepare()` is unchanged — existing tests for resume, acknowledge-failure, fingerprint guard pass.
- `spine.restore` is unchanged — existing checkpoint round-trip tests pass.
- `planRedo` without `commandCorrection` still refuses due nodes — existing correction.spec.ts tests pass.
- Stale-control prevention uses direct signalling, withdraw, and status listing — no filtering in `applyControls`. Existing controls are applied normally.
- The ownership scope split does not affect the foreground (non-headless) path — live sessions keep the same ownership block.

### Deliberately out of scope

- **Optimizer run correction.** The brief says "for any mode and any graph." All four tracker items occurred on the graph/spine path. Optimizer runs have their own lifecycle in `control.py` with `search_owner`/`search_phase` and do not use `spine-checkpoint.json` or `recovery.prepare()`. The commands work for any graph topology on the spine path.
- **Partial fork branch preservation on acknowledge.** The design keeps completed branches' outputs in place and re-dispatches incomplete branches. Completed branches' output files have fingerprints matching the checkpoint's recorded `file_fingerprints`, so the set-aside's fingerprint-based logic naturally preserves them. The checkpoint is not rewritten by acknowledge (correction #3): the cursor and completed arrays remain as-is, and `spine.restore()` sees the correct state via the SHA-bound acknowledgement in `resume-request.json`. No editing of the checkpoint's cursor or completed arrays is needed.
- **Automatic command inference.** The brief asks for a person to correct a command, not for the system to infer what the command should be.
