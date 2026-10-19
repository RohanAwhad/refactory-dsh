"""Detached supervisor process for a graph run's owner lifecycle.

Runnable as ``python -m refactory_dsh.supervisor <root>``.  Starts the owner
child, waits on it, classifies the exit, records start failures, and respawns
transient exits up to a bounded number of times.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refactory_dsh import run_state

logger = logging.getLogger(__name__)

START_FAILURE_WINDOW_S = 10.0


def max_respawns_from_env() -> int:
    """Read REFACTORY_MAX_RESPAWNS from the environment, default 3."""
    raw = os.environ.get('REFACTORY_MAX_RESPAWNS')
    if raw is None:
        return 3
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return 3
    return max(value, 0)


def record_start_failure(root: Path, pid: int, exit_code: int,
                         spine_before: str, spine_after: str,
                         reason: str | None = None) -> None:
    """Write start-failure.json with the reason from recovery.log or a given reason."""
    root = Path(root)
    if reason is None:
        reason = _last_log_lines(root)
    failure: dict[str, Any] = {
        'at': datetime.now(timezone.utc).isoformat(),
        'pid': pid,
        'exit_code': exit_code,
        'reason': reason,
        'spine_status_at_start': spine_before,
        'spine_status_at_exit': spine_after,
    }
    path = root / '.refactory/start-failure.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(failure, indent=2))
    tmp.replace(path)


def _last_log_lines(root: Path, max_lines: int = 5, max_chars: int = 500) -> str:
    """Extract the last non-empty lines from recovery.log."""
    log_path = root / 'recovery.log'
    if not log_path.exists():
        return 'owner exited with no log output'
    try:
        text = log_path.read_text(errors='replace')
    except OSError:
        return 'could not read recovery.log'
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    tail = lines[-max_lines:] if lines else ['(empty log)']
    combined = '\n'.join(tail)
    return combined[:max_chars] if len(combined) > max_chars else combined


def _append_supervision_history(root: Path, entry: dict[str, Any]) -> None:
    """Append a respawn entry to supervision-history.json."""
    path = root / '.refactory/supervision-history.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        history = json.loads(path.read_text()) if path.exists() else []
    except (OSError, json.JSONDecodeError):
        history = []
    if not isinstance(history, list):
        history = []
    history.append(entry)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(history, indent=2))
    tmp.replace(path)


def _read_resume_pending(root: Path) -> dict | None:
    """Read and remove resume-pending.json if present and SHA-valid.

    The file is consumed only when its ``checkpoint_sha256`` matches the
    current ``spine-checkpoint.json`` bytes.  A mismatch means the checkpoint
    has changed since the resume was requested; the stale pending is deleted
    and a start-failure is recorded so that the mismatch is visible in
    ``refactory status``.
    """
    path = root / '.refactory/resume-pending.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if not isinstance(data, dict):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    # Validate checkpoint_sha256
    checkpoint_path = root / '.refactory/spine-checkpoint.json'
    try:
        current_sha = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    except OSError:
        current_sha = None
    pending_sha = data.get('checkpoint_sha256')
    if current_sha is None or pending_sha != current_sha:
        reason = (f'resume-pending.json dropped: checkpoint SHA mismatch '
                  f'(pending {pending_sha}, current {current_sha})')
        logger.warning('%s', reason)
        record_start_failure(root, os.getpid(), 0,
                             run_state.spine_status(root), run_state.spine_status(root),
                             reason=reason)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return data


def _build_owner_argv(root: Path) -> tuple[list[str], str]:
    """Build the owner command line from run-config.json. Returns (argv, project)."""
    config = json.loads((root / '.refactory/run-config.json').read_text())
    mode = config['mode']
    project = config.get('project', '.')
    argv = [sys.executable, '-m', 'refactory_dsh.interactive', mode,
            '--workdir', str(root), '--headless', '--resume']
    return argv, project


def _start_child(root: Path) -> subprocess.Popen:
    """Start the owner child process."""
    argv, project = _build_owner_argv(root)
    log_path = root / 'recovery.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = log_path.open('ab')
    child = subprocess.Popen(
        argv, cwd=project,
        env={**os.environ, 'PYTHONUNBUFFERED': '1'},
        stdout=output, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return child


def supervise(root: Path, *, max_respawns: int | None = None,
              first_exit_code: int | None = None,
              owner_start_time: float | None = None) -> int | None:
    """The main supervision loop.

    Acquires an exclusive lock, starts and watches the owner child,
    respawns on transient exits, records start failures, and consumes
    resume-pending.json.

    When *first_exit_code* is given (inline host), the loop begins with
    that exit code instead of starting an initial child.  Returns the
    final child exit code as ``int`` in that case, or ``None`` for the
    detached host.

    The lock also prevents nesting: a supervised child that calls
    ``supervise()`` itself fails the non-blocking flock and returns
    immediately, because ``Popen`` does not pass the lock descriptor to
    children and the parent still holds the file lock.
    """
    root = Path(root).resolve()
    if max_respawns is None:
        max_respawns = max_respawns_from_env()
    inline = first_exit_code is not None

    # Acquire exclusive lock
    lock_path = root / '.refactory/supervisor.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        os.close(lock_fd)
        return first_exit_code  # another supervisor is running

    _signal_exit = False
    _current_child: subprocess.Popen | None = None

    def _handle_signal(signum: int, _frame: Any) -> None:
        nonlocal _signal_exit
        _signal_exit = True
        if _current_child is not None and _current_child.poll() is None:
            try:
                os.kill(_current_child.pid, signum)
            except (ProcessLookupError, OSError):
                pass

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    respawn_count = 0
    last_exit_code = first_exit_code
    sf_path = root / '.refactory/start-failure.json'

    try:
        config = json.loads((root / '.refactory/run-config.json').read_text())
        name = config['mode'].replace('/', '-')
        from refactory_dsh import recovery

        if not inline:
            # Detached host: initial prepare + start
            try:
                recovery.prepare(root, name)
            except ValueError as exc:
                record_start_failure(root, os.getpid(), 1, run_state.spine_status(root),
                                     run_state.spine_status(root), reason=str(exc))
                return None

            child_start_time = int(time.time() * 1000) / 1000
            child = _start_child(root)
            _current_child = child

            # Clear start-failure from a previous cycle if we got this far
            if sf_path.exists():
                try:
                    sf_path.unlink()
                except OSError:
                    pass
        else:
            # Inline host: the owner already ran and exited
            child = None
            child_start_time = owner_start_time if owner_start_time is not None else int(time.time() * 1000) / 1000

        while True:
            if child is not None:
                start_time = time.monotonic()
                spine_before = run_state.spine_status(root)

                child.wait()
                exit_code = child.returncode
                last_exit_code = exit_code

                elapsed = time.monotonic() - start_time
                spine_after = run_state.spine_status(root)
            else:
                # First iteration of inline host
                exit_code = first_exit_code  # type: ignore[assignment]
                last_exit_code = exit_code
                spine_before = run_state.spine_status(root)
                spine_after = spine_before
                elapsed = 0.0

            if _signal_exit:
                return last_exit_code if inline else None

            # Check for resume-pending.json (dm-81 fix)
            pending = _read_resume_pending(root)
            if pending is not None:
                # A person's explicit request — reset counter and start a new child
                respawn_count = 0
                try:
                    recovery.prepare(root, name)
                except ValueError as exc:
                    pid_for_record = child.pid if child is not None else os.getpid()
                    record_start_failure(root, pid_for_record, exit_code,
                                         spine_before, spine_after, reason=str(exc))
                    return last_exit_code if inline else None
                child_start_time = int(time.time() * 1000) / 1000
                child = _start_child(root)
                _current_child = child
                # Clear any start-failure from the previous child
                if sf_path.exists():
                    try:
                        sf_path.unlink()
                    except OSError:
                        pass
                continue

            # Check if supervision is needed — single decision function
            if not run_state.needs_supervision(root, since=child_start_time):
                return last_exit_code if inline else None

            # Supervision is needed — record start failure if the child
            # exited quickly with no status change (a deliberate stop has
            # already returned above via needs_supervision).
            if child is not None and elapsed < START_FAILURE_WINDOW_S and spine_before == spine_after and exit_code != 0:
                record_start_failure(root, child.pid, exit_code, spine_before, spine_after)

            # Classify the exit
            if respawn_count >= max_respawns:
                pid_for_record = child.pid if child is not None else os.getpid()
                reason = f'supervision exhausted after {respawn_count} respawn(s)'
                record_start_failure(root, pid_for_record, exit_code, spine_before, spine_after,
                                     reason=reason)
                return last_exit_code if inline else None

            # Try recovery.prepare() to distinguish transient vs deterministic
            try:
                recovery.prepare(root, name)
            except ValueError as exc:
                # Deterministic refusal — do not retry
                pid_for_record = child.pid if child is not None else os.getpid()
                record_start_failure(root, pid_for_record, exit_code, spine_before, spine_after,
                                     reason=str(exc))
                return last_exit_code if inline else None
            except Exception:
                # Transient I/O or other error — treat as transient
                pass

            # Transient exit — respawn
            respawn_count += 1
            history_entry = {
                'at': datetime.now(timezone.utc).isoformat(),
                'attempt': respawn_count,
                'reason': f'owner exited with code {exit_code}; spine status {spine_after!r} is not terminal',
                'exit_code_of_previous': exit_code,
                'spine_status': spine_after,
                'supervisor_pid': os.getpid(),
            }

            child_start_time = int(time.time() * 1000) / 1000
            child = _start_child(root)
            _current_child = child
            history_entry['child_pid'] = child.pid

            _append_supervision_history(root, history_entry)

            # Emit event
            try:
                from refactory_dsh.live import emit
                emit(root, 'supervisor_respawn', state='respawned',
                     summary=f'respawn #{respawn_count}: {history_entry["reason"]}',
                     child_pid=child.pid, attempt=respawn_count)
            except Exception:
                pass  # event logging must not break the supervisor

            # Clear start-failure on successful respawn
            if sf_path.exists():
                try:
                    sf_path.unlink()
                except OSError:
                    pass

    finally:
        os.close(lock_fd)


if __name__ == '__main__':
    import sys as _sys

    if len(_sys.argv) < 2:
        print('usage: python -m refactory_dsh.supervisor <root>', file=_sys.stderr)
        raise SystemExit(2)
    root_dir = Path(_sys.argv[1]).resolve()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    supervise(root_dir)
