"""Explicit recovery of a stopped graph boundary, never of unknown side effects."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def fingerprint(path: Path) -> Any:
    if not path.exists():
        return None
    if path.is_dir():
        return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(path.rglob('*')) if p.is_file() and not p.is_symlink()}
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configuration_fingerprint(path: Path) -> Any:
    """Ignore interpreter caches, but retain executable source and package data."""
    if not path.is_dir():
        return fingerprint(path)
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob('*')) if p.is_file() and not p.is_symlink()
            and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')}


def configuration_fingerprints(root: Path, identifier: str, project: Path) -> dict[str, Any]:
    """Pin the prepared graph, declarations and imported mode implementation."""
    import importlib.util
    import inspect
    from refactory_dsh import lowering, registry
    package = registry.where(identifier.split('/', 1)[0], project)
    if package is None:
        raise ValueError('cannot locate the source of the prepared mode')
    manifest, mode = registry.resolve(project, identifier)
    paths = [Path(root) / 'graphs', Path(root) / lowering.DECLARATIONS, package]
    source = inspect.getsourcefile(mode)
    if source:
        paths.append(Path(source))
    # Packages may name library ops instead of containing their implementation
    # (the default fast modes do). Pin those executable modules as well.
    for module in manifest.ops:
        spec = importlib.util.find_spec(module)
        if spec is None or not spec.origin or not Path(spec.origin).is_file():
            raise ValueError(f'cannot fingerprint declared operation module {module!r}')
        paths.append(Path(spec.origin))
    return {str(path.resolve()): _configuration_fingerprint(path) for path in paths}


def prepare(root: Path, name: str, *, resource_limits: dict[str, Any] | None = None,
            acknowledge_failed: bool = False) -> Path:
    root = Path(root).resolve()
    checkpoint = root / '.refactory/spine-checkpoint.json'
    payload = checkpoint.read_bytes()
    saved = json.loads(payload)
    if saved.get('run_id') != str(root.resolve()) or saved.get('graph', {}).get('name') != name:
        raise ValueError('checkpoint belongs to a different run or mode')
    pid = saved.get('owner_pid')
    if pid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError('the graph owner is still alive; use its boundary resume control')
    # When the parent's start() already wrote resume-request.json with
    # acknowledge_failed: true, the child inherits the acknowledgement
    # from the file rather than requiring it as a call argument — but only
    # when the file's checkpoint_sha256 matches the current checkpoint bytes,
    # so a stale request left behind by a crashed child cannot let a later
    # plain resume of a failed run through.
    if not acknowledge_failed:
        existing_request = root / '.refactory/resume-request.json'
        if existing_request.is_file():
            try:
                existing = json.loads(existing_request.read_text())
                if (existing.get('acknowledge_failed') is True
                        and existing.get('checkpoint_sha256') == hashlib.sha256(payload).hexdigest()):
                    acknowledge_failed = True
            except (OSError, json.JSONDecodeError, ValueError):
                pass
    allowed = {'ready', 'paused', 'halted', 'done', 'cancelled', 'waiting_user', 'refused'}
    if acknowledge_failed:
        allowed.add('failed')
    # An executing checkpoint is allowed only when a matching acknowledgement exists
    # in the resume-request.json file (written by graph_control.acknowledge).
    if saved.get('status') == 'executing':
        existing_request = root / '.refactory/resume-request.json'
        ack_ok = False
        if existing_request.is_file():
            try:
                existing = json.loads(existing_request.read_text())
                if (existing.get('acknowledge_executing') is True
                        and existing.get('checkpoint_sha256') == hashlib.sha256(payload).hexdigest()):
                    ack_ok = True
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        if not ack_ok:
            raise ValueError('last node outcome is unknown or failed; inspect side effects before any explicit rerun')
        # With a valid acknowledgement, treat as halted for recovery purposes
        allowed.add('executing')
    if saved.get('status') not in allowed:
        raise ValueError('last node outcome is unknown or failed; inspect side effects before any explicit rerun')
    for pending in (root / '.refactory/agent-delivery/pending').glob('*.json'):
        if pending.is_file():
            raise ValueError('a dispatched worker outcome is unknown; automatic replay is unsafe')
    for name, expected in saved.get('file_fingerprints', {}).items():
        if fingerprint(root / name) != expected:
            raise ValueError(f'input/output {name!r} changed; dependent results need explicit invalidation')
    config = json.loads((root / '.refactory/run-config.json').read_text())
    expected_configuration = config.get('configuration_fingerprints')
    if not isinstance(expected_configuration, dict) or not expected_configuration:
        raise ValueError('saved run has no configuration fingerprints; automatic recovery cannot verify its source')
    for name, expected in expected_configuration.items():
        if _configuration_fingerprint(Path(name)) != expected:
            raise ValueError(f'prepared configuration {name!r} changed; dependent results need explicit invalidation')
    if resource_limits:
        from refactory_dsh.budgets import ResourceLimits, RunBudget
        RunBudget.from_path(root / '.refactory/resource-usage.json').resume(ResourceLimits.from_dict(resource_limits))
    request = root / '.refactory/resume-request.json'
    temporary = request.with_suffix('.tmp')
    request_data: dict[str, Any] = {'checkpoint_sha256': hashlib.sha256(payload).hexdigest()}
    if acknowledge_failed:
        request_data['acknowledge_failed'] = True
    temporary.write_text(json.dumps(request_data))
    temporary.replace(request)
    return root / 'graphs'


def start(root: Path, *, acknowledge_failed: bool = False) -> dict[str, Any]:
    """Resume a known stopped boundary via a detached supervisor process.

    The supervisor (``python -m refactory_dsh.supervisor <root>``) starts the
    owner child, waits on it, and respawns transient exits automatically.  One
    supervisor per run, held by a file lock.
    """
    import subprocess
    import sys
    from refactory_dsh.run_ownership import ownership
    root = Path(root).resolve()
    with ownership(root):
        config = json.loads((root / '.refactory/run-config.json').read_text())
        # Validate while owning the directory, then release before the child
        # acquires its own nonblocking lock. Otherwise a fast child can reject
        # its parent as the still-active owner. The child repeats validation;
        # an intervening invocation can refuse this queued request safely.
        prepare(root, config['mode'].replace('/', '-'), acknowledge_failed=acknowledge_failed)
    supervisor_log = root / '.refactory/supervisor.log'
    supervisor_log.parent.mkdir(parents=True, exist_ok=True)
    with supervisor_log.open('ab') as output:
        child = subprocess.Popen([sys.executable, '-m', 'refactory_dsh.supervisor', str(root)],
            cwd=config['project'],
            env={**os.environ, 'PYTHONUNBUFFERED': '1'}, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
    # Record the supervisor's PID in the console's launch record so that
    # console.stop finds and signals it exactly as it does a launched run's.
    # The supervisor forwards signals to its child.
    from refactory_dsh.console import CONSOLE_FILE
    console_path = root / CONSOLE_FILE
    try:
        existing = json.loads(console_path.read_text()) if console_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    if isinstance(existing, dict):
        existing['pid'] = child.pid
        console_path.write_text(json.dumps(existing, indent=2))
    return {'status': 'queued', 'action': 'resume', 'run_id': str(root), 'pid': child.pid,
            'message': 'Recovery requested for the saved boundary; inspect live state for confirmation.'}
