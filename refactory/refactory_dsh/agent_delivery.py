"""Agent artifact delivery and context receipts shared by the walker and DSH.

Receipts establish which context was supplied and which bytes were delivered. They
are not model acknowledgements or evidence that the work satisfies that context.
Artifact bytes and the model's final response are retained separately, unchanged.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refactory_dsh import target_guard
from refactory_dsh.invocations import EVIDENCE, REVIEWERS, WORKTREE_RECORD

ROOT = Path('.refactory/agent-delivery')
CONTRACT_START = '<!-- refactory worker execution -->'
CONTRACT_END = '<!-- end refactory worker execution -->'

_UNSET: object = object()
_worktree_state: Any | None | object = _UNSET
_delivery_identity: Any | None | object = _UNSET


def get_worktree_state() -> Any | None:
    """Return the registered worktree-state namespace, discovering it via entry points on first call."""
    global _worktree_state
    if _worktree_state is _UNSET:
        eps = importlib.metadata.entry_points(group='refactory.worktree_state')
        if len(eps) > 1:
            raise RuntimeError(
                f"expected at most one 'refactory.worktree_state' entry point, "
                f"got {len(eps)}: {[ep.name for ep in eps]}"
            )
        if eps:
            ep = next(iter(eps))
            _worktree_state = ep.load()
        else:
            _worktree_state = None
    return _worktree_state


def get_delivery_identity() -> Any | None:
    """Return the registered delivery-identity callable, discovering it via entry points on first call."""
    global _delivery_identity
    if _delivery_identity is _UNSET:
        eps = importlib.metadata.entry_points(group='refactory.delivery_identity')
        if len(eps) > 1:
            raise RuntimeError(
                f"expected at most one 'refactory.delivery_identity' entry point, "
                f"got {len(eps)}: {[ep.name for ep in eps]}"
            )
        if eps:
            ep = next(iter(eps))
            _delivery_identity = ep.load()
        else:
            _delivery_identity = None
    return _delivery_identity


def _execution_contract(workdir: Path, node: str, prompt: str, reads: list[str],
                        writes: list[str], model_context: dict[str, Any] | None,
                        *, set_aside: dict[str, str] | None = None) -> tuple[str, dict[str, Any]]:
    """Orient one node without making its worker discover the delivery protocol."""
    context = model_context or {}
    # Absolute path to refactory's own Python, for nested dispatch (worker_node,
    # sandboxed validation). This is NOT the target's interpreter.
    python = str(Path(context['python_bin']) / 'python') if context.get('python_bin') else sys.executable
    execution = {
        'node': node, 'workdir': str(workdir),
        'inputs': [str((workdir / name).resolve()) for name in reads],
        'outputs': [str(_path(workdir, name)) for name in writes],
        'python': python, 'refactory_source': str(Path(__file__).resolve().parent),
    }
    if context.get('runtime_root'):
        execution['runtime_root'] = str(context['runtime_root'])
    di = get_delivery_identity()
    if di is None and node in REVIEWERS and (workdir / EVIDENCE).exists():
        raise RuntimeError(
            "the delivery-identity entry point is not installed but this node "
            "needs authoring evidence; re-run `uv sync` or `./install.sh`"
        )
    if node in REVIEWERS:
        authoring = di(workdir) if di else None
        if authoring is not None:
            execution['authoring'] = authoring
    if set_aside:
        execution['set_aside'] = set_aside
    # A repeated view may already contain our block. Replace only that block;
    # the task and the current steering ledger remain intact.
    start = prompt.find(CONTRACT_START)
    end = prompt.find(CONTRACT_END, start)
    if start >= 0 and end >= start:
        suffix = prompt[end + len(CONTRACT_END):]
        if start == 0 and suffix.startswith('\n\n'):
            suffix = suffix[2:]
        prompt = prompt[:start] + suffix
    instructions = (
        'Execute this workflow node only. The paths below are exact; relative task paths use workdir. '
        'Use the supplied task, API and reference source first; inspect additional code when needed for this task. '
        'The runtime owns delivery receipts and advancing the graph. Its graphs, worker specs, delivery history '
        'and adjacent run reports are not completion instructions; inspect them only if this task explicitly needs them. '
        'Perform the checks required by the current task. After delivering the required outputs, return immediately '
        'with a brief completion response; do not execute later workflow stages or wait for a receipt. '
        'Do not duplicate a file artifact in the final response. If no output file is declared, return the requested result.'
    )
    if set_aside:
        instructions += (
            ' This step\'s previous outputs were moved to the paths listed in set_aside.'
            ' Read them there when the task revises earlier work, and write each output afresh at its declared path.'
        )
    block = f'{CONTRACT_START}\n{instructions}\n{json.dumps(execution, indent=2)}\n{CONTRACT_END}'
    return f'{block}\n\n{prompt}', execution


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


def _set_aside(workdir: Path, record: dict[str, Any], names: list[str],
               subfolder: str = 'refused') -> str | None:
    """Move an attempt's outputs out of the run, kept beside its receipt.

    Left in place, a refused output is what the redo's worker finds, and it answers that
    the file is already right rather than writing it afresh. On the CEO's route the redo
    is judged against the snapshot from before the refused attempt, where that same file
    would count as newly delivered.

    *subfolder* selects the archive directory under the receipt: ``refused`` for
    target-guard refusals, ``previous`` for outputs set aside before a re-dispatch.
    """
    if not names:
        return None
    parent = workdir / ROOT / record['id'] / subfolder
    archive = parent / str(sum(1 for _ in parent.glob('*')))
    moved: list[Path] = []
    for name in sorted(names, key=lambda value: len(Path(value).parts)):
        relative = Path(name)
        if any(parent in relative.parents for parent in moved):
            continue
        destination = archive / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _path(workdir, name).rename(destination)
        moved.append(relative)
    return str(archive.relative_to(workdir))


def _set_aside_stale_outputs(workdir: Path, node: str, reads: list[str],
                              writes: list[str]) -> dict[str, str] | None:
    """Move a previous attempt's surviving write-only outputs before a new dispatch.

    On re-dispatch — after a materialise refusal, a failed check, a CEO redo or
    a rerun — each declared output that is write-only (not also read) and still
    exists on disk is moved under the latest receipt's .refactory/agent-delivery/
    folder, so the worker writes each one fresh.  An output the node also reads
    stays in place (it must be read before writing anyway).  An output with no
    receipt from this node is left alone (some other step wrote it).
    """
    write_only = [w for w in writes if w not in reads]
    if not write_only:
        return None
    # Find the latest receipt for this node (the one whose outputs we are moving).
    latest: dict[str, Any] | None = None
    for receipt_path in sorted((workdir / ROOT).glob('*/receipt.json')):
        try:
            receipt = json.loads(receipt_path.read_text())
            if receipt.get('node') != node:
                continue
            if receipt.get('status') not in ('delivered', 'failed', 'superseded'):
                continue
            if latest is None or receipt.get('started', '') >= latest.get('started', ''):
                latest = receipt
        except (OSError, ValueError, KeyError):
            continue
    if latest is None:
        return None
    to_move = [name for name in write_only if _path(workdir, name).exists()]
    if not to_move:
        return None
    archive = _set_aside(workdir, latest, to_move, subfolder='previous')
    if archive is None:
        return None
    # Absolute, like the contract's inputs and outputs.
    return {name: str(workdir / archive / name) for name in to_move}


def _attempt_failed(workdir: Path, record: dict[str, Any]) -> bool:
    """Whether a finish for this pending delivery already ran and refused it."""
    try:
        return json.loads((workdir / record['receipt']).read_text()).get('status') == 'failed'
    except (OSError, ValueError, KeyError):
        return False


def _pending(workdir: Path, node: str) -> Path:
    return workdir / ROOT / 'pending' / (hashlib.sha256(node.encode()).hexdigest() + '.json')


def begin(workdir: Path, node: str, prompt: str, writes: list[str], *, reuse: bool = False,
          reads: list[str] | None = None, model_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Snapshot outputs and supply the current ledger before dispatching one agent.

    DSH can report the same due node repeatedly; reuse keeps its original output
    snapshot until completion rather than mistaking an in-flight write for old data.
    A changed prompt or live ledger is supplied afresh and archived in the context
    history. A DSH view records context supplied to the CEO, not proof of forwarding.
    """
    from refactory_dsh.steering import Ledger, parse_entries, prompt_with_ledger

    workdir = Path(workdir).resolve()
    ws = get_worktree_state()
    if ws is None and (workdir / WORKTREE_RECORD).exists():
        raise RuntimeError(
            "the worktree record exists but no worktree-state entry point is installed; "
            "re-run `uv sync` or `./install.sh`"
        )
    pending = _pending(workdir, node)
    previous = None
    if reuse and pending.exists():
        previous = json.loads(pending.read_text())
        if previous['writes'] != writes:
            raise ValueError(f'agent {node!r} output declarations changed during delivery')
        if previous.get('target') is not None and _attempt_failed(workdir, previous):
            # A refused attempt keeps its record for the retry. Judging the retry against
            # the snapshot from before the first attempt would refuse it for changes the
            # person already saw, and chose to keep, every time.
            previous['target'] = target_guard.watch(workdir, node)
            pending.write_text(json.dumps(previous, indent=2) + '\n')
    ledger = Ledger(workdir)
    ledger_text = ledger.path.read_text() if ledger.exists() else ""
    entries = parse_entries(ledger_text)
    annotated = prompt_with_ledger(prompt, ledger, entries=entries)
    set_aside = _set_aside_stale_outputs(workdir, node, reads or [], writes) if previous is None else None
    annotated, execution = _execution_contract(workdir, node, annotated, reads or [], writes, model_context,
                                               set_aside=set_aside)
    if previous is not None and previous['prompt'] == annotated:
        return previous
    before = previous['before'] if previous is not None else {name: _fingerprint(workdir, name) for name in writes}
    # Taken here because every route dispatches through this bracket (walker, worker
    # bridge, CEO subagent), so a change an output-only node makes to the target is
    # attributed to that node alone, not to whatever ran since prepare (#319).
    target = previous.get('target') if previous is not None else target_guard.watch(workdir, node)
    identifier = previous['id'] if previous is not None else uuid.uuid4().hex
    directory = workdir / ROOT / identifier
    directory.mkdir(parents=True, exist_ok=True)
    contexts = list(previous.get('contexts', [])) if previous is not None else []
    context = {'at': _now(), 'entries': entries, 'execution': execution,
               'prompt': str(ROOT / identifier / f'context-{len(contexts)}.prompt.txt'),
               'ledger': str(ROOT / identifier / f'context-{len(contexts)}.ledger.md')}
    (workdir / context['prompt']).write_text(annotated)
    (workdir / context['ledger']).write_text(ledger_text)
    contexts.append(context)
    (directory / 'prompt.txt').write_text(annotated)
    if ledger_text:
        (directory / 'ledger.md').write_text(ledger_text)
    prompt_hash = hashlib.sha256(annotated.encode()).hexdigest()
    record = {'id': identifier, 'node': node,
              'started': previous['started'] if previous is not None else context['at'], 'writes': writes,
              'before': before, 'prompt': annotated, 'entries': entries, 'contexts': contexts,
              'execution': execution, 'target': target, 'prompt_hash': prompt_hash,
              'receipt': str(ROOT / identifier / 'receipt.json')}
    (directory / 'receipt.json').write_text(json.dumps({
        **{key: value for key, value in record.items() if key not in ('prompt', 'target')},
        'status': 'pending', 'artifacts': before,
    }, indent=2) + '\n')
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(record, indent=2) + '\n')
    return record


