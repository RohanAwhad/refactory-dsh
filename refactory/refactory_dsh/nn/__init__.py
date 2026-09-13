"""The mode library: the ``nn`` of the harness world.

A mode is a Python class. Its architecture and parameters are a serializable
state dict, its score and its invariants are methods no optimizer can reach, and
every structural change is typed data validated before it costs anything.

    from refactory_dsh import nn

    class MyMode(nn.Mode):
        @classmethod
        def declare(cls):
            return {"rounds": nn.Knob(3, "int", lo=1, hi=8)}

        def build(self):
            graph = nn.Graph()
            graph.add(nn.Node("run", "op", call="mypkg.ops:run", knobs=("rounds",)))
            return graph

        def score(self, run):
            return float(run.facts["best"])
"""

from __future__ import annotations

from . import edits, resolve
from .executor import ExecutionError, Executor, ModelClient, RunContext, Step, execute
from .graph import Edge, Graph, GraphError, Node
from .knob import KINDS, Knob, KnobError
from .mode import STATE_VERSION, Mode, RunResult, StateError
from .package import Package, PackageError, mode_id, split
from .validate import (
    Invariant,
    Issue,
    declared_knobs,
    fact_produced,
    validate,
    validate_state,
)

__all__ = [
    "KINDS",
    "STATE_VERSION",
    "Edge",
    "ExecutionError",
    "Executor",
    "Graph",
    "GraphError",
    "Invariant",
    "Issue",
    "Knob",
    "KnobError",
    "Mode",
    "ModelClient",
    "Node",
    "Package",
    "PackageError",
    "RunContext",
    "RunResult",
    "StateError",
    "Step",
    "declared_knobs",
    "edits",
    "execute",
    "fact_produced",
    "mode_id",
    "resolve",
    "split",
    "validate",
    "validate_state",
]
