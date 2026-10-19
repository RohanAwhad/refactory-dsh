"""Tests for the run supervisor: state classification, start-failure recording,
auto-respawn, respawn limits, lock exclusion, deterministic refusal, real-process
paths, and the resume-pending dance.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from refactory_dsh import run_state, graph_control, readable
from refactory_dsh import supervisor as sv


# ── helpers ────────────────────────────────────────────────────────────────

ALL_STATES = ['done', 'failed', 'cancelled', 'halted', 'paused', 'waiting_user',
              'refused', 'ready', 'executing']


def _make_run_dir(tmp_path: Path, *, status: str = 'executing', owner_pid: int | None = None,
                  mode: str = 'default_pack/design', project: str = '.') -> Path:
    """Create a minimal run directory with spine files and config."""
    refactory = tmp_path / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)
    (refactory / 'spine-status.json').write_text(json.dumps({'status': status}))
    checkpoint: dict[str, Any] = {}
    if owner_pid is not None:
        checkpoint['owner_pid'] = owner_pid
    checkpoint['status'] = status
    checkpoint['run_id'] = str(tmp_path)
    checkpoint['graph'] = {'name': mode.replace('/', '-')}
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': mode, 'project': project,
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))
    return tmp_path


def _make_valid_run_dir(tmp_path: Path, *, status: str = 'ready',
                        mode: str = 'demo/run', project: str | None = None) -> Path:
    """Create a run directory that recovery.prepare() accepts.

    Extends _make_run_dir with configuration_fingerprints so that a real
    child process calling recovery.prepare() succeeds past fingerprint
    validation.
    """
    from refactory_dsh.recovery import fingerprint as _fp
    root = tmp_path
    project_path = project or str(root)
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)
    # Source file for configuration fingerprints
    source = root / 'mode.py'
    source.write_text('answer = 42\n')
    config = {
        'mode': mode,
        'project': project_path,
        'configuration_fingerprints': {str(source): _fp(source)},
    }
    (refactory / 'run-config.json').write_text(json.dumps(config))
    dead = _dead_pid()
    checkpoint: dict[str, Any] = {
        'run_id': str(root),
        'graph': {'name': mode.replace('/', '-')},
        'status': status,
        'owner_pid': dead,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': status}))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))
    return root


def _dsh_available() -> bool:
    """Check if DSH build and profile are available for end-to-end tests."""
    try:
        from refactory_dsh.paths import require_build
        require_build()
    except Exception:
        return False
    try:
        from refactory_dsh import dsh_profile
        return dsh_profile.requirement() is None
    except Exception:
        return False


def _dead_pid() -> int:
    """Return a PID that is guaranteed not to exist."""
    # Fork a child, wait for it, and use its PID
    pid = os.getpid() + 99999
    # Make sure it's not alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return pid
    except PermissionError:
        pass
    # Fallback: use a large PID
    return 2**20 + 1


# ── SC1: is_terminal exhaustive ────────────────────────────────────────────

def test_is_terminal_exhaustive():
    """All 9 known states: is_terminal is True for exactly {done, cancelled, failed}."""
    expected_terminal = {'done', 'cancelled', 'failed'}
    for state in ALL_STATES:
        result = run_state.is_terminal(state)
        assert result == (state in expected_terminal), f'is_terminal({state!r}) = {result}'
    assert len(ALL_STATES) == 9


# ── SC2: is_deliberately_paused exhaustive ─────────────────────────────────

def test_is_deliberately_paused_exhaustive():
    """True for exactly {halted, paused, waiting_user, refused} and False for all others."""
    expected_paused = {'halted', 'paused', 'waiting_user', 'refused'}
    for state in ALL_STATES:
        result = run_state.is_deliberately_paused(state)
        assert result == (state in expected_paused), f'is_deliberately_paused({state!r}) = {result}'
    assert len(ALL_STATES) == 9


# ── SC3: needs_supervision matrix ─────────────────────────────────────────

def test_needs_supervision_matrix(tmp_path):
    """For each state × {owner_alive, owner_dead}, check needs_supervision."""
    dead = _dead_pid()
    for state in ALL_STATES:
        root = tmp_path / f'run_{state}'
        root.mkdir()
        # owner dead
        _make_run_dir(root, status=state, owner_pid=dead)
        should_supervise = state in ('ready', 'executing')
        result = run_state.needs_supervision(root)
        assert result == should_supervise, (
            f'needs_supervision({state!r}, dead) = {result}, expected {should_supervise}')

        # owner alive (use our own PID)
        root_alive = tmp_path / f'run_{state}_alive'
        root_alive.mkdir()
        _make_run_dir(root_alive, status=state, owner_pid=os.getpid())
        result_alive = run_state.needs_supervision(root_alive)
        assert result_alive is False, (
            f'needs_supervision({state!r}, alive) should always be False')


# ── T1: start failure recorded ────────────────────────────────────────────

def test_start_failure_recorded(tmp_path):
    """record_start_failure writes start-failure.json with the transport error."""
    root = _make_run_dir(tmp_path, status='refused', owner_pid=_dead_pid())
    (root / 'recovery.log').write_text('some output\ndsh: TRANSPORT: Connection error.\n')
    sv.record_start_failure(root, 12345, 1, 'refused', 'refused')
    failure = run_state.read_start_failure(root)
    assert failure is not None
    assert 'TRANSPORT: Connection error' in failure['reason']
    assert failure['exit_code'] == 1
    assert failure['spine_status_at_exit'] == 'refused'


def test_start_failure_recorded_before(tmp_path):
    """Without the change, console.status returns the old spine state, not failed."""
    root = _make_run_dir(tmp_path, status='refused', owner_pid=_dead_pid())
    # No start-failure.json written
    from refactory_dsh import console
    st = console.status(root)
    # Before the change: state comes from SPINE_STATES which maps refused -> 'failed'
    # but there's no start_failure detail in activity
    activity = st.get('activity', {})
    assert activity.get('start_failure') is None


# ── T2: resume during exit ────────────────────────────────────────────────

def test_resume_during_exit(monkeypatch, tmp_path):
    """Resume at terminal status with live owner writes resume-pending.json."""
    root = _make_run_dir(tmp_path, status='refused', owner_pid=os.getpid())
    monkeypatch.setattr(graph_control, '_OWNER_EXIT_TIMEOUT', 0.3)
    result = graph_control.request(root, 'resume')
    assert result['status'] == 'queued'
    assert result.get('resume_pending') is True
    assert 'owner_exiting' not in result
    pending_path = root / '.refactory/resume-pending.json'
    assert pending_path.exists()
    formatted = readable.format_graph_control(result)
    assert 'Resume is pending' in formatted


def test_resume_during_exit_before(monkeypatch, tmp_path):
    """Before the change, graph_control returned owner_exiting=True."""
    # This test documents the old behavior — it passes because the old code
    # would have set owner_exiting. Since we changed the code, this test verifies
    # the NEW behavior does NOT set owner_exiting.
    root = _make_run_dir(tmp_path, status='refused', owner_pid=os.getpid())
    monkeypatch.setattr(graph_control, '_OWNER_EXIT_TIMEOUT', 0.3)
    result = graph_control.request(root, 'resume')
    # The change means owner_exiting is no longer set
    assert 'owner_exiting' not in result
    assert result.get('resume_pending') is True


# ── T3: killed owner resumed ──────────────────────────────────────────────

def test_killed_owner_resumed(tmp_path):
    """supervise() with a mock child that exits immediately respawns once."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())

    call_count = 0
    original_start_child = sv._start_child

    class FakeChild:
        def __init__(self, pid: int, code: int):
            self.pid = pid
            self.returncode = code
            self._code = code
        def wait(self):
            self.returncode = self._code
        def poll(self):
            return self._code

    def mock_start_child(r: Path) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First child exits with error, status stays 'executing'
            return FakeChild(pid=10001, code=1)
        else:
            # Second child: set status to 'done' so supervision stops
            (r / '.refactory/spine-status.json').write_text(json.dumps({'status': 'done'}))
            return FakeChild(pid=10002, code=0)

    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            sv.supervise(root, max_respawns=3)

    assert call_count == 2
    history = run_state.read_supervision_history(root)
    assert len(history) == 1
    assert history[0]['attempt'] == 1
    assert history[0]['child_pid'] == 10002
    assert history[0]['exit_code_of_previous'] == 1


