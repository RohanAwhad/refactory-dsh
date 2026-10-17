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


def test_graph_snapshot_tolerates_old_and_partial_checkpoints(tmp_path):
    assert live.snapshot(tmp_path)['graph'] is None
    checkpoint = tmp_path / '.refactory/spine-checkpoint.json'
    checkpoint.parent.mkdir()
    for content in ['{', '{}', '{"graph": {"nodes": []}}']:
        checkpoint.write_text(content)
        assert live.snapshot(tmp_path)['graph'] is None
