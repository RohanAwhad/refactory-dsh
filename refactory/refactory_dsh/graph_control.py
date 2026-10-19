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

#: How long a resume waits for an exiting owner at a terminal status.
#: Short enough that the CLI stays responsive; long enough to cover
#: the normal exit delay after a headless run prints its next-commands.
_OWNER_EXIT_TIMEOUT = 3.0

#: Spine statuses an owner does not go on from by itself (a headless run records
#: `refused` at a user gate); its owner exits after printing the next commands.
OWNER_STOPPED = frozenset({'done', 'halted', 'failed', 'refused', 'cancelled'})


def request(root: Path, action: str, *, node: str = '', text: str = '', revision: str = '',
            acknowledge_failure: bool = False,
            field: str = '', value: str = '', note: str = '') -> dict[str, Any]:
    root = Path(root).resolve()
    checkpoint = root / '.refactory/spine-checkpoint.json'
    if not checkpoint.exists():
        raise ValueError('this run has no durable graph checkpoint; controls require its owning runtime')
    saved = read(checkpoint)
    current_revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()

    # ── acknowledge: transition executing → halted via a SHA-bound acknowledgement ──
    if action == 'acknowledge':
        return _acknowledge(root, checkpoint, saved)

    # ── amend-command: correct a run's setup/check/generate command ──
    if action == 'amend-command':
        return _amend_command(root, checkpoint, saved, field=field, value=value, note=note,
                              acknowledge_failure=acknowledge_failure)

    # ── withdraw: remove a queued control that was never applied ──
    if action == 'withdraw':
        return _withdraw(root, node)

    # ── stop on a terminal run with a live owner: signal directly ──
    if action == 'stop':
        from refactory_dsh import run_state
        spine_stat = read(root / '.refactory/spine-status.json').get('status', '')
        if spine_stat in run_state.TERMINAL:
            pid = saved.get('owner_pid')
            if isinstance(pid, int) and run_state.is_alive(pid):
                import signal as _sig
                try:
                    os.kill(pid, _sig.SIGTERM)
                except ProcessLookupError:
                    pass
                return {'action': 'stop', 'status': 'signalled', 'pid': pid,
                        'message': f'Graph is {spine_stat}; sent SIGTERM to lingering owner {pid}.'}

    if action == 'preview':
        plan = saved.get('redo_plans', {}).get(node)
        deferred_plan = saved.get('deferred_redo_plans', {}).get(node)
        if isinstance(plan, dict):
            return {'action': action, 'target': node, 'status': 'preview', 'revision': current_revision,
                    'invalidated': plan.get('nodes', []),
                    'message': 'Prior evidence will be archived. Confirm this revision to queue replay.'}
        if isinstance(deferred_plan, dict):
            return {'action': action, 'target': node, 'status': 'deferred', 'revision': current_revision,
                    'invalidated': deferred_plan.get('nodes', []),
                    'message': 'Replay will apply after concurrent work joins; confirm to queue.'}
        raise ValueError('node cannot safely be replayed at the current boundary')
    if action == 'resume':
        from refactory_dsh import recovery
        from refactory_dsh.run_state import is_alive as _alive
        pid = saved.get('owner_pid')
        if not pid or not _alive(pid):
            return recovery.start(root)
        # An owner that stopped where it will not go on by itself is exiting: wait
        # for it, then start a fresh one, rather than queue a control it never reads.
        if read(root / '.refactory/spine-status.json').get('status') in OWNER_STOPPED:
            deadline = time.monotonic() + _OWNER_EXIT_TIMEOUT
            while time.monotonic() < deadline:
                if not _alive(pid):
                    return recovery.start(root)
                time.sleep(0.1)
            # dm-81 fix: write resume-pending.json so the supervisor (or a new
            # recovery.start) picks it up once the owner exits — no second command.
            pending_path = root / '.refactory/resume-pending.json'
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_bytes = (root / '.refactory/spine-checkpoint.json').read_bytes()
            pending_data = {
                'at': datetime.now(timezone.utc).isoformat(),
                'checkpoint_sha256': hashlib.sha256(checkpoint_bytes).hexdigest(),
            }
            tmp = pending_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(pending_data))
            tmp.replace(pending_path)
    if action not in ('pause', 'resume', 'stop', 'rerun'):
        raise ValueError(f'unsupported graph control {action!r}; '
                         f'supported: pause, resume, stop, rerun, acknowledge, amend-command, withdraw')
    if action == 'rerun':
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
        if not revision or revision != current_revision:
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
                result['last_failed_receipt'] = {
                    'action': last_receipt.get('action'),
                    'status': 'failed',
                    'message': last_receipt.get('message', ''),
                }
        except (OSError, json.JSONDecodeError):
            pass
    if action == 'rerun' and restart_owner:
        from refactory_dsh import recovery
        owner_result = recovery.start(root, acknowledge_failed=True)
        return {**result, 'owner': owner_result}
    return result


