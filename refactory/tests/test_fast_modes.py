from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from refactory_dsh import nn, registry
from refactory_dsh.fast import CreateFastMode, DesignFastMode, EditFastMode
from tests_support import FakeModel, good_files, render


PLAN = "Change only the requested file, preserve its public behavior, and review the required check result."
QA_PASS = json.dumps({"status": "pass", "checks": [{"name": "requested behavior", "command": "source inspection",
                     "status": "pass", "evidence": "the implementation returns the requested literal"}],
                     "findings": [], "not_run": ["broad research and full reviewer campaign not requested"]})


@pytest.mark.parametrize("cls", [CreateFastMode, EditFastMode, DesignFastMode])
def test_fast_graphs_are_inspectable_and_have_one_focused_review(cls):
    mode = cls()
    assert nn.validate_state(mode.state_dict(), resolve_calls=True) == []
    agents = [node.id for node in mode.graph().nodes.values() if node.kind == "agent"]
    expected = {"emit", "focused_qa"} if cls is EditFastMode else {"strategy", "emit", "focused_qa"}
    assert set(agents) == expected
    assert not any(node.kind in ("fork", "join") for node in mode.graph().nodes.values())
    if cls is EditFastMode:
        assert mode.graph().out_edges("survey")[0].target == "emit"
        assert "implementation-plan.txt" not in mode.graph().nodes["emit"].reads
        assert "{{implementation_plan}}" not in mode.graph().nodes["emit"].prompt_template
    else:
        assert mode.graph().out_edges("strategy")[0].target == "emit"
    assert all(edge.target != "strategy" for edge in mode.graph().edges if edge.condition == "reloop")
    assert mode.values()["repair_attempts"] == 1
    assert not mode.is_scored()


def test_design_catches_known_defect_and_repairs_within_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    bad = "===FILE: answer.py===\nassert 1 + 1 == 3\n"
    good = "===FILE: answer.py===\nassert 1 + 1 == 2\n"
    client = FakeModel([PLAN, bad, QA_PASS, good, QA_PASS])
    run = DesignFastMode().run(tmp_path / "run", model=client,
                               task={"name": "small", "brief": "assert two", "check": f"{sys.executable} answer.py"})
    assert run.exit_ok, run.error
    report = json.loads((tmp_path / "run/fast-report.json").read_text())
    assert report["repairs_used"] == report["repair_limit"] == 1
    assert report["runtime_check"]["status"] == "pass"
    assert report["research"] == "not requested/run"
    assert report["canonical_smoke"] == "not run"
    assert (tmp_path / "run/repair.txt").read_text().startswith("requested runtime check failed")
    assert len(client.prompts) == 5, "repairs repeat coder/QA, never the strategist"
    assert (tmp_path / "run/implementation-plan.txt").read_text() == PLAN
    assert PLAN in client.prompts[1] and PLAN in client.prompts[3]