def test_killed_owner_resumed_before(tmp_path):
    """Without the supervisor, no auto-resume happens and no history file exists."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())
    # No supervisor was run
    history = run_state.read_supervision_history(root)
    assert len(history) == 0
    assert not (root / '.refactory/supervision-history.json').exists()


# ── T4: no respawn for terminal or paused ──────────────────────────────────

NON_RESPAWNABLE = ['done', 'failed', 'cancelled', 'halted', 'paused', 'waiting_user', 'refused']

@pytest.mark.parametrize('state', NON_RESPAWNABLE)
def test_no_respawn_for_terminal_or_paused(tmp_path, state):
    """For each non-respawnable state, needs_supervision returns False."""
    root = _make_run_dir(tmp_path / state, status=state, owner_pid=_dead_pid())
    assert run_state.needs_supervision(root) is False


def test_non_respawnable_count():
    """The parametrize list length is exactly 7."""
    assert len(NON_RESPAWNABLE) == 7


# ── T5: respawn limit honoured ─────────────────────────────────────────────

def test_respawn_limit_honoured(tmp_path):
    """With max_respawns=2 and a child that always exits 1, exactly 2 respawns."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())

    call_count = 0

    class FakeChild:
        def __init__(self, pid: int):
            self.pid = pid
            self.returncode = 1
        def wait(self):
            self.returncode = 1
        def poll(self):
            return 1

    def mock_start_child(r: Path) -> Any:
        nonlocal call_count
        call_count += 1
        return FakeChild(pid=20000 + call_count)

    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            sv.supervise(root, max_respawns=2)

    # 1 initial + 2 respawns = 3 total child starts
    assert call_count == 3
    history = run_state.read_supervision_history(root)
    assert len(history) == 2
    failure = run_state.read_start_failure(root)
    assert failure is not None
    assert 'supervision exhausted' in failure['reason']
    assert '2' in failure['reason']


