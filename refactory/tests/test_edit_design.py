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
from tests_support import (
    RESEARCH_ANSWER,
    REVIEW_ANSWERS,
    FakeModel,
    create_answers,
    good_files,
    render,
)

NAME = "edited_pkg"
ARTIFACT = "designed_tool"


@pytest.fixture(autouse=True)
def _clean():
    for root in (registry.workspace_root(Path(".")) / NAME, ARTIFACTS / ARTIFACT):
        shutil.rmtree(root, ignore_errors=True)
    yield
    for root in (registry.workspace_root(Path(".")) / NAME, ARTIFACTS / ARTIFACT):
        shutil.rmtree(root, ignore_errors=True)


def _edit_config(**overrides) -> EditConfig:
    """An edit config with the person's strategy approval recorded.

    `EditMode` is the create-v2 graph, so it carries the same `user` gate: a
    headless run refuses it, and a test that stands in for the person says so.
    """
    return EditConfig(verdicts={"gate_strategy": "proceed"}, **overrides)


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
        edit_module.edit(_edit_config(name=NAME, brief="anything"))


def test_a_revision_produces_a_package_and_reports_before_and_after():
    result = edit_module.edit(
        _edit_config(
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
        _edit_config(
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
            _edit_config(
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
        _edit_config(
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
            _edit_config(name=NAME, brief="count", mode="circle_packing/adaptive"),
            client=FakeModel(_answers()),
        )


# ── designing software ─────────────────────────────────────────────────────
def _design_answers(emit: list[str]) -> list[str]:
    """Every answer design-v2's agents need, in the order the graph asks."""
    return [
        # The research answer is the plan array its gate checks, for the same reason
        # create's fixture is: a prose answer is the failure the gate catches.
        RESEARCH_ANSWER,
        "perspectives: the simplest thing that passes the check",
        "one plan: the files, and how the check exercises them",
        "the design document",
        *emit,
        *REVIEW_ANSWERS,
    ]


def test_the_design_graph_is_a_different_shape_from_a_package_one():
    graph = DesignMode().graph()
    assert sorted(graph.nodes) == [
        "check", "code_reviewer", "design_doc", "emit", "fork_qa", "gate_overwatch",
        "gate_qa", "gate_research", "gate_strategy", "health_checker", "join_qa",
        "materialise", "overwatch", "prepare", "qa_director", "record_findings", "report",
        "research_director", "strategy_director", "synthesize_qa", "synthesize_strategy",
    ], "the graph is design-v2's phases, not v1's"
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
    model = FakeModel(_design_answers([render({"tool.py": "print('built')\n"})]))
    result = mode.run(
        workdir,
        model=model,
        task={"name": ARTIFACT, "brief": "a tool", "check": "python tool.py"},
        verdicts={"gate_strategy": "proceed"},
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
    model = FakeModel(_design_answers([render({"tool.py": "raise SystemExit(1)\n"})] * 4))
    result = mode.run(
        workdir,
        model=model,
        task={"name": ARTIFACT, "brief": "a tool", "check": "python tool.py"},
        verdicts={"gate_strategy": "proceed"},
    )
    assert result.exit_ok is False
    assert "does not pass its check" in (result.error or "")


def test_a_revision_records_the_state_it_came_from(tmp_path):
    """Provenance is the state, not only the name of a class that may have moved.

    A mode is not fixed: the outer loop edits it. "Revised from
    `circle_packing/adaptive`" therefore names a class that may have changed since,
    and a revision recorded that way cannot be reproduced or diffed against what it
    actually started from.
    """
    result = edit_module.edit(
        _edit_config(name=NAME, mode="circle_packing/adaptive",
                   brief="restart on a stall", workdir=tmp_path / "run"),
        client=FakeModel(_answers()),
    )
    assert result["revision_of"] == "circle_packing/adaptive"
    assert result["revision_of_digest"], "no digest of the state that was revised"

    recorded = json.loads(
        (registry.workspace_root(Path(".")) / NAME / registry.ORIGIN).read_text()
    )
    state = recorded["revision_of_state"]
    assert sorted(state) == sorted(
        registry.resolve(Path("."), "circle_packing/adaptive")[1]().state_dict()
    )
    assert recorded["revision_of_digest"] == result["revision_of_digest"]

    # The digest identifies the state it was recorded from.
    from refactory_dsh.optim.base import candidate_id

    assert recorded["revision_of_digest"] == candidate_id(state)


# ── the comparison, on the path a person actually runs ─────────────────────
#
# `refactory ceo "…" --mode edit` lowers `default_pack/edit` and runs its nodes on
# DSH; it does not call `edit.edit()`. The comparison therefore belongs to the
# `smoke` node, which is the step that has just scored the new mode, and these hold
# it there. Before this existed the CLI recorded `revision_of` and nothing else: no
# before score, no after score, no refusal — the documented behaviour of `--mode
# edit`, implemented only in a function the CLI never reached.

#: The declarations lowering writes for the smoke node of an edit run.
_SMOKE_DECLARATIONS = {
    "smoke": {
        "reads": [],
        "writes": ["smoke.json", "revision.json"],
        "produces": ["smoke_score", "smoke_ok", "before_score", "after_score",
                     "regressed", "revision_note"],
    },
}


def _smoke_run(tmp_path, monkeypatch, *, scores, revision="counting_pack/fixed_step",
               package_path=None, allow=False):
    """A run directory the smoke node can be invoked in, as the runtime invokes it.

    `counting_pack` is a shipped, deterministic package, so the node's own smoke of
    the new mode is real; only the two scores the comparison reads are stubbed, so
    what is under test is the comparison rather than a search for a mode whose
    numbers happen to differ.
    """
    from refactory_dsh import lowering, sandbox
    from refactory_dsh.sandbox import SandboxResult

    workdir = tmp_path / "run"
    (workdir / ".refactory").mkdir(parents=True)
    (workdir / ".refactory" / "declarations.json").write_text(
        json.dumps(_SMOKE_DECLARATIONS)
    )
    # A shipped, deterministic package, so the node under test does not depend on
    # anything this machine happened to have created.
    facts = {"name": "circle_packing", "revision_of": revision}
    if package_path is not None:
        facts["package_path"] = str(package_path)
    (workdir / "facts.json").write_text(json.dumps(facts))

    def stub(identifier, workdir_, *, project=None, model=False, **kwargs):
        return SandboxResult(ok=True, payload={"score": scores[identifier], "iterations": 2})

    monkeypatch.setattr(sandbox, "smoke", stub)
    if allow:
        monkeypatch.setenv(lowering.KNOBS_ENV, json.dumps({"allow_regression": True}))
    return workdir


def test_the_smoke_node_refuses_a_revision_that_scores_lower(tmp_path, monkeypatch):
    """A regression is refused on the path the CLI takes, and the run says both scores."""
    from refactory_dsh.run import run_node

    workdir = _smoke_run(tmp_path, monkeypatch, scores={
        "circle_packing/adaptive": 0.4,
        "counting_pack/fixed_step": 0.9,
    })
    with pytest.raises(ValueError, match="scores 0.4 where counting_pack/fixed_step scores 0.9"):
        run_node("refactory_dsh.create_ops:smoke", workdir=workdir, node_id="smoke")
    # The evidence outlives the refusal: a person reading the run can see both numbers.
    recorded = json.loads((workdir / "revision.json").read_text())
    assert recorded["before_score"] == 0.9
    assert recorded["after_score"] == 0.4
    assert recorded["regressed"] is True


def test_the_smoke_node_accepts_the_same_revision_when_the_trade_is_intended(
    tmp_path, monkeypatch
):
    """`allow_regression=1` is the person saying the score is not the whole story."""
    from refactory_dsh.run import run_node

    package = tmp_path / "counting_pack"
    package.mkdir()
    workdir = _smoke_run(
        tmp_path, monkeypatch, allow=True,
        package_path=package,
        scores={"circle_packing/adaptive": 0.4, "counting_pack/fixed_step": 0.9},
    )
    produced = run_node("refactory_dsh.create_ops:smoke", workdir=workdir, node_id="smoke")
    assert produced["regressed"] is True
    assert (produced["before_score"], produced["after_score"]) == (0.9, 0.4)
    recorded = json.loads((package / ".origin.json").read_text())
    assert recorded["revision_of"] == "counting_pack/fixed_step"
    assert recorded["before_score"] == 0.9 and recorded["after_score"] == 0.4
    assert recorded["revision_of_digest"], "the provenance names no state"


def test_an_improvement_is_not_a_regression(tmp_path, monkeypatch):
    from refactory_dsh.run import run_node

    workdir = _smoke_run(tmp_path, monkeypatch, scores={
        "circle_packing/adaptive": 1.0,
        "counting_pack/fixed_step": 0.9,
    })
    produced = run_node("refactory_dsh.create_ops:smoke", workdir=workdir, node_id="smoke")
    assert produced["regressed"] is False
    assert produced["revision_note"] == "compared with counting_pack/fixed_step: 0.9 → 1.0"


def test_a_revision_of_an_unscored_mode_is_recorded_as_uncompared(tmp_path, monkeypatch):
    """There is no number to compare against, and saying so beats inventing one."""
    from refactory_dsh.run import run_node

    workdir = _smoke_run(
        tmp_path, monkeypatch, revision="default_pack/design",
        scores={"circle_packing/adaptive": 0.4},
    )
    produced = run_node("refactory_dsh.create_ops:smoke", workdir=workdir, node_id="smoke")
    assert produced["before_score"] is None
    assert produced["regressed"] is False
    assert "unscored" in produced["revision_note"]


def test_a_revision_of_a_mode_that_calls_a_model_is_not_refused_on_one_sample(
    tmp_path, monkeypatch
):
    """One smoke score of a model call is a sample, not a measurement.

    Refusing a revision on one sample would refuse the wrong thing, so a revision of a
    mode that calls a model is recorded as not compared rather than as passing.
    """
    from refactory_dsh import registry
    from refactory_dsh.run import run_node

    source = registry.workspace_root(Path(".")) / "agent_probe"
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir(parents=True)
    (source / "__init__.py").write_text(
        "from refactory_dsh.nn import Package\n"
        "from .mode import Drafting\n\n"
        "PACKAGE = Package(name='agent_probe', description='drafts with an agent',\n"
        "                  modes=(Drafting,), ops=('refactory_packages.agent_probe.ops',))\n"
    )
    (source / "ops.py").write_text(
        "def finish(ctx):\n"
        "    ctx.write_json('out.json', {'done': True})\n"
        "    return {'done': True}\n"
    )
    (source / "mode.py").write_text(
        "from refactory_dsh import nn\n\n"
        "OPS = 'refactory_packages.agent_probe.ops'\n\n\n"
        "class Drafting(nn.Mode):\n"
        "    name = 'draft'\n"
        "    description = 'one agent writes a line'\n\n"
        "    def build(self):\n"
        "        graph = nn.Graph()\n"
        "        graph.add(nn.Node('draft', 'agent', prompt_template='write a line',\n"
        "                          writes=('line.txt',), produces=('line',)))\n"
        "        graph.add(nn.Node('finish', 'op', call=f'{OPS}:finish',\n"
        "                          reads=('line.txt',), writes=('out.json',),\n"
        "                          produces=('done',)))\n"
        "        graph.connect('draft', 'finish')\n"
        "        return graph\n\n"
        "    def score(self, run):\n"
        "        return 1.0 if run.facts.get('done') else 0.0\n"
    )
    try:
        workdir = _smoke_run(
            tmp_path, monkeypatch, revision="agent_probe/draft",
            scores={"circle_packing/adaptive": 0.4},
        )
        produced = run_node("refactory_dsh.create_ops:smoke", workdir=workdir, node_id="smoke")
        assert produced["before_score"] is None
        assert "calls a model" in produced["revision_note"]
    finally:
        shutil.rmtree(source, ignore_errors=True)


def test_the_run_summary_reports_what_a_revision_recorded(tmp_path):
    """`ceo`/`run` end with what the run recorded, not only DSH's final word."""
    from refactory_dsh.interactive import run_summary

    workdir = tmp_path / "run"
    workdir.mkdir()
    (workdir / "facts.json").write_text(json.dumps({
        "name": "counting_revised",
        "revision_of": "counting_pack/fixed_step",
        "before_score": 0.5,
        "after_score": 1.0,
        "regressed": False,
        "package_path": "/tmp/nowhere",
    }))
    line = run_summary(workdir)
    assert line.startswith("refactory: what the run recorded")
    for wanted in ("revision_of", "before_score", "after_score", "regressed"):
        assert wanted in line


def test_the_run_summary_says_nothing_when_there_is_nothing_to_say(tmp_path):
    from refactory_dsh.interactive import run_summary

    workdir = tmp_path / "run"
    workdir.mkdir()
    assert run_summary(workdir) == ""
    (workdir / "facts.json").write_text("{ not json")
    assert run_summary(workdir) == ""


def test_a_comparison_with_no_package_path_writes_no_provenance(tmp_path, monkeypatch):
    """The provenance belongs to the package; a node with no package writes none.

    `Path("") / ".origin.json"` is `./.origin.json`, so an unguarded write put a
    revision's provenance into whatever directory the node was standing in — which for
    a hand-invoked node is the checkout. Found exactly that way: a run of the suite
    left an untracked `.origin.json` at the repository root.
    """
    from refactory_dsh.run import run_node

    stray = Path(".origin.json")
    stray.unlink(missing_ok=True)
    workdir = _smoke_run(
        tmp_path, monkeypatch, revision="default_pack/design",
        scores={"circle_packing/adaptive": 0.4},
    )
    run_node("refactory_dsh.create_ops:smoke", workdir=workdir, node_id="smoke")
    assert not stray.exists(), "the comparison wrote provenance into the working directory"
