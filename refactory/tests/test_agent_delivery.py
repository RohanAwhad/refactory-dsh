from __future__ import annotations

import json
from pathlib import Path
import sys

from refactory_dsh import agent_delivery, nn
from refactory_dsh.model import DshModelClient
from refactory_dsh.steering import Ledger


def test_contract_keeps_task_steering_and_exact_paths_without_exposing_settings(tmp_path):
    Ledger.seed(tmp_path, 'Preserve the public API and run the requested check.')
    task = 'Write result.json after checking input.py.\n'
    context = {'python_bin': str(Path(sys.executable).parent), 'runtime_root': '/selected/runtime',
               'api_key': 'never-in-a-prompt', 'permission_mode': 'skip'}
    first = agent_delivery.begin(tmp_path, 'check', task, ['result.json'], reads=['input.py'], model_context=context)
    prompt = first['prompt']
    assert task in prompt and 'Preserve the public API' in prompt
    assert 'never-in-a-prompt' not in prompt and 'permission_mode' not in prompt
    assert first['execution'] == {
        'node': 'check', 'workdir': str(tmp_path), 'inputs': [str(tmp_path / 'input.py')],
        'outputs': [str(tmp_path / 'result.json')], 'python': str(Path(sys.executable).parent / 'python'),
        'refactory_source': str(Path(agent_delivery.__file__).parent), 'runtime_root': '/selected/runtime',
    }
    repeated = agent_delivery.begin(tmp_path, 'check', task, ['result.json'], reads=['input.py'], model_context=context, reuse=True)
    assert repeated == first
    assert (tmp_path / first['contexts'][0]['prompt']).read_text() == prompt
    assert json.loads((tmp_path / first['receipt']).read_text())['execution'] == first['execution']


def test_brief_final_response_does_not_replace_delivered_artifact(tmp_path):
    artifact = '{"answer":42}\n'
    first = agent_delivery.begin(tmp_path, 'build', 'Write result.json.', ['result.json'])
    (tmp_path / 'result.json').write_text(artifact)
    receipt = agent_delivery.finish(tmp_path, 'build', final_response='Done.', capture_final=True)
    assert receipt['status'] == 'delivered' and receipt['captured_final'] is None
    assert (tmp_path / 'result.json').read_text() == artifact
    assert (tmp_path / receipt['final_response']).read_text() == 'Done.'
    assert receipt['execution'] == first['execution']


def test_a_non_output_only_step_that_changes_hooks_warns_naming_the_step(tmp_path, capsys):
    """Per-step shared_changed check in finish(): warning path (#347)."""
    import subprocess
    from refactory_dsh import target_guard, worktree
    from tests_support import git_repo

    repo = git_repo(tmp_path / 'repo')
    run = tmp_path / 'run'
    found = worktree.create(repo, 'HEAD', 'hook0000warn', run)
    target_guard.record(run, repo, output_only=('emit',))
    agent_delivery.begin(run, 'research_director', 'Research.', ['research-plan.json'])
    # Modify a hook file in the shared hooks directory
    hooks = Path(subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute',
                                 '--git-common-dir'], capture_output=True, text=True,
                                check=True).stdout.strip()) / 'hooks'
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / 'pre-commit').write_text('#!/bin/sh\nexit 0\n')
    (run / 'research-plan.json').write_text('[]\n')
    receipt = agent_delivery.finish(run, 'research_director')
    assert receipt['status'] == 'delivered'
    err = capsys.readouterr().err
    assert 'changed during research_director' in err
    assert target_guard.WARNING.strip(':').strip() in err


def test_an_output_only_step_that_changes_hooks_fails_the_step(tmp_path):
    """Per-step shared_changed check in finish(): failure path for output-only (#347)."""
    import subprocess
    from refactory_dsh import target_guard, worktree
    from tests_support import git_repo

    repo = git_repo(tmp_path / 'repo')
    run = tmp_path / 'run'
    found = worktree.create(repo, 'HEAD', 'hook1111fail', run)
    target_guard.record(run, repo, output_only=('emit', 'design_doc'))
    agent_delivery.begin(run, 'design_doc', 'Write the design.', ['design.md'])
    hooks = Path(subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute',
                                 '--git-common-dir'], capture_output=True, text=True,
                                check=True).stdout.strip()) / 'hooks'
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / 'pre-commit').write_text('#!/bin/sh\nexit 0\n')
    (run / 'design.md').write_text('the design\n')
    import pytest
    with pytest.raises(ValueError, match='changed shared git state'):
        agent_delivery.finish(run, 'design_doc')


def test_a_step_that_does_not_change_hooks_is_not_warned(tmp_path, capsys):
    """No false positive when hooks are unchanged (#347)."""
    from refactory_dsh import target_guard, worktree
    from tests_support import git_repo

    repo = git_repo(tmp_path / 'repo')
    run = tmp_path / 'run'
    found = worktree.create(repo, 'HEAD', 'hook2222safe', run)
    target_guard.record(run, repo, output_only=('emit',))
    agent_delivery.begin(run, 'research_director', 'Research.', ['research-plan.json'])
    (run / 'research-plan.json').write_text('[]\n')
    receipt = agent_delivery.finish(run, 'research_director')
    assert receipt['status'] == 'delivered'
    err = capsys.readouterr().err
    assert 'changed during' not in err
    assert 'changed shared git state' not in err


