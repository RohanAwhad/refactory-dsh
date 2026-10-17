"""Queue controls for the graph owner; a reader never executes a node."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from refactory_dsh.live import emit, read


def request(root: Path, action: str, *, node: str = '', text: str = '', revision: str = '',
            acknowledge_failure: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    checkpoint = root / '.refactory/spine-checkpoint.json'
    if not checkpoint.exists():
        raise ValueError('this run has no durable graph checkpoint; controls require its owning runtime')
    saved = read(checkpoint)
    current_revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
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
        pid = saved.get('owner_pid')
        if not pid:
            return recovery.start(root)
        if pid:
            import os
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return recovery.start(root)
    if action not in ('pause', 'resume', 'stop', 'rerun'):
        raise ValueError(f'unsupported graph control {action!r}')
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
    result: dict[str, Any] = {
        'id': identifier, 'action': action, 'target': node, 'text': text,
        'revision': revision, 'run_id': str(root),
        'invocation': read(root / '.refactory/invocation.json').get('id'),
        'status': 'queued',
        'message': 'Takes effect at the next graph boundary; no in-flight termination is claimed.',
    }
    if action == 'rerun' and isinstance(deferred_plan, dict) and not isinstance(immediate_plan, dict):
        result['deferred_nodes'] = deferred_plan.get('nodes', [])
    directory = root / '.refactory/graph-controls'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f'{identifier}.tmp'
    temporary.write_text(json.dumps(result))
    temporary.replace(directory / f'{identifier}.json')
    emit(root, 'control_requested', state='queued', summary=action, node_id=node)
    if action == 'rerun' and restart_owner:
        from refactory_dsh import recovery
        owner_result = recovery.start(root, acknowledge_failed=True)
        return {**result, 'owner': owner_result}
    return result
