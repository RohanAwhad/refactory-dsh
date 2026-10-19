"""Canonical state classification for a graph run's supervision decisions.

Pure functions that read spine-status.json and spine-checkpoint.json to decide
whether a run is terminal, deliberately paused, or needs automatic supervision.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

TERMINAL = frozenset({'done', 'cancelled', 'failed'})
DELIBERATELY_PAUSED = frozenset({'halted', 'paused', 'waiting_user', 'refused'})


def is_terminal(status: str) -> bool:
    """True when the spine status is done, cancelled, or failed."""
    return status in TERMINAL


def is_deliberately_paused(status: str) -> bool:
    """True when the spine status is halted, paused, waiting_user, or refused."""
    return status in DELIBERATELY_PAUSED


def is_alive(pid: int) -> bool:
    """Whether the process is running.

    ``kill(pid, 0)`` succeeds for a zombie, and a run this process spawned and
    never waited on stays a zombie until it is reaped -- so a finished run would
    read as alive from the very process that started it.
    """
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass  # not our child: the signal probe below is the only answer
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def spine_status(root: Path) -> str:
    """Read and return the status string from spine-status.json."""
    path = Path(root) / '.refactory/spine-status.json'
    try:
        data = json.loads(path.read_text())
        return str(data.get('status', '')) if isinstance(data, dict) else ''
    except (OSError, json.JSONDecodeError, ValueError):
        return ''


def needs_supervision(root: Path, *, since: float | None = None) -> bool:
    """True when the run needs automatic supervision (owner dead, not terminal/paused).

    When *since* is given (epoch seconds from ``time.time()``), a
    ``stop-requested.json`` whose ``at`` is at or after *since* suppresses
    supervision — the stop was deliberate for this owner.  An older stop
    (from a previous owner) does not block a resumed run's supervision.
    """
    root = Path(root)
    status = spine_status(root)
    if not status:
        return False
    if is_terminal(status):
        return False
    if is_deliberately_paused(status):
        return False
    # A stop requested since the current owner started is deliberate.
    if since is not None:
        stop_path = root / '.refactory/stop-requested.json'
        if stop_path.exists():
            try:
                stop_data = json.loads(stop_path.read_text())
                stop_at = stop_data.get('at')
                if isinstance(stop_at, (int, float)) and stop_at >= since:
                    return False
            except (OSError, json.JSONDecodeError, ValueError):
                pass
    # Check owner liveness
    try:
        checkpoint = json.loads((root / '.refactory/spine-checkpoint.json').read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    pid = checkpoint.get('owner_pid')
    if not isinstance(pid, int):
        return False
    if is_alive(pid):
        return False
    return True


def read_start_failure(root: Path) -> dict | None:
    """Read .refactory/start-failure.json if present."""
    path = Path(root) / '.refactory/start-failure.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def read_supervision_history(root: Path) -> list[dict]:
    """Read .refactory/supervision-history.json if present."""
    path = Path(root) / '.refactory/supervision-history.json'
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError, ValueError):
        return []