def test_shared_state_check_does_not_run_without_a_worktree(tmp_path, capsys):
    """The check only fires when worktree.recorded returns a Worktree (#347)."""
    import subprocess
    from refactory_dsh import target_guard

    root = tmp_path / 'target'
    root.mkdir()
    subprocess.run(['git', '-C', str(root), 'init', '-q'], check=True, capture_output=True)
    (root / 'model.py').write_text('ORIGINAL = True\n')
    run = tmp_path / 'run'
    target_guard.record(run, root, output_only=('emit',))
    agent_delivery.begin(run, 'research_director', 'Research.', ['research-plan.json'])
    # Create hooks dir and modify hooks -- but no worktree record, so no shared check
    hooks = root / '.git' / 'hooks'
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / 'pre-commit').write_text('#!/bin/sh\nexit 0\n')
    (run / 'research-plan.json').write_text('[]\n')
    receipt = agent_delivery.finish(run, 'research_director')
    assert receipt['status'] == 'delivered'
    err = capsys.readouterr().err
    assert 'changed shared git state' not in err
    assert 'changed during' not in err


def test_planning_steps_are_output_only_in_design_but_not_in_a_plain_locate(tmp_path, monkeypatch):
    """Mode-supplied output_only through locate(): mode isolation (#347)."""
    import subprocess
    from refactory_dsh import design_ops, target_guard, worktree
    from refactory_dsh.design import DesignMode
    from tests_support import FakeModel, git_repo

    repo = git_repo(tmp_path / 'repo')
    monkeypatch.setenv('REFACTORY_PROJECT', str(repo))

    # A design-mode run records planning steps as output-only
    design_run = tmp_path / 'design_run'
    ctx_design = nn.RunContext(design_run, model=FakeModel([]), knobs={'worktree': False},
                               task={'name': 'guard_tool', 'brief': 'a tool', 'check': 'true'})
    ctx_design.facts.update(design_ops.prepare_design(ctx_design))
    recorded_design = json.loads((design_run / target_guard.RECORD).read_text())
    for step in design_ops.PLANNING_STEPS:
        assert step in recorded_design['output_only'], f'{step} must be output-only in design'
    assert 'emit' in recorded_design['output_only']

    # A plain locate (not through prepare_design) does NOT include planning steps
    from refactory_dsh import repo_change
    plain_run = tmp_path / 'plain_run'
    ctx_plain = nn.RunContext(plain_run, model=FakeModel([]), knobs={'worktree': False},
                              task={'name': 'other_tool', 'brief': 'a tool', 'check': 'true'})
    ctx_plain.facts.update(repo_change.locate(ctx_plain, 'other_tool',
                                              dict(ctx_plain.task or {})))
    recorded_plain = json.loads((plain_run / target_guard.RECORD).read_text())
    assert recorded_plain['output_only'] == ['emit'], \
        f'plain locate must have only emit, got {recorded_plain["output_only"]}'
    for step in design_ops.PLANNING_STEPS:
        assert step not in recorded_plain['output_only'], \
            f'{step} must NOT be output-only in a plain locate'


def test_python_and_native_worker_dispatch_share_execution_contract(tmp_path, monkeypatch):
    from refactory_dsh.worker_node import execute

    seen = []
    def complete(self, prompt, *, system='', workdir=None):
        seen.append(prompt)
        (workdir / 'answer.txt').write_text('42')
        return 'Done.'
    monkeypatch.setattr(DshModelClient, 'complete', complete)
    client = DshModelClient(python_bin=Path(sys.executable).parent)

    class OneNode(nn.Mode):
        name = 'contract'
        def build(self):
            graph = nn.Graph()
            graph.add(nn.Node('build', 'agent', prompt_template='Read input.txt; write answer.txt.',
                              reads=('input.txt',), writes=('answer.txt',)))
            return graph

    contexts = []
    for backend in ('python', 'native'):
        workdir = tmp_path / backend
        workdir.mkdir()
        (workdir / 'input.txt').write_text('42')
        if backend == 'python':
            result = OneNode().run(workdir, model=client)
            assert result.exit_ok, result.error
        else:
            execute({'schema_version': 1, 'node_id': 'build', 'reads': ['input.txt'],
                     'writes': ['answer.txt'], 'produces': [], 'prompt': 'Read input.txt; write answer.txt.',
                     'model_context': client.context()}, workdir)
        receipt = agent_delivery.receipts_in(workdir)['build']
        context = receipt['execution']
        assert context['workdir'] == str(workdir)
        assert context['inputs'] == [str(workdir / 'input.txt')]
        assert context['outputs'] == [str(workdir / 'answer.txt')]
        assert seen[-1].endswith('Read input.txt; write answer.txt.')
        contexts.append({key: value for key, value in context.items() if key not in ('workdir', 'inputs', 'outputs')})
    assert contexts[0] == contexts[1]
