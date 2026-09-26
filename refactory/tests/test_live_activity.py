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
