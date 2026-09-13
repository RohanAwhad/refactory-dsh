"""create-mode: a brief in, a validated and registered package out.

Phase 7 gate of ``docs/plans/pythonic-modes.md``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from refactory_dsh import create as create_module
from refactory_dsh import registry
from refactory_dsh.create import CreateConfig, CreateError, CreateMode
from refactory_dsh.create_ops import parse_files

NAME = "created_pkg"

GOOD_FILES = {
    "__init__.py": '''\
"""A generated package."""
from refactory_dsh.nn import Package

from .mode import Counter

PACKAGE = Package(
    name="created_pkg",
    description="count to a target",
    modes=(Counter,),
    ops=("refactory_packages.created_pkg.ops",),
)
''',
    "ops.py": '''\
"""Operations for the counter mode."""


def start(ctx):
    target = int(ctx.knob("target", 3))
    ctx.write_json("state.json", {"value": 0, "target": target})
    return {"value": 0}


def step(ctx):
    state = ctx.read_json("state.json", {"value": 0, "target": 3})
    state["value"] += 1
    ctx.write_json("state.json", state)
    return {"value": state["value"]}


def check(ctx):
    state = ctx.read_json("state.json", {"value": 0, "target": 3})
    done = state["value"] >= state["target"]
    ctx.set_fact("done", done)
    return "proceed" if done else "retry"


def finish(ctx):
    state = ctx.read_json("state.json", {"value": 0, "target": 3})
    ctx.write_json("best.json", {"best": state["value"]})
    return {"best": state["value"]}
''',
    "mode.py": '''\
"""The counter mode."""
from refactory_dsh import nn

OPS = "refactory_packages.created_pkg.ops"


class Counter(nn.Mode):
    name = "counter"
    description = "count to a target"

    @classmethod
    def declare(cls):
        return {"target": nn.Knob(3, "int", lo=1, hi=9, description="where to stop")}

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("start", "op", call=OPS + ":start",
                          writes=("state.json",), produces=("value",), knobs=("target",)))
        graph.add(nn.Node("step", "op", call=OPS + ":step",
                          reads=("state.json",), writes=("state.json",), produces=("value",)))
        graph.add(nn.Node("check", "gate", evaluator=OPS + ":check",
                          reads=("state.json",), produces=("done",), max_iterations=20))
        graph.add(nn.Node("finish", "op", call=OPS + ":finish",
                          reads=("state.json",), writes=("best.json",), produces=("best",)))
        graph.connect("start", "step")
        graph.connect("step", "check")
        graph.connect("check", "step", condition="retry")
        graph.connect("check", "finish", condition="proceed")
        return graph

    def score(self, run):
        return float(run.facts["best"])

    def invariants(self):
        return (nn.fact_produced("best"),)
''',
}


def _render(files: dict[str, str]) -> str:
    return "\n".join(f"===FILE: {name}===\n{body}" for name, body in files.items()) + "\n"


class FakeModel:
    """Answers with the given texts, in order."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, system: str = "") -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""


@pytest.fixture(autouse=True)
def _clean_package():
    """A created package is real: leave the tree as it was found."""
    root = registry.packages_root() / NAME
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
        "design", "emit", "materialise", "prepare", "register", "report", "smoke", "validate",
    ]
    assert {node.kind for node in graph.nodes.values()} == {"op", "agent", "gate"}
    assert mode.is_scored() is False, "creating is judged by the package, not by a number"


def test_prepare_needs_a_package_name(tmp_path):
    mode = CreateMode()
    result = mode.run(tmp_path, task={"brief": "no name given"})
    assert result.exit_ok is False
    assert "needs a package name" in (result.error or "")


def test_a_good_answer_creates_registers_and_smoke_scores(tmp_path):
    model = FakeModel(["a design", _render(GOOD_FILES)])
    result = create_module.create(
        CreateConfig(name=NAME, brief="count to a target", workdir=tmp_path / "run"),
        client=model,
    )
    assert result.package == NAME
    assert result.mode == f"{NAME}/counter"
    assert result.smoke_score and result.smoke_score > 0
    assert f"{NAME}/counter" in registry.render_catalogue(Path("."))
    assert (result.path / "mode.py").exists()


def test_the_created_mode_runs_and_scores(tmp_path):
    model = FakeModel(["a design", _render(GOOD_FILES)])
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
    model = FakeModel(["a design", _render(broken), _render(GOOD_FILES)])
    result = create_module.create(
        CreateConfig(name=NAME, brief="count", attempts=3, workdir=tmp_path / "run"),
        client=model,
    )
    assert result.smoke_score and result.smoke_score > 0
    retry_prompt = model.prompts[-1]
    assert "What went wrong last time" in retry_prompt
    assert "must define PACKAGE" in retry_prompt


def test_a_package_that_never_works_fails_loudly(tmp_path):
    broken = {"__init__.py": "PACKAGE = object()\n"}
    model = FakeModel(["a design"] + [_render(broken)] * 3)
    with pytest.raises(CreateError, match="does not work after"):
        create_module.create(
            CreateConfig(name=NAME, brief="count", attempts=2, workdir=tmp_path / "run"),
            client=model,
        )


def test_the_gate_refuses_a_mode_with_no_signal(tmp_path):
    """A mode with no score() and no knobs is not a deliverable."""
    hollow = dict(GOOD_FILES)
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
    model = FakeModel(["a design", _render(hollow), _render(hollow), _render(GOOD_FILES)])
    result = create_module.create(
        CreateConfig(name=NAME, brief="count", attempts=3, workdir=tmp_path / "run"),
        client=model,
    )
    assert result.smoke_score and result.smoke_score > 0
    combined = "\n".join(model.prompts)
    assert "does not override score()" in combined
    assert "declares no knobs" in combined


def test_extends_hands_the_model_the_reference_source(tmp_path):
    model = FakeModel(["a design", _render(GOOD_FILES)])
    create_module.create(
        CreateConfig(
            name=NAME, brief="count", extends="circle_packing/adaptive",
            workdir=tmp_path / "run",
        ),
        client=model,
    )
    design_prompt = model.prompts[0]
    assert "circle_packing" in design_prompt
    assert "greedy_pack" in design_prompt, "the reference source should be included"
