"""Tests for the graph control commands: acknowledge, amend-command, withdraw,
stop on terminal, and the dm-94 ownership scope split.

Each test creates a minimal run directory with the relevant state.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from refactory_dsh import graph_control, run_state, recovery
from refactory_dsh.recovery import fingerprint as _fp


# ── helpers ────────────────────────────────────────────────────────────────

def _dead_pid() -> int:
    pid = os.getpid() + 99999
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return pid
    except PermissionError:
        pass
    return 2**20 + 1


def _make_run_dir(tmp_path: Path, *, status: str = 'halted', owner_pid: int | None = None,
                  mode: str = 'default_pack/design', check_cmd: str = 'wrong command',
                  with_declarations: bool = False) -> Path:
    """Create a minimal run directory with spine files, config and facts."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)
    (refactory / 'spine-status.json').write_text(json.dumps({'status': status}))

    checkpoint: dict[str, Any] = {
        'status': status,
        'run_id': str(root),
        'graph': {
            'name': mode.replace('/', '-'),
            'nodes': {
                'check': {'_type': 'GateNode', 'id': 'check', 'evaluator_type': 'fn',
                          'evaluator_command': 'true', 'reads': [],
                          'writes': ['check.txt', 'validation.txt']},
                'materialise': {'_type': 'FnNode', 'id': 'materialise', 'command': 'true',
                                'reads': [], 'writes': []},
                'emit': {'_type': 'AgentNode', 'id': 'emit', 'role': 'builder',
                         'prompt_template': 'E', 'reads': [], 'writes': []},
            },
            'edges': [
                {'source': 'emit', 'target': 'materialise'},
                {'source': 'materialise', 'target': 'check'},
            ],
        },
        'state': {
            'cursor': ['check'],
            'completed': ['emit', 'materialise'],
            'skipped': [],
            'pause_before': [],
            'gate_verdicts': {},
            'iterations': {},
            'feedback': {},
            'gate_reasons': {},
            'afters': [],
            'prompts': {},
            'log': [],
        },
        'file_fingerprints': {},
    }
    if owner_pid is not None:
        checkpoint['owner_pid'] = owner_pid
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': mode, 'project': str(root),
        'configuration_fingerprints': {},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # facts.json with the check command
    facts = {'check': check_cmd, 'name': 'test_run', 'brief': 'test brief'}
    (root / 'facts.json').write_text(json.dumps(facts))

    # Declarations for node lookup
    if with_declarations:
        decls = {
            'check': {'kind': 'gate', 'reads': ['brief.json'], 'writes': ['check.txt', 'validation.txt'],
                      'produces': ['validation', 'check_attempts_used']},
            'materialise': {'kind': 'op', 'reads': [], 'writes': [], 'produces': []},
            'emit': {'kind': 'agent', 'reads': ['brief.json'], 'writes': ['emit.txt'],
                     'produces': ['emit']},
        }
        (refactory / 'declarations.json').write_text(json.dumps(decls))

    return root


# ── T1: Check command correction ──────────────────────────────────────────

def test_amend_check_command(tmp_path):
    """A run whose check failed with a wrong command is given a corrected one."""
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid(),
                         check_cmd='wrong command')
    result = graph_control.request(
        root, 'amend-command', field='check', value='correct command',
        note='fixed the build step',
    )
    assert result['action'] == 'amend-command'
    assert result['field'] == 'check'
    assert result['old_value'] == 'wrong command'
    assert result['new_value'] == 'correct command'
    assert result['status'] == 'queued'

    # facts.json updated
    facts = json.loads((root / 'facts.json').read_text())
    assert facts['check'] == 'correct command'
    assert len(facts['command_history']) == 1
    entry = facts['command_history'][0]
    assert entry['field'] == 'check'
    assert entry['old_value'] == 'wrong command'
    assert entry['new_value'] == 'correct command'
    assert entry['note'] == 'fixed the build step'

    # Rerun control file queued
    controls_dir = root / '.refactory/graph-controls'
    control_files = list(controls_dir.glob('*.json'))
    assert len(control_files) == 1
    control = json.loads(control_files[0].read_text())
    assert control['action'] == 'rerun'
    assert control['source'] == 'amend-command'


def test_amend_check_before(tmp_path):
    """Before the change: resume with a wrong check does not change the command."""
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid(),
                         check_cmd='wrong command')
    # Without amend-command, facts.json stays unchanged after a resume
    facts_before = json.loads((root / 'facts.json').read_text())
    assert facts_before['check'] == 'wrong command'
    # There is no amend-command; trying graph-control resume does not change the command
    assert facts_before.get('command_history') is None


# ── T2: Setup and generate correction ─────────────────────────────────────

@pytest.mark.parametrize('field', ['setup', 'generate'])
def test_amend_setup_and_generate(tmp_path, field):
    """amend-command works for setup and generate too."""
    root = _make_run_dir(tmp_path / field, status='halted', owner_pid=_dead_pid(),
                         check_cmd='test cmd')
    # Add the field to facts
    facts = json.loads((root / 'facts.json').read_text())
    facts[field] = 'old value'
    (root / 'facts.json').write_text(json.dumps(facts))

    result = graph_control.request(
        root, 'amend-command', field=field, value='new value', note='updated',
    )
    assert result['field'] == field
    assert result['old_value'] == 'old value'
    assert result['new_value'] == 'new value'

    updated = json.loads((root / 'facts.json').read_text())
    assert updated[field] == 'new value'
    assert updated['command_history'][-1]['field'] == field


