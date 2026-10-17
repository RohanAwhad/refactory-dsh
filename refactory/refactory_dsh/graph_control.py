"""Queue controls for the graph owner; a reader never executes a node."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from refactory_dsh.live import emit, read


def request(root: Path, action: str, *, node: str = '', text: str = '', revision: str = '') -> dict[str, Any]:
    root = Path(root).resolve()
    checkpoint = root / '.refactory/spine-checkpoint.json'
    if not checkpoint.exists():
        raise ValueError('this run has no durable graph checkpoint; controls require its owning runtime')
    saved = read(checkpoint)
    current_revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if action == 'preview':
        plan = saved.get('redo_plans', {}).get(node)
        if not isinstance(plan, dict):
            raise ValueError('node cannot safely be replayed at the current boundary; finish concurrent work first')
        return {'action': action, 'target': node, 'status': 'preview', 'revision': current_revision,
                'invalidated': plan.get('nodes', []), 'message': 'Prior evidence will be archived. Confirm this revision to queue replay.'}
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
        if not revision or revision != current_revision:
            raise ValueError('preview the current invalidation plan before confirming rerun')
        if not isinstance(saved.get('redo_plans', {}).get(node), dict):
            raise ValueError('node is not safely replayable')
    # The owner consumes filenames in lexical order. Random UUIDs alone could
    # apply a later resume before its earlier pause.
    identifier = f'{time.time_ns():020d}-{uuid.uuid4().hex}'
    result = {'id': identifier, 'action': action, 'target': node, 'text': text,
              'revision': revision, 'run_id': str(root),
              'invocation': read(root / '.refactory/invocation.json').get('id'),
              'status': 'queued', 'message': 'Takes effect at the next graph boundary; no in-flight termination is claimed.'}
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
    return result
