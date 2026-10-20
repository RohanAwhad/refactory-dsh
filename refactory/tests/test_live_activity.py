import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactory_dsh import console, graph_control, live, readable


def test_cursor_does_not_acknowledge_partial_write(tmp_path):
    live.emit(tmp_path, 'node_start', node_id='a', state='running')
    journal = tmp_path / live.EVENTS
    with journal.open('ab') as handle:
        handle.write(b'{"kind":"node_complete"')
    events, cursor = live.events(tmp_path)
    assert cursor == 1
    assert events[0]['run_id'] == str(tmp_path)
    assert live.events(tmp_path, after=cursor) == ([], 1)


def test_unknown_usage_is_absent_and_completed_stages_survive_terminal(tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-status.json').write_text(json.dumps({'status': 'done', 'completed': ['a', 'qa'], 'nodes': []}))
    activity = live.snapshot(tmp_path)
    assert activity['completed'] == ['a', 'qa']
    assert activity['resources'] is None
    assert activity['last_event_at'] is None
    assert console.status(tmp_path)['activity'] == activity


def test_rerun_requires_exact_preview_and_never_executes_work(tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    checkpoint = directory / 'spine-checkpoint.json'
    checkpoint.write_text(json.dumps({'redo_plans': {'a': {'nodes': ['a', 'qa']}}}))
    (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
    preview = graph_control.request(tmp_path, 'preview', node='a')
    assert preview['invalidated'] == ['a', 'qa']
    assert not (directory / 'graph-controls').exists()
    with pytest.raises(ValueError, match='preview'):
        graph_control.request(tmp_path, 'rerun', node='a')
    receipt = graph_control.request(tmp_path, 'rerun', node='a', revision=preview['revision'])
    assert receipt['status'] == 'queued'
    assert receipt['run_id'] == str(tmp_path)
    checkpoint.write_text('{}')
    with pytest.raises(ValueError, match='preview'):
        graph_control.request(tmp_path, 'rerun', node='a', revision=preview['revision'])


def test_cursor_validation(tmp_path):
    with pytest.raises(ValueError, match='nonnegative'):
        live.events(tmp_path, -1)


def test_append_after_interrupted_record_exposes_gap_and_new_activity(tmp_path):
    live.emit(tmp_path, 'before')
    with (tmp_path / live.EVENTS).open('ab') as handle:
        handle.write(b'{"partial":')
    live.emit(tmp_path, 'after')
    events, cursor = live.events(tmp_path, after=1)
    assert [event['kind'] for event in events] == ['journal_gap', 'after']
    assert cursor == 3
    assert live.events(tmp_path, after=cursor) == ([], cursor)


def test_malformed_complete_record_cannot_hide_later_events(tmp_path):
    live.emit(tmp_path, 'before')
    with (tmp_path / live.EVENTS).open('ab') as handle:
        handle.write(b'bad record\n\n')
    live.emit(tmp_path, 'after')
    assert [event['kind'] for event in live.events(tmp_path)[0]] == ['before', 'journal_gap', 'after']


def test_control_file_order_preserves_request_order(monkeypatch, tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-checkpoint.json').write_text('{}')
    # UUID order deliberately opposes arrival order.
    from types import SimpleNamespace
    identifiers = iter(['z', 'a'])
    moments = iter([2, 10])
    monkeypatch.setattr(graph_control.uuid, 'uuid4', lambda: SimpleNamespace(hex=next(identifiers)))
    monkeypatch.setattr(graph_control.time, 'time_ns', lambda: next(moments))
    graph_control.request(tmp_path, 'pause')
    graph_control.request(tmp_path, 'stop')
    queued = [json.loads(path.read_text()) for path in sorted((directory / 'graph-controls').glob('*.json'))]
    assert [item['action'] for item in queued] == ['pause', 'stop']


def test_exhausted_or_exited_graph_never_claims_running(tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-status.json').write_text(json.dumps({'status': 'executing', 'nodes': ['work']}))
    (directory / 'spine-checkpoint.json').write_text(json.dumps({'owner_pid': 99999999}))
    result = console.status(tmp_path)
    assert result['state'] == 'died'
    assert result['owner_alive'] is False
    (directory / 'resource-usage.json').write_text(json.dumps({'exhausted': True, 'reason': 'token budget exhausted'}))
    result = console.status(tmp_path)
    assert result['state'] == 'failed'
    assert not result['running']
    assert result['activity']['wait_reason'] == 'token budget exhausted'


def test_owner_alive_reflects_pid_liveness(tmp_path):
    """owner_alive=True for a live PID, False for a dead one, None when absent."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-status.json').write_text(json.dumps({'status': 'executing', 'nodes': []}))

    # Live PID
    (directory / 'spine-checkpoint.json').write_text(json.dumps({'owner_pid': os.getpid()}))
    result = console.status(tmp_path)
    assert result['owner_alive'] is True

    # Dead PID
    (directory / 'spine-checkpoint.json').write_text(json.dumps({'owner_pid': 99999999}))
    result = console.status(tmp_path)
    assert result['owner_alive'] is False

    # No owner PID
    (directory / 'spine-checkpoint.json').write_text(json.dumps({}))
    result = console.status(tmp_path)
    assert result['owner_alive'] is None


def test_status_surfaces_failed_control_in_activity(tmp_path):
    """console.status() includes failed graph control receipt from activity.control."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-status.json').write_text(json.dumps({'status': 'executing', 'nodes': ['work']}))
    (directory / 'spine-checkpoint.json').write_text(json.dumps({'owner_pid': os.getpid()}))
    (directory / 'graph-control-receipt.json').write_text(json.dumps({
        'action': 'rerun', 'status': 'failed',
        'message': 'graph changed since preview; preview invalidation again',
    }))
    result = console.status(tmp_path)
    control = result['activity'].get('control')
    assert control is not None
    assert control['status'] == 'failed'
    assert 'graph changed since preview' in control['message']


def test_graph_control_shows_last_failed_receipt(tmp_path):
    """graph_control.request() returns the last failed receipt when one exists."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-checkpoint.json').write_text(json.dumps({}))
    (directory / 'graph-control-receipt.json').write_text(json.dumps({
        'action': 'rerun', 'status': 'failed',
        'message': 'graph changed since preview; preview invalidation again',
    }))
    result = graph_control.request(tmp_path, 'pause')
    assert result['status'] == 'queued'
    assert result['last_failed_receipt']['status'] == 'failed'
    assert 'graph changed since preview' in result['last_failed_receipt']['message']


def test_graph_snapshot_reads_saved_topology_without_commands_or_mutations(tmp_path):
    checkpoint = tmp_path / '.refactory/spine-checkpoint.json'
    checkpoint.parent.mkdir()
    graph = {'nodes': {
        'fork': {'_type': 'ForkNode', 'targets': ['work', 'qa']},
        'work': {'_type': 'AgentNode', 'command': 'DO NOT EXECUTE', 'prompt_template': 'private'},
        'qa': {'_type': 'FnNode'},
        'join': {'_type': 'JoinNode', 'sources': ['work', 'qa']},
        'review': {'_type': 'GateNode'},
    }, 'edges': [{'source': 'join', 'target': 'review'},
                  {'source': 'review', 'target': 'fork', 'condition': 'reloop'},
                  {'source': 'missing', 'target': 'review'}]}
    checkpoint.write_text(json.dumps({'graph': graph, 'skipped': ['qa', 'missing']}))
    before = checkpoint.read_bytes()
    result = live.snapshot(tmp_path)['graph']
    assert result['nodes'][1] == {'id': 'work', 'kind': 'AgentNode'}
    assert result['skipped'] == ['qa']
    assert {(edge['source'], edge['target']) for edge in result['edges']} == {
        ('fork', 'work'), ('fork', 'qa'), ('work', 'join'), ('qa', 'join'),
        ('join', 'review'), ('review', 'fork'),
    }
    assert result['edges'][1]['label'] == 'reloop'
    assert 'private' not in json.dumps(result)
    assert 'DO NOT EXECUTE' not in json.dumps(result)
    assert checkpoint.read_bytes() == before
    assert not (tmp_path / live.EVENTS).exists()


def test_preview_returns_deferred_when_concurrent_work_blocks(tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    checkpoint = directory / 'spine-checkpoint.json'
    checkpoint.write_text(json.dumps({
        'deferred_redo_plans': {'a': {'nodes': ['a', 'join', 'end']}},
        'redo_plans': {},
    }))
    preview = graph_control.request(tmp_path, 'preview', node='a')
    assert preview['status'] == 'deferred'
    assert preview['invalidated'] == ['a', 'join', 'end']


def test_rerun_from_deferred_preview_records_deferred_nodes(tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
    checkpoint = directory / 'spine-checkpoint.json'
    checkpoint.write_text(json.dumps({
        'deferred_redo_plans': {'a': {'nodes': ['a', 'join', 'end']}},
        'redo_plans': {},
    }))
    revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    receipt = graph_control.request(tmp_path, 'rerun', node='a', revision=revision)
    assert receipt['status'] == 'queued'
    assert receipt['deferred_nodes'] == ['a', 'join', 'end']
    # Verify the control file on disk
    controls = list((directory / 'graph-controls').glob('*.json'))
    assert len(controls) == 1
    control = json.loads(controls[0].read_text())
    assert control['deferred_nodes'] == ['a', 'join', 'end']


def test_rerun_of_failed_run_refused_without_acknowledge(tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    checkpoint = directory / 'spine-checkpoint.json'
    checkpoint.write_text(json.dumps({
        'status': 'failed',
        'redo_plans': {'a': {'nodes': ['a']}},
    }))
    revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='acknowledge_failure'):
        graph_control.request(tmp_path, 'rerun', node='a', revision=revision)


def test_rerun_of_failed_run_queues_control_then_starts_owner(monkeypatch, tmp_path):
    from refactory_dsh import recovery
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
    checkpoint = directory / 'spine-checkpoint.json'
    checkpoint.write_text(json.dumps({
        'status': 'failed',
        'redo_plans': {'a': {'nodes': ['a']}},
    }))
    revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    start_calls = []
    def mock_start(root, *, acknowledge_failed=False):
        # Verify control file exists BEFORE start is called
        controls = list((root / '.refactory/graph-controls').glob('*.json'))
        start_calls.append({
            'root': root,
            'acknowledge_failed': acknowledge_failed,
            'controls_existed': len(controls) > 0,
        })
        return {'status': 'queued', 'pid': 999}
    monkeypatch.setattr(recovery, 'start', mock_start)
    result = graph_control.request(
        tmp_path, 'rerun', node='a', revision=revision,
        acknowledge_failure=True,
    )
    assert result['status'] == 'queued'
    assert result['target'] == 'a'
    assert len(start_calls) == 1
    assert start_calls[0]['acknowledge_failed'] is True
    assert start_calls[0]['controls_existed'] is True
    assert result['owner']['pid'] == 999
    assert 'Started a new owner' in readable.format_graph_control(result)


def test_resume_says_whether_it_started_an_owner(monkeypatch, tmp_path):
    """A resume that starts an owner names it; one queued for the live owner does not."""
    from refactory_dsh import recovery
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'run-config.json').write_text(json.dumps({'mode': 'demo/run', 'project': str(tmp_path)}))
    (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
    checkpoint = directory / 'spine-checkpoint.json'
    checkpoint.write_text(json.dumps({'status': 'halted'}))
    monkeypatch.setattr(recovery, 'prepare', lambda *args, **kwargs: None)
    monkeypatch.setattr('subprocess.Popen', lambda *args, **kwargs: SimpleNamespace(pid=4242))
    started = readable.format_graph_control(graph_control.request(tmp_path, 'resume'))
    assert 'Started a new owner' in started and 'pid 4242' in started
    checkpoint.write_text(json.dumps({'status': 'ready', 'owner_pid': os.getpid()}))
    queued = readable.format_graph_control(graph_control.request(tmp_path, 'resume'))
    assert 'Queued resume' in queued and 'Started a new owner' not in queued


def test_acknowledged_rerun_child_gets_past_validation(tmp_path):
    """Full parent-write → child-read → spine-delete lifecycle: one-shot semantics."""
    from refactory_dsh import recovery

    # -- setup: a failed run --
    directory = tmp_path / '.refactory'
    directory.mkdir()
    source = tmp_path / 'mode.py'
    source.write_text('answer = 42\n')
    config = {'mode': 'demo/run', 'project': str(tmp_path),
              'configuration_fingerprints': {str(source): recovery.fingerprint(source)}}
    (directory / 'run-config.json').write_text(json.dumps(config))
    payload = {'run_id': str(tmp_path), 'graph': {'name': 'demo-run'},
               'status': 'failed', 'file_fingerprints': {}}
    (directory / 'spine-checkpoint.json').write_text(json.dumps(payload))

    # -- parent path: prepare with acknowledgement --
    recovery.prepare(tmp_path, 'demo-run', acknowledge_failed=True)
    request_path = directory / 'resume-request.json'
    assert request_path.exists()
    assert json.loads(request_path.read_text())['acknowledge_failed'] is True

    # -- child path: prepare without acknowledgement --
    recovery.prepare(tmp_path, 'demo-run')
    assert json.loads(request_path.read_text())['acknowledge_failed'] is True

    # -- spine consumption: delete the file --
    request_path.unlink()

    # -- one-shot: a plain resume now refuses --
    with pytest.raises(ValueError, match='resume --acknowledge-failure'):
        recovery.prepare(tmp_path, 'demo-run')
    assert not request_path.exists()


def test_acknowledged_rerun_child_interactive_resume_path(monkeypatch, tmp_path):
    """The child's interactive resume path (interactive.prepare with resume=True)
    gets past validation when the parent wrote an acknowledged resume-request.json.
    This exercises the exact code path the bug was in: interactive.prepare(resume=True)
    → recovery.prepare(workdir, name) without acknowledge_failed."""
    from refactory_dsh import recovery, interactive

    # -- setup: a failed run with run-config --
    directory = tmp_path / '.refactory'
    directory.mkdir()
    source = tmp_path / 'mode.py'
    source.write_text('answer = 42\n')
    config = {'mode': 'demo/run', 'project': str(tmp_path),
              'configuration_fingerprints': {str(source): recovery.fingerprint(source)}}
    (directory / 'run-config.json').write_text(json.dumps(config))
    payload = {'run_id': str(tmp_path), 'graph': {'name': 'demo-run'},
               'status': 'failed', 'file_fingerprints': {}}
    (directory / 'spine-checkpoint.json').write_text(json.dumps(payload))

    # -- parent's prepare (as start() does inside the ownership block) --
    recovery.prepare(tmp_path, 'demo-run', acknowledge_failed=True)
    assert json.loads((directory / 'resume-request.json').read_text())['acknowledge_failed'] is True

    # -- child's interactive.prepare(resume=True): this is the path the bug was in --
    # interactive.prepare acquires ownership, so we need to release before it.
    # We call it directly — it calls recovery.prepare(workdir, name) without acknowledge_failed.
    name, graphs = interactive.prepare('demo/run', workdir=tmp_path, resume=True)
    assert name == 'demo-run'
    # The child got past validation — no ValueError raised.
    request = json.loads((directory / 'resume-request.json').read_text())
    assert request['acknowledge_failed'] is True


def test_console_main_graph_control_acknowledge_failure(monkeypatch, tmp_path):
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
    checkpoint = directory / 'spine-checkpoint.json'
    checkpoint.write_text(json.dumps({
        'status': 'failed',
        'redo_plans': {'a': {'nodes': ['a']}},
    }))
    revision = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    captured = {}
    def mock_request(root, action, **kwargs):
        captured.update(kwargs)
        captured['action'] = action
        return {'status': 'queued', 'target': 'a'}
    monkeypatch.setattr(graph_control, 'request', mock_request)
    console.main([
        'graph-control', 'rerun',
        '--run', str(tmp_path),
        '--node', 'a',
        '--revision', revision,
        '--acknowledge-failure',
    ])
    assert captured.get('acknowledge_failure') is True
    assert captured.get('node') == 'a'


def test_running_nodes_passes_through_live_snapshot(tmp_path):
    """running_nodes from spine-status.json flows through live.snapshot()."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    running_nodes = [{'id': 'research_director', 'kind': 'agent'},
                     {'id': 'gate_check', 'kind': 'gate'}]
    (directory / 'spine-status.json').write_text(json.dumps({
        'status': 'executing', 'nodes': ['research_director', 'gate_check'],
        'running_nodes': running_nodes,
        'wait_reason': 'waiting for worker delivery: research_director (agent), gate_check (gate)',
    }))
    activity = live.snapshot(tmp_path)
    assert activity['running_nodes'] == running_nodes
    assert 'research_director' in activity['wait_reason']


def test_step_tokens_sums_workers_by_node_id(tmp_path):
    """Two workers for the same node_id sum to one step_tokens entry."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    workers_dir = directory / 'workers'
    workers_dir.mkdir()
    (workers_dir / 'w1.json').write_text(json.dumps({
        'worker_id': 'w1', 'node_id': 'research_director',
        'usage': {'status': 'measured', 'tokens': 6000},
    }))
    (workers_dir / 'w2.json').write_text(json.dumps({
        'worker_id': 'w2', 'node_id': 'research_director',
        'usage': {'status': 'measured', 'tokens': 4000},
    }))
    (workers_dir / 'w3.json').write_text(json.dumps({
        'worker_id': 'w3', 'node_id': 'strategy',
        'usage': {'status': 'measured', 'tokens': 2000},
    }))
    spine = {'status': 'executing', 'nodes': ['design_doc'],
             'completed': ['research_director', 'strategy']}
    (directory / 'spine-status.json').write_text(json.dumps(spine))
    authoring = console._authoring(tmp_path, spine)
    assert authoring['step_tokens']['research_director'] == 10000
    assert authoring['step_tokens']['strategy'] == 2000
    assert authoring['total_tokens'] == 12000


def test_step_tokens_is_none_when_no_workers(tmp_path):
    """No worker receipts → step_tokens is None, not an empty dict."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    spine = {'status': 'executing', 'nodes': ['prepare']}
    (directory / 'spine-status.json').write_text(json.dumps(spine))
    authoring = console._authoring(tmp_path, spine)
    assert authoring['step_tokens'] is None
    assert authoring['total_tokens'] is None


def test_warn_tokens_emits_exactly_one_run_warning_per_step(tmp_path):
    """--warn-tokens triggers one run_warning per step via events.jsonl."""
    from refactory_dsh.budgets import RunBudget, ResourceLimits

    budget = RunBudget.create(tmp_path, ResourceLimits(warn_tokens=8000))
    # Record two workers for same node totaling 10000
    budget.record_usage('w1', 5000, node_id='research_director')
    budget.record_usage('w2', 5000, node_id='research_director')
    node_total = budget.node_tokens('research_director')
    assert node_total == 10000
    budget.warn_if_exceeded('research_director', node_total)
    # Read events.jsonl
    events_path = tmp_path / '.refactory' / 'events.jsonl'
    assert events_path.exists()
    all_lines = [json.loads(line) for line in events_path.read_text().strip().split('\n')]
    warnings = [e for e in all_lines if e.get('kind') == 'run_warning']
    assert len(warnings) == 1
    assert 'research_director' in warnings[0]['summary']
    assert '10,000' in warnings[0]['summary']

    # Second call for the same node does NOT emit a second warning
    budget.warn_if_exceeded('research_director', node_total)
    all_lines2 = [json.loads(line) for line in events_path.read_text().strip().split('\n')]
    warnings2 = [e for e in all_lines2 if e.get('kind') == 'run_warning']
    assert len(warnings2) == 1  # still only one


def test_warn_tokens_does_not_fire_below_threshold(tmp_path):
    """Below the threshold, no warning is emitted."""
    from refactory_dsh.budgets import RunBudget, ResourceLimits

    budget = RunBudget.create(tmp_path, ResourceLimits(warn_tokens=20000))
    budget.record_usage('w1', 5000, node_id='research_director')
    budget.warn_if_exceeded('research_director', 5000)
    events_path = tmp_path / '.refactory' / 'events.jsonl'
    if events_path.exists():
        all_lines = [json.loads(line) for line in events_path.read_text().strip().split('\n')]
        warnings = [e for e in all_lines if e.get('kind') == 'run_warning']
        assert len(warnings) == 0


def test_warn_tokens_validation_rejects_invalid_values():
    """warn_tokens must be a positive integer or unset."""
    from refactory_dsh.budgets import ResourceLimits
    with pytest.raises(ValueError, match="warn_tokens"):
        ResourceLimits(warn_tokens=0)
    with pytest.raises(ValueError, match="warn_tokens"):
        ResourceLimits(warn_tokens=-1)
    with pytest.raises(ValueError, match="warn_tokens"):
        ResourceLimits(warn_tokens=True)
    # Valid: positive integer
    limits = ResourceLimits(warn_tokens=1000)
    assert limits.warn_tokens == 1000
    # Valid: None (unset)
    limits = ResourceLimits(warn_tokens=None)
    assert limits.warn_tokens is None


def test_resume_waits_for_exiting_owner_then_starts_fresh(monkeypatch, tmp_path):
    """A resume of a terminal-status run whose owner exits during the wait starts a fresh owner."""
    import subprocess
    import sys
    from refactory_dsh import recovery

    directory = tmp_path / '.refactory'
    directory.mkdir()
    # Spawn a real child that exits after 0.5s — within the 3s timeout.
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.5)'])
    try:
        (directory / 'spine-checkpoint.json').write_text(
            json.dumps({'owner_pid': child.pid})
        )
        (directory / 'spine-status.json').write_text(
            json.dumps({'status': 'refused'})
        )
        (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
        start_calls = []
        monkeypatch.setattr(recovery, 'start', lambda root, **_kw: (
            start_calls.append(root),
            {'status': 'queued', 'action': 'resume', 'run_id': str(root), 'pid': 9999},
        )[1])
        result = graph_control.request(tmp_path, 'resume')
        assert len(start_calls) == 1, "recovery.start must be called when the owner exits"
        assert result['pid'] == 9999
        # No stale control file should be queued.
        controls = list((directory / 'graph-controls').glob('*.json'))
        assert len(controls) == 0, "no control file should be queued when a fresh owner was started"
    finally:
        try:
            child.kill()
        except OSError:
            pass
        child.wait()


def test_resume_reports_owner_still_alive_after_timeout(monkeypatch, tmp_path):
    """A resume at a terminal status where the owner outlives the timeout files a pending resume."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-checkpoint.json').write_text(
        json.dumps({'owner_pid': os.getpid()})
    )
    (directory / 'spine-status.json').write_text(
        json.dumps({'status': 'refused'})
    )
    (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
    monkeypatch.setattr(graph_control, '_OWNER_EXIT_TIMEOUT', 0.3)
    result = graph_control.request(tmp_path, 'resume')
    assert result['status'] == 'queued'
    assert result.get('resume_pending') is True
    assert 'owner_exiting' not in result
    formatted = readable.format_graph_control(result)
    assert 'Resume is pending; a new owner will start when the current one exits.' in formatted
    assert (directory / 'resume-pending.json').exists()


def test_resume_queued_for_paused_live_owner(tmp_path):
    """A live owner paused at a boundary gets a queued resume — today's behaviour."""
    directory = tmp_path / '.refactory'
    directory.mkdir()
    (directory / 'spine-checkpoint.json').write_text(
        json.dumps({'owner_pid': os.getpid()})
    )
    (directory / 'spine-status.json').write_text(
        json.dumps({'status': 'paused'})
    )
    (directory / 'invocation.json').write_text(json.dumps({'id': 'inv1'}))
    result = graph_control.request(tmp_path, 'resume')
    assert result['status'] == 'queued'
    assert 'owner_exiting' not in result
    assert 'resume_pending' not in result


def test_graph_snapshot_tolerates_old_and_partial_checkpoints(tmp_path):
    assert live.snapshot(tmp_path)['graph'] is None
    checkpoint = tmp_path / '.refactory/spine-checkpoint.json'
    checkpoint.parent.mkdir()
    for content in ['{', '{}', '{"graph": {"nodes": []}}']:
        checkpoint.write_text(content)
        assert live.snapshot(tmp_path)['graph'] is None