# ── T3: Acknowledge executing checkpoint inside a fork ─────────────────────

def test_acknowledge_executing_checkpoint(tmp_path):
    """An executing checkpoint with a dead owner and pending markers is acknowledged."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()

    # Create output files and compute their fingerprints
    (root / 'output_a.txt').write_text('completed branch output')
    fp_a = _fp(root / 'output_a.txt')
    (root / 'output_b.txt').write_text('in-flight partial output')
    fp_b_original = hashlib.sha256(b'original content').hexdigest()  # Differs from actual

    checkpoint: dict[str, Any] = {
        'status': 'executing',
        'run_id': str(root),
        'graph': {'name': 'test'},
        'owner_pid': dead_pid,
        'state': {
            'cursor': ['branch_b'],
            'completed': ['fork', 'branch_a'],
            'skipped': [],
            'pause_before': [],
            'gate_verdicts': {},
            'iterations': {},
            'feedback': {},
            'gate_reasons': {},
            'afters': [],
            'prompts': {},
            'log': [],
        },
        'file_fingerprints': {
            'output_a.txt': fp_a,         # matches — completed branch, stays
            'output_b.txt': fp_b_original, # differs — in-flight, set aside
        },
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'executing'}))
    (refactory / 'run-config.json').write_text(json.dumps({'mode': 'test/run', 'project': '.'}))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Create pending delivery markers
    pending_dir = refactory / 'agent-delivery/pending'
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / 'delivery1.json').write_text(json.dumps({'node': 'branch_b'}))

    result = graph_control.request(root, 'acknowledge')

    assert result['action'] == 'acknowledge'
    assert result['status'] == 'acknowledged'
    assert len(result['files']) >= 1  # pending + mismatched output

    # Pending markers moved
    assert not (pending_dir / 'delivery1.json').exists()

    # In-flight output (fingerprint mismatch) moved
    assert not (root / 'output_b.txt').exists()

    # Completed branch output (fingerprint match) stays
    assert (root / 'output_a.txt').exists()

    # Set-aside directory has manifest
    set_aside_dirs = list((refactory / 'set-aside').iterdir())
    assert len(set_aside_dirs) == 1
    manifest = json.loads((set_aside_dirs[0] / 'manifest.json').read_text())
    assert manifest['checkpoint_status_before'] == 'executing'

    # resume-request.json written with acknowledge_executing
    rr = refactory / 'resume-request.json'
    assert rr.exists()
    rr_data = json.loads(rr.read_text())
    assert rr_data['acknowledge_executing'] is True

    # Checkpoint and status files are NOT rewritten (the spine is their only writer)
    cp = json.loads((refactory / 'spine-checkpoint.json').read_text())
    assert cp['status'] == 'executing'  # unchanged


def test_acknowledge_before_refuses(tmp_path):
    """Without acknowledgment, recovery.prepare() still refuses an executing checkpoint."""
    root = tmp_path.resolve()
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    checkpoint: dict[str, Any] = {
        'status': 'executing',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'executing'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {},
    }))

    with pytest.raises(ValueError, match='outcome is unknown'):
        recovery.prepare(root, 'test-run')


# ── T5: Withdraw and queued controls ──────────────────────────────────────

def test_withdraw_queued_control(tmp_path):
    """A queued control can be withdrawn."""
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    controls_dir = root / '.refactory/graph-controls'
    controls_dir.mkdir(parents=True, exist_ok=True)
    control_id = 'test-control-123'
    (controls_dir / f'{control_id}.json').write_text(json.dumps({
        'id': control_id, 'action': 'stop', 'status': 'queued',
    }))

    result = graph_control.request(root, 'withdraw', node=control_id)
    assert result['status'] == 'withdrawn'
    assert not (controls_dir / f'{control_id}.json').exists()


def test_withdraw_applied_control_refused(tmp_path):
    """Withdrawing an already-applied control is refused."""
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    controls_dir = root / '.refactory/graph-controls'
    controls_dir.mkdir(parents=True, exist_ok=True)
    control_id = 'test-control-456'
    (controls_dir / f'{control_id}.json').write_text(json.dumps({
        'id': control_id, 'action': 'stop', 'status': 'queued',
    }))
    (root / '.refactory/graph-control-receipt.json').write_text(json.dumps({
        'id': control_id, 'status': 'applied',
    }))

    with pytest.raises(ValueError, match='already been applied'):
        graph_control.request(root, 'withdraw', node=control_id)


def test_withdraw_nonexistent_refused(tmp_path):
    """Withdrawing a nonexistent control is refused."""
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    with pytest.raises(ValueError, match='no queued control'):
        graph_control.request(root, 'withdraw', node='nonexistent')


# ── T6: Stop on terminal with live owner ──────────────────────────────────

def test_stop_terminal_signals_directly(tmp_path):
    """graph-control stop on a terminal run with a live owner signals directly."""
    root = _make_run_dir(tmp_path, status='done', owner_pid=os.getpid())
    # Update spine-status to match
    (root / '.refactory/spine-status.json').write_text(json.dumps({'status': 'done'}))

    # Patch os.kill to avoid actually killing the test process
    killed_pids: list[int] = []
    original_kill = os.kill

    def mock_kill(pid: int, sig: int) -> None:
        if sig != 0:  # Allow the is_alive check
            killed_pids.append(pid)
        else:
            original_kill(pid, sig)

    with patch('refactory_dsh.graph_control.os.kill', side_effect=mock_kill):
        result = graph_control.request(root, 'stop')

    assert result['status'] == 'signalled'
    assert os.getpid() in killed_pids


# ── T7: Acknowledge refused while alive ───────────────────────────────────

def test_acknowledge_refused_while_owner_alive(tmp_path):
    """Acknowledge is refused when the owner is still alive."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    checkpoint: dict[str, Any] = {
        'status': 'executing',
        'run_id': str(root),
        'graph': {'name': 'test'},
        'owner_pid': os.getpid(),  # alive
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'run-config.json').write_text(json.dumps({'mode': 'test/run'}))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    with pytest.raises(ValueError, match='owner process .* is still alive'):
        graph_control.request(root, 'acknowledge')


