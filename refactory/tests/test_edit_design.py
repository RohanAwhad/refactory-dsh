"""Revising a mode from a brief, and designing software from a brief.

The edit issue: the loop searches and create starts from nothing, so there was no way
to say "change this mode to do that" in words. The design issue: the third
meta-mode, which produces software rather than a package of modes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from refactory_dsh import design as design_module
from refactory_dsh import edit as edit_module
from refactory_dsh import nn, registry, sandbox
from refactory_dsh.create import CreateConfig, CreateError
from refactory_dsh.design import DesignMode
from refactory_dsh.design_ops import ARTIFACTS
from refactory_dsh.edit import EditConfig, EditMode
from refactory_dsh.run import run_node
from tests_support import FakeModel, create_answers, good_files, render

NAME = "edited_pkg"
ARTIFACT = "designed_tool"


@pytest.fixture(autouse=True)
def _clean():
    for root in (registry.workspace_root(Path(".")) / NAME, ARTIFACTS / ARTIFACT):
        shutil.rmtree(root, ignore_errors=True)
    yield
    for root in (registry.workspace_root(Path(".")) / NAME, ARTIFACTS / ARTIFACT):
        shutil.rmtree(root, ignore_errors=True)


def _answers() -> list[str]:
    return [*create_answers(NAME, good_files(NAME))]


# ── editing a mode ─────────────────────────────────────────────────────────
def test_the_edit_mode_is_the_create_graph_with_a_different_intent():
    """A revision is not a second graph; it is the same one with a different brief."""
    from refactory_dsh.create import CreateMode

    assert isinstance(EditMode(), CreateMode)
    assert sorted(EditMode().graph().nodes) == sorted(CreateMode().graph().nodes)
    assert EditMode.intent != CreateMode.intent
    assert "smallest edit" in EditMode.intent


def test_prepare_hands_the_model_the_source_it_is_revising():
    workdir = Path(__file__).parent / "_edit_prepare"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        ctx = nn.RunContext(workdir)
        ctx.task = {"name": NAME, "brief": "keep it", "mode": "circle_packing/adaptive"}
        import refactory_dsh.create_ops as ops

        facts = ops.prepare(ctx)
        assert facts["revision_of"] == "circle_packing/adaptive"
        assert "revision of `circle_packing/adaptive`" in facts["reference"]
        assert "def greedy_pack" in facts["reference"], (
            "the source being revised must be in the prompt"
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_a_revision_needs_a_mode():
    with pytest.raises(CreateError, match="needs the mode to revise"):
        edit_module.edit(EditConfig(name=NAME, brief="anything"))


def test_a_revision_produces_a_package_and_reports_before_and_after():
    result = edit_module.edit(
        EditConfig(
            name=NAME, brief="count to a target", mode="circle_packing/adaptive",
            workdir=Path(".factory/edit-run"),
        ),
        client=FakeModel(_answers()),
    )
    assert result["revision_of"] == "circle_packing/adaptive"
    assert result["before_score"] is not None and result["after_score"] is not None
    assert result["regressed"] is False
    assert f"{NAME}/counter" in registry.render_catalogue(Path("."))


def test_the_revision_records_what_it_came_from():
    edit_module.edit(
        EditConfig(
            name=NAME, brief="count", mode="circle_packing/adaptive",
            workdir=Path(".factory/edit-run"),
        ),
        client=FakeModel(_answers()),
    )
    recorded = registry.provenance(NAME, Path("."))
    assert recorded["revision_of"] == "circle_packing/adaptive"
    assert recorded["brief"] == "count"
    assert "before_score" in recorded and "after_score" in recorded


def test_a_regression_is_refused_by_default(monkeypatch):
    scores = iter([5.0, 1.0])
    monkeypatch.setattr(edit_module, "_smoke", lambda identifier, project: {
        "exit_ok": True, "error": None, "score": next(scores),
    })
    with pytest.raises(CreateError, match="pass --allow-regression"):
        edit_module.edit(
            EditConfig(
                name=NAME, brief="count", mode="circle_packing/adaptive",
                workdir=Path(".factory/edit-run"),
            ),
            client=FakeModel(_answers()),
        )


def test_a_regression_can_be_intended(monkeypatch):
    scores = iter([5.0, 1.0])
    monkeypatch.setattr(edit_module, "_smoke", lambda identifier, project: {
        "exit_ok": True, "error": None, "score": next(scores),
    })
    result = edit_module.edit(
        EditConfig(
            name=NAME, brief="count", mode="circle_packing/adaptive",
            allow_regression=True, workdir=Path(".factory/edit-run"),
        ),
        client=FakeModel(_answers()),
    )
    assert result["regressed"] is True


def test_a_revision_of_a_mode_that_does_not_run_is_refused(monkeypatch):
    monkeypatch.setattr(edit_module, "_smoke", lambda identifier, project: {
        "exit_ok": False, "error": "it never ran", "score": None,
    })
    with pytest.raises(CreateError, match="its own smoke run failed"):
        edit_module.edit(
            EditConfig(name=NAME, brief="count", mode="circle_packing/adaptive"),
            client=FakeModel(_answers()),
        )


# ── designing software ─────────────────────────────────────────────────────
def test_the_design_graph_is_a_different_shape_from_a_package_one():
    graph = DesignMode().graph()
    assert sorted(graph.nodes) == [
        "check", "design", "emit", "materialise", "prepare", "report"
    ]
    assert graph.nodes["check"].evaluator.endswith("check_artifact")
    assert "register" not in graph.nodes, "an artifact is not a package"
    assert "smoke" not in graph.nodes


def test_the_design_state_validates():
    mode = DesignMode()
    assert nn.validate_state(
        mode.state_dict(), invariants=mode.invariants(), resolve_calls=True
    ) == []


def test_designing_needs_a_check():
    """Without a check the artifact is unverified, and unverified is not a deliverable."""
    mode = DesignMode()
    result = mode.run(Path(".factory/design-nocheck"), task={"name": ARTIFACT, "brief": "x"})
    assert result.exit_ok is False
    assert "needs a check" in (result.error or "")


def test_the_artifact_lands_beside_the_workspace_not_in_it():
    ctx = nn.RunContext(Path(".factory/design-prep"))
    ctx.task = {"name": ARTIFACT, "brief": "a tool", "check": "python -c 'pass'"}
    from refactory_dsh import design_ops

    design_ops.prepare_design(ctx)
    ctx.facts["name"] = ARTIFACT
    ctx.write_text("emit.txt", render({"tool.py": "print('hi')\n"}))
    facts = design_ops.materialise_artifact(ctx)
    assert Path(facts["artifact"]).is_relative_to(ARTIFACTS)
    assert not Path(facts["artifact"]).is_relative_to(registry.workspace_root(Path(".")))


def test_the_check_gate_passes_an_artifact_that_runs():
    workdir = Path(".factory/design-check")
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    ctx = nn.RunContext(Path(".factory/design-check-ctx"))
    ctx.facts.update({"artifact": str(workdir), "check": "python -c \"print(1)\""})
    import refactory_dsh.design_ops as ops

    assert ops.check_artifact(ctx) == "proceed"
    assert "ok" in ctx.read_text("check.txt")


def test_the_check_gate_refuses_an_artifact_that_does_not_run():
    workdir = Path(".factory/design-check")
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    ctx = nn.RunContext(Path(".factory/design-check-ctx2"))
    ctx.facts.update({"artifact": str(workdir), "check": "python -c \"raise SystemExit(3)\""})
    import refactory_dsh.design_ops as ops

    assert ops.check_artifact(ctx) == "reloop"
    assert "exit 3" in ctx.read_text("check.txt"), (
        "the refusal must say what the check said, or the retry is guessing"
    )


def test_the_check_runs_without_the_real_credentials(tmp_path):
    """The check runs arbitrary code a brief named, so it is sandboxed like the rest."""
    workdir = tmp_path / "artifact"
    result = sandbox.run_command(
        "python -c \"import os,sys; sys.exit(0 if 'DEEPSEEK_API_KEY' not in os.environ else 9)\"",
        workdir=workdir,
        env={**__import__("os").environ, "DEEPSEEK_API_KEY": "sk-hidden"},
    )
    assert result.ok, result.stderr


def test_a_design_run_produces_a_checked_artifact():
    """End to end, with a fake model: design, emit, and the check passing."""
    mode = DesignMode()
    state = mode.state_dict()
    mode.load_state_dict(state)
    workdir = Path(".factory/design-run")
    shutil.rmtree(workdir, ignore_errors=True)
    model = FakeModel(["a design", render({"tool.py": "print('built')\n"})])
    result = mode.run(
        workdir,
        model=model,
        task={"name": ARTIFACT, "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok, result.error
    payload = json.loads((workdir / "design.json").read_text())
    assert payload["name"] == ARTIFACT
    assert payload["checked"] is True
    assert (ARTIFACTS / ARTIFACT / "tool.py").exists()


def test_a_design_that_never_passes_its_check_fails_loudly():
    mode = DesignMode()
    workdir = Path(".factory/design-fail")
    shutil.rmtree(workdir, ignore_errors=True)
    model = FakeModel(["a design"] + [render({"tool.py": "raise SystemExit(1)\n"})] * 3)
    result = mode.run(
        workdir,
        model=model,
        task={"name": ARTIFACT, "brief": "a tool", "check": "python tool.py"},
    )
    assert result.exit_ok is False
    assert "does not pass its check" in (result.error or "")
