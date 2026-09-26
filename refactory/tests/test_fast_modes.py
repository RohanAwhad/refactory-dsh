from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from refactory_dsh import nn, registry
from refactory_dsh.fast import CreateFastMode, DesignFastMode, EditFastMode
from tests_support import FakeModel, good_files, render


QA_PASS = json.dumps({"status": "pass", "checks": [{"name": "requested behavior", "command": "source inspection",
                     "status": "pass", "evidence": "the implementation returns the requested literal"}],
                     "findings": [], "not_run": ["broad research and full reviewer campaign not requested"]})


@pytest.mark.parametrize("cls", [CreateFastMode, EditFastMode, DesignFastMode])
def test_fast_graphs_are_inspectable_and_have_one_focused_review(cls):
    mode = cls()
    assert nn.validate_state(mode.state_dict(), resolve_calls=True) == []
    agents = [node.id for node in mode.graph().nodes.values() if node.kind == "agent"]
    assert agents == ["emit", "focused_qa"]
    assert mode.values()["repair_attempts"] == 1
    assert not mode.is_scored()


def test_design_catches_known_defect_and_repairs_within_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    bad = "===FILE: answer.py===\nassert 1 + 1 == 3\n"
    good = "===FILE: answer.py===\nassert 1 + 1 == 2\n"
    client = FakeModel([bad, QA_PASS, good, QA_PASS])
    run = DesignFastMode().run(tmp_path / "run", model=client,
                               task={"name": "small", "brief": "assert two", "check": f"{sys.executable} answer.py"})
    assert run.exit_ok, run.error
    report = json.loads((tmp_path / "run/fast-report.json").read_text())
    assert report["repairs_used"] == report["repair_limit"] == 1
    assert report["runtime_check"]["status"] == "pass"
    assert report["research"] == "not requested/run"
    assert report["canonical_smoke"] == "not run"
    assert (tmp_path / "run/repair.txt").read_text().startswith("requested runtime check failed")


def test_design_exhausted_repair_is_unresolved_and_has_no_success(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 0
    mode.load_state_dict(state)
    run = mode.run(tmp_path / "run", model=FakeModel(["===FILE: answer.py===\nassert False\n", QA_PASS]),
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
    run = DesignFastMode().run(tmp_path / "run", model=FakeModel(["===FILE: feature.py===\nanswer = 2\n", QA_PASS]),
                               task={"name": "feature", "brief": "set answer to two", "source_project": str(source),
                                     "check": f'{sys.executable} -c "from feature import answer; assert answer == 2"'})
    assert run.exit_ok, run.error
    output = Path(run.facts["artifact"])
    assert (source / "feature.py").read_text() == "answer = 1\n"
    assert (output / "feature.py").read_text() == "answer = 2\n"
    assert (output / "keep.txt").read_text() == "preserve me\n"
    assert not (output / ".env").exists()


def test_create_fast_keeps_admission_and_canonical_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    name = "fast_counter"
    mode = CreateFastMode()
    state = mode.state_dict()
    state["knobs"]["single_shot"]["value"] = True
    mode.load_state_dict(state)
    run = mode.run(tmp_path / "run", model=FakeModel([render(good_files(name)), QA_PASS]),
                   task={"name": name, "brief": "a reusable counter"})
    assert run.exit_ok, run.error
    result = json.loads((tmp_path / "run/create.json").read_text())
    assert result["fast"]["structural_admission"] == "passed"
    assert result["fast"]["canonical_smoke"] == "passed"
    assert result["fast"]["runtime_check"]["status"] == "not_requested"
    assert registry.load(name, tmp_path).ids() == [f"{name}/counter"]


def test_qa_cannot_silently_repair_or_invalidate_checked_source(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    class MutatingQA(FakeModel):
        def complete(self, *args, **kwargs):
            if len(self.prompts) == 1:
                artifact = tmp_path / ".factory/artifacts/immutable_qa/answer.py"
                artifact.write_text("assert False\n")
            return super().complete(*args, **kwargs)
    mode = DesignFastMode()
    state = mode.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 0
    mode.load_state_dict(state)
    run = mode.run(tmp_path / "run", model=MutatingQA(["===FILE: answer.py===\nassert True\n", QA_PASS]),
                   task={"name": "immutable_qa", "brief": "assert true", "check": f"{sys.executable} answer.py"})
    assert not run.exit_ok and "focused QA changed the checked output" in run.error
    assert json.loads((tmp_path / "run/focused-check.json").read_text())["status"] == "pass"
    assert json.loads((tmp_path / "run/fast-report.json").read_text())["status"] == "unresolved"
    assert not (tmp_path / "run/design.json").exists()
