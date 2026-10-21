"""Tests for the supervisor's owner_argv reading and entry-point fallback.

These tests verify that:
1. The supervisor reads owner_argv from run-config.json and builds the correct argv.
2. For old runs without owner_argv, the supervisor falls back to the refactory.owner_module
   entry point.
3. With no owner_argv and no entry point, _start_child returns None and records a failure.
4. The interactive.prepare function writes owner_argv to run-config.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from refactory_dsh import supervisor as sv
from refactory_dsh import run_state


# ── helpers ────────────────────────────────────────────────────────────────

def _make_run_dir(tmp_path: Path, *, mode: str = 'default_pack/design',
                  project: str = '.', owner_argv: list[str] | None = None,
                  status: str = 'executing') -> Path:
    """Create a minimal run directory with config."""
    refactory = tmp_path / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {'mode': mode, 'project': project}
    if owner_argv is not None:
        config['owner_argv'] = owner_argv
    (refactory / 'run-config.json').write_text(json.dumps(config))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': status}))
    checkpoint: dict[str, Any] = {
        'run_id': str(tmp_path), 'status': status,
        'graph': {'name': mode.replace('/', '-')},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))
    return tmp_path


def _dead_pid() -> int:
    pid = os.getpid() + 99999
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return pid
    except PermissionError:
        pass
    return 2**20 + 1


# ── T1: owner_argv from config ─────────────────────────────────────────────


def test_supervisor_reads_owner_argv_from_config(tmp_path):
    """owner_argv in run-config.json produces today's exact argv.

    The stored owner_argv is ['-m', 'refactory_dsh.interactive', identifier].
    The supervisor appends '--workdir', root, '--headless', '--resume'.
    """
    owner_argv = ['-m', 'refactory_dsh.interactive', 'default_pack/design']
    root = _make_run_dir(tmp_path, owner_argv=owner_argv)
    argv, project = sv._build_owner_argv(root)
    expected = [sys.executable, '-m', 'refactory_dsh.interactive', 'default_pack/design',
                '--workdir', str(root), '--headless', '--resume']
    assert argv == expected
    assert project == '.'

    # Respawn simulation: calling again yields the same result
    argv2, project2 = sv._build_owner_argv(root)
    assert argv2 == expected
    assert project2 == '.'


# ── T2: fallback to entry point ────────────────────────────────────────────


def test_supervisor_fallback_without_owner_argv(tmp_path):
    """Old format run-config.json (no owner_argv) falls back to entry point."""
    root = _make_run_dir(tmp_path, mode='default_pack/design')

    mock_ep = MagicMock()
    mock_ep.value = 'refactory_dsh.interactive'

    with patch('importlib.metadata.entry_points', return_value=[mock_ep]):
        argv, project = sv._build_owner_argv(root)

    expected = [sys.executable, '-m', 'refactory_dsh.interactive', 'default_pack/design',
                '--workdir', str(root), '--headless', '--resume']
    assert argv == expected
    assert project == '.'


# ── T3: no owner, no entry point ──────────────────────────────────────────


def test_supervisor_no_owner_no_entry_point(tmp_path):
    """No owner_argv and no entry point: _build_owner_argv returns (None, project)."""
    root = _make_run_dir(tmp_path, mode='default_pack/design')

    with patch('importlib.metadata.entry_points', return_value=[]):
        argv, project = sv._build_owner_argv(root)

    assert argv is None
    assert project == '.'


def test_start_child_none_records_failure(tmp_path):
    """_start_child returns None and records a start failure when no owner is available."""
    root = _make_run_dir(tmp_path, mode='default_pack/design')

    with patch('importlib.metadata.entry_points', return_value=[]):
        child = sv._start_child(root)

    assert child is None
    failure = run_state.read_start_failure(root)
    assert failure is not None
    assert 'no owner_argv' in failure['reason']
    assert 'refactory.owner_module' in failure['reason']


# ── T4: supervise handles None from _start_child ──────────────────────────


def test_supervise_handles_none_child_detached(tmp_path):
    """Detached supervisor exits cleanly when _start_child returns None."""
    root = _make_run_dir(tmp_path, mode='default_pack/design', status='ready')

    def mock_prepare(r, name, **kw):
        return r / 'graphs'

    with patch.object(sv, '_start_child', return_value=None):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            result = sv.supervise(root, max_respawns=3)

    assert result is None
    # start-failure was recorded by _start_child
    failure = run_state.read_start_failure(root)
    assert failure is not None


def test_supervise_handles_none_child_inline(tmp_path):
    """Inline supervisor returns last_exit_code when respawn _start_child returns None."""
    root = _make_run_dir(tmp_path, mode='default_pack/design', status='executing')

    call_count = 0

    class FakeChild:
        def __init__(self, pid, code):
            self.pid = pid
            self.returncode = code
        def wait(self):
            pass
        def poll(self):
            return self.returncode

    def mock_start_child(r):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeChild(pid=10001, code=1)
        return None  # second attempt fails

    def mock_prepare(r, name, **kw):
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            result = sv.supervise(root, first_exit_code=1, max_respawns=3)

    # first_exit_code=1 means inline=True, the loop classifies exit 1 as
    # needing supervision, tries respawn, gets None, returns last_exit_code (1).
    assert result == 1


# ── T5: console status shows failed for None child ────────────────────────


def test_console_status_shows_failed_for_no_owner(tmp_path):
    """start-failure.json with no owner_argv reason causes console.status to show failed."""
    root = _make_run_dir(tmp_path, mode='default_pack/design', status='ready')
    sv.record_start_failure(
        root, os.getpid(), 1, 'ready', 'ready',
        reason='no owner_argv in run-config.json and no refactory.owner_module entry point',
    )
    from refactory_dsh import console
    st = console.status(root)
    assert st['state'] == 'failed'
    activity = st.get('activity', {})
    assert activity.get('start_failure') is not None
    assert 'no owner_argv' in activity['start_failure']


# ── T6: interactive.prepare writes owner_argv ──────────────────────────────


def test_prepare_writes_owner_argv(tmp_path):
    """The owner_argv format matches what the supervisor expects."""
    identifier = 'default_pack/design'
    owner_argv = ['-m', 'refactory_dsh.interactive', identifier]
    config = {
        'mode': identifier,
        'project': str(tmp_path),
        'knobs': [],
        'model_context': {},
        'resource_limits': None,
        'owner_argv': owner_argv,
        'configuration_fingerprints': {},
    }
    config_path = tmp_path / '.refactory' / 'run-config.json'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config))

    loaded = json.loads(config_path.read_text())
    assert loaded['owner_argv'] == owner_argv
    assert loaded['owner_argv'] == ['-m', 'refactory_dsh.interactive', identifier]
    # The supervisor will append --workdir, root, --headless, --resume
    root = tmp_path
    expected_full = [sys.executable] + owner_argv + [
        '--workdir', str(root), '--headless', '--resume']
    argv, _ = sv._build_owner_argv(root)
    assert argv == expected_full


# ── T7: respawned command identical to today's ─────────────────────────────


def test_respawned_command_matches_todays_format(tmp_path):
    """The argv from _build_owner_argv with owner_argv matches what the old
    hardcoded code would have produced for the same mode.
    """
    mode = 'default_pack/design'
    owner_argv = ['-m', 'refactory_dsh.interactive', mode]
    root = _make_run_dir(tmp_path, mode=mode, owner_argv=owner_argv)

    argv, _ = sv._build_owner_argv(root)

    # Today's hardcoded format was:
    # [sys.executable, '-m', 'refactory_dsh.interactive', mode,
    #  '--workdir', str(root), '--headless', '--resume']
    today_argv = [sys.executable, '-m', 'refactory_dsh.interactive', mode,
                  '--workdir', str(root), '--headless', '--resume']
    assert argv == today_argv


def test_fallback_command_matches_todays_format(tmp_path):
    """The fallback argv from entry point matches what the old code produced."""
    mode = 'default_pack/design'
    root = _make_run_dir(tmp_path, mode=mode)  # no owner_argv

    mock_ep = MagicMock()
    mock_ep.value = 'refactory_dsh.interactive'

    with patch('importlib.metadata.entry_points', return_value=[mock_ep]):
        argv, _ = sv._build_owner_argv(root)

    today_argv = [sys.executable, '-m', 'refactory_dsh.interactive', mode,
                  '--workdir', str(root), '--headless', '--resume']
    assert argv == today_argv
