"""Durable activity shared by terminal and native GUI readers.

Readers never execute or restart work. Cursors count complete journal records;
an interrupted final write is not acknowledged until its boundary is repaired.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENTS = Path('.refactory/events.jsonl')


def read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def emit(root: Path, kind: str, **fields: Any) -> dict[str, Any]:
    root = Path(root).resolve()
    invocation = read(root / '.refactory/invocation.json')
    event = {**fields, 'schema_version': 1, 'run_id': str(root),
             'invocation': invocation.get('id'), 'at': datetime.now(timezone.utc).isoformat(), 'kind': kind}
    path = root / EVENTS
    path.parent.mkdir(parents=True, exist_ok=True)
    # Repair only the boundary after an interrupted write. The damaged bytes
    # remain as a visible journal gap; later events must still be readable.
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        size = os.fstat(fd).st_size
        separator = b'\n' if size and os.pread(fd, 1, size - 1) != b'\n' else b''
        os.write(fd, separator + (json.dumps(event, ensure_ascii=False) + '\n').encode())
    finally:
        os.close(fd)
    return event


def events(root: Path, after: int = 0) -> tuple[list[dict[str, Any]], int]:
    if after < 0:
        raise ValueError('event cursor must be nonnegative')
    try:
        lines = (root / EVENTS).read_bytes().splitlines(keepends=True)
    except FileNotFoundError:
        return [], 0
    records = []
    for line in lines:
        if not line.endswith(b'\n'):
            break
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            record = {'schema_version': 1, 'run_id': str(Path(root).resolve()),
                      'kind': 'journal_gap', 'state': 'unavailable',
                      'summary': 'An incomplete or malformed activity record could not be recovered.'}
        records.append(record)
    return records[after:], len(records)


def graph_snapshot(root: Path) -> dict[str, Any] | None:
    """Read topology from this run's checkpoint, without exposing commands/prompts.

    Checkpoints may be absent or from an older runtime. Never infer connections
    from event order or import/execute a mode to reconstruct missing topology.
    """
    checkpoint = read(root / '.refactory/spine-checkpoint.json')
    graph = checkpoint.get('graph')
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), dict):
        return None
    nodes = [{'id': key, 'kind': str(node.get('_type', 'Node'))}
             for key, node in graph['nodes'].items() if isinstance(node, dict)]
    ids = {node['id'] for node in nodes}
    edges = []
    for edge in graph.get('edges', []) if isinstance(graph.get('edges'), list) else []:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get('source'), edge.get('target')
        if not isinstance(source, str) or not isinstance(target, str) or source not in ids or target not in ids:
            continue
        edges.append({'source': source, 'target': target,
                      'label': str(edge.get('condition') or '')})
    # The spine declares these connections on nodes, rather than in `edges`.
    def connect(source: Any, target: Any, label: str) -> None:
        if isinstance(source, str) and isinstance(target, str) and source in ids and target in ids:
            if not any(edge['source'] == source and edge['target'] == target for edge in edges):
                edges.append({'source': source, 'target': target, 'label': label})
    for key, node in graph['nodes'].items():
        if not isinstance(node, dict):
            continue
        if node.get('_type') == 'ForkNode' and isinstance(node.get('targets'), list):
            for target in node['targets']:
                connect(key, target, 'branch')
        if node.get('_type') == 'JoinNode' and isinstance(node.get('sources'), list):
            for source in node['sources']:
                connect(source, key, 'join')
        if node.get('_type') == 'DataNode':
            connect(key, node.get('subgraph_entry'), 'each item')
            connect(node.get('subgraph_exit'), key, 'next item')
    successors = checkpoint.get('successors')
    if isinstance(successors, dict):
        for source, target in successors.items():
            connect(source, target, '')
    skipped = checkpoint.get('skipped', [])
    return {'nodes': nodes, 'edges': edges,
            'skipped': [node for node in skipped if isinstance(node, str) and node in ids]
            if isinstance(skipped, list) else []} if nodes else None


def snapshot(root: Path, *, after: int = 0) -> dict[str, Any]:
    root = Path(root).resolve()
    spine = read(root / '.refactory/spine-status.json')
    invocation = read(root / '.refactory/invocation.json')
    history, cursor = events(root, after)
    all_events, _ = events(root)
    workers = [read(p) for p in sorted((root / '.refactory/workers').glob('*.json'))]
    deliveries = [read(p) for p in sorted((root / '.refactory/agent-delivery').glob('*/receipt.json'))]
    # A delivery context does not prove the coordinator actually dispatched it.
    supplied = [{'worker_id': row.get('id'), 'node_id': row.get('node'),
                 'state': row.get('status'), 'started': row.get('started'),
                 'context_revision': len(row.get('contexts', [])),
                 'receipt': str(Path('.refactory/agent-delivery') / str(row.get('id')) / 'receipt.json')}
                for row in deliveries if row]
    nodes = spine.get('nodes', [])
    completed = spine.get('completed', [])
    wait = spine.get('wait_reason')
    if not wait:
        wait = ('stopped at graph boundary' if spine.get('status') == 'halted' else
                'paused by user' if spine.get('status') == 'paused' else
                'failure requires attention' if spine.get('status') in ('failed', 'refused') else
                'graph boundary; worker dispatch unconfirmed' if nodes else None)
    return {'schema_version': 1, 'run_id': str(root), 'invocation': invocation.get('id'),
            'events': history, 'cursor': cursor, 'last_event_at': all_events[-1].get('at') if all_events else None,
            'started_at': invocation.get('started'), 'nodes': nodes, 'completed': completed,
            'running_nodes': spine.get('running_nodes'),
            'graph': graph_snapshot(root),
            'wait_reason': wait, 'workers': workers, 'deliveries': supplied,
            'resources': read(root / '.refactory/resource-usage.json') or None,
            'control': read(root / '.refactory/graph-control-receipt.json') or None,
            'outputs': read(root / 'facts.json')}
