import hashlib
import json
from pathlib import Path

import pytest

from refactory_dsh import console, graph_control, live


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
    assert console.status(tmp_path)['state'] == 'died'
    (directory / 'resource-usage.json').write_text(json.dumps({'exhausted': True, 'reason': 'token budget exhausted'}))
    result = console.status(tmp_path)
    assert result['state'] == 'failed'
    assert not result['running']
    assert result['activity']['wait_reason'] == 'token budget exhausted'


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


def test_graph_snapshot_tolerates_old_and_partial_checkpoints(tmp_path):
    assert live.snapshot(tmp_path)['graph'] is None
    checkpoint = tmp_path / '.refactory/spine-checkpoint.json'
    checkpoint.parent.mkdir()
    for content in ['{', '{}', '{"graph": {"nodes": []}}']:
        checkpoint.write_text(content)
        assert live.snapshot(tmp_path)['graph'] is None