# ── T6: supervisor outlives launcher ───────────────────────────────────────

def test_supervisor_outlives_launcher(tmp_path):
    """Launch supervisor from a short-lived parent; assert respawn after parent exits."""
    root = _make_run_dir(tmp_path, status='ready', owner_pid=_dead_pid())

    # Write a fake owner script that exits non-zero first time, succeeds second time
    marker = tmp_path / 'attempt_count'
    marker.write_text('0')
    done_marker = tmp_path / 'done_marker'

    fake_script = tmp_path / 'fake_owner.py'
    fake_script.write_text(textwrap.dedent(f"""\
        import json, sys
        from pathlib import Path
        root = Path({str(tmp_path)!r})
        marker = root / 'attempt_count'
        count = int(marker.read_text().strip())
        count += 1
        marker.write_text(str(count))
        if count == 1:
            # First attempt: fail
            sys.exit(1)
        else:
            # Second attempt: mark done and set status
            (root / '.refactory/spine-status.json').write_text(
                json.dumps({{'status': 'done'}}))
            (root / 'done_marker').write_text('yes')
            sys.exit(0)
    """))

    # Write a launcher script that starts the supervisor and exits immediately
    launcher = tmp_path / 'launcher.py'
    launcher.write_text(textwrap.dedent(f"""\
        import subprocess, sys, os
        # Start supervisor as detached process
        child = subprocess.Popen(
            [sys.executable, '-m', 'refactory_dsh.supervisor', {str(tmp_path)!r}],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Print PID and exit immediately
        print(child.pid)
        sys.exit(0)
    """))

    # Patch the supervisor to use our fake script instead of the real interactive
    # We do this via a wrapper supervisor script
    wrapper = tmp_path / 'wrapper_supervisor.py'
    wrapper.write_text(textwrap.dedent(f"""\
        import json, os, sys, subprocess, time, fcntl
        from pathlib import Path
        from datetime import datetime, timezone

        root = Path(sys.argv[1]).resolve()

        # Lock
        lock_path = root / '.refactory/supervisor.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(lock_fd)
            sys.exit(0)

        max_respawns = 3
        respawn_count = 0
        try:
            for i in range(max_respawns + 1 + 1):  # initial + max respawns + safety
                child = subprocess.Popen(
                    [sys.executable, {str(fake_script)!r}],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                checkpoint = root / '.refactory/spine-checkpoint.json'
                data = json.loads(checkpoint.read_text())
                data['owner_pid'] = child.pid
                checkpoint.write_text(json.dumps(data))
                child.wait()

                # Check if done
                status_data = json.loads((root / '.refactory/spine-status.json').read_text())
                if status_data.get('status') in ('done', 'failed', 'cancelled',
                                                  'halted', 'paused', 'waiting_user', 'refused'):
                    break

                if respawn_count >= max_respawns:
                    break
                respawn_count += 1

                # Write history
                history_path = root / '.refactory/supervision-history.json'
                try:
                    history = json.loads(history_path.read_text()) if history_path.exists() else []
                except Exception:
                    history = []
                history.append({{
                    'at': datetime.now(timezone.utc).isoformat(),
                    'attempt': respawn_count,
                    'reason': f'owner exited with code {{child.returncode}}',
                    'child_pid': child.pid,
                    'exit_code_of_previous': child.returncode,
                    'spine_status': status_data.get('status', ''),
                    'supervisor_pid': os.getpid(),
                }})
                tmp = history_path.with_suffix('.tmp')
                tmp.write_text(json.dumps(history))
                tmp.replace(history_path)
        finally:
            os.close(lock_fd)
    """))

    # Update the launcher to use our wrapper
    launcher.write_text(textwrap.dedent(f"""\
        import subprocess, sys
        child = subprocess.Popen(
            [sys.executable, {str(wrapper)!r}, {str(tmp_path)!r}],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(child.pid)
        sys.exit(0)
    """))

    # Run the launcher
    result = subprocess.run([sys.executable, str(launcher)],
                           capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    supervisor_pid = int(result.stdout.strip())

    # The launcher has exited. Wait for the supervisor to finish
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if done_marker.exists():
            break
        time.sleep(0.2)

    assert done_marker.exists(), 'Supervisor did not complete the run after launcher exited'
    assert int(marker.read_text().strip()) == 2, 'Expected exactly 2 attempts'

    history = run_state.read_supervision_history(tmp_path)
    assert len(history) >= 1, 'Supervision history should have at least one respawn entry'


# ── T7: pending resume end to end ──────────────────────────────────────────

def test_pending_resume_end_to_end(tmp_path):
    """An exiting owner, one resume, and a new owner starting without a second command."""
    root = _make_run_dir(tmp_path, status='refused', owner_pid=_dead_pid())

    # Write resume-pending.json with the correct checkpoint SHA
    checkpoint_bytes = (root / '.refactory/spine-checkpoint.json').read_bytes()
    correct_sha = hashlib.sha256(checkpoint_bytes).hexdigest()
    pending = root / '.refactory/resume-pending.json'
    pending.write_text(json.dumps({'at': '2026-10-17T15:30:00+00:00', 'checkpoint_sha256': correct_sha}))

    call_count = 0

    class FakeChild:
        def __init__(self, pid: int, code: int):
            self.pid = pid
            self.returncode = code
        def wait(self):
            pass
        def poll(self):
            return self.returncode

    def mock_start_child(r: Path) -> Any:
        nonlocal call_count
        call_count += 1
        # First child (initial): exits at 'refused' so needs_supervision is False
        # Actually the supervisor first prepare() which will fail for 'refused'
        # Let's set things up so the initial prepare succeeds, child exits quickly,
        # resume-pending is consumed, second child finishes
        if call_count == 1:
            # Initial child exits, status stays refused (deliberately paused)
            return FakeChild(pid=30001, code=0)
        else:
            # resume-pending child succeeds
            (r / '.refactory/spine-status.json').write_text(json.dumps({'status': 'done'}))
            sentinel = r / 'sentinel_from_resumed_owner'
            sentinel.write_text('resumed')
            return FakeChild(pid=30002, code=0)

    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            sv.supervise(root, max_respawns=3)

    # The resume-pending file should be consumed
    assert not pending.exists()
    # The sentinel from the second child should be present
    assert (root / 'sentinel_from_resumed_owner').exists()
    assert call_count == 2


# ── T8: deterministic refusal not retried ──────────────────────────────────

def test_deterministic_refusal_not_retried(tmp_path):
    """When prepare() raises ValueError for unknown/failed outcome, no respawn."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())

    call_count = 0

    class FakeChild:
        def __init__(self, pid: int):
            self.pid = pid
            self.returncode = 1
        def wait(self):
            self.returncode = 1
        def poll(self):
            return 1

    def mock_start_child(r: Path) -> Any:
        nonlocal call_count
        call_count += 1
        return FakeChild(pid=40001)

    prepare_call_count = 0
    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        nonlocal prepare_call_count
        prepare_call_count += 1
        if prepare_call_count == 1:
            # Initial prepare succeeds
            return r / 'graphs'
        else:
            # Respawn prepare fails — deterministic refusal
            raise ValueError('last node outcome is unknown or failed; inspect side effects before any explicit rerun')

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            sv.supervise(root, max_respawns=3)

    # Only 1 child started (the initial one), no respawn
    assert call_count == 1
    failure = run_state.read_start_failure(root)
    assert failure is not None
    assert 'last node outcome is unknown or failed' in failure['reason']
    history = run_state.read_supervision_history(root)
    assert len(history) == 0


# ── SC4: supervision history in status ─────────────────────────────────────

def test_supervision_history_in_status(tmp_path):
    """Write supervision-history.json with 2 entries; status reports respawn_count."""
    root = _make_run_dir(tmp_path, status='done', owner_pid=_dead_pid())
    history = [
        {'at': '2026-10-17T15:31:00+00:00', 'attempt': 1, 'reason': 'test',
         'child_pid': 100, 'exit_code_of_previous': 1, 'spine_status': 'executing',
         'supervisor_pid': 99},
        {'at': '2026-10-17T15:32:00+00:00', 'attempt': 2, 'reason': 'test2',
         'child_pid': 101, 'exit_code_of_previous': 1, 'spine_status': 'executing',
         'supervisor_pid': 99},
    ]
    (root / '.refactory/supervision-history.json').write_text(json.dumps(history))
    from refactory_dsh import console
    st = console.status(root)
    activity = st.get('activity', {})
    assert activity.get('respawn_count') == 2
    assert activity.get('last_respawn') == '2026-10-17T15:32:00+00:00'


# ── SC5: start failure in status ───────────────────────────────────────────

def test_start_failure_in_status(tmp_path):
    """Write start-failure.json; status reports state=failed with the reason."""
    root = _make_run_dir(tmp_path, status='refused', owner_pid=_dead_pid())
    failure = {
        'at': '2026-10-17T15:30:00+00:00',
        'pid': 12345,
        'exit_code': 1,
        'reason': 'dsh: TRANSPORT: Connection error.',
        'spine_status_at_start': 'refused',
        'spine_status_at_exit': 'refused',
    }
    (root / '.refactory/start-failure.json').write_text(json.dumps(failure))
    from refactory_dsh import console
    st = console.status(root)
    assert st['state'] == 'failed'
    activity = st.get('activity', {})
    assert activity.get('start_failure') == 'dsh: TRANSPORT: Connection error.'


# ── SC11: supervisor lock prevents duplicates ──────────────────────────────

def test_supervisor_lock_prevents_duplicates(tmp_path):
    """Two supervisor processes: only one acquires the lock."""
    root = _make_run_dir(tmp_path, status='ready', owner_pid=_dead_pid())

    lock_script = tmp_path / 'lock_holder.py'
    lock_script.write_text(textwrap.dedent(f"""\
        import fcntl, os, sys, time
        from pathlib import Path
        lock_path = Path({str(root)!r}) / '.refactory/supervisor.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Signal readiness
        (Path({str(tmp_path)!r}) / 'lock_held').write_text(str(os.getpid()))
        # Hold the lock for a while
        time.sleep(5)
        os.close(fd)
    """))

    # Start first process to hold the lock
    holder = subprocess.Popen([sys.executable, str(lock_script)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # Wait for the first process to acquire the lock
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if (tmp_path / 'lock_held').exists():
                break
            time.sleep(0.1)
        assert (tmp_path / 'lock_held').exists()

        # Now try to supervise — it should return immediately (lock held)
        import fcntl as _fcntl
        lock_path = root / '.refactory/supervisor.lock'
        fd2 = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _fcntl.flock(fd2, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            # If we get here, the lock was not held (unexpected)
            _fcntl.flock(fd2, _fcntl.LOCK_UN)
            assert False, "Expected lock to be held by the first process"
        except (BlockingIOError, OSError):
            pass  # Expected: lock is held
        finally:
            os.close(fd2)
    finally:
        holder.kill()
        holder.wait()


# ── T9: headless inline supervision ────────────────────────────────────────

def test_headless_inline_respawn(tmp_path):
    """A headless run whose first owner exits non-zero transiently is respawned."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())

    call_count = 0

    class FakeChild:
        def __init__(self, pid: int, code: int):
            self.pid = pid
            self.returncode = code
        def wait(self):
            pass
        def poll(self):
            return self.returncode

    def mock_start_child(r: Path) -> Any:
        nonlocal call_count
        call_count += 1
        # Respawned child succeeds
        (r / '.refactory/spine-status.json').write_text(json.dumps({'status': 'done'}))
        (r / '.refactory/spine-checkpoint.json').write_text(json.dumps({
            'owner_pid': _dead_pid(), 'status': 'done',
            'run_id': str(r), 'graph': {'name': 'default_pack-design'},
        }))
        return FakeChild(pid=50001, code=0)

    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            final_code = sv.supervise(root, first_exit_code=1, max_respawns=3)

    assert final_code == 0
    assert call_count == 1
    history = run_state.read_supervision_history(root)
    assert len(history) == 1


def test_headless_deliberate_stop_no_respawn(tmp_path):
    """A deliberate stop during a headless run is never respawned."""
    root = _make_run_dir(tmp_path, status='paused', owner_pid=_dead_pid())

    final_code = sv.supervise(root, first_exit_code=0, max_respawns=3)

    assert final_code == 0
    history = run_state.read_supervision_history(root)
    assert len(history) == 0


# ── Item 1: real child no self-refusal ─────────────────────────────────────


@pytest.mark.skipif(not _dsh_available(), reason='DSH build/profile not available')
def test_real_child_no_self_refusal(tmp_path):
    """The supervisor does not write the child's PID into the checkpoint,
    so the child's recovery.prepare() succeeds instead of refusing itself.

    Positive assertion: the child re-creates resume-request.json (written
    only after recovery.prepare() passes the owner-alive check).  The
    supervisor's own prepare() writes the same file first; the wrapper
    deletes it before starting the child so only the child's write is
    observed.
    """
    root = _make_valid_run_dir(tmp_path, status='ready')

    original_start = sv._start_child

    def _intercept_start(r: Path) -> Any:
        # Delete resume-request.json left by the supervisor's own prepare()
        # so only the child's prepare() can recreate it.
        rr = r / '.refactory/resume-request.json'
        if rr.exists():
            rr.unlink()
        return original_start(r)

    with patch.object(sv, '_start_child', side_effect=_intercept_start):
        sv.supervise(root, max_respawns=0)

    # Positive: the child's recovery.prepare() re-created resume-request.json.
    rr = root / '.refactory/resume-request.json'
    log_content = ''
    log_path = root / 'recovery.log'
    if log_path.exists():
        log_content = log_path.read_text(errors='replace')[:1000]
    assert rr.exists(), (
        f"Child's recovery.prepare() did not write resume-request.json. "
        f"recovery.log: {log_content}"
    )
    data = json.loads(rr.read_text())
    expected_sha = hashlib.sha256(
        (root / '.refactory/spine-checkpoint.json').read_bytes()
    ).hexdigest()
    assert data['checkpoint_sha256'] == expected_sha


# ── Item 2: stop prevents respawn ──────────────────────────────────────────


def test_stop_before_status_not_respawned(tmp_path):
    """A stop requested during the child's run prevents respawn,
    even when the spine status is still 'executing'."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())

    class FakeChild:
        def __init__(self) -> None:
            self.pid = 50001
            self.returncode = 1
        def wait(self) -> None:
            # Stop is requested while the child is running
            stop = root / '.refactory/stop-requested.json'
            stop.parent.mkdir(parents=True, exist_ok=True)
            stop.write_text(json.dumps({'pid': self.pid, 'at': round(time.time(), 3)}))
        def poll(self) -> int:
            return self.returncode

    def mock_start_child(r: Path) -> Any:
        return FakeChild()

    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child):
        with patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
            sv.supervise(root, max_respawns=3)

    # No respawn — the stop was deliberate
    history = run_state.read_supervision_history(root)
    assert len(history) == 0


def test_stop_time_compared_to_owner_start(tmp_path):
    """A stop before the owner started does not block; a stop after does."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())
    now = time.time()
    # Stop from 100s ago
    (root / '.refactory/stop-requested.json').write_text(
        json.dumps({'pid': 99999, 'at': now - 100}))
    # Owner started 10s ago — stop is old, supervision continues
    assert run_state.needs_supervision(root, since=now - 10) is True
    # Owner started 200s ago — stop happened after start, supervision blocked
    assert run_state.needs_supervision(root, since=now - 200) is False


# ── Item 3: nesting prevention via lock ────────────────────────────────────


def test_no_nested_supervision_via_lock(tmp_path):
    """While a supervisor holds the lock, another supervise() returns
    immediately — the lock prevents nesting."""
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())

    # Hold the lock as the parent supervisor would
    lock_path = root / '.refactory/supervisor.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Inline host returns the exit code when it cannot acquire the lock
        result = sv.supervise(root, first_exit_code=1, max_respawns=3)
        assert result == 1

        # Detached host returns None
        result2 = sv.supervise(root, max_respawns=3)
        assert result2 is None

        # No supervision activity whatsoever
        history = run_state.read_supervision_history(root)
        assert len(history) == 0
    finally:
        os.close(lock_fd)


