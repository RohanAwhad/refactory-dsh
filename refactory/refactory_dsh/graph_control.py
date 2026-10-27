"""Queue controls for the graph owner; a reader never executes a node."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from datetime import datetime, timezone

from refactory_dsh.live import emit, read
from refactory_dsh import recovery

#: How long a resume waits for an exiting owner at a terminal status.
#: Short enough that the CLI stays responsive; long enough to cover
#: the normal exit delay after a headless run prints its next-commands.
_OWNER_EXIT_TIMEOUT = 3.0

#: Spine statuses an owner does not go on from by itself (a headless run records
#: `refused` at a user gate); its owner exits after printing the next commands.
OWNER_STOPPED = frozenset({'done', 'halted', 'failed', 'refused', 'cancelled'})

#: Stopped checkpoint statuses recovery accepts, which acknowledge also takes when a
#: dispatched worker's pending marker is what keeps recovery from them (dm-275).
STOPPED_WITH_MARKERS = frozenset({'ready', 'halted'})


def request(root: Path, action: str, *, node: str = '', text: str = '', revision: str = '',
            acknowledge_failure: bool = False,
            field: str = '', value: str = '', note: str = '') -> dict[str, Any]:
    root = Path(root).resolve()
    checkpoint = root / '.refactory/spine-checkpoint.json'
    if not checkpoint.exists():
        from refactory_dsh import run_state
        if run_state.walked(root):
            # The walker's controls are its main's own flags (dm-160): name them.
            config = read(root / '.refactory/run-config.json')
            resume = config.get('resume_command') or f'<the main that started it> --resume {root}'
            raise ValueError(f'this run is walked, and the walker has no graph controls; continue it with '
                             f'`{resume}`, adding --acknowledge-failure once you have inspected a failed '
                             'or unknown boundary')
        raise ValueError('this run has no durable graph checkpoint; controls require its owning runtime')
    saved = read(checkpoint)
    current_revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()

    # ── acknowledge: transition executing → halted via a SHA-bound acknowledgement ──
    if action == 'acknowledge':
        return _acknowledge(root, checkpoint, saved)

    # ── amend-command: correct a run's setup/check/generate command ──
    if action == 'amend-command':
        return _amend_command(root, checkpoint, saved, field=field, value=value, note=note,
                              node=node, acknowledge_failure=acknowledge_failure)

    # ── withdraw: remove a queued control that was never applied ──
    if action == 'withdraw':
        return _withdraw(root, node)

    # ── stop on a terminal run with a live owner: signal directly ──
    if action == 'stop':
        from refactory_dsh import run_state
        from refactory_dsh.run_state import is_alive as _alive
        spine_stat = read(root / '.refactory/spine-status.json').get('status', '')
        if spine_stat in run_state.TERMINAL:
            pid = saved.get('owner_pid')
            if isinstance(pid, int) and _alive(pid):
                import signal as _sig
                try:
                    os.kill(pid, _sig.SIGTERM)
                except ProcessLookupError:
                    pass
                return {'action': 'stop', 'status': 'signalled', 'pid': pid,
                        'message': f'Graph is {spine_stat}; sent SIGTERM to lingering owner {pid}.'}
        # dm-244: stop on a paused run whose owner is dead — queue stop and
        # start an owner via recovery.start so its drain applies the stop.
        if spine_stat == 'paused':
            pid = saved.get('owner_pid')
            if not isinstance(pid, int) or not _alive(pid):
                queued = _write_control_file(root, 'stop')
                _start_owner_for(root, queued, 'stop')
                return _wait_for_halted(root)

    if action == 'preview':
        graph_revision = saved.get('graph_revision')
        if graph_revision is None:
            graph_revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        plan = saved.get('redo_plans', {}).get(node)
        deferred_plan = saved.get('deferred_redo_plans', {}).get(node)
        if isinstance(plan, dict):
            return {'action': action, 'target': node, 'status': 'preview', 'revision': graph_revision,
                    'run_id': str(root),
                    'invalidated': plan.get('nodes', []),
                    'message': 'Prior evidence will be archived. Confirm this revision to queue replay.'}
        if isinstance(deferred_plan, dict):
            return {'action': action, 'target': node, 'status': 'deferred', 'revision': graph_revision,
                    'run_id': str(root),
                    'invalidated': deferred_plan.get('nodes', []),
                    'message': 'Replay will apply after concurrent work joins; confirm to queue.'}
        raise ValueError('node cannot safely be replayed at the current boundary')
    if action == 'resume':
        from refactory_dsh import recovery
        from refactory_dsh.run_state import is_alive as _alive
        pid = saved.get('owner_pid')
        if not pid or not _alive(pid):
            return recovery.start(root, acknowledge_failed=acknowledge_failure)
        # An owner that stopped where it will not go on by itself is exiting: wait
        # for it, then start a fresh one, rather than queue a control it never reads.
        if read(root / '.refactory/spine-status.json').get('status') in OWNER_STOPPED:
            deadline = time.monotonic() + _OWNER_EXIT_TIMEOUT
            while time.monotonic() < deadline:
                if not _alive(pid):
                    return recovery.start(root, acknowledge_failed=acknowledge_failure)
                time.sleep(0.1)
            # dm-81 fix: write resume-pending.json so the supervisor (or a new
            # recovery.start) picks it up once the owner exits — no second command.
            pending_path = root / '.refactory/resume-pending.json'
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_bytes = (root / '.refactory/spine-checkpoint.json').read_bytes()
            pending_data: dict[str, Any] = {
                'at': datetime.now(timezone.utc).isoformat(),
                'checkpoint_sha256': hashlib.sha256(checkpoint_bytes).hexdigest(),
            }
            if acknowledge_failure:
                pending_data['acknowledge_failed'] = True
            tmp = pending_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(pending_data))
            tmp.replace(pending_path)
    if action == 'redeliver':
        return _redeliver(root, saved, current_revision)
    if action == 'confirm-completion':
        return _confirm_completion(root)
    if action == 'skip':
        if not node:
            raise ValueError('--node is required for skip')
        graph_nodes = saved.get('graph', {}).get('nodes', {})
        if node not in graph_nodes:
            raise ValueError(f'unknown node {node!r}')
        state = saved.get('state', {})
        if node in (state.get('completed') or []):
            raise ValueError(f'{node!r} already completed')
        # Pre-validate: target must be a runtime_evidence producer.
        decl_path = root / '.refactory/declarations.json'
        if not decl_path.exists():
            raise ValueError('declarations.json is missing; cannot validate skip target')
        decl = json.loads(decl_path.read_text())
        node_decl = decl.get(node, {})
        produces = node_decl.get('produces', [])
        if 'runtime_evidence' not in produces:
            raise ValueError(
                f'{node!r} does not produce runtime evidence; '
                'graph-control skip is limited to check nodes'
            )
        identifier = uuid.uuid4().hex
        invocation_data = None
        invocation_path = root / '.refactory/invocation.json'
        if invocation_path.exists():
            try:
                invocation_data = json.loads(invocation_path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        payload = {
            'id': identifier, 'action': 'skip', 'target': node,
            'source': 'person',
            'at': datetime.now(timezone.utc).isoformat(),
            'run_id': str(root),
            'invocation': invocation_data.get('id') if invocation_data else None,
        }
        directory = root / '.refactory/graph-controls'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{identifier}.json').write_text(json.dumps(payload))
        emit(root, 'control_requested', state='queued', summary='skip', node_id=node)
        return payload
    if action not in ('pause', 'resume', 'stop', 'rerun', 'redeliver'):
        raise ValueError(f'unsupported graph control {action!r}; '
                         f'supported: pause, resume, stop, rerun, redeliver, acknowledge, amend-command, withdraw, confirm-completion, skip')
    if action == 'rerun':
        if text and text.strip():
            graph_nodes = saved.get('graph', {}).get('nodes', {})
            target_node = graph_nodes.get(node, {})
            if target_node.get('_type') not in ('AgentNode', 'LLMNode'):
                raise ValueError("this node takes no text; drop --text")
        immediate_plan = saved.get('redo_plans', {}).get(node)
        deferred_plan = saved.get('deferred_redo_plans', {}).get(node)
        restart_owner = False
        if saved.get('status') == 'failed':
            if not acknowledge_failure:
                raise ValueError(
                    'checkpoint status is failed; pass acknowledge_failure to confirm you have '
                    'inspected the side effects and want to rerun from this state'
                )
            restart_owner = True
        rerun_graph_revision = saved.get('graph_revision')
        if rerun_graph_revision is None:
            rerun_graph_revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        if not revision or revision != rerun_graph_revision:
            raise ValueError('preview the current invalidation plan before confirming rerun')
        if not isinstance(immediate_plan, dict) and not isinstance(deferred_plan, dict):
            raise ValueError('node is not safely replayable')
    # The owner consumes filenames in lexical order. Random UUIDs alone could
    # apply a later resume before its earlier pause.
    identifier = f'{time.time_ns():020d}-{uuid.uuid4().hex}'
    invocation_data = read(root / '.refactory/invocation.json')
    result: dict[str, Any] = {
        'id': identifier, 'action': action, 'target': node, 'text': text,
        'revision': revision, 'run_id': str(root),
        'invocation': invocation_data.get('id'),
        'status': 'queued',
        'message': 'Takes effect at the next graph boundary; no in-flight termination is claimed.',
    }
    if action == 'resume' and (root / '.refactory/resume-pending.json').exists():
        result['resume_pending'] = True
        result['message'] = 'Resume is pending; a new owner will start when the current one exits.'
    if action == 'rerun' and isinstance(deferred_plan, dict) and not isinstance(immediate_plan, dict):
        result['deferred_nodes'] = deferred_plan.get('nodes', [])
    directory = root / '.refactory/graph-controls'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f'{identifier}.tmp'
    temporary.write_text(json.dumps(result))
    temporary.replace(directory / f'{identifier}.json')
    emit(root, 'control_requested', state='queued', summary=action, node_id=node)
    receipt_path = root / '.refactory/graph-control-receipt.json'
    if receipt_path.exists():
        try:
            last_receipt = json.loads(receipt_path.read_text())
            if isinstance(last_receipt, dict) and last_receipt.get('status') == 'failed':
                # dm-101: A stale failed control is cleared only when a later
                # control on the same run succeeded (the receipt file is
                # overwritten with status 'applied').  Since we are reading
                # the receipt file and it still says 'failed', no later
                # success has occurred — always surface it.
                result['last_failed_receipt'] = {
                    'action': last_receipt.get('action'),
                    'status': 'failed',
                    'message': last_receipt.get('message', ''),
                }
        except (OSError, json.JSONDecodeError):
            pass
    if action == 'rerun' and restart_owner:
        owner_result = _start_owner_for(root, directory / f'{identifier}.json', 'rerun',
                                        acknowledge_failed=True)
        return {**result, 'owner': owner_result}
    return result


def _start_owner_for(root: Path, queued: Path, action: str, *,
                     acknowledge_failed: bool = False) -> dict[str, Any]:
    """Start an owner to apply the control just queued at ``queued``.

    When recovery refuses (an unknown worker outcome, changed configuration, a live
    owner), nothing may stay queued: a later resume's owner would apply the orphaned
    control as if the person had just asked for it (dm-244, dm-252). So the queued
    file is removed, the withdrawal is journalled, and the refusal is raised.
    """
    try:
        return recovery.start(root, acknowledge_failed=acknowledge_failed)
    except BaseException:
        queued.unlink(missing_ok=True)
        emit(root, 'control_requested', state='withdrawn',
             summary=f'{action} withdrawn: no owner could start to apply it')
        raise


def _write_control_file(root: Path, action: str) -> Path:
    """Queue a graph-control file for ``action``, journal it, and return its path."""
    identifier = f'{time.time_ns():020d}-{uuid.uuid4().hex}'
    invocation_data = read(root / '.refactory/invocation.json')
    payload = {
        'id': identifier, 'action': action, 'target': '',
        'run_id': str(root),
        'invocation': invocation_data.get('id'),
        'status': 'queued',
    }
    directory = root / '.refactory/graph-controls'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{identifier}.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)
    emit(root, 'control_requested', state='queued', summary=action, node_id='')
    return path


def _wait_for_halted(root: Path, timeout: float = 30.0) -> dict[str, Any]:
    """Poll spine-status.json until it reads 'halted', with a bounded wait."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status = read(root / '.refactory/spine-status.json').get('status', '')
            if status == 'halted':
                return {'action': 'stop', 'status': 'applied',
                        'message': 'Stop applied; the run is halted.'}
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(0.2)
    return {'action': 'stop', 'status': 'stopping',
            'message': 'Stop control queued; an owner is applying it.'}