# ── acknowledge ────────────────────────────────────────────────────────────

def _acknowledge(root: Path, checkpoint: Path, saved: dict[str, Any]) -> dict[str, Any]:
    """Acknowledge an executing checkpoint whose owner is gone.

    Writes an acknowledgement bound to the checkpoint's SHA into
    resume-request.json. recovery.prepare() and the spine's restore accept
    an executing checkpoint only with a matching acknowledgement. Pending
    delivery markers and outputs whose fingerprints differ from the
    checkpoint's recorded fingerprints are set aside under
    .refactory/set-aside/<timestamp>/.
    """
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

    # 4. Move pending delivery markers and fingerprint-mismatched outputs to set-aside
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    set_aside_dir = root / '.refactory/set-aside' / timestamp
    set_aside_dir.mkdir(parents=True, exist_ok=True)
    manifest_files: list[dict[str, Any]] = []

    # Move pending delivery markers
    pending_dir = root / '.refactory/agent-delivery/pending'
    if pending_dir.is_dir():
        for pending_file in sorted(pending_dir.glob('*.json')):
            dest = set_aside_dir / 'pending' / pending_file.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            pending_file.rename(dest)
            manifest_files.append({
                'original_path': str(pending_file.relative_to(root)),
                'set_aside_path': str(dest.relative_to(root)),
                'size': dest.stat().st_size,
            })

    # Move outputs whose fingerprint differs from the checkpoint's recorded one.
    # This restores the filesystem to the state the fingerprints describe, so
    # recovery.prepare()'s fingerprint guard sees consistent state. Completed
    # fork branches' outputs match the recorded fingerprints and stay in place;
    # in-flight branches' partial outputs differ and are set aside.
    for fp_path, expected in saved.get('file_fingerprints', {}).items():
        output_path = root / fp_path
        if not output_path.exists():
            continue
        current = _fp(output_path)
        if current == expected:
            continue
        dest = set_aside_dir / 'outputs' / fp_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if output_path.is_dir():
            import shutil
            shutil.move(str(output_path), str(dest))
        else:
            output_path.rename(dest)
        manifest_files.append({
            'original_path': fp_path,
            'set_aside_path': str(dest.relative_to(root)),
            'size': dest.stat().st_size if dest.is_file() else -1,
        })

    # Write manifest
    manifest = {
        'at': datetime.now(timezone.utc).isoformat(),
        'checkpoint_status_before': 'executing',
        'owner_pid': owner_pid,
        'files': manifest_files,
    }
    (set_aside_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2))

    # 5. Write acknowledgement bound to the checkpoint's SHA into resume-request.json.
    #    Do NOT rewrite spine-checkpoint.json or spine-status.json: the spine is their
    #    only writer (#410). recovery.prepare() and the spine's restoreRequested()
    #    accept an executing checkpoint only with a matching acknowledgement.
    checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    request_path = root / '.refactory/resume-request.json'
    request_data: dict[str, Any] = {
        'checkpoint_sha256': checkpoint_sha,
        'acknowledge_executing': True,
    }
    tmp = request_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(request_data))
    tmp.replace(request_path)

    emit(root, 'control_requested', state='acknowledged',
         summary='acknowledge executing checkpoint',
         set_aside=str(set_aside_dir.relative_to(root)),
         files_count=len(manifest_files))

    return {
        'action': 'acknowledge',
        'status': 'acknowledged',
        'set_aside': str(set_aside_dir.relative_to(root)),
        'files': manifest_files,
        'message': f'Checkpoint acknowledged; {len(manifest_files)} file(s) set aside under '
                   f'{set_aside_dir.name}. Run "refactory graph-control resume" to continue.',
    }


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
    return {'action': 'withdraw', 'control_id': control_id, 'status': 'withdrawn',
            'message': f'Control {control_id} withdrawn.'}


