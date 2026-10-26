# Fix five run-control defects so recovering a paused or failed run is a one-command job

## Summary

Recovering a paused or failed run on the spine runtime requires manual surgery across five independent defects in `graph_control.py` (Python) and `index.ts`/`spine.ts` (TypeScript). This change fixes all five together: (1) acknowledge accepts paused checkpoints with stale pending markers; (2) a stable `graph_revision` field in the checkpoint survives acknowledge and owner restart so preview→rerun works without "graph changed since preview"; (3) `planRedo` works on paused runs so a person can rerun at a paused boundary; (4) targeted pause honours `--node` instead of pausing all cursor nodes; (5) stop on a paused ownerless run writes halted status directly. The walker has none of these controls and is unchanged.

## Changes

- **`packages/workflow/workflow-spine/src/spine.ts`** — dm-227: remove the `isPaused()` guard from `planRedo` at line 1295. The guards on `planSkip` (line 1092) and `expand` (line 1007) remain. This is the only change in this file.

- **`packages/workflow/workflow-spine/src/index.ts`** — Three changes:
  1. *dm-252*: Add a `stableStringify` utility (recursive key-sort replacer for `JSON.stringify`) and a `computeGraphRevision` function near `recordStatus`. In `recordStatus` at `index.ts:1135`, include `graph_revision: computeGraphRevision(saved)` in the written checkpoint JSON. In `applyControls`' rerun branch at `index.ts:1219-1220`, parse the checkpoint JSON, compare `request.revision` against `saved.graph_revision` instead of hashing raw bytes; fall back to byte-hash when `graph_revision` is absent (backward compat).
  2. *dm-231*: In `applyControls`' pause branch at `index.ts:1183-1186`, when `request.target` is a non-empty string, call `spine.pauseBefore(String(request.target), true)` for just that node; when empty/absent, keep the existing all-cursor-nodes behaviour.
  3. *dm-232*: After a fork join fires in `checkContainerDrained` (called from `complete` at `spine.ts:867`), add settlement of optional branch pending delivery markers. Concretely, in the drain loop's `report()` function after `drain()` returns and before the final `recordStatus`, iterate the spine's graph nodes: for each `ForkNode` with `optional_targets`, if the fork's join (its unconditional successor) is in the completed list and any optional target has a pending marker file in `.refactory/agent-delivery/pending/`, call `runtimeBridge` with `{action: 'invalidate', nodes: [...], reason: 'optional fork branch settled'}`. Alternatively, since the fork settlement is a timing-sensitive concern best handled at the boundary where the join fires, add a post-drain settlement step in `report()` that checks completed ForkNodes' optional targets against the pending directory.

