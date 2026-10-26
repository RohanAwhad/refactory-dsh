from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactory_dsh import interactive, lowering
from refactory_dsh.budgets import RunBudget
from refactory_dsh.fast import CreateFastMode, DesignFastMode


PROJECT = Path(__file__).resolve().parents[2]


#: The graph runtime starts from the `graph` profile: the test's own, linked to this checkout (dm-267).
pytestmark = pytest.mark.usefixtures("graph_profile_home")


def test_fresh_invocation_archives_old_budget_and_workers(tmp_path):
    run = tmp_path / "run"
    interactive.prepare("review_pack/review", workdir=run, project=PROJECT, resource_limits={"max_elapsed_s": 60})
    first = RunBudget.open(run)
    first.record_usage("previous", 17)
    first.pause()
    (run / ".refactory/resume-request.json").write_text("{}")
    (run / ".refactory/run-config.json").write_text("{}")
    (run / ".refactory/workers").mkdir()
    (run / ".refactory/workers/old.json").write_text('{"state":"completed"}')
    interactive.prepare("review_pack/review", workdir=run, project=PROJECT, resource_limits={"max_elapsed_s": 90})
    current = RunBudget.open(run).snapshot()
    assert current["limits"]["max_elapsed_s"] == 90
    assert current["started_at"] is not None and current["tokens"] is None
    assert current["workers"] == {}
    assert not (run / ".refactory/workers/old.json").exists()
    invocation = json.loads((run / ".refactory/invocation.json").read_text())
    archive = run / invocation["previous"] / "artifacts"
    assert json.loads((archive / ".refactory/resource-usage.json").read_text())["tokens"] == 17
    assert (archive / ".refactory/workers/old.json").exists()
    assert (archive / ".refactory/resume-request.json").exists()
    assert (archive / ".refactory/run-config.json").exists()
    assert not (run / ".refactory/resume-request.json").exists()


def test_final_validation_role_survives_lowering_and_worker_bridge(tmp_path):
    for cls, node in ((CreateFastMode, "smoke"), (DesignFastMode, "focused_check")):
        workflow = lowering.to_workflow(cls().state_dict())
        assert workflow.to_dict()["nodes"][node]["resource_role"] == "final_validation"
        assert lowering.node_declarations(cls().state_dict())[node]["resource_role"] == "final_validation"
    interactive.prepare("default_pack/design_fast", workdir=tmp_path, project=PROJECT,
                        resource_limits={"max_elapsed_s": 60, "validation_reserve_s": 10})
    graph = json.loads((tmp_path / "graphs/default_pack-design_fast.graph.json").read_text())
    assert graph["nodes"]["focused_check"]["resource_role"] == "final_validation"
    assert graph["nodes"]["emit"]["resource_role"] == "work"
    assert graph["nodes"]["focused_qa"]["resource_role"] == "final_validation"
    assert graph["nodes"]["focused_qa"]["_type"] == "AgentNode"
    assert graph["nodes"]["focused_qa"]["dispatch_command"]


def test_resource_phase_is_optional_serializable_and_independent_of_routing():
    from refactory_dsh.nn import Graph, Node
    from refactory_dsh.ir import Workflow

    legacy = Node("legacy", "op", call="refactory_dsh.fast_ops:report_artifact")
    assert "resource_role" not in legacy.to_state()
    assert Node.from_state(legacy.to_state()).to_state() == legacy.to_state()
    assert legacy.resource_phase is None
    assert Node("validation", "op", call=legacy.call, role="validation").resource_phase == "final_validation"
    graph = Graph()
    graph.add(legacy)
    graph.add(Node("review", "agent", prompt_template="review", role="code_reviewer", resource_role="final_validation"))
    graph.add(Node("repair", "agent", prompt_template="repair", role="builder", resource_role="work"))
    graph.connect("legacy", "review")
    graph.connect("review", "repair")
    state = {"graph": graph.to_state()}
    payload = lowering.to_workflow(state).to_dict()
    assert "resource_role" not in payload["nodes"]["legacy"]
    assert payload["nodes"]["review"]["resource_role"] == "final_validation"
    assert payload["nodes"]["review"]["role"] == "code_reviewer"
    assert payload["nodes"]["repair"]["resource_role"] == "work"
    assert Workflow.from_dict(payload).to_dict() == payload
    assert Graph.from_state(graph.to_state()).to_state() == graph.to_state()


def test_unknown_resource_phase_is_refused():
    from refactory_dsh.nn import Node

    with pytest.raises(ValueError, match="unknown resource_role"):
        Node("invalid", "agent", prompt_template="invalid", resource_role="free")


def test_graph_parent_timeout_keeps_final_reserve_and_pauses_after_failure(tmp_path, monkeypatch):
    from refactory_dsh import workers

    seen = {}
    def run_graph_command(*args, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("owned runtime failed")
    monkeypatch.setattr(workers, "run_graph_command", run_graph_command)
    with pytest.raises(RuntimeError, match="owned runtime failed"):
        interactive.run("review_pack/review", workdir=tmp_path, project=PROJECT, headless=True,
                        resource_limits={"max_elapsed_s": 60, "validation_reserve_s": 20})
    assert 55 < seen["timeout"] <= 60
    assert RunBudget.open(tmp_path).snapshot()["started_at"] is None


def test_resume_restores_saved_knobs_project_route_and_budget(tmp_path, monkeypatch):
    from refactory_dsh import workers

    budget = RunBudget.create(tmp_path, {"max_elapsed_s": 90})
    budget.record_usage("old-worker", 12)
    budget.pause()
    context = {"provider": "saved-provider", "model": "saved-model", "worker_backend": "dsh", "budget_ledger": str(budget.path)}
    (tmp_path / ".refactory/run-config.json").write_text(json.dumps({
        "mode": "counting_pack/fixed_step", "project": str(PROJECT), "knobs": ["target=7"],
        "model_context": context, "resource_limits": {"max_elapsed_s": 90},
    }))
    def prepare(*args, **kwargs):
        assert kwargs["resume"] and kwargs["resource_limits"]["max_elapsed_s"] == 90
        budget.resume()
        return "counting_pack-fixed_step", tmp_path / "graphs"
    seen = {}
    monkeypatch.setattr(interactive, "prepare", prepare)
    monkeypatch.setattr(interactive, "command", lambda *a, **kw: seen.update(command=kw) or ["dsh"])
    monkeypatch.setattr(workers, "run_graph_command", lambda *a, **kw: seen.update(runtime=kw) or SimpleNamespace(returncode=0))
    assert interactive.run("counting_pack/fixed_step", workdir=tmp_path, resume=True) == 0
    assert seen["command"]["knobs"] == ["target=7"]
    assert seen["command"]["provider"] == "saved-provider"
    assert seen["runtime"]["context"] == {**context, "permission_mode": "default"}
    assert json.loads((tmp_path / ".refactory/run-config.json").read_text())["model_context"] == context
    assert RunBudget.open(tmp_path).snapshot()["tokens"] == 12
    assert RunBudget.open(tmp_path).snapshot()["started_at"] is None


def test_resume_refuses_source_override_before_mutation(tmp_path):
    with pytest.raises(ValueError, match="resume retains saved inputs"):
        interactive.run("counting_pack/fixed_step", workdir=tmp_path, resume=True, source="changed.jsonl")
    assert not (tmp_path / "items.json").exists()