# ── amend-command ──────────────────────────────────────────────────────────

def _amend_command(root: Path, checkpoint: Path, saved: dict[str, Any], *,
                   field: str, value: str, note: str,
                   acknowledge_failure: bool) -> dict[str, Any]:
    """Correct a run's setup, check or generate command and queue a rerun.

    The field and new value are recorded in facts.json's command_history as
    an explicit, timestamped entry. The handler finds which node to rerun
    from the graph's declarations: the earliest due or completed node whose
    op reads the amended fact. A rerun control with source='amend-command'
    is queued; the spine consumes it with commandCorrection semantics that
    accept due nodes.
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

    # Find which node to rerun from the graph's declarations.
    target_node = _find_rerun_target(root, saved, field)

    # Queue a rerun control targeting the relevant node
    invocation_data = read(root / '.refactory/invocation.json')
    rerun_id = f'{time.time_ns():020d}-{uuid.uuid4().hex}'
    rerun_result: dict[str, Any] = {
        'id': rerun_id,
        'action': 'rerun',
        'target': target_node,
        'source': 'amend-command',
        'text': '',
        'revision': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
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
        'status': 'queued',
        'rerun_control_id': rerun_id,
        'rerun_target': target_node,
        'message': f'{field} corrected: {old_value!r} \u2192 {value!r}; rerun queued for node {target_node}.',
    }

    # If the checkpoint is failed and acknowledge_failure is set, start a new owner
    if acknowledge_failure and saved.get('status') == 'failed':
        from refactory_dsh import recovery
        owner_result = recovery.start(root, acknowledge_failed=True)
        result['owner'] = owner_result
    return result


def _find_rerun_target(root: Path, saved: dict[str, Any], field: str) -> str:
    """Find the earliest due or completed node whose operation reads the amended fact.

    Looks at the graph inside the checkpoint to find nodes, then checks the run's
    declarations (produces lists) to identify which nodes consume the fact.
    For setup/check/generate the consuming node is typically the check gate (the node
    whose evaluator runs repo_change.prepare_commands and then the check command).
    Falls back to looking for a node named after the field if declarations are unavailable.
    """
    from refactory_dsh.lowering import read_declarations

    graph = saved.get('graph', {})
    nodes = graph.get('nodes', {})
    cursor = saved.get('state', saved).get('cursor', [])
    completed = saved.get('state', saved).get('completed', [])

    # Read the run's declarations to find which nodes' ops produce or consume the fact.
    declarations = read_declarations(root)

    # Strategy: find all nodes whose declaration lists check-related writes or whose
    # id/type matches the field. The check command, setup and generate all run inside
    # nodes whose evaluator or command references prepare_commands / run_check.
    candidates: list[str] = []
    check_writes = {'check.txt', 'validation.txt', 'authoring-evidence.json'}

    for node_id, node_body in nodes.items():
        if not isinstance(node_body, dict):
            continue
        node_type = node_body.get('_type', '')
        decl = declarations.get(node_id, {})
        node_writes = set(decl.get('writes', []) + (node_body.get('writes') or []))

        if field in ('check', 'setup'):
            # The check gate runs setup + check
            if node_writes & check_writes:
                candidates.append(node_id)
            elif 'check' in node_id.lower() and node_type == 'GateNode':
                candidates.append(node_id)
        elif field == 'generate':
            # The materialise FnNode runs the generate command
            if 'materialise' in node_id.lower() or 'materialize' in node_id.lower():
                candidates.append(node_id)
            elif node_writes & check_writes:
                candidates.append(node_id)

    # If we found candidates, pick the earliest one that is due (in cursor) or completed
    if candidates:
        for c in candidates:
            if c in cursor:
                return c
        for c in candidates:
            if c in completed:
                return c
        return candidates[0]

    # Fallback: look for a node named after the field
    if field in nodes:
        return field

    # Last resort: look for any node in the cursor
    if cursor:
        return cursor[0]

    raise ValueError(
        f'cannot find a node to rerun for amended field {field!r}; '
        f'the graph has no node whose operation reads this fact'
    )


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
