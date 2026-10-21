"""Tests for the supervisor's owner_argv reading and entry-point fallback.

These tests verify that:
1. The supervisor reads owner_argv from run-config.json and builds the correct argv.
2. For old runs without owner_argv, the supervisor falls back to the refactory.owner_module
   entry point.
3. With no owner_argv and no entry point, _start_child returns None and records a failure.
4. The interactive.prepare function writes owner_argv to run-config.json.
5. recovery.start → supervisor records start-failure for no owner (via in-process Popen).
6. The resume-pending and respawn code paths handle None from _start_child.
"""
from __future__ import annotations

import hashlib
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

    def mock_start_child_none(r):
        """Simulate _start_child returning None: record start failure then return None."""
        sv.record_start_failure(
            r, os.getpid(), 1,
            run_state.spine_status(r), run_state.spine_status(r),
            reason='no owner_argv in run-config.json and no '
                   'refactory.owner_module entry point')
        return None

    with patch.object(sv, '_start_child', side_effect=mock_start_child_none):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            result = sv.supervise(root, max_respawns=3)

    assert result is None
    # start-failure was recorded by _start_child
    failure = run_state.read_start_failure(root)
    assert failure is not None


def test_supervise_handles_none_child_inline(tmp_path):
    """Inline supervisor exits cleanly when the respawn _start_child returns None.

    Without the ``if child is None`` guard at the respawn site,
    ``history_entry['child_pid'] = child.pid`` raises ``AttributeError``.
    This test sets ``owner_pid`` to a dead process so
    ``needs_supervision`` returns True and the respawn site is reached.
    """
    root = _make_run_dir(tmp_path, mode='default_pack/design', status='executing')

    # Set owner_pid to a dead process so needs_supervision returns True.
    dead = _dead_pid()
    cp = json.loads((root / '.refactory/spine-checkpoint.json').read_text())
    cp['owner_pid'] = dead
    (root / '.refactory/spine-checkpoint.json').write_text(json.dumps(cp))

    call_count = 0

    def mock_start_child(r):
        nonlocal call_count
        call_count += 1
        # Simulate no owner: every call returns None.
        sv.record_start_failure(
            r, os.getpid(), 1,
            run_state.spine_status(r), run_state.spine_status(r),
            reason='no owner_argv in run-config.json and no '
                   'refactory.owner_module entry point')
        return None

    def mock_prepare(r, name, **kw):
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            result = sv.supervise(root, first_exit_code=1, max_respawns=3)

    # Inline returns last_exit_code; no crash (AttributeError would surface
    # without the guard).
    assert result == 1
    assert call_count >= 1, (
        f'_start_child was called {call_count} time(s); '
        'the respawn site was not reached — needs_supervision must return True')


# ── T5: recovery.start → supervisor records start-failure ─────────────────


def test_recovery_start_records_failure_for_no_owner(tmp_path):
    """recovery.start → supervisor in-process → start-failure for no owner.

    Patches subprocess.Popen so recovery.start runs the supervisor
    in-process rather than as a subprocess.  The supervisor discovers
    no owner_argv and no entry point, records start-failure.json, and
    exits.  Console status reflects the failure.
    """
    from contextlib import contextmanager
    from refactory_dsh import recovery, console

    root = _make_run_dir(tmp_path, mode='default_pack/design', status='ready')

    @contextmanager
    def fake_ownership(wd):
        yield 0

    def fake_popen(argv, **kwargs):
        # Run the supervisor in-process so we can assert its side effects.
        sv.supervise(root, max_respawns=0)
        m = MagicMock()
        m.pid = os.getpid()
        return m

    with (
        patch('refactory_dsh.run_ownership.ownership', fake_ownership),
        patch.object(recovery, 'prepare', lambda r, n, **kw: None),
        patch('refactory_dsh.pin.detect_drift', return_value=None),
        patch('subprocess.Popen', side_effect=fake_popen),
        patch('importlib.metadata.entry_points', return_value=[]),
    ):
        result = recovery.start(root)

    assert result['status'] == 'queued'

    failure = run_state.read_start_failure(root)
    assert failure is not None
    assert 'no owner_argv' in failure['reason']
    assert 'refactory.owner_module' in failure['reason']

    st = console.status(root)
    assert st['state'] == 'failed'
    activity = st.get('activity', {})
    assert activity.get('start_failure') is not None
    assert 'no owner_argv' in activity['start_failure']


# ── T6: interactive.prepare writes owner_argv ──────────────────────────────