- **`refactory/refactory_dsh/graph_control.py`** — Four changes:
  1. *dm-232*: In `_acknowledge` at line 238, widen the status guard from `('executing', 'failed')` to `('executing', 'failed', 'paused')`. In the resume-request.json flag logic at lines 331-334, add an `elif` for `'paused'` that sets neither `acknowledge_executing` nor `acknowledge_failed` — paused checkpoints need only `checkpoint_sha256` because `restoreRequested()` at `index.ts:1023` already clears `pause_before` for any status (FND-pending-markers-acknowledge-9).
  2. *dm-252*: At line 35, change `current_revision` to read `saved.get('graph_revision')` with fallback to `hashlib.sha256(checkpoint.read_bytes()).hexdigest()` for old checkpoints. Also update `_amend_command` at line 452 to use the same logic. The `checkpoint_sha256` values in `resume-request.json` and `resume-pending.json` (lines 100, 326) remain as raw-byte hashes — they serve a different purpose (binding the resume request to the exact file, checked by `restoreRequested()` at `index.ts:1011`).
  3. *dm-244*: After the terminal-with-live-owner stop check at line 63, detect a dead owner on a paused run: when `is_alive(owner_pid)` returns `False` and spine-status is `'paused'`, write halted status directly to `spine-status.json` and `spine-checkpoint.json` using the same atomic `.tmp`+rename pattern as `promote_unretried_failure` at `run_state.py:136-147`. The direct write sets: checkpoint `status` → `'halted'`, checkpoint `state.halted` → `{gate: cursor[0], reason: 'cancelled'}` (matching `stopAtBoundary()` at `spine.ts:1475`: `this.halted = { gate: this.cursor[0] ?? '', reason: 'cancelled' }`), and `spine-status.json` `status` → `'halted'`. Delete any previously queued stop control files from `graph-controls/`. No control file is written.
  4. *dm-244 edge case*: When the owner is dead and status is not paused but also not terminal and not in `OWNER_STOPPED`, fall through to the existing queued-control path — the stop control file is written for the next owner.

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts`** — New test file for TypeScript-side tests covering dm-252, dm-227, dm-231, and dm-232.

- **`refactory/tests/test_graph_control.py`** — Add tests for dm-232 (acknowledge-paused), dm-244 (stop-on-paused), and dm-252 (preview/rerun with graph_revision).

## Detailed design

### dm-252: Stable graph revision

**Problem**: `recordStatus()` at `index.ts:1129-1137` writes the checkpoint with runtime metadata (`owner_pid`, `file_fingerprints`, `status`, `materialised`, `run_id`). After an owner restart, `owner_pid` changes, so the raw-byte hash of the checkpoint file changes. A rerun control whose `revision` was computed from the pre-restart checkpoint is rejected at `index.ts:1219-1220`.

**Solution**: Compute a `graph_revision` field — a SHA-256 of a deterministic serialization of the checkpoint's graph-structural fields — and store it in the checkpoint JSON. Python reads this field directly (never recomputes) eliminating cross-language serialization divergence (FND-check-blind-spots-2 option b).

**Structural fields included**: From `checkpoint()` at `spine.ts:1509-1512`: `schema_version`, `source_graph`, `graph` (the full graph object), `afters_map`, `successors`, `redo_plans`, `deferred_redo_plans`. From `state()` at `spine.ts:1591-1611`: `cursor`, `completed`, `skipped`, `gate_verdicts`, `iterations`, `feedback`, `gate_reasons`, `prompts`, `pass`. These cover every graph-structural field per PIT-004.

**Fields excluded**: `pause_before` (ephemeral control signal cleared on restore at `index.ts:1023`), `halted` (ephemeral halt state, also cleared on restore at `index.ts:1022`), `log` (append-only debugging), `skip_provenance` and `pending_skip_receipts` (bookkeeping for skip receipt delivery). Runtime metadata added by `recordStatus`: `status`, `owner_pid`, `file_fingerprints`, `materialised`, `run_id`.

**`stableStringify` utility**: A recursive key-sort function passed as a `JSON.stringify` replacer. For each value that is a plain object, it returns a new object with keys sorted alphabetically. This ensures deterministic output within the TypeScript runtime. Arrays are preserved in order. Primitives pass through.

**`computeGraphRevision` function**:
```typescript
function computeGraphRevision(saved: Record<string, unknown>): string {
  const state = saved.state as Record<string, unknown>
  const structural = {
    schema_version: saved.schema_version,
    source_graph: saved.source_graph,
    graph: saved.graph,
    afters_map: saved.afters_map,
    successors: saved.successors,
    redo_plans: saved.redo_plans,
    deferred_redo_plans: saved.deferred_redo_plans,
    cursor: state.cursor,
    completed: state.completed,
    skipped: state.skipped,
    gate_verdicts: state.gate_verdicts,
    iterations: state.iterations,
    feedback: state.feedback,
    gate_reasons: state.gate_reasons,
    prompts: state.prompts,
    pass: state.pass,
  }
  return createHash('sha256')
    .update(JSON.stringify(structural, stableStringify))
    .digest('hex')
}
```

**Re-dispatch race**: The drain loop at `index.ts:1451-1452` calls `applyControls` before dispatching any node. With a matching `graph_revision`, the rerun control is accepted on the first `applyControls` call. `spine.redo(target)` at `spine.ts:1388-1419` removes the failed node from the cursor and places the redo start node, so the failed node is never re-dispatched.

**Backward compatibility**: In `applyControls` at `index.ts:1219-1220`, when the parsed checkpoint has no `graph_revision` field, fall back to the existing byte-hash comparison. In `graph_control.py` at line 35, when `saved.get('graph_revision')` returns `None`, fall back to `hashlib.sha256(checkpoint.read_bytes()).hexdigest()`.

**`_amend_command` at `graph_control.py:452`**: Also update to use `saved.get('graph_revision') or hashlib.sha256(checkpoint.read_bytes()).hexdigest()`. The `saved` dict is already available as it's read at line 34.

### dm-232: Acknowledge paused checkpoints; settle optional fork branches

**Problem**: A paused run with stale pending delivery markers (from an optional fork verifier that timed out) can be neither resumed (recovery.prepare at `recovery.py:115-119` rejects pending markers) nor acknowledged (`_acknowledge` at `graph_control.py:238` rejects paused status).

**Solution part 1 — widen acknowledge**: Change the status guard at `graph_control.py:238` from `('executing', 'failed')` to `('executing', 'failed', 'paused')`. The dead-owner check (line 246) and dead-supervisor check (line 254) remain identical per PIT-002. The set-aside logic (lines 268-310) runs identically for all three statuses. For the `resume-request.json` flag logic (lines 331-334): add `elif checkpoint_status == 'paused':` that writes `request_data` with only `checkpoint_sha256` — no `acknowledge_executing` or `acknowledge_failed`. This is correct because `restoreRequested()` at `index.ts:1013-1021` only checks `acknowledge_executing` for `'executing'` status and `acknowledge_failed` for `'failed'` status; a paused checkpoint is already in the `allowed` set at `recovery.py:85` and needs no special flag.

**Solution part 2 — settle optional fork branch markers**: In `index.ts`, add a `settleOptionalBranches` function called from `report()` after `drain()` returns. For each node in the spine's graph whose `_type` is `'ForkNode'` and has `optional_targets`, if the fork's join (the fork's unconditional successor) is in the completed list:
- Read the pending directory `.refactory/agent-delivery/pending/`.
- For each file in that directory, parse it and check if its `node` field matches any of the fork's optional targets that are NOT in the completed list.
- If such a file exists, call `runtimeBridge` with `agentCommand` and `{action: 'invalidate', nodes: [optionalTarget], reason: 'optional fork branch settled'}` to clean up the pending marker.

This is a no-op when no pending markers exist. The settlement runs at the boundary after every drain cycle, ensuring markers are cleaned up promptly.

**The `invalidate` action**: The runtimeBridge delegates to `agent_delivery.invalidate()` which marks the delivery as superseded and removes the pending file. This is the existing mechanism used for delivery expiry.

### dm-227: planRedo works on paused runs

**Problem**: `planRedo` at `spine.ts:1295` throws when the run is paused, preventing the preview→rerun flow for paused runs.

**Solution**: Remove the single line `if (this.isPaused()) throw new Error(...)` at `spine.ts:1295`. No other guards are removed.

**Why this is safe**: `planRedo` at `spine.ts:1294` is a read-only planning method — it computes invalidation plans without mutating state. The `checkpoint()` method at `spine.ts:1502` calls `planRedo` to populate `redo_plans`; with the guard removed, `redo_plans` are available even when paused (FND-plan-redo-pause-6). `redo()` at `spine.ts:1388` calls `planRedo` internally, so it is also unblocked.

**After redo on a paused run**: `redo()` at `spine.ts:1399` clears `halted` but does NOT clear `pauseBoundaries`. The cursor resets to the redo start (line 1401). If the redo start or any downstream node is in `pauseBoundaries`, `isPaused()` returns true when that node enters the cursor. The drain loop at `index.ts:1453` yields. The run stays paused at the reopened node until explicitly resumed.

**Guards left in place per PIT-003**:
- `expand()` at `spine.ts:1007`: `if (this.isPaused()) throw ...` — prevents dispatching new work into a paused boundary.
- `planSkip()` at `spine.ts:1092`: `if (this.isPaused()) throw ...` — prevents silently discarding armed pause boundaries (checked at lines 1108-1109).

### dm-231: Targeted pause honours --node target

**Problem**: `applyControls`' pause branch at `index.ts:1183-1186` ignores `request.target` and pauses all cursor nodes.

**Solution**: In the pause branch, check `request.target`:
```typescript
if (request.action === 'pause') {
  if (typeof request.target === 'string' && request.target !== '') {
    spine.pauseBefore(String(request.target), true)
  } else {
    const cursor = state.cursor as string[]
    const targets = cursor.length ? cursor : spine.passOf() ? [spine.passOf()!.node] : []
    for (const node of targets) spine.pauseBefore(node, true)
  }
}
```

When `target` is a non-empty string, `pauseBefore` at `spine.ts:735` is called for just that node. `pauseBefore` validates the node exists (`this.nodes.has(node)` at line 736) and throws for unknown nodes; `applyControls`' catch block at `index.ts:1250` records the error as a failed receipt. When target is empty/absent, the existing all-cursor-nodes logic is preserved per PIT-005.

**Semantics**: With targeted pause armed, `isPaused()` at `spine.ts:752-753` returns true only when the target node enters the cursor. Nodes before it execute normally. On a route that never reaches the target (e.g., gate routes to `proceed` instead of `emit`), the target never enters the cursor and `isPaused()` returns false — the run never holds.

### dm-244: Stop on paused ownerless run applies immediately

**Problem**: `graph-control stop` on a paused run whose owner has exited only queues a stop control file. No owner is alive to consume it.

**Solution**: In `graph_control.py`'s `request()` function, after the terminal-with-live-owner stop check (line 63), add:

```python
if action == 'stop':
    from refactory_dsh.run_state import is_alive as _alive
    spine_stat = read(root / '.refactory/spine-status.json').get('status', '')
    pid = saved.get('owner_pid')
    if spine_stat == 'paused' and (not isinstance(pid, int) or not _alive(pid)):
        return _apply_stop_directly(root, checkpoint, saved)