# ── acknowledge ────────────────────────────────────────────────────────────

def _acknowledge(root: Path, checkpoint: Path, saved: dict[str, Any]) -> dict[str, Any]:
    """Acknowledge an executing or failed checkpoint whose owner is gone.

    Writes an acknowledgement bound to the checkpoint's SHA into
    resume-request.json. recovery.prepare() and the spine's restore accept
    an executing or failed checkpoint only with a matching acknowledgement.
    Pending delivery markers and outputs whose fingerprints differ from the
    checkpoint's recorded fingerprints are set aside under
    .refactory/set-aside/<timestamp>/.

    A stopped checkpoint (``ready`` or ``halted``) is acknowledged too when a
    dispatched worker's pending marker is all that keeps recovery from it (dm-275):
    when the worker dies while its owner lives on (a ``refactory stop`` SIGINT, a kill,
    a crash), the spine records the failed node as retryable (``ready``) and the
    marker stays. On such a stopped checkpoint a stop control still queued for the
    owner that exited is set aside with the markers: the run already stopped, and the
    owner a later resume starts would otherwise apply it as if the person had just
    asked (dm-244).
    """
    import fcntl
    from refactory_dsh.run_state import is_alive as _is_alive

    # 1. Verify checkpoint is executing, failed or paused, or stopped with an
    #    unknown worker outcome
    pending_dir = root / '.refactory/agent-delivery/pending'
    pending_markers = sorted(pending_dir.glob('*.json')) if pending_dir.is_dir() else []
    status = saved.get('status')
    if status not in ('executing', 'failed', 'paused') and not (
            status in STOPPED_WITH_MARKERS and pending_markers):
        raise ValueError(
            f"checkpoint status is {status!r}; "
            "acknowledge applies to an executing, failed, or paused checkpoint whose owner died, "
            "or a stopped (ready or halted) one that left a dispatched worker's outcome unknown"
        )

    # 2. Verify owner is dead. A process of another user answers the signal probe
    #    with EPERM, which `is_alive` reads as dead; here it is a live owner.
    owner_pid = saved.get('owner_pid')
    if isinstance(owner_pid, int) and (_is_alive(owner_pid) or _signal_denied(owner_pid)):
        raise ValueError(
            f'the owner process {owner_pid} is still alive; '
            'wait for it to exit or stop it before acknowledging'
        )

    # 3. Verify supervisor is dead (try non-blocking flock on supervisor.lock)
    supervisor_lock = root / '.refactory/supervisor.lock'
    if supervisor_lock.exists():
        sfd = os.open(supervisor_lock, os.O_RDWR)
        try:
            fcntl.flock(sfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Got the lock — no supervisor is running; release it
            fcntl.flock(sfd, fcntl.LOCK_UN)
        except (BlockingIOError, OSError):
            raise ValueError(
                'a supervisor process holds supervisor.lock; '
                'stop it before acknowledging'
            )
        finally:
            os.close(sfd)

    # 4. Move pending delivery markers, stop controls queued for the owner that died
    #    (on a stopped checkpoint only: that run is stopped already; on an executing,
    #    failed or paused one a queued stop is still the person's standing request, and
    #    the next owner applies it) and fingerprint-mismatched outputs to set-aside.
    controls_dir = root / '.refactory/graph-controls'
    stops: list[Path] = []
    stopped = status in STOPPED_WITH_MARKERS
    for control in sorted(controls_dir.glob('*.json')) if stopped and controls_dir.is_dir() else []:
        try:
            queued = json.loads(control.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(queued, dict) and queued.get('action') == 'stop':
            stops.append(control)
    checkpoint_status = saved.get('status', 'executing')
    set_aside_dir, manifest_files = recovery.set_aside(
        root, fingerprints=saved.get('file_fingerprints', {}), markers=pending_markers,
        controls=stops, status=checkpoint_status, owner_pid=owner_pid)
    withdrawn_stops = [control.stem for control in stops]
    for control_id in withdrawn_stops:
        emit(root, 'control_requested', state='withdrawn',
             summary=f'stop {control_id} withdrawn: the run stopped before its owner applied it')

    # 5. Write acknowledgement bound to the checkpoint's SHA into resume-request.json.
    #    Do NOT rewrite spine-checkpoint.json or spine-status.json: the spine is their
    #    only writer (#410). recovery.prepare() and the spine's restoreRequested()
    #    accept an executing checkpoint only with a matching acknowledgement.
    checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    request_path = root / '.refactory/resume-request.json'
    request_data: dict[str, Any] = {
        'checkpoint_sha256': checkpoint_sha,
    }
    if checkpoint_status == 'failed':
        request_data['acknowledge_failed'] = True
    elif checkpoint_status == 'executing':
        request_data['acknowledge_executing'] = True
    # For paused, ready and halted checkpoints: only checkpoint_sha256 is needed —
    # they are already in the allowed set for recovery.prepare(), and
    # restoreRequested() clears pause_before for any status.
    tmp = request_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(request_data))
    tmp.replace(request_path)

    emit(root, 'control_requested', state='acknowledged',
         summary=f'acknowledge {checkpoint_status} checkpoint',
         set_aside=str(set_aside_dir.relative_to(root)),
         files_count=len(manifest_files))

    return {
        'action': 'acknowledge',
        'status': 'acknowledged',
        'run_id': str(root),
        'checkpoint_status_before': checkpoint_status,
        'set_aside': str(set_aside_dir.relative_to(root)),
        'files': manifest_files,
        'withdrawn_controls': withdrawn_stops,
        'message': f'Checkpoint ({checkpoint_status}) acknowledged; {len(manifest_files)} file(s) set aside under '
                   f'{set_aside_dir.name}'
                   + (f', including {len(withdrawn_stops)} queued stop(s) the run no longer needs'
                      if withdrawn_stops else '')
                   + '. Run "refactory graph-control resume" to continue.',
    }


def _signal_denied(pid: int) -> bool:
    """Whether ``pid`` exists but belongs to another user (the probe is refused)."""
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return False


# ── withdraw ───────────────────────────────────────────────────────────────

def _withdraw(root: Path, control_id: str) -> dict[str, Any]:
    """Remove a queued control that was never applied."""
    if not control_id:
        raise ValueError('withdraw requires --node <control_id>')
    control_path = root / '.refactory/graph-controls' / f'{control_id}.json'
    if not control_path.exists():
        raise ValueError(f'no queued control with id {control_id!r}')
    receipt_path = root / '.refactory/graph-control-receipt.json'
    if receipt_path.exists():
        try:
            receipt = json.loads(receipt_path.read_text())
            if (isinstance(receipt, dict) and receipt.get('id') == control_id
                    and receipt.get('status') == 'applied'):
                raise ValueError(f'control {control_id!r} has already been applied')
        except (OSError, json.JSONDecodeError):
            pass
    control_path.unlink()
    emit(root, 'control_requested', state='withdrawn', summary=f'withdraw {control_id}')
    return {'action': 'withdraw', 'control_id': control_id, 'run_id': str(root),
            'status': 'withdrawn',
            'message': f'Control {control_id} withdrawn.'}


# ── amend-command ──────────────────────────────────────────────────────────

def _amend_command(root: Path, checkpoint: Path, saved: dict[str, Any], *,
                   field: str, value: str, note: str, node: str = '',
                   acknowledge_failure: bool) -> dict[str, Any]:
    """Correct a run's setup, check or generate command and queue a rerun.

    The field and new value are recorded in facts.json's command_history as
    an explicit, timestamped entry. The node rerun is the one the person names
    (``node``), or else the run's single due node: the one that stopped with the
    old command. A rerun control with source='amend-command' is queued; the spine
    consumes it with commandCorrection semantics that accept due nodes.
    """
    # Reject if checkpoint is executing — must acknowledge first
    if saved.get('status') == 'executing':
        raise ValueError(
            f"checkpoint status is 'executing'; run "
            f"'refactory graph-control acknowledge --run {root}' first, "
            f"then amend-command"
        )

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

    # Resolved before anything is written, so a refusal leaves the run as it was.
    target_node = _find_rerun_target(saved, node)

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

    # Queue a rerun control targeting the relevant node
    invocation_data = read(root / '.refactory/invocation.json')
    rerun_id = f'{time.time_ns():020d}-{uuid.uuid4().hex}'
    rerun_result: dict[str, Any] = {
        'id': rerun_id,
        'action': 'rerun',
        'target': target_node,
        'source': 'amend-command',
        'text': '',
        'revision': saved.get('graph_revision') or hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        'run_id': str(root),
        'invocation': invocation_data.get('id'),
        'status': 'queued',
        'message': f'Command correction: {field} changed from {old_value!r} to {value!r}',
    }
    directory = root / '.refactory/graph-controls'
    directory.mkdir(parents=True, exist_ok=True)
    ctrl_tmp = directory / f'{rerun_id}.tmp'
    ctrl_tmp.write_text(json.dumps(rerun_result))
    ctrl_tmp.replace(directory / f'{rerun_id}.json')

    emit(root, 'control_requested', state='queued', summary=f'amend-command {field}',
         node_id=target_node)

    result: dict[str, Any] = {
        'action': 'amend-command',
        'field': field,
        'old_value': old_value,
        'new_value': value,
        'note': note,
        'run_id': str(root),
        'status': 'queued',
        'rerun_control_id': rerun_id,
        'rerun_target': target_node,
        'message': f'{field} corrected: {old_value!r} \u2192 {value!r}; rerun queued for node {target_node}.',
    }

    # If the checkpoint is failed and acknowledge_failure is set, start a new owner
    if acknowledge_failure and saved.get('status') == 'failed':
        result['owner'] = _start_owner_for(root, directory / f'{rerun_id}.json', 'amend-command',
                                           acknowledge_failed=True)
    return result


def _find_rerun_target(saved: dict[str, Any], node: str = '') -> str:
    """The node a command correction reruns: the one the person names, or the run's single due node.

    A run is corrected because a node stopped with the old command, and that node is
    the one left due in the checkpoint's cursor, whatever the mode or graph. Which node
    reads setup, check or generate is not guessed from node names or file names: with
    more than one due node, or none, the person names the node with ``--node``.
    """
    graph_nodes = (saved.get('graph') or {}).get('nodes') or {}
    state = saved.get('state') if isinstance(saved.get('state'), dict) else saved
    cursor = [n for n in (state.get('cursor') or []) if isinstance(n, str)]
    if node:
        if node not in graph_nodes:
            raise ValueError(f'no node {node!r} in this run\'s graph')
        return node
    if len(cursor) == 1:
        return cursor[0]
    due = ', '.join(cursor) if cursor else 'none'
    raise ValueError(f'name the node to rerun with --node: the run has {len(cursor)} due node(s) ({due})')


def _find_deliver_node(graph_nodes: dict[str, Any]) -> str | None:
    """The deliver node's id, found by its callable from ``invocations.DELIVER_ENTRY_POINTS``.

    Checks ``callable_name`` first (set by lowering) and falls back to
    the ``command`` field for older checkpoints that lack the field.
    """
    from refactory_dsh.invocations import DELIVER_ENTRY_POINTS

    for nid, ndata in graph_nodes.items():
        if not isinstance(ndata, dict):
            continue
        call = ndata.get('callable_name') or ''
        if call in DELIVER_ENTRY_POINTS:
            return nid
        cmd = ndata.get('command') or ''
        for ep in DELIVER_ENTRY_POINTS:
            if ep in cmd:
                return nid
    return None


def _find_delivery_gate_node(graph_nodes: dict[str, Any],
                              graph_edges: list[dict[str, Any]],
                              deliver_node_id: str) -> str | None:
    """The delivery gate node's id: a GateNode immediately after the deliver node."""
    for edge in graph_edges:
        if not isinstance(edge, dict):
            continue
        if edge.get('source') == deliver_node_id:
            target_id = edge.get('target', '')
            target_node = graph_nodes.get(target_id)
            if isinstance(target_node, dict) and target_node.get('_type') == 'GateNode':
                return target_id
    return None


def _redeliver(root: Path, saved: dict[str, Any], current_revision: str) -> dict[str, Any]:
    """Merge current main, regenerate, re-check and push in one command.

    Works on runs that finished (``done``) or that failed at the deliver
    node or the delivery gate.  When the checkpoint is ``failed`` and the
    due node is the deliver node or delivery gate, the checkpoint-bound
    acknowledgement is passed automatically, as ``rerun --acknowledge-failure``
    does.
    """
    spine_status = read(root / '.refactory/spine-status.json').get('status', '')
    if spine_status not in OWNER_STOPPED:
        raise ValueError(f'redeliver requires a stopped run (status: {spine_status!r})')

    # Find the deliver node by its callable, not by its id
    graph_nodes = saved.get('graph', {}).get('nodes', {})
    deliver_node_id = _find_deliver_node(graph_nodes)
    if deliver_node_id is None:
        raise ValueError('this run has no deliver node')

    # Also find the delivery gate (the GateNode immediately after deliver),
    # so redeliver accepts runs that stopped at the gate (e.g. conflict
    # resolution exhausted its budget).
    graph_edges = saved.get('graph', {}).get('edges', [])
    delivery_gate_id = _find_delivery_gate_node(graph_nodes, graph_edges, deliver_node_id)
    delivery_node_ids = {deliver_node_id}
    if delivery_gate_id:
        delivery_node_ids.add(delivery_gate_id)

    # Handle failed-at-deliver or failed-at-delivery-gate: pass the acknowledgement automatically
    acknowledge_failed = False
    if saved.get('status') == 'failed':
        state = saved.get('state') if isinstance(saved.get('state'), dict) else saved
        cursor = [n for n in (state.get('cursor') or []) if isinstance(n, str)]
        if any(n in delivery_node_ids for n in cursor):
            acknowledge_failed = True
        else:
            due = ', '.join(cursor) if cursor else 'none'
            raise ValueError(
                f'this run failed at node(s) {due}, not at delivery; '
                f'redeliver works only when the failure is at the deliver node'
            )

    redeliver_revision = saved.get('graph_revision') or current_revision

    # Queue a rerun control file targeting the deliver node
    identifier = f'{time.time_ns():020d}-{uuid.uuid4().hex}'
    invocation_data = read(root / '.refactory/invocation.json')
    result: dict[str, Any] = {
        'id': identifier, 'action': 'rerun', 'target': deliver_node_id,
        'source': 'redeliver', 'text': '', 'revision': redeliver_revision,
        'run_id': str(root), 'invocation': invocation_data.get('id'),
        'status': 'queued',
        'message': 'Redeliver: merge current main, regenerate, re-check and push.',
    }
    directory = root / '.refactory/graph-controls'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f'{identifier}.tmp'
    temporary.write_text(json.dumps(result))
    temporary.replace(directory / f'{identifier}.json')
    emit(root, 'control_requested', state='queued', summary='redeliver',
         node_id=deliver_node_id)

    # Start the owner, acknowledging the failure when the due node is deliver
    owner_result = _start_owner_for(root, directory / f'{identifier}.json', 'redeliver',
                                    acknowledge_failed=acknowledge_failed)
    return {**result, 'owner': owner_result}


def _confirm_completion(root: Path) -> dict[str, Any]:
    """Write a person-only completion_override into spine-status.json.

    The override is bound to the current status and invocation; it is cleared
    when recordStatus() writes a different status.
    """
    status_path = root / '.refactory/spine-status.json'
    if not status_path.exists():
        raise ValueError('this run has no spine-status.json')
    status_record = json.loads(status_path.read_text())
    current_status = status_record.get('status', '')
    invocation_path = root / '.refactory/invocation.json'
    invocation_id = None
    if invocation_path.exists():
        try:
            invocation_id = json.loads(invocation_path.read_text()).get('id')
        except (OSError, json.JSONDecodeError):
            pass
    status_record['completion_override'] = {
        'confirmed_status': current_status,
        'invocation': invocation_id,
        'at': datetime.now(timezone.utc).isoformat(),
    }
    tmp = status_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(status_record, indent=2))
    tmp.replace(status_path)
    return {
        'action': 'confirm-completion',
        'status': 'confirmed',
        'confirmed_status': current_status,
    }


def queued_controls(root: Path) -> list[dict[str, Any]]:
    """List all queued control files for display in status."""
    root = Path(root).resolve()
    directory = root / '.refactory/graph-controls'
    if not directory.is_dir():
        return []
    controls: list[dict[str, Any]] = []
    for name in sorted(directory.glob('*.json')):
        try:
            data = json.loads(name.read_text())
            if isinstance(data, dict):
                controls.append({
                    'id': data.get('id', name.stem),
                    'action': data.get('action', ''),
                    'target': data.get('target', ''),
                    'status': data.get('status', ''),
                })
        except (OSError, json.JSONDecodeError):
            pass
    return controls
