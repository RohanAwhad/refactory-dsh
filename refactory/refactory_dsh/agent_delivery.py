"""Agent artifact delivery and context receipts shared by the walker and DSH.

Receipts establish which context was supplied and which bytes were delivered. They
are not model acknowledgements or evidence that the work satisfies that context.
Artifact bytes and the model's final response are retained separately, unchanged.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.refactory/agent-delivery')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(workdir: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError(f'agent output {name!r} must be relative to its run directory')
    path = workdir / relative
    if not path.resolve().is_relative_to(workdir.resolve()):
        raise ValueError(f'agent output {name!r} resolves outside its run directory')
    return path


def _fingerprint(workdir: Path, name: str) -> dict[str, Any] | None:
    path = _path(workdir, name)
    if not path.exists():
        return None
    if path.is_dir():
        # Delivery's own audit writes cannot make a declared directory look new.
        files = [p for p in sorted(path.rglob('*')) if p.is_file()
                 and not p.is_relative_to(workdir / ROOT)]
        return {'kind': 'directory', 'inode': path.stat().st_ino, 'files': {
            str(p.relative_to(path)): _fingerprint(workdir, str(p.relative_to(workdir)))
            for p in files
        }}
    stat = path.stat()
    return {'kind': 'file', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns,
            'inode': stat.st_ino}


def _pending(workdir: Path, node: str) -> Path:
    return workdir / ROOT / 'pending' / (hashlib.sha256(node.encode()).hexdigest() + '.json')


def begin(workdir: Path, node: str, prompt: str, writes: list[str], *, reuse: bool = False) -> dict[str, Any]:
    """Snapshot outputs and supply the current ledger before dispatching one agent.

    DSH can report the same due node repeatedly; reuse keeps its original output
    snapshot until completion rather than mistaking an in-flight write for old data.
    A changed prompt or live ledger is supplied afresh and archived in the context
    history. A DSH view records context supplied to the CEO, not proof of forwarding.
    """
    from refactory_dsh.steering import Ledger, parse_entries, prompt_with_ledger

    workdir = Path(workdir).resolve()
    pending = _pending(workdir, node)
    previous = None
    if reuse and pending.exists():
        previous = json.loads(pending.read_text())
        if previous['writes'] != writes:
            raise ValueError(f'agent {node!r} output declarations changed during delivery')
    ledger = Ledger(workdir)
    ledger_text = ledger.path.read_text() if ledger.exists() else ""
    entries = parse_entries(ledger_text)
    annotated = prompt_with_ledger(prompt, ledger, entries=entries)
    if previous is not None and previous['prompt'] == annotated:
        return previous
    before = previous['before'] if previous is not None else {name: _fingerprint(workdir, name) for name in writes}
    identifier = previous['id'] if previous is not None else uuid.uuid4().hex
    directory = workdir / ROOT / identifier
    directory.mkdir(parents=True, exist_ok=True)
    contexts = list(previous.get('contexts', [])) if previous is not None else []
    context = {'at': _now(), 'entries': entries,
               'prompt': str(ROOT / identifier / f'context-{len(contexts)}.prompt.txt'),
               'ledger': str(ROOT / identifier / f'context-{len(contexts)}.ledger.md')}
    (workdir / context['prompt']).write_text(annotated)
    (workdir / context['ledger']).write_text(ledger_text)
    contexts.append(context)
    (directory / 'prompt.txt').write_text(annotated)
    if ledger_text:
        (directory / 'ledger.md').write_text(ledger_text)
    record = {'id': identifier, 'node': node,
              'started': previous['started'] if previous is not None else context['at'], 'writes': writes,
              'before': before, 'prompt': annotated, 'entries': entries, 'contexts': contexts,
              'receipt': str(ROOT / identifier / 'receipt.json')}
    (directory / 'receipt.json').write_text(json.dumps({
        **{key: value for key, value in record.items() if key != 'prompt'},
        'status': 'pending', 'artifacts': before,
    }, indent=2) + '\n')
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(record, indent=2) + '\n')
    return record


def finish(workdir: Path, node: str, *, final_response: str | None = None,
           capture_final: bool = False) -> dict[str, Any]:
    """Preserve fresh tool-written files; capture final text only as the fallback.

    Every required output must be newly delivered, including unchanged bytes that
    were actually rewritten (stat metadata changes). Pre-existing untouched files
    cannot establish success. No model text is stripped or reformatted.
    """
    workdir = Path(workdir).resolve()
    pending = _pending(workdir, node)
    if not pending.exists():
        raise ValueError(f'agent {node!r} was not dispatched; no delivery snapshot exists')
    record = json.loads(pending.read_text())
    directory = workdir / ROOT / record['id']
    if final_response is not None:
        (directory / 'final-response.txt').write_text(final_response)
    writes = record['writes']
    after = {name: _fingerprint(workdir, name) for name in writes}
    fresh = {name for name in writes if after[name] is not None and after[name] != record['before'][name]}
    captured = None
    if capture_final and writes and writes[0] not in fresh and final_response is not None:
        first = _path(workdir, writes[0])
        if first.is_dir():
            raise ValueError(f'agent {node!r} returned text for directory output {writes[0]!r}')
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text(final_response)
        captured = writes[0]
        after[captured] = _fingerprint(workdir, captured)
        fresh.add(captured)
    missing = [name for name in writes if name not in fresh]
    receipt = {key: value for key, value in record.items() if key != 'prompt'}
    receipt.update(finished=_now(), status='failed' if missing else 'delivered',
                   delivery='context supplied; semantic compliance not verified',
                   artifacts=after, captured_final=captured,
                   final_response=str(ROOT / record['id'] / 'final-response.txt') if final_response is not None else None,
                   missing_or_unchanged=missing)
    (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    if missing:
        raise ValueError(f'agent {node!r} did not deliver required outputs: {", ".join(missing)} (missing or unchanged from before dispatch)')
    pending.unlink()
    return receipt


def invalidate(workdir: Path, nodes: list[str], reason: str) -> None:
    """Retire in-flight snapshots when an explicit skip or redo invalidates work."""
    for node in nodes:
        pending = _pending(workdir, node)
        if not pending.exists():
            continue
        record = json.loads(pending.read_text())
        receipt = workdir / ROOT / record['id'] / 'receipt.json'
        previous = json.loads(receipt.read_text())
        previous.update(status='superseded', finished=_now(), reason=reason)
        receipt.write_text(json.dumps(previous, indent=2) + '\n')
        pending.unlink()


def receipts_in(workdir: Path) -> dict[str, dict[str, Any]]:
    """Latest receipts, with a separate comparison to the current artifact state.

    A later deterministic node may legitimately enrich an agent's output. That is
    reported as a modification, not as proof that the earlier delivery failed.
    """
    workdir = Path(workdir)
    found: dict[str, dict[str, Any]] = {}
    for path in sorted((workdir / ROOT).glob('*/receipt.json')):
        try:
            receipt = json.loads(path.read_text())
            node = receipt['node']
            previous = found.get(node)
            if previous is not None and previous['started'] >= receipt['started']:
                continue
            current = {name: _fingerprint(workdir, name) for name in receipt['artifacts']}
            receipt['current_artifacts'] = current
            receipt['missing_now'] = [name for name, value in current.items() if value is None]
            receipt['current'] = current == receipt['artifacts']
            found[node] = receipt
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def main() -> int:
    """JSON stdin protocol for the configured DSH dispatch/completion bridge."""
    try:
        request = json.load(sys.stdin)
        workdir = Path.cwd()
        if request['action'] == 'begin':
            result = begin(workdir, request['node'], request['prompt'], request['writes'], reuse=True)
        elif request['action'] == 'finish':
            result = finish(workdir, request['node'])
        elif request['action'] == 'invalidate':
            nodes = request['nodes']
            reason = request['reason']
            if (not isinstance(nodes, list)
                    or not all(isinstance(node, str) for node in nodes)
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError('invalidate needs node ids and a non-empty reason')
            invalidate(workdir, nodes, reason)
            result = {'requested': nodes}
        else:
            raise ValueError('unknown agent delivery action')
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'agent delivery: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
