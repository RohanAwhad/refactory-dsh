"""The lowered native path must admit initial fast workers before any model call."""
import json

import pytest

from refactory_dsh import fast_ops, lowering, workers
from refactory_dsh.fast import CreateFastMode, EditFastMode, DesignFastMode
from refactory_dsh.model import DshModelClient
from refactory_dsh.nn.executor import RunContext
from refactory_dsh.worker_node import execute
from tests_support import FakeModel


@pytest.mark.parametrize('mode_class,node_id', [
    (CreateFastMode, 'strategy'), (CreateFastMode, 'emit'),
    (EditFastMode, 'emit'), (DesignFastMode, 'strategy'), (DesignFastMode, 'emit')])
def test_initial_fast_inputs_reach_lowered_worker_dispatch(tmp_path, monkeypatch, mode_class, node_id):
    monkeypatch.setenv('REFACTORY_PROJECT', str(tmp_path))
    mode = mode_class()
    ctx = RunContext(tmp_path / 'run', model=FakeModel([]), knobs={**mode.values(), 'single_shot': True},
                     task={'name': 'admission_control', 'brief': 'a tiny reusable greeting', 'check': 'true'})
    prepare = fast_ops.prepare_artifact if mode_class is DesignFastMode else fast_ops.prepare_package
    ctx.facts.update(prepare(ctx))
    if mode_class is not DesignFastMode:
        ctx.facts.update(fast_ops.survey_package(ctx))
    if node_id == 'emit' and mode_class is not EditFastMode:
        ctx.write_text('implementation-plan.txt', 'Implement the brief, then review the requested check.')
        ctx.facts['implementation_plan'] = ctx.read_text('implementation-plan.txt')
    (ctx.workdir / 'facts.json').write_text(json.dumps(ctx.facts))
    workers.lower_workflow(lowering.to_workflow(mode.state_dict()), ctx.workdir, DshModelClient().context())
    spec = next(json.loads(path.read_text()) for path in (ctx.workdir / '.refactory/worker-specs').glob('*.json')
                if json.loads(path.read_text())['node_id'] == node_id)
    assert all((ctx.workdir / name).exists() for name in spec['reads'])
    assert 'no repair requested' in (ctx.workdir / 'repair.txt').read_text()
    def dispatch(*args, **kwargs):
        raise RuntimeError('admission passed; deterministic stop before provider')
    monkeypatch.setattr(DshModelClient, 'complete', dispatch)
    with pytest.raises(RuntimeError, match='admission passed'):
        execute(spec, ctx.workdir)