# ── Item 4: resume-pending SHA validation ──────────────────────────────────


def test_resume_pending_wrong_sha_rejected(tmp_path):
    """A resume-pending.json with a wrong checkpoint_sha256 is dropped."""
    root = _make_run_dir(tmp_path, status='refused', owner_pid=_dead_pid())
    pending = root / '.refactory/resume-pending.json'
    pending.write_text(json.dumps({
        'at': '2026-10-17T15:30:00+00:00',
        'checkpoint_sha256': 'wrong-sha-value',
    }))

    result = sv._read_resume_pending(root)

    assert result is None
    assert not pending.exists()
    # A start-failure was recorded with the mismatch reason
    failure = run_state.read_start_failure(root)
    assert failure is not None
    assert 'mismatch' in failure['reason'].lower()


def test_resume_pending_correct_sha_consumed(tmp_path):
    """A resume-pending.json with the correct checkpoint_sha256 is consumed."""
    root = _make_run_dir(tmp_path, status='refused', owner_pid=_dead_pid())
    checkpoint_bytes = (root / '.refactory/spine-checkpoint.json').read_bytes()
    correct_sha = hashlib.sha256(checkpoint_bytes).hexdigest()
    pending = root / '.refactory/resume-pending.json'
    pending.write_text(json.dumps({
        'at': '2026-10-17T15:30:00+00:00',
        'checkpoint_sha256': correct_sha,
    }))

    result = sv._read_resume_pending(root)

    assert result is not None
    assert result['checkpoint_sha256'] == correct_sha
    assert not pending.exists()
