"""create-mode: a brief in, a validated and registered package out.

Phase 7 gate of ``docs/plans/pythonic-modes.md``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from refactory_dsh import create as create_module
from refactory_dsh import registry
from refactory_dsh.create import CreateConfig, CreateError, CreateMode
from refactory_dsh.create_ops import parse_files
from tests_support import (
    FakeModel,
    create_answers,
    good_files,
    render,
    strategy_answer,
)

NAME = "created_pkg"



@pytest.fixture(autouse=True)
def _clean_package():
    """A created package is real: leave the workspace as it was found."""
    root = registry.workspace_root(Path(".")) / NAME
    shutil.rmtree(root, ignore_errors=True)
    yield
    shutil.rmtree(root, ignore_errors=True)


def test_parse_files_splits_marked_blocks():
    answer = (
        "===FILE: __init__.py===\nvalue = 1\n"
        "===FILE: ops.py===\nx = 2\n"
    )
    files = parse_files(answer)
    assert sorted(files) == ["__init__.py", "ops.py"]
    assert files["__init__.py"] == "value = 1\n"
    assert files["ops.py"] == "x = 2\n"


def test_parse_files_returns_nothing_for_a_bare_answer():
    assert parse_files("here is some prose") == {}


def test_create_mode_is_itself_a_mode():
    mode = CreateMode()
    graph = mode.graph()
    assert sorted(graph.nodes) == [
        "design", "emit", "materialise", "prepare", "register", "report", "review",
        "smoke", "strategy", "survey", "validate",
    ]
    assert {node.kind for node in graph.nodes.values()} == {"op", "agent", "gate"}
    assert mode.is_scored() is False, "creating is judged by the package, not by a number"


def test_prepare_needs_a_package_name(tmp_path):
    mode = CreateMode()
    result = mode.run(tmp_path, task={"brief": "no name given"})
    assert result.exit_ok is False
    assert "needs a package name" in (result.error or "")


def test_a_good_answer_creates_registers_and_smoke_scores(tmp_path):
    model = FakeModel(create_answers(NAME, good_files(NAME)))
    result = create_module.create(
        CreateConfig(name=NAME, brief="count to a target", workdir=tmp_path / "run"),
        client=model,
    )
    assert result.package == NAME
    assert result.mode == f"{NAME}/counter"
    assert result.smoke_score and result.smoke_score > 0
    assert f"{NAME}/counter" in registry.render_catalogue(Path("."))
    assert (result.path / "mode.py").exists()
    assert result.path.resolve().is_relative_to(
        registry.workspace_root(Path(".")).resolve()
    ), "a generated package must not be written into the source tree"


def test_the_created_mode_runs_and_scores(tmp_path):
    model = FakeModel(create_answers(NAME, good_files(NAME)))
    create_module.create(
        CreateConfig(name=NAME, brief="count", workdir=tmp_path / "run"), client=model
    )
    _package, mode_class = registry.resolve(Path("."), f"{NAME}/counter")
    workdir = tmp_path / "smoke"
    result = mode_class().evaluate(workdir)
    assert result.exit_ok, result.error
    assert result.score == 3.0
    assert result.facts["value"] == 3


def test_a_broken_package_is_refused_and_then_regenerated(tmp_path):
    broken = {
        "__init__.py": "PACKAGE = object()\n",
        "ops.py": "def nothing():\n    return None\n",
        "mode.py": "X = 1\n",
    }
    model = FakeModel([*create_answers(NAME, broken), render(good_files(NAME))])
    result = create_module.create(
        CreateConfig(name=NAME, brief="count", attempts=3, workdir=tmp_path / "run"),
        client=model,
    )
    assert result.smoke_score and result.smoke_score > 0
    retry_prompt = model.prompts[-1]
    assert "What went wrong last time" in retry_prompt
    assert "PACKAGE" in retry_prompt, (
        "the retry must say what was wrong; the AST check reports the manifest"
    )


def test_a_package_that_never_works_fails_loudly(tmp_path):
    broken = {"__init__.py": "PACKAGE = object()\n"}
    model = FakeModel([create_answers(NAME, broken)[0], "a design"] + [render(broken)] * 3)
    with pytest.raises(CreateError, match="does not work after"):
        create_module.create(
            CreateConfig(name=NAME, brief="count", attempts=2, workdir=tmp_path / "run"),
            client=model,
        )


def test_the_gate_refuses_a_mode_with_no_signal(tmp_path):
    """A mode with no score() and no knobs is not a deliverable."""
    hollow = dict(good_files(NAME))
    hollow["mode.py"] = (
        "from refactory_dsh import nn\n"
        "OPS = 'refactory_packages.created_pkg.ops'\n\n"
        "class Counter(nn.Mode):\n"
        "    name = 'counter'\n"
        "    def build(self):\n"
        "        graph = nn.Graph()\n"
        "        graph.add(nn.Node('finish', 'op', call=OPS + ':finish',"
        " writes=('best.json',), produces=('best',)))\n"
        "        return graph\n"
    )
    model = FakeModel([create_answers(NAME, hollow)[0], "a design", render(hollow), render(hollow), render(good_files(NAME))])
    result = create_module.create(
        CreateConfig(name=NAME, brief="count", attempts=3, workdir=tmp_path / "run"),
        client=model,
    )
    assert result.smoke_score and result.smoke_score > 0
    combined = "\n".join(model.prompts)
    assert "score()" in combined, "the retry must say the mode produces no signal"
    assert "knobs" in combined, "the retry must say there is nothing to optimize"


def test_extends_hands_the_model_the_reference_source(tmp_path):
    model = FakeModel(create_answers(NAME, good_files(NAME)))
    create_module.create(
        CreateConfig(
            name=NAME, brief="count", extends="circle_packing/adaptive",
            workdir=tmp_path / "run",
        ),
        client=model,
    )
    combined = "\n".join(model.prompts)
    assert "circle_packing" in combined
    assert "greedy_pack" in combined, "the reference source should be included"


# ── the phases create-v2 had and this did not ──────────────────────────────
def _tmpdir() -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix="create-phase-"))


def test_the_survey_names_what_already_exists():
    """It makes "does this already exist?" a question the system can answer."""
    from refactory_dsh.run import run_node

    from refactory_dsh.lowering import DECLARATIONS

    workdir = _tmpdir()
    # The runner enforces the declarations it is given, and outside a graph the
    # caller supplies them.
    (workdir / DECLARATIONS).parent.mkdir(parents=True, exist_ok=True)
    (workdir / DECLARATIONS).write_text(json.dumps({
        "survey": {"reads": [], "writes": [], "produces": ["survey", "existing"]}
    }))
    facts = run_node("refactory_dsh.create_ops:survey", workdir=workdir, node_id="survey")
    assert "circle_packing/adaptive" in facts["survey"]
    assert "annealing_pack/anneal" in facts["survey"]
    assert "circle_packing" in facts["existing"]


def test_the_strategy_prompt_carries_the_survey_and_the_schema():
    model = FakeModel(create_answers(NAME, good_files(NAME)))
    create_module.create(
        CreateConfig(name=NAME, brief="count", workdir=_tmpdir()), client=model
    )
    strategy_prompt = model.prompts[0]
    assert "What already exists" in strategy_prompt
    assert "circle_packing/adaptive" in strategy_prompt
    assert '"considered"' in strategy_prompt, "the schema must be in the prompt"
    assert "gate only" in strategy_prompt, "a gate declares an evaluator, not a call"


def test_the_design_prompt_carries_the_chosen_approach():
    model = FakeModel(create_answers(NAME, good_files(NAME)))
    create_module.create(
        CreateConfig(name=NAME, brief="count", workdir=_tmpdir()), client=model
    )
    design_prompt = model.prompts[1]
    assert "The approach that was chosen" in design_prompt
    assert "count up one at a time" in design_prompt


def _coherent() -> dict:
    return json.loads(strategy_answer(NAME))


def _review(strategy: dict) -> list[str]:
    from refactory_dsh.create_ops import _review as review

    return review(json.dumps(strategy))


def test_a_coherent_design_passes_review():
    assert _review(_coherent()) == []


def test_review_insists_on_a_real_choice():
    strategy = _coherent()
    strategy["considered"] = [{"approach": "the only one", "why_not": ""}]
    assert any("at least two" in p for p in _review(strategy))


def test_review_requires_each_explanatory_field():
    for field in ("chosen", "why", "score"):
        strategy = _coherent()
        strategy[field] = ""
        assert any(field in p for p in _review(strategy)), field


def test_review_refuses_a_closest_existing_that_is_not_installed():
    strategy = _coherent()
    strategy["closest_existing"] = "nowhere/mode"
    assert any("not an installed mode" in p for p in _review(strategy))


def test_review_accepts_none_as_an_answer_about_what_is_closest():
    strategy = _coherent()
    strategy["closest_existing"] = "none"
    assert _review(strategy) == []


def test_review_refuses_a_design_with_no_knobs():
    strategy = _coherent()
    strategy["knobs"] = []
    assert any("no knobs" in p for p in _review(strategy))


def test_review_refuses_a_declared_graph_that_does_not_hold_together():
    """The same validator the loop uses, applied before any code exists."""
    strategy = _coherent()
    strategy["nodes"].append({"id": "orphan", "kind": "op", "call": "x:y",
                              "reads": [], "writes": [], "produces": [], "knobs": []})
    problems = _review(strategy)
    assert any("V2" in p or "not reachable" in p for p in problems), problems


def test_review_refuses_a_gate_that_is_not_a_gate():
    strategy = _coherent()
    for node in strategy["nodes"]:
        if node["id"] == "check":
            node.pop("evaluator")
            node["call"] = "x:y"
    assert any("evaluator" in p for p in _review(strategy))


def test_review_refuses_an_answer_that_is_not_json():
    from refactory_dsh.create_ops import _review as review

    assert review("prose, with no object at all")[0].startswith(
        "the strategy answer is not a JSON object"
    )


def test_the_gate_reloops_then_fails_loudly(tmp_path):
    """A design that never holds together is refused, with its reasons."""
    bad = _coherent()
    bad["considered"] = []
    model = FakeModel([json.dumps(bad), json.dumps(bad), json.dumps(bad),
                       "a design", render(good_files(NAME))])
    with pytest.raises(CreateError, match="design is still not coherent"):
        create_module.create(
            CreateConfig(name=NAME, brief="count", attempts=1, workdir=tmp_path / "run"),
            client=model,
        )


def test_a_bad_design_is_retried_with_its_reasons(tmp_path):
    bad = _coherent()
    bad["considered"] = [{"approach": "one", "why_not": ""}]
    model = FakeModel([
        json.dumps(bad), strategy_answer(NAME), "a design", render(good_files(NAME)),
    ])
    result = create_module.create(
        CreateConfig(name=NAME, brief="count", workdir=tmp_path / "run"), client=model
    )
    assert result.smoke_score and result.smoke_score > 0
    assert "at least two" in model.prompts[1], (
        "the strategy retry must be told what was wrong with the design"
    )
