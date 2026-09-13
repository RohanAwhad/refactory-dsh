"""Fixtures shared by the tests that need a generated package.

Kept in one module so `test_create.py` and `test_example.py` exercise the same
package rather than two copies that drift apart.
"""

from __future__ import annotations

_INIT = '''\
"""A generated package."""
from refactory_dsh.nn import Package

from .mode import Counter

PACKAGE = Package(
    name="{name}",
    description="count to a target",
    modes=(Counter,),
    ops=("refactory_packages.{name}.ops",),
)
'''

_OP_HEAD = '''\
"""Operations for the counter mode."""
'''

_OPS = '''

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
'''

_MODE_HEAD = '''\
"""The counter mode."""
from refactory_dsh import nn

OPS = "refactory_packages.{name}.ops"


class Counter(nn.Mode):
'''

_MODE_TAIL = '''\
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
'''


def good_files(name: str) -> dict[str, str]:
    """A working generated package under the given name."""
    return {
        "__init__.py": _INIT.format(name=name),
        "ops.py": _OP_HEAD + _OPS,
        "mode.py": _MODE_HEAD.format(name=name) + _MODE_TAIL,
    }


def render(files: dict[str, str]) -> str:
    """The emit agent's answer format: `===FILE: name===` blocks."""
    return "\n".join(f"===FILE: {name}===\n{body}" for name, body in files.items()) + "\n"


class FakeModel:
    """Answers with the given texts, in order."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, system: str = "") -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""
