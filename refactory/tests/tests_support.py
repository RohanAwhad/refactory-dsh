"""Fixtures shared by the tests that need a generated package.

Kept in one module so `test_create.py` and `test_example.py` exercise the same
package rather than two copies that drift apart.
"""

from __future__ import annotations

import json

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


def strategy_answer(name: str) -> str:
    """A coherent strategy answer, in the shape create-mode's review gate checks."""
    import json

    ops = f"refactory_packages.{name}.ops"
    return json.dumps({
        "considered": [
            {"approach": "count up one at a time", "why_not": ""},
            {"approach": "compute the answer directly", "why_not": "nothing to iterate"},
        ],
        "chosen": "count up one at a time",
        "why": "it is the simplest thing that has a state to watch",
        "will_not": ["threads", "a second package"],
        "closest_existing": "circle_packing/adaptive",
        "knobs": [
            {"name": "target", "kind": "int", "default": 3, "lo": 1, "hi": 9,
             "description": "where to stop"},
        ],
        "nodes": [
            {"id": "start", "kind": "op", "call": f"{ops}:start",
             "writes": ["state.json"], "produces": ["value"], "knobs": ["target"]},
            {"id": "step", "kind": "op", "call": f"{ops}:step",
             "reads": ["state.json"], "writes": ["state.json"], "produces": ["value"]},
            {"id": "check", "kind": "gate", "evaluator": f"{ops}:check",
             "reads": ["state.json"], "produces": ["done"], "max_iterations": 20},
            {"id": "finish", "kind": "op", "call": f"{ops}:finish",
             "reads": ["state.json"], "writes": ["best.json"], "produces": ["best"]},
        ],
        "edges": [
            {"source": "start", "target": "step", "condition": None},
            {"source": "step", "target": "check", "condition": None},
            {"source": "check", "target": "step", "condition": "retry"},
            {"source": "check", "target": "finish", "condition": "proceed"},
        ],
        "start": "start",
        "score": "the fact 'best', produced by finish",
    })


#: The answers create-v2's non-emit agents give, in the order the graph asks.
#:
#: The research answer is the plan array the gate checks, not a sentence about having
#: researched: a prose answer is exactly the failure the gate exists to catch, so a
#: fixture that fed one would be testing the bug.
RESEARCH_ANSWER = json.dumps([
    {
        "focus": "which API surface the package builds against",
        "slug": "api-surface",
        "finding": "the mode API in the checkout: nn.Mode, nn.Graph, ctx.knob/read_json/write_json",
    },
    {
        "focus": "what already exists that this resembles",
        "slug": "existing-modes",
        "finding": "circle_packing/adaptive is the closest existing mode and is not this",
    },
    {
        "focus": "the acceptance criteria the brief asks for",
        "slug": "acceptance",
        "finding": "the mode must count to a target and score how close it landed",
    },
])
PERSPECTIVES_ANSWER = "perspectives: the simplest thing that works, and the criteria"
DESIGN_ANSWER = "a design"
#: The three reviews and the overwatch pass. Overwatch states its verdict because
#: the gate reads that line rather than trusting the review's own summary.
REVIEW_ANSWERS = [
    "health: the package imports, registers and smoke-scores",
    "code review: the ops, nodes and knobs agree with the design",
    "qa: every acceptance criterion has a check and they hold",
    "VERDICT: PASS",
]


def create_script(name: str, emits: list[str]) -> list[str]:
    """The whole answer script, with the given emit answers in the order used.

    A reloop back to `emit` consumes the next one, so a test that expects a retry
    passes two; a run whose validation never passes is scripted without the reviews,
    because it never reaches them.
    """
    return [
        RESEARCH_ANSWER,
        PERSPECTIVES_ANSWER,
        strategy_answer(name),
        DESIGN_ANSWER,
        *emits,
        *REVIEW_ANSWERS,
    ]


def create_answers(name: str, files: dict[str, str]) -> list[str]:
    """Every answer create-v2's agents need, in the order the graph asks for them.

    The three reviews and the overwatch pass answer as text; the executor writes
    each to the file the node declares, and the QA synthesis and its gate read them
    back. Overwatch must state its verdict line, because the gate reads it.
    """
    return create_script(name, [render(files)])


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