def test_design_exhausted_repair_is_unresolved_and_has_no_success(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 0
    mode.load_state_dict(state)
    run = mode.run(tmp_path / "run", model=FakeModel([PLAN, "===FILE: answer.py===\nassert False\n", QA_PASS]),
                   task={"name": "unresolved", "brief": "assert true", "check": f"{sys.executable} answer.py"})
    assert not run.exit_ok
    assert "after 0 repair(s)" in run.error
    assert not (tmp_path / "run/design.json").exists()
    assert json.loads((tmp_path / "run/fast-report.json").read_text())["status"] == "unresolved"


def test_design_project_change_preserves_source_and_unmodified_files(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path / "project"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "feature.py").write_text("answer = 1\n")
    (source / "keep.txt").write_text("preserve me\n")
    (source / ".env").write_text("PRIVATE=never-copy\n")
    client = FakeModel([PLAN, "===FILE: feature.py===\nanswer = 2\n", QA_PASS])
    run = DesignFastMode().run(tmp_path / "run", model=client,
                               task={"name": "feature", "brief": "set answer to two", "source_project": str(source),
                                     "check": f'{sys.executable} -c "from feature import answer; assert answer == 2"'})
    assert run.exit_ok, run.error
    output = Path(run.facts["artifact"])
    assert (source / "feature.py").read_text() == "answer = 1\n"
    assert (output / "feature.py").read_text() == "answer = 2\n"
    assert (output / "keep.txt").read_text() == "preserve me\n"
    assert not (output / ".env").exists()
    assert run.facts["source_files"] == '["feature.py", "keep.txt"]'
    assert run.facts["review_files"] == '["feature.py"]'
    assert 'Emitted or changed files to review: ["feature.py"]' in client.prompts[2]
    assert json.loads((tmp_path / "run/focused-source.json").read_text())["review_files"] == ["feature.py"]


def test_create_fast_keeps_admission_and_canonical_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    name = "fast_counter"
    mode = CreateFastMode()
    state = mode.state_dict()
    state["knobs"]["single_shot"]["value"] = True
    mode.load_state_dict(state)
    run = mode.run(tmp_path / "run", model=FakeModel([PLAN, render(good_files(name)), QA_PASS]),
                   task={"name": name, "brief": "a reusable counter"})
    assert run.exit_ok, run.error
    result = json.loads((tmp_path / "run/create.json").read_text())
    assert result["fast"]["structural_admission"] == "passed"
    assert result["fast"]["canonical_smoke"] == "passed"
    assert result["fast"]["runtime_check"]["status"] == "not_requested"
    assert registry.load(name, tmp_path).ids() == [f"{name}/counter"]
    assert json.loads((tmp_path / "run/survey.json").read_text()) == {"packages": []}


def test_qa_cannot_silently_repair_or_invalidate_checked_source(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    class MutatingQA(FakeModel):
        def complete(self, *args, workdir=None, **kwargs):
            if len(self.prompts) == 2:
                artifact = tmp_path / ".factory/artifacts/immutable_qa/answer.py"
                artifact.write_text("assert False\n")
            return super().complete(*args, workdir=workdir, **kwargs)
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 0
    mode.load_state_dict(state)
    run = mode.run(tmp_path / "run", model=MutatingQA([PLAN, "===FILE: answer.py===\nassert True\n", QA_PASS]),
                   task={"name": "immutable_qa", "brief": "assert true", "check": f"{sys.executable} answer.py"})
    assert not run.exit_ok and "focused QA changed the checked output" in run.error
    assert json.loads((tmp_path / "run/focused-check.json").read_text())["status"] == "pass"
    assert json.loads((tmp_path / "run/fast-report.json").read_text())["status"] == "unresolved"
    assert not (tmp_path / "run/design.json").exists()


@pytest.mark.parametrize("needs_repair,qa_tokens", [(True, 25), (False, 25), (False, 40)])
def test_design_validation_reserve_cannot_fund_repair(tmp_path, monkeypatch, needs_repair, qa_tokens):
    from refactory_dsh.budgets import RunBudget

    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    good = "===FILE: answer.py===\nassert True\n"
    initial = "===FILE: answer.py===\nassert False\n" if needs_repair else good

    class MeteredModel(FakeModel):
        def with_budget(self, budget):
            self.budget = budget
            return self

        def complete(self, prompt, *, system="", workdir=None):
            index = len(self.prompts)
            self.budget.record_usage(f"request-{index}", [5, 55, qa_tokens, 0, 0][index])
            return super().complete(prompt, system=system, workdir=workdir)

    client = MeteredModel([PLAN, initial, QA_PASS, good, QA_PASS])
    workdir = tmp_path / "run"
    result = DesignFastMode().run(
        workdir, model=client,
        task={"name": "reserved", "brief": "assert true", "check": f"{sys.executable} answer.py"},
        resource_limits={"max_tokens": 100, "validation_reserve_tokens": 20},
    )
    assert len(client.prompts) == 3, "implementation must not restart using validation reserve"
    state = RunBudget.open(workdir).snapshot()
    assert state["known_tokens"] == 60 + qa_tokens
    if needs_repair:
        assert not result.exit_ok and "reserve retained" in result.error
        assert state["phase"] == "work"
        assert not (workdir / "design.json").exists()
    elif qa_tokens == 40:
        assert not result.exit_ok and "token budget exhausted" in result.error
        assert not (workdir / "design.json").exists()
    else:
        assert result.exit_ok, result.error
        assert json.loads((workdir / "fast-report.json").read_text())["status"] == "completed"
        assert state["phase"] == "final_validation"


def test_fast_survey_keeps_selected_reference_without_discovering_other_packages(tmp_path, monkeypatch):
    from refactory_dsh import fast_ops
    from refactory_dsh.nn.executor import RunContext

    def forbidden(*args, **kwargs):
        raise AssertionError("fast authoring must not discover unrelated package sources")

    monkeypatch.setattr(registry, "discover_names", forbidden)
    ctx = RunContext(tmp_path)
    ctx.facts.update(revision_of="counting_pack/fixed_step", extends="other_pack")
    result = fast_ops.survey_package(ctx)
    assert result["existing"] == ["counting_pack"]
    assert "counting_pack/fixed_step" in result["survey"]
    assert ctx.read_json("survey.json")["packages"] == [
        {"package": "counting_pack", "reference": "counting_pack/fixed_step"}]