def finish(workdir: Path, node: str, *, final_response: str | None = None,
           capture_final: bool = False,
           sent_prompt_hash: str | None = None,
           sent_prompt: str | None = None,
           unresolved_keys: list[str] | None = None) -> dict[str, Any]:
    """Preserve fresh tool-written files; capture final text only as the fallback.

    Every required output must be newly delivered, including unchanged bytes that
    were actually rewritten (stat metadata changes). Pre-existing untouched files
    cannot establish success. No model text is stripped or reformatted.
    """
    workdir = Path(workdir).resolve()
    ws = get_worktree_state()
    if ws is None and (workdir / WORKTREE_RECORD).exists():
        raise RuntimeError(
            "the worktree record exists but no worktree-state entry point is installed; "
            "re-run `uv sync` or `./install.sh`"
        )
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
    fail_changes, warn_changes = target_guard.strayed(
        workdir, record.get('target'))
    set_aside = (_set_aside(workdir, record,
                            [name for name in writes if name in fresh])
                 if fail_changes else None)
    all_changes = fail_changes + warn_changes
    receipt = {key: value for key, value in record.items() if key not in ('prompt', 'target')}
    receipt['prompt_hash'] = record.get('prompt_hash')
    receipt['prompt_path'] = str(ROOT / record['id'] / 'prompt.txt')
    if sent_prompt_hash is not None:
        receipt['sent_prompt_hash'] = sent_prompt_hash
    if sent_prompt is not None:
        sent_path = directory / 'sent-prompt.txt'
        sent_path.write_text(sent_prompt)
        receipt['sent_prompt_path'] = str(ROOT / record['id'] / 'sent-prompt.txt')
    if unresolved_keys:
        receipt['unresolved_keys'] = unresolved_keys
    receipt.update(finished=_now(), status='failed' if missing or fail_changes else 'delivered',
                   delivery='context supplied; semantic compliance not verified',
                   artifacts=after, captured_final=captured,
                   final_response=str(ROOT / record['id'] / 'final-response.txt') if final_response is not None else None,
                   missing_or_unchanged=missing, **({'target_changes': all_changes} if all_changes else {}),
                   **({'set_aside': set_aside} if set_aside else {}))
    (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    pending.unlink()
    if warn_changes:
        target_guard.warn(
            f"while agent {record['node']!r} ran, files outside its "
            f"worktree changed: {'; '.join(warn_changes)}. The run works "
            "in its own worktree, so these changes are not its result; "
            "check whether another session or person made them.")
    # Refused even when the declared output arrived: the output may be a summary of
    # edits that never pass through the step that writes the artifact.
    if fail_changes:
        raise ValueError(target_guard.refusal(
            record['node'], record['target']['root'],
            fail_changes, writes, set_aside=set_aside))
    if missing:
        raise ValueError(
            f'agent {record["node"]!r} did not deliver required outputs: '
            f'{", ".join(missing)} (missing or unchanged from before dispatch)')
    # Check whether the git configuration or hooks the run's worktree shares
    # with its target changed during this step (#347).  Beside the existing
    # target-guard bracket.
    wt_found = ws.recorded(workdir) if ws else None
    if wt_found is not None and ws.shared_changed(wt_found):
        wt_msg = (f"the git configuration or hooks the run's worktree shares "
                  f"with {wt_found.target} changed during {node}; check them "
                  f"before your next commit there")
        if record.get('target') is not None:
            # This step was output-only (target_guard.watch returned a snapshot
            # for it), so a shared-state change fails the step.
            raise ValueError(f"agent {node!r} changed shared git state: {wt_msg}")
        else:
            target_guard.warn(wt_msg)
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
            import re as _re
            from refactory_dsh.run import _knobs, _read_facts
            from refactory_dsh.nn.executor import RunContext, render
            prompt = request['prompt']
            context = RunContext(workdir, knobs=_knobs(), facts=_read_facts(workdir))
            rendered = render(prompt, context)
            # Detect unresolved template keys from the template itself (not
            # substituted values).  Record as a warning, not a refusal: the
            # steering ledger folds the brief verbatim into every template,
            # so a brief containing {{word}} must not block begin (#347, C2).
            # Strip the STEERING_START…STEERING_END block before scanning so
            # keys inside the brief (folded in via the ledger) are not warned.
            from refactory_dsh.steering import STEERING_START, STEERING_END
            scan_prompt = prompt
            st_start = scan_prompt.find(STEERING_START)
            st_end = scan_prompt.find(STEERING_END, st_start) if st_start >= 0 else -1
            if st_start >= 0 and st_end >= st_start:
                scan_prompt = scan_prompt[:st_start] + scan_prompt[st_end + len(STEERING_END):]
            values = {**context.knobs, **context.facts}
            template_keys = _re.findall(r'\{\{\s*([A-Za-z_]\w*)\s*\}\}', scan_prompt)
            unresolved = [k for k in template_keys if k.strip() not in values]
            result = begin(workdir, request['node'], rendered, request['writes'], reuse=True,
                           reads=request.get('reads'))
            # The bridge reads the prompt; the target snapshot is this module's own state.
            result = {key: value for key, value in result.items() if key != 'target'}
            if unresolved:
                result['unresolved_keys'] = unresolved
        elif request['action'] == 'finish':
            sent_prompt_hash = request.get('sent_prompt_hash')
            sent_prompt = request.get('sent_prompt')
            unresolved_keys = request.get('unresolved_keys')
            result = finish(workdir, request['node'],
                            sent_prompt_hash=sent_prompt_hash,
                            sent_prompt=sent_prompt,
                            unresolved_keys=unresolved_keys)
        elif request['action'] == 'invalidate':
            nodes = request['nodes']
            reason = request['reason']
            if (not isinstance(nodes, list)
                    or not all(isinstance(node, str) for node in nodes)
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError('invalidate needs node ids and a non-empty reason')
            invalidate(workdir, nodes, reason)
            result = {'requested': nodes}
        elif request['action'] == 'reviewers':
            result = {'reviewers': list(REVIEWERS)}
        elif request['action'] == 'pending-prompt':
            node = request['node']
            pending = _pending(workdir, node)
            if pending.exists():
                record = json.loads(pending.read_text())
                result = {'prompt': record.get('prompt', '')}
            else:
                result = {'prompt': ''}
        else:
            raise ValueError('unknown agent delivery action')
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'agent delivery: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