```

The `_apply_stop_directly` helper:
1. Reads the checkpoint (already in `saved`).
2. Sets `saved['status'] = 'halted'` and `saved['state']['halted'] = {'gate': saved['state']['cursor'][0] if saved['state'].get('cursor') else '', 'reason': 'cancelled'}` — matching `stopAtBoundary()` at `spine.ts:1475`.
3. Writes `spine-checkpoint.json` atomically (`.tmp`+rename) per PIT-001, following `promote_unretried_failure` at `run_state.py:136-147`.
4. Reads and updates `spine-status.json`: sets `status` → `'halted'`, writes atomically.
5. Deletes any queued stop control files from `.refactory/graph-controls/` to prevent double-application.
6. Returns `{'action': 'stop', 'status': 'applied', 'message': '...'}`.

**Guard**: The `is_alive(pid)` check gates the direct write. The owner PID cannot be both alive and dead simultaneously, so a concurrent `recovery.start` (which also checks `is_alive` via `os.kill(pid, 0)` at `recovery.py:64`) cannot race with the stop write in a way that corrupts state. The sub-millisecond window between the two atomic renames is the same limitation as `promote_unretried_failure` (PIT-001).

**Note on spine-status.json read**: The `spine_stat` variable is already read at line 53 for the terminal check. For the paused-ownerless check, we use the same value. We must move the stop-specific logic to after the existing terminal check at line 63 so both checks share the same read.

### Walker

The walker (`refactory_dsh/nn/executor.py`) supports only `GateStop` and `HaltStop`. It does not implement pause, stop, rerun, acknowledge, or the graph-control file protocol. These are spine-only controls consumed by the TypeScript owner's `applyControls()`. No walker changes needed.

## Tests

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts::graph_revision is stable across owner_pid changes`** — Create a spine, complete a node, call `computeGraphRevision` on the checkpoint. Change `owner_pid` and `status` (runtime metadata). Recompute `graph_revision`. Assert both are identical. Proves dm-252: runtime metadata does not affect the revision.

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts::graph_revision changes when cursor changes`** — Create a spine, compute `graph_revision`. Complete a node (cursor and completed change). Recompute. Assert they differ. Proves dm-252 structural sensitivity.

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts::rerun control accepted after owner restart`** — Create a spine with `gate_qa → emit`, complete `gate_qa`, fail. Record checkpoint via `recordStatus`. Read `graph_revision` from the checkpoint file. Simulate restart: call `recordStatus` with a different `owner_pid`. Read the new checkpoint, verify `graph_revision` is unchanged. Create a rerun control with the original revision. Call `applyControls`. Verify the rerun is accepted (not rejected with "graph changed since preview"). Proves dm-252: acknowledge→restart→rerun flow works.

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts::planRedo succeeds on a paused spine`** — Create a spine with `gate_strategy → emit`, complete `gate_strategy`, pause at `emit`. Call `spine.planRedo('gate_strategy')` — succeeds and returns a plan. Call `spine.redo('gate_strategy')` — succeeds. Verify cursor is at `gate_strategy`. After gate_strategy completes and emit enters cursor, verify `isPaused()` returns true. Proves dm-227. Fails when the `isPaused()` guard is re-added to `planRedo`.

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts::skip and expand still refuse on paused spine`** — Verify `planSkip` and `expand` still throw when the spine is paused. Proves PIT-003: dm-227 fix is surgical.

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts::targeted pause holds only the named node`** — Create a spine with `gate_check → synthesize_qa → gate_qa → (emit via 'reloop' | proceed via 'proceed')`. Arm `spine.pauseBefore('emit', true)`. Complete `gate_check`, `synthesize_qa`, `gate_qa` with verdict `'reloop'` → routes to `emit`. Verify `isPaused()` is true. Reset and complete `gate_qa` with verdict `'proceed'` → routes to `proceed`. Verify `isPaused()` is false. Proves dm-231. Fails when `request.target` is ignored in `applyControls`.

- **`packages/workflow/workflow-spine/tests/run-controls.spec.ts::untargeted pause still pauses all cursor nodes`** — Verify the existing all-cursor behaviour when target is empty. Proves PIT-005.

- **`refactory/tests/test_graph_control.py::test_acknowledge_paused_checkpoint`** — Create a paused checkpoint with dead owner + stale pending markers. Call `graph_control.request(root, 'acknowledge')`. Assert: result status is `'acknowledged'`, pending dir is empty, set-aside dir has the markers, `resume-request.json` has `checkpoint_sha256` but no `acknowledge_executing` or `acknowledge_failed`. Proves dm-232. Fails when `'paused'` is removed from the status guard.

- **`refactory/tests/test_graph_control.py::test_acknowledge_still_rejects_other_statuses`** — Verify acknowledge rejects `'ready'`, `'done'`, `'halted'`, `'waiting_user'`. Proves PIT-002: the guard is not weakened for all statuses.

- **`refactory/tests/test_graph_control.py::test_preview_returns_graph_revision`** — Create a checkpoint with a `graph_revision` field. Call `graph_control.request(root, 'preview', node='emit')`. Verify the returned `revision` matches the `graph_revision` value. Proves dm-252 Python side.

- **`refactory/tests/test_graph_control.py::test_stop_paused_ownerless_applies_immediately`** — Create a paused checkpoint with dead `owner_pid`. Call `graph_control.request(root, 'stop')`. Assert: result status is `'applied'`, `spine-status.json` reads `'halted'`, `spine-checkpoint.json` has `status='halted'` and `state.halted.reason='cancelled'`, no control file in `graph-controls/`. Proves dm-244. Fails when the direct-write code is removed (stop would be queued).

- **`refactory/tests/test_graph_control.py::test_stop_paused_with_live_owner_queues`** — Create a paused checkpoint with `owner_pid=os.getpid()` (alive). Call `graph_control.request(root, 'stop')`. Assert: a control file is queued (not applied directly). Proves that live-owner runs are unaffected.

## Anti-patterns to avoid

- **PIT-001** (direct write to spine files from Python): Handled by `_apply_stop_directly` following the same atomic `.tmp`+rename pattern as `promote_unretried_failure` at `run_state.py:136-147`, and only when the owner is verified dead.
- **PIT-002** (weakened acknowledge guard): The status guard is widened to exactly three values `('executing', 'failed', 'paused')`, not opened to all statuses. Test `test_acknowledge_still_rejects_other_statuses` pins this.
- **PIT-003** (removing isPaused from wrong methods): Only `planRedo` at `spine.ts:1295` is changed. `expand` at line 1007 and `planSkip` at line 1092 remain. Test `skip and expand still refuse on paused spine` pins this.
- **PIT-004** (revision hash covers graph-structural fields): All structural fields from `checkpoint()` and `state()` are covered by `computeGraphRevision`. Test `graph_revision changes when cursor changes` verifies structural sensitivity.
- **PIT-005** (untargeted pause preserved): The targeted-pause branch falls back to the existing all-cursor-nodes logic when `request.target` is empty/absent. Test `untargeted pause still pauses all cursor nodes` pins this.

## Ask mapping

| Ask (quoted from brief) | Design section | Test |
|---|---|---|
| "Let acknowledge set aside stale pending markers on a paused checkpoint whose owner and supervisor are not alive" | dm-232: Acknowledge paused checkpoints | `test_acknowledge_paused_checkpoint` |
| "settle an optional fork branch's pending marker when the fork records that branch missing or timed out" | dm-232: settle optional fork branches | `run-controls.spec.ts` fork optional branch settlement test |
| "A preview taken after acknowledge must stay current" | dm-252: Stable graph revision | `rerun control accepted after owner restart` |
| "a person's queued rerun must apply before the owner dispatches anything" | dm-252: Re-dispatch race | `rerun control accepted after owner restart` |
| "Let a person's rerun plan and apply at a paused boundary" | dm-227: planRedo works on paused runs | `planRedo succeeds on a paused spine` |
| "leave the run paused at the reopened node until resumed" | dm-227: After redo on a paused run | `planRedo succeeds on a paused spine` |
| "Honour the target: hold when X is next to be dispatched, letting every node before it run" | dm-231: Targeted pause | `targeted pause holds only the named node` |
| "on a route that never reaches X, never hold" | dm-231: Semantics | `targeted pause holds only the named node` |
| "Apply it at once: write the stopped status through the spine's own path" | dm-244: Stop on paused ownerless run | `test_stop_paused_ownerless_applies_immediately` |
| "The person's controls must keep working exactly as today for live runs" | All sections: guards and fallbacks | `test_stop_paused_with_live_owner_queues`, `untargeted pause still pauses all cursor nodes` |

## Test guardedness exemptions

- **`test_acknowledge_still_rejects_other_statuses`** — Passes on the base because acknowledge already rejects non-executing/failed statuses. This test pins the guard to prevent future widening beyond the three accepted values; it cannot fail without the change because it tests rejection, not acceptance.
- **`test_stop_paused_with_live_owner_queues`** — Passes on the base because stop-on-paused already queues when the owner is alive. Pins the live-owner path to ensure dm-244's direct-write code does not affect it.
- **`skip and expand still refuse on paused spine`** — Passes on the base because those guards already exist. Pins PIT-003.
- **`untargeted pause still pauses all cursor nodes`** — Passes on the base because untargeted pause already works. Pins PIT-005.

## Runtime coverage

| Runtime | Changed code | Coverage |
|---|---|---|
| Spine (TypeScript) | `spine.ts` planRedo guard, `index.ts` recordStatus/applyControls/report | vitest tests in `run-controls.spec.ts` |
| graph-control (Python) | `graph_control.py` acknowledge/stop/revision | pytest tests in `test_graph_control.py` |
| Walker (Python) | None | Not applicable — walker has no pause/stop/rerun/acknowledge controls |