def test_prepare_writes_owner_argv(tmp_path):
    """interactive.prepare() writes owner_argv to run-config.json.

    Calls the real prepare(), with heavy I/O dependencies mocked out, and
    asserts that the resulting run-config.json contains the owner_argv the
    supervisor expects.
    """
    from contextlib import contextmanager
    from refactory_dsh import interactive
    from refactory_dsh.design import DesignMode

    identifier = 'default_pack/design'
    workdir = tmp_path / 'run'
    project = tmp_path / 'proj'
    project.mkdir()

    graphs_dir = tmp_path / 'graphs'
    graphs_dir.mkdir(parents=True)

    @contextmanager
    def fake_ownership(wd):
        Path(wd).mkdir(parents=True, exist_ok=True)
        Path(wd).joinpath('.refactory').mkdir(parents=True, exist_ok=True)
        yield 0

    mock_client_cls = MagicMock()
    mock_client_cls.return_value.context.return_value = {}

    with (
        patch.object(interactive, 'ownership', fake_ownership),
        patch.object(interactive.registry, 'resolve',
                     return_value=(MagicMock(), DesignMode)),
        patch('refactory_dsh.invocations.prepare', return_value=graphs_dir),
        patch('refactory_dsh.model.DshModelClient', mock_client_cls),
        patch('refactory_dsh.recovery.configuration_fingerprints',
              return_value={}),
    ):
        name, graphs = interactive.prepare(
            identifier, workdir=workdir, project=project)

    config_path = workdir / '.refactory' / 'run-config.json'
    assert config_path.exists(), 'run-config.json was not written by prepare()'
    config = json.loads(config_path.read_text())

    assert 'owner_argv' in config, 'prepare() did not write owner_argv'
    expected_argv = ['-m', 'refactory_dsh.interactive', identifier]
    assert config['owner_argv'] == expected_argv

    # The supervisor must be able to read it back and build the full command
    full_argv, _ = sv._build_owner_argv(workdir)
    expected_full = [sys.executable] + expected_argv + [
        '--workdir', str(workdir), '--headless', '--resume']
    assert full_argv == expected_full


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


# ── T8: resume-pending path handles None child ────────────────────────────


def test_supervise_resume_pending_none_child(tmp_path):
    """Resume-pending: _start_child → None preserves start-failure and exits.

    Without the ``if child is None`` guard at the resume-pending site, the
    code falls through to ``sf_path.unlink()`` and clears start-failure.json.
    Two assertions catch a removed guard:
    (a) start-failure.json still exists (the unlink did not fire),
    (b) _start_child was called exactly once (the guard returned before the
        respawn site could call it again).
    """
    root = _make_run_dir(tmp_path, mode='default_pack/design', status='executing')

    # Write resume-pending.json with correct checkpoint SHA.
    cp_bytes = (root / '.refactory/spine-checkpoint.json').read_bytes()
    pending = {
        'at': '2026-01-01T00:00:00+00:00',
        'checkpoint_sha256': hashlib.sha256(cp_bytes).hexdigest(),
    }
    (root / '.refactory/resume-pending.json').write_text(json.dumps(pending))

    call_count = 0

    def mock_start_child(r):
        nonlocal call_count
        call_count += 1
        sv.record_start_failure(
            r, os.getpid(), 1,
            run_state.spine_status(r), run_state.spine_status(r),
            reason=f'no owner (call {call_count})')
        return None

    with (
        patch.object(sv, '_start_child', side_effect=mock_start_child),
        patch('refactory_dsh.recovery.prepare', lambda r, n, **kw: r / 'graphs'),
    ):
        result = sv.supervise(root, first_exit_code=1, max_respawns=3)

    assert result == 1  # inline → last_exit_code

    # (a) The guard must prevent sf_path.unlink() from clearing start-failure.
    failure = run_state.read_start_failure(root)
    assert failure is not None, (
        'start-failure.json was cleared; the resume-pending None guard is missing')
    assert 'no owner (call 1)' in failure['reason'], (
        f'start-failure.json was overwritten by a later _start_child call: '
        f'got reason={failure["reason"]!r}')

    # (b) The guard must exit immediately — _start_child called exactly once.
    assert call_count == 1, (
        f'Expected 1 _start_child call (resume-pending only), '
        f'got {call_count} — the resume-pending None guard is missing')


# ── T9: respawn path handles None child ───────────────────────────────────


def test_supervise_respawn_none_child(tmp_path):
    """Respawn: _start_child → None exits cleanly without AttributeError.

    Without the ``if child is None`` guard at the respawn site,
    ``history_entry['child_pid'] = child.pid`` raises ``AttributeError``
    and the ``supervise()`` call crashes instead of returning 1.
    This test fails when that guard is removed.
    """
    root = _make_run_dir(tmp_path, mode='default_pack/design', status='executing')

    # Set owner_pid to a dead process so needs_supervision returns True.
    dead = _dead_pid()
    cp = json.loads((root / '.refactory/spine-checkpoint.json').read_text())
    cp['owner_pid'] = dead
    (root / '.refactory/spine-checkpoint.json').write_text(json.dumps(cp))

    call_count = 0

    class FakeChild:
        def __init__(self):
            self.pid = dead
            self.returncode = 1
        def wait(self):
            pass
        def poll(self):
            return self.returncode

    def mock_start_child(r):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FakeChild()
        # Second call: simulate no owner.
        sv.record_start_failure(
            r, os.getpid(), 1,
            run_state.spine_status(r), run_state.spine_status(r),
            reason='no owner_argv in run-config.json and no '
                   'refactory.owner_module entry point')
        return None

    with (
        patch.object(sv, '_start_child', side_effect=mock_start_child),
        patch('refactory_dsh.recovery.prepare', lambda r, n, **kw: r / 'graphs'),
    ):
        result = sv.supervise(root, first_exit_code=1, max_respawns=3)

    assert result == 1  # no crash, returns last_exit_code
    assert call_count >= 2, (
        'respawn site was not reached; _start_child was called '
        f'{call_count} time(s)')