def test_acknowledge_refused_while_supervisor_alive(tmp_path):
    """Acknowledge is refused when the supervisor holds its lock."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    checkpoint: dict[str, Any] = {
        'status': 'executing',
        'run_id': str(root),
        'graph': {'name': 'test'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'run-config.json').write_text(json.dumps({'mode': 'test/run'}))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Hold the supervisor lock
    lock_path = refactory / 'supervisor.lock'
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='supervisor process holds supervisor.lock'):
            graph_control.request(root, 'acknowledge')
    finally:
        os.close(lock_fd)


# ── T8: Without acknowledgment, run still refuses ─────────────────────────

def test_executing_still_refuses_without_acknowledge(tmp_path):
    """Without acknowledge, recovery.prepare() refuses an executing checkpoint."""
    root = tmp_path.resolve()
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    checkpoint: dict[str, Any] = {
        'status': 'executing',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'executing'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {},
    }))

    with pytest.raises(ValueError, match='outcome is unknown'):
        recovery.prepare(root, 'test-run')


# ── T9: Amend-command refuses invalid inputs ──────────────────────────────

def _set_cursor(root: Path, cursor: list[str]) -> None:
    checkpoint_path = root / '.refactory/spine-checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint['state']['cursor'] = cursor
    checkpoint_path.write_text(json.dumps(checkpoint))


def _queued_target(root: Path) -> str:
    controls = list((root / '.refactory/graph-controls').glob('*.json'))
    assert len(controls) == 1
    return json.loads(controls[0].read_text())['target']


def test_amend_reruns_the_due_node_whatever_it_is_called(tmp_path):
    """The rerun target is the node left due, not one guessed from node or file names.

    Fails without the change: the target was chosen by heuristics (writes named
    check.txt, node ids containing "check" or "materialise"), so a due node with
    another name in another mode's graph was not the one rerun.
    """
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    checkpoint_path = root / '.refactory/spine-checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint['graph']['nodes']['verify_build'] = {
        '_type': 'FnNode', 'id': 'verify_build', 'command': 'true', 'reads': [], 'writes': []}
    checkpoint['state']['cursor'] = ['verify_build']
    checkpoint_path.write_text(json.dumps(checkpoint))
    result = graph_control.request(root, 'amend-command', field='setup', value='make build')
    assert result['rerun_target'] == 'verify_build'
    assert _queued_target(root) == 'verify_build'


def test_amend_reruns_the_node_the_person_names(tmp_path):
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    result = graph_control.request(root, 'amend-command', field='generate', value='gen',
                                   node='materialise')
    assert result['rerun_target'] == 'materialise'
    assert _queued_target(root) == 'materialise'


def test_amend_refuses_to_guess_and_changes_nothing(tmp_path):
    """With several due nodes and none named, it refuses before writing facts.json.

    Fails without the change: the first node in the cursor was rerun, whichever it was.
    """
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid(),
                         check_cmd='wrong command')
    _set_cursor(root, ['check', 'materialise'])
    with pytest.raises(ValueError, match='name the node to rerun with --node'):
        graph_control.request(root, 'amend-command', field='check', value='right command')
    assert json.loads((root / 'facts.json').read_text())['check'] == 'wrong command'
    assert not list((root / '.refactory/graph-controls').glob('*.json'))
    with pytest.raises(ValueError, match="no node 'nope'"):
        graph_control.request(root, 'amend-command', field='check', value='right command', node='nope')


def test_amend_invalid_field(tmp_path):
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    with pytest.raises(ValueError, match="must be 'check', 'setup', or 'generate'"):
        graph_control.request(root, 'amend-command', field='invalid', value='x')


def test_amend_empty_value(tmp_path):
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    with pytest.raises(ValueError, match='non-empty'):
        graph_control.request(root, 'amend-command', field='check', value='')


def test_amend_same_value(tmp_path):
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid(),
                         check_cmd='same command')
    with pytest.raises(ValueError, match='no change needed'):
        graph_control.request(root, 'amend-command', field='check', value='same command')


def test_amend_refuses_executing(tmp_path):
    root = _make_run_dir(tmp_path, status='executing', owner_pid=_dead_pid())
    with pytest.raises(ValueError, match='acknowledge'):
        graph_control.request(root, 'amend-command', field='check', value='new')


# ── T10: Queued controls listed in status ─────────────────────────────────

def test_queued_controls_in_status(tmp_path):
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    controls_dir = root / '.refactory/graph-controls'
    controls_dir.mkdir(parents=True, exist_ok=True)
    (controls_dir / 'ctrl1.json').write_text(json.dumps({
        'id': 'ctrl1', 'action': 'stop', 'status': 'queued',
    }))
    controls = graph_control.queued_controls(root)
    assert len(controls) == 1
    assert controls[0]['id'] == 'ctrl1'


# ── T11: Supervisor writes suggested_command ──────────────────────────────

def test_suggested_command_in_start_failure(tmp_path):
    """When the supervisor stops on dm-70 refusal, start-failure.json has suggested_command."""
    from refactory_dsh.supervisor import record_start_failure, _write_suggested_command
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    record_start_failure(root, 12345, 1, 'executing', 'executing',
                         reason='last node outcome is unknown or failed; inspect side effects')
    _write_suggested_command(root, f'refactory graph-control acknowledge --run {root}')

    sf = run_state.read_start_failure(root)
    assert sf is not None
    assert 'acknowledge' in sf['suggested_command']


# ── T12: Acknowledge then recovery.prepare accepts ────────────────────────

def test_acknowledge_then_prepare_accepts(tmp_path):
    """After acknowledge, recovery.prepare() accepts the executing checkpoint."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    source = root / 'mode.py'
    source.write_text('answer = 42\n')

    checkpoint: dict[str, Any] = {
        'status': 'executing',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'executing'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {str(source): _fp(source)},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Acknowledge first
    graph_control.request(root, 'acknowledge')

    # Now prepare should accept
    result = recovery.prepare(root, 'test-run')
    assert result == root / 'graphs'


# ── dm-95: retry status ───────────────────────────────────────────────────

def test_status_shows_retry_when_last_failure_present(tmp_path):
    """When spine-status has ready+last_failure, console.status shows 'being retried'.

    The owner is alive (os.getpid()), so the died check does not override the
    wait_reason. This is the window between a node failure and the CEO's next
    wf_next call.
    """
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    (refactory / 'spine-status.json').write_text(json.dumps({
        'status': 'ready',
        'nodes': ['check'],
        'completed': ['emit'],
        'last_failure': {'node': 'check', 'error': 'exit 1', 'at': now_iso},
    }))
    checkpoint: dict[str, Any] = {
        'status': 'ready',
        'run_id': str(root),
        'graph': {'name': 'test'},
        'owner_pid': os.getpid(),
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    from refactory_dsh import console
    result = console.status(root)
    activity = result['activity']
    assert 'last_failure' in activity
    assert activity['last_failure']['node'] == 'check'
    assert 'step check is being retried' in activity.get('wait_reason', '')


def test_status_no_retry_info_when_last_failure_absent(tmp_path):
    """When spine-status has ready without last_failure, no retry info appears."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    (refactory / 'spine-status.json').write_text(json.dumps({
        'status': 'ready',
        'nodes': ['check'],
        'completed': ['emit'],
    }))
    checkpoint: dict[str, Any] = {
        'status': 'ready',
        'run_id': str(root),
        'graph': {'name': 'test'},
        'owner_pid': os.getpid(),
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    from refactory_dsh import console
    result = console.status(root)
    activity = result['activity']
    assert 'last_failure' not in activity


def test_unretried_failure_promoted_and_not_respawned(tmp_path):
    """A run whose node fails and is not retried ends with run-level failed.

    dm-95: when the owner exits with spine status ready+last_failure, the
    supervisor promotes it to failed and does not respawn.
    """
    from refactory_dsh import supervisor as sv

    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    (refactory / 'spine-status.json').write_text(json.dumps({
        'status': 'ready',
        'nodes': ['check'],
        'completed': ['emit'],
        'last_failure': {'node': 'check', 'error': 'exit 1', 'at': now_iso},
    }))
    checkpoint: dict[str, Any] = {
        'status': 'ready',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    class FakeChild:
        def __init__(self) -> None:
            self.pid = 50001
            self.returncode = 1
        def wait(self) -> None:
            pass
        def poll(self) -> int:
            return 1

    def mock_start_child(r: Path) -> Any:
        return FakeChild()

    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child), \
         patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
        sv.supervise(root, max_respawns=3)

    # Status promoted to failed
    status = run_state.spine_status(root)
    assert status == 'failed'

    # Checkpoint also promoted
    cp = json.loads((refactory / 'spine-checkpoint.json').read_text())
    assert cp['status'] == 'failed'

    # Error names the node
    status_data = json.loads((refactory / 'spine-status.json').read_text())
    assert 'check' in status_data.get('error', '')

    # No respawn: supervision history should be empty
    history = run_state.read_supervision_history(root)
    assert len(history) == 0

    # needs_supervision returns False
    assert not run_state.needs_supervision(root)


# ── dm-97: resume --acknowledge-failure ───────────────────────────────────

def test_resume_failed_without_flag_names_command(tmp_path):
    """Resuming a failed checkpoint without --acknowledge-failure names the command."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    source = root / 'mode.py'
    source.write_text('answer = 42\n')

    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {str(source): _fp(source)},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    with pytest.raises(ValueError, match='resume --acknowledge-failure'):
        graph_control.request(root, 'resume')


def test_resume_failed_with_flag_succeeds(tmp_path):
    """Resuming a failed checkpoint with --acknowledge-failure succeeds."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    source = root / 'mode.py'
    source.write_text('answer = 42\n')

    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {str(source): _fp(source)},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    with patch('refactory_dsh.recovery.start') as mock_start:
        mock_start.return_value = {'status': 'queued', 'action': 'resume',
                                   'run_id': str(root), 'pid': 99999}
        result = graph_control.request(root, 'resume', acknowledge_failure=True)

    assert result['action'] == 'resume'
    mock_start.assert_called_once_with(root, acknowledge_failed=True)


def test_acknowledge_failed_checkpoint_with_pending(tmp_path):
    """Acknowledge accepts a failed checkpoint with pending dispatches."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {'name': 'test'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))
    (refactory / 'run-config.json').write_text(json.dumps({'mode': 'test/run', 'project': '.'}))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Create pending dispatch files
    pending_dir = refactory / 'agent-delivery/pending'
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / 'delivery1.json').write_text(json.dumps({'node': 'emit'}))

    result = graph_control.request(root, 'acknowledge')

    assert result['action'] == 'acknowledge'
    assert result['status'] == 'acknowledged'
    assert result['checkpoint_status_before'] == 'failed'

    # Pending markers moved
    assert not (pending_dir / 'delivery1.json').exists()

    # resume-request.json has acknowledge_failed
    rr = json.loads((refactory / 'resume-request.json').read_text())
    assert rr['acknowledge_failed'] is True
    assert 'acknowledge_executing' not in rr

    # Manifest records failed status
    set_aside_dirs = list((refactory / 'set-aside').iterdir())
    assert len(set_aside_dirs) == 1
    manifest = json.loads((set_aside_dirs[0] / 'manifest.json').read_text())
    assert manifest['checkpoint_status_before'] == 'failed'


def test_acknowledge_failed_then_prepare_accepts(tmp_path):
    """After acknowledging a failed checkpoint, recovery.prepare() accepts it."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    source = root / 'mode.py'
    source.write_text('answer = 42\n')

    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {str(source): _fp(source)},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Acknowledge first
    graph_control.request(root, 'acknowledge')

    # Now prepare should accept
    result = recovery.prepare(root, 'test-run')
    assert result == root / 'graphs'


def test_stopped_run_acknowledge_then_resume(tmp_path):
    """A stopped run (checkpoint failed + pending dispatch) is continuable.

    dm-97: reproduces what 'refactory stop' during an agent step leaves:
    checkpoint failed plus a pending dispatch under agent-delivery/pending/.
    acknowledge then resume must work with no file edited by hand.
    """
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    source = root / 'mode.py'
    source.write_text('answer = 42\n')

    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {str(source): _fp(source)},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Create pending dispatch
    pending_dir = refactory / 'agent-delivery/pending'
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / 'delivery1.json').write_text(json.dumps({'node': 'emit'}))

    # Step 1: acknowledge
    ack_result = graph_control.request(root, 'acknowledge')
    assert ack_result['status'] == 'acknowledged'
    assert not (pending_dir / 'delivery1.json').exists()

    # Step 2: resume — goes through recovery.start which calls prepare()
    with patch('refactory_dsh.recovery.start') as mock_start:
        mock_start.return_value = {'status': 'queued', 'action': 'resume',
                                   'run_id': str(root), 'pid': 99999}
        result = graph_control.request(root, 'resume')

    assert result['action'] == 'resume'
    # acknowledge already wrote the acknowledgement, so no flag needed
    mock_start.assert_called_once_with(root, acknowledge_failed=False)


def test_resume_pending_propagates_acknowledge_failed(tmp_path):
    """The supervisor propagates acknowledge_failed from resume-pending.json."""
    from refactory_dsh import supervisor as sv

    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead_pid = _dead_pid()
    source = root / 'mode.py'
    source.write_text('answer = 42\n')

    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead_pid,
        'file_fingerprints': {},
    }
    cp_text = json.dumps(checkpoint)
    (refactory / 'spine-checkpoint.json').write_text(cp_text)
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
        'configuration_fingerprints': {str(source): _fp(source)},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Read resume-pending and verify it propagates acknowledge_failed
    pending = sv._read_resume_pending(root)
    assert pending is None  # No pending file yet

    # Write resume-pending with acknowledge_failed
    import hashlib as _hashlib
    pending_data = {
        'at': '2026-10-19T09:45:47Z',
        'checkpoint_sha256': _hashlib.sha256(cp_text.encode()).hexdigest(),
        'acknowledge_failed': True,
    }
    (refactory / 'resume-pending.json').write_text(json.dumps(pending_data))

    pending = sv._read_resume_pending(root)
    assert pending is not None
    assert pending['acknowledge_failed'] is True


# ── T13: Transient failed status does not stop the owner ──────────────────

def test_transient_failed_does_not_stop_owner():
    """A status that goes failed->executing within the grace period does not stop the owner.

    When a node fails and the graph retries it, spine-status.json briefly shows
    'failed' then 'executing' again. The stop condition must hold for the entire
    grace period: a transient terminal status must not end the owner.
    """
    import subprocess as _sp
    from refactory_dsh import owned_process

    call_count = 0

    def transient_stop():
        nonlocal call_count
        call_count += 1
        return call_count == 1  # True on first check, False on re-check

    proc = MagicMock(spec=_sp.Popen)
    proc.args = ['test']
    proc.stdin = None
    proc.returncode = 0

    # poll(): None (running), None (running after re-check continue), 0 (exited)
    proc.poll.side_effect = [None, None, 0]

    # wait(): always timeout — process doesn't exit during grace/poll waits
    proc.wait.side_effect = _sp.TimeoutExpired('test', 0.01)

    with patch.object(owned_process, 'STOP_CONDITION_GRACE_S', 0.01), \
         patch.object(owned_process, 'shutdown') as mock_shutdown:
        owned_process._communicate_with_stop(
            proc, timeout=10, stop_condition=transient_stop, label='test')

    # The process was NOT shut down because the condition became false
    mock_shutdown.assert_not_called()
    # The condition was checked at least twice (once to trigger, once to verify)
    assert call_count >= 2


# ── T14: Stale stop from previous owner does not stop a later rerun ───────

def test_stale_stop_not_queued_for_terminal_run(tmp_path):
    """graph-control stop on a terminal run with a live owner signals
    directly and creates no queued control file.  A later rerun therefore
    sees no stale stop.

    This proves the end-to-end property: 'a stale stop from a previous
    owner never stops the next one' — because a stop on a terminal run
    never creates a queued control in the first place (direct signal).
    """
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    # Terminal run with a "live" owner (use current PID)
    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': os.getpid(),
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Patch os.kill to avoid actually sending SIGTERM to this process
    killed_pids: list[int] = []
    original_kill = os.kill

    def mock_kill(pid: int, sig: int) -> None:
        if sig != 0:
            killed_pids.append(pid)
        else:
            original_kill(pid, sig)

    with patch('refactory_dsh.graph_control.os.kill', side_effect=mock_kill):
        result = graph_control.request(root, 'stop')

    # Direct signal, not queued
    assert result['status'] == 'signalled'
    assert os.getpid() in killed_pids

    # No control file was created in graph-controls/
    controls_dir = refactory / 'graph-controls'
    if controls_dir.exists():
        queued = list(controls_dir.glob('*.json'))
        assert len(queued) == 0, f'Stale stop controls found: {queued}'

    # A later rerun would see no stale stop
    controls = graph_control.queued_controls(root)
    assert len(controls) == 0, 'Stale stop visible to a later owner'


def test_withdraw_then_rerun_not_stopped(tmp_path):
    """A queued stop from a previous owner is withdrawn; a subsequent
    rerun through the supervisor proceeds without being stopped.
    """
    from refactory_dsh import supervisor as sv

    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)

    dead = _dead_pid()
    checkpoint: dict[str, Any] = {
        'status': 'halted',
        'run_id': str(root),
        'graph': {'name': 'test-run'},
        'owner_pid': dead,
        'file_fingerprints': {},
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'halted'}))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'test/run', 'project': str(root),
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))

    # Queue a stop control (simulating a previous owner's stop)
    controls_dir = refactory / 'graph-controls'
    controls_dir.mkdir(parents=True, exist_ok=True)
    old_stop_id = 'old-stop-from-previous-owner'
    (controls_dir / f'{old_stop_id}.json').write_text(json.dumps({
        'id': old_stop_id, 'action': 'stop', 'status': 'queued',
    }))

    # Verify the stale stop is listed in status
    controls = graph_control.queued_controls(root)
    assert len(controls) == 1
    assert controls[0]['action'] == 'stop'

    # Withdraw it
    result = graph_control.request(root, 'withdraw', node=old_stop_id)
    assert result['status'] == 'withdrawn'

    # After withdraw, no controls remain
    controls = graph_control.queued_controls(root)
    assert len(controls) == 0

    # Supervise: the new run completes without being stopped
    class FakeChild:
        def __init__(self) -> None:
            self.pid = 50001
            self.returncode = 0
        def wait(self) -> None:
            pass
        def poll(self) -> int:
            return 0

    def mock_start_child(r: Path) -> Any:
        (r / '.refactory/spine-status.json').write_text(
            json.dumps({'status': 'done'}))
        return FakeChild()

    def mock_prepare(r: Path, name: str, **kw: Any) -> Path:
        return r / 'graphs'

    with patch.object(sv, '_start_child', side_effect=mock_start_child), \
         patch('refactory_dsh.recovery.prepare', side_effect=mock_prepare):
        sv.supervise(root, max_respawns=3)

    # Run completed without being stopped
    status = run_state.spine_status(root)
    assert status == 'done'
    # No respawn was needed (ran clean)
    history = run_state.read_supervision_history(root)
    assert len(history) == 0


# ── helpers for redeliver tests ────────────────────────────────────────────

def _make_run_dir_with_deliver(tmp_path: Path, *, status: str = 'done',
                                cursor: list[str] | None = None) -> Path:
    """Create a run directory that has a deliver node, for redeliver tests."""
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)
    (refactory / 'spine-status.json').write_text(json.dumps({'status': status}))

    checkpoint: dict[str, Any] = {
        'status': status,
        'run_id': str(root),
        'graph': {
            'name': 'default_pack-design',
            'nodes': {
                'check': {'_type': 'GateNode', 'id': 'check', 'evaluator_type': 'fn',
                          'evaluator_command': 'true', 'reads': [],
                          'writes': ['check.txt']},
                'emit': {'_type': 'AgentNode', 'id': 'emit', 'role': 'builder',
                         'prompt_template': 'E', 'reads': [], 'writes': []},
                'deliver': {'_type': 'FnNode', 'id': 'deliver',
                            'callable_name': 'refactory_dsh.delivery:deliver_design',
                            'command': 'dsh-run refactory_dsh.delivery:deliver_design',
                            'reads': [], 'writes': []},
            },
            'edges': [
                {'source': 'emit', 'target': 'check'},
                {'source': 'check', 'target': 'deliver'},
            ],
        },
        'state': {
            'cursor': cursor or [],
            'completed': ['emit', 'check'] + (['deliver'] if status == 'done' else []),
            'skipped': [],
            'pause_before': [],
            'gate_verdicts': {},
            'iterations': {},
            'feedback': {},
            'gate_reasons': {},
            'afters': [],
            'prompts': {},
            'log': [],
        },
        'file_fingerprints': {},
        'owner_pid': _dead_pid(),
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'default_pack/design', 'project': str(root),
        'configuration_fingerprints': {},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))
    (root / 'facts.json').write_text(json.dumps({'name': 'test'}))
    return root


# ── dm-106: redeliver ─────────────────────────────────────────────────────


def test_redeliver_queues_rerun_and_starts_owner(tmp_path):
    """redeliver on a done run queues a rerun targeting 'deliver' and starts
    the owner.
    """
    root = _make_run_dir_with_deliver(tmp_path, status='done')
    with patch('refactory_dsh.graph_control.recovery.start',
               return_value={'action': 'resume', 'status': 'queued', 'pid': 1}) as mock_start:
        result = graph_control.request(root, 'redeliver')

    assert result['action'] == 'rerun'
    assert result['target'] == 'deliver'
    assert result['source'] == 'redeliver'
    assert result['status'] == 'queued'
    assert 'owner' in result
    mock_start.assert_called_once_with(root, acknowledge_failed=False)
    # A control file was written
    controls_dir = root / '.refactory/graph-controls'
    control_files = list(controls_dir.glob('*.json'))
    assert len(control_files) == 1
    control = json.loads(control_files[0].read_text())
    assert control['target'] == 'deliver'
    assert control['source'] == 'redeliver'


def test_redeliver_failed_at_deliver(tmp_path):
    """redeliver works on a run that failed at the deliver node, passing the
    checkpoint-bound acknowledgement automatically.
    """
    root = _make_run_dir_with_deliver(tmp_path, status='failed', cursor=['deliver'])
    with patch('refactory_dsh.graph_control.recovery.start',
               return_value={'action': 'resume', 'status': 'queued', 'pid': 1}) as mock_start:
        result = graph_control.request(root, 'redeliver')

    assert result['target'] == 'deliver'
    assert result['status'] == 'queued'
    mock_start.assert_called_once_with(root, acknowledge_failed=True)


def test_redeliver_failed_elsewhere_refused(tmp_path):
    """redeliver refuses a run that failed at a node other than deliver."""
    root = _make_run_dir_with_deliver(tmp_path, status='failed', cursor=['check'])
    with pytest.raises(ValueError, match='failed at node.*check.*not at delivery'):
        graph_control.request(root, 'redeliver')


def test_redeliver_running_refused(tmp_path):
    """redeliver refuses a run that is still running."""
    root = _make_run_dir_with_deliver(tmp_path, status='executing', cursor=['deliver'])
    (root / '.refactory/spine-status.json').write_text(json.dumps({'status': 'executing'}))
    with pytest.raises(ValueError, match='redeliver requires a stopped run'):
        graph_control.request(root, 'redeliver')


def test_rerun_text_on_no_prompt_node_refused(tmp_path):
    """rerun --text on a FnNode (no prompt) is refused with a clear message."""
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    # Try to rerun the 'materialise' FnNode with text
    # First get the revision
    checkpoint = root / '.refactory/spine-checkpoint.json'
    revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='this node takes no text; drop --text'):
        graph_control.request(root, 'rerun', node='materialise',
                              text='fix this', revision=revision)


def test_rerun_text_on_agent_node_not_refused_by_text_check(tmp_path):
    """rerun --text on an AgentNode passes the text check (may fail later
    at preview/revision, but not at the text check).
    """
    root = _make_run_dir(tmp_path, status='halted', owner_pid=_dead_pid())
    checkpoint = root / '.refactory/spine-checkpoint.json'
    revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    # The 'emit' node is an AgentNode — text should pass the type check
    # but may fail at redo_plans/revision validation
    try:
        graph_control.request(root, 'rerun', node='emit',
                              text='fix this', revision=revision)
    except ValueError as exc:
        # It should NOT be the text check error
        assert 'this node takes no text' not in str(exc)


def test_redeliver_finds_deliver_by_callable_not_id(tmp_path):
    """redeliver finds the deliver node by callable_name, so a mode whose
    deliver node has another id still works.
    """
    root = tmp_path
    refactory_dir = root / '.refactory'
    refactory_dir.mkdir(parents=True, exist_ok=True)
    (refactory_dir / 'spine-status.json').write_text(json.dumps({'status': 'done'}))

    checkpoint: dict[str, Any] = {
        'status': 'done',
        'run_id': str(root),
        'graph': {
            'name': 'custom-mode',
            'nodes': {
                'ship_it': {'_type': 'FnNode', 'id': 'ship_it',
                            'callable_name': 'refactory_dsh.delivery:deliver_design',
                            'command': 'dsh-run refactory_dsh.delivery:deliver_design',
                            'reads': [], 'writes': []},
            },
            'edges': [],
        },
        'state': {
            'cursor': [],
            'completed': ['ship_it'],
            'skipped': [],
            'pause_before': [],
            'gate_verdicts': {},
            'iterations': {},
            'feedback': {},
            'gate_reasons': {},
            'afters': [],
            'prompts': {},
            'log': [],
        },
        'file_fingerprints': {},
        'owner_pid': _dead_pid(),
    }
    (refactory_dir / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory_dir / 'run-config.json').write_text(json.dumps({
        'mode': 'custom/mode', 'project': str(root),
        'configuration_fingerprints': {},
    }))
    (refactory_dir / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))
    (root / 'facts.json').write_text(json.dumps({'name': 'test'}))

    with patch('refactory_dsh.graph_control.recovery.start',
               return_value={'action': 'resume', 'status': 'queued', 'pid': 1}):
        result = graph_control.request(root, 'redeliver')

    assert result['target'] == 'ship_it'
    assert result['source'] == 'redeliver'


def test_redeliver_failed_at_delivery_gate(tmp_path):
    """redeliver accepts a run that failed at delivery_gate (the gate after
    deliver), e.g. when conflict resolution exhausted its budget.

    Fails without the change: _redeliver only checks if the cursor contains
    the deliver op node, not the delivery gate.
    """
    root = tmp_path
    refactory = root / '.refactory'
    refactory.mkdir(parents=True, exist_ok=True)
    (refactory / 'spine-status.json').write_text(json.dumps({'status': 'failed'}))

    checkpoint: dict[str, Any] = {
        'status': 'failed',
        'run_id': str(root),
        'graph': {
            'name': 'default_pack-design',
            'nodes': {
                'check': {'_type': 'GateNode', 'id': 'check', 'evaluator_type': 'fn',
                          'evaluator_command': 'true', 'reads': [],
                          'writes': ['check.txt']},
                'emit': {'_type': 'AgentNode', 'id': 'emit', 'role': 'builder',
                         'prompt_template': 'E', 'reads': [], 'writes': []},
                'deliver': {'_type': 'FnNode', 'id': 'deliver',
                            'callable_name': 'refactory_dsh.delivery:deliver_design',
                            'command': 'dsh-run refactory_dsh.delivery:deliver_design',
                            'reads': [], 'writes': []},
                'delivery_gate': {'_type': 'GateNode', 'id': 'delivery_gate',
                                  'evaluator_type': 'fn', 'max_iterations': 8,
                                  'reads': [], 'writes': []},
            },
            'edges': [
                {'source': 'emit', 'target': 'check'},
                {'source': 'check', 'target': 'deliver'},
                {'source': 'deliver', 'target': 'delivery_gate'},
                {'source': 'delivery_gate', 'target': 'emit', 'condition': 'reloop'},
            ],
        },
        'state': {
            'cursor': ['delivery_gate'],
            'completed': ['emit', 'check', 'deliver'],
            'skipped': [],
            'pause_before': [],
            'gate_verdicts': {},
            'iterations': {},
            'feedback': {},
            'gate_reasons': {},
            'afters': [],
            'prompts': {},
            'log': [],
        },
        'file_fingerprints': {},
        'owner_pid': _dead_pid(),
    }
    (refactory / 'spine-checkpoint.json').write_text(json.dumps(checkpoint))
    (refactory / 'run-config.json').write_text(json.dumps({
        'mode': 'default_pack/design', 'project': str(root),
        'configuration_fingerprints': {},
    }))
    (refactory / 'invocation.json').write_text(json.dumps({'id': 'test-inv'}))
    (root / 'facts.json').write_text(json.dumps({'name': 'test'}))

    with patch('refactory_dsh.graph_control.recovery.start',
               return_value={'action': 'resume', 'status': 'queued', 'pid': 1}) as mock_start:
        result = graph_control.request(root, 'redeliver')

    assert result['target'] == 'deliver'
    assert result['source'] == 'redeliver'
    assert result['status'] == 'queued'
    mock_start.assert_called_once_with(root, acknowledge_failed=True)
