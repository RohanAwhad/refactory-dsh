"""The reference graph language, used by the tests as an oracle.

The nine node and workflow types this package lowers into live in
`refactory_dsh.ir`. The reference they were copied from, remote-factory's
`factory`, is no longer a runtime dependency: it is in the `dev` dependency group
and reached only from tests, where it answers the questions a copy cannot answer
about itself.

`reference_lowering` runs the real `lowering.to_workflow` with the reference's own
classes bound in place of ours, so one lowering produces the two graphs the parity
test compares. `validate_reference` asks the reference's NetworkX validator whether
a lowered graph is well formed. `needs_factory` skips a test with a reason when the
reference is not installed, and the `toolchain` marker beside it makes
`--toolchain-gate` report that skip instead of counting it as verified.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from refactory_dsh import lowering

#: The names `lowering` binds to the IR. Swapping them for the reference's own
#: classes runs the same lowering code against the reference implementation.
IR_NAMES = (
    "AgentNode",
    "AgentRole",
    "DataNode",
    "Edge",
    "FnNode",
    "ForkNode",
    "GateNode",
    "JoinNode",
    "Workflow",
)

#: Whether the dev-only reference is importable by this interpreter.
HAS_FACTORY = importlib.util.find_spec("factory") is not None

needs_factory = pytest.mark.skipif(
    not HAS_FACTORY,
    reason=(
        "the reference graph language ('factory', remote-factory) is not installed; "
        "it is a dev-only test oracle, so install the dev group to verify this path"
    ),
)


def reference_lowering(state: dict[str, Any], *, name: str = "") -> Any:
    """Lower a mode state through `lowering`, but building the reference's IR."""
    import factory.workflow.primitives as reference

    saved = {attribute: getattr(lowering, attribute) for attribute in IR_NAMES}
    for attribute in IR_NAMES:
        setattr(lowering, attribute, getattr(reference, attribute))
    try:
        return lowering.to_workflow(state, name=name)
    finally:
        for attribute, value in saved.items():
            setattr(lowering, attribute, value)


def reference_workflow(workflow: Any) -> Any:
    """The reference's reading of a lowered graph, reconstructed from its dict."""
    import factory.workflow.primitives as reference

    return reference.Workflow.from_dict(workflow.to_dict())


def validate_reference(workflow: Any) -> list[str]:
    """Ask the reference whether a lowered graph is well formed."""
    return list(reference_workflow(workflow).validate_graph())
