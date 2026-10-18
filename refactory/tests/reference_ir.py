"""The reference graph language, used by the tests as an oracle.

The nine node and workflow types this package lowers into live in
`refactory_dsh.ir`. The reference they were copied from, remote-factory's
`factory`, is no longer a runtime dependency: it lives in the non-default
`reference` dependency group and is reached only from tests, where it answers the
questions a copy cannot answer about itself.

`EXTENSION_FIELDS` names the fields `refactory_dsh.ir` added that the reference
never had. The oracle boundary strips them: `reference_lowering` filters them from
constructor kwargs so the reference classes accept the lowering's output,
`reference_workflow` strips them from the serialized dict before the reference reads
it, and `strip_extension_keys` lets the byte-parity test compare only the shared
vocabulary. A field added to the IR without being named here or present in the
reference fails the parity-guard test.

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

#: Fields the local IR declares that the reference does not have.  Named once
#: here so the parity-guard test catches any new local-only field that is not
#: declared, and any stale declaration whose field no longer differs.
EXTENSION_FIELDS: frozenset[str] = frozenset({
    "resource_role",
    "command_timeout_s",
    "dispatch_command",
})

#: Whether the dev-only reference is importable by this interpreter.
HAS_FACTORY = importlib.util.find_spec("factory") is not None

needs_factory = pytest.mark.skipif(
    not HAS_FACTORY,
    reason=(
        "the reference graph language ('factory', remote-factory) is not installed; "
        "it is a test-time oracle, so run `uv sync --group reference` to verify "
        "this path"
    ),
)


def strip_extension_keys(workflow_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove refactory's extension fields from a to_dict() result.

    Returns a shallow copy with the node dicts stripped of EXTENSION_FIELDS keys,
    so the result carries only the vocabulary the reference shares.
    """
    out = dict(workflow_dict)
    nodes = {}
    for nid, node_data in out["nodes"].items():
        d = dict(node_data)
        for key in EXTENSION_FIELDS:
            d.pop(key, None)
        nodes[nid] = d
    out["nodes"] = nodes
    return out


def _wrap_reference_class(ref_cls: type) -> type:
    """A subclass of *ref_cls* whose __init__ silently drops EXTENSION_FIELDS kwargs.

    A subclass, not a function, because `to_dict()` writes `type(node).__name__` as
    the `_type` discriminator, so the wrapper must carry the reference class's name.
    """
    def __init__(self: Any, **kwargs: Any) -> None:
        for field_name in EXTENSION_FIELDS:
            kwargs.pop(field_name, None)
        super(wrapper, self).__init__(**kwargs)
    wrapper = type(ref_cls.__name__, (ref_cls,), {"__init__": __init__})
    return wrapper


def reference_lowering(state: dict[str, Any], *, name: str = "") -> Any:
    """Lower a mode state through `lowering`, but building the reference's IR."""
    import factory.workflow.primitives as reference

    saved = {attribute: getattr(lowering, attribute) for attribute in IR_NAMES}
    for attribute in IR_NAMES:
        ref_obj = getattr(reference, attribute)
        if hasattr(ref_obj, "model_fields"):
            setattr(lowering, attribute, _wrap_reference_class(ref_obj))
        else:
            setattr(lowering, attribute, ref_obj)
    try:
        return lowering.to_workflow(state, name=name)
    finally:
        for attribute, value in saved.items():
            setattr(lowering, attribute, value)


def reference_workflow(workflow: Any) -> Any:
    """The reference's reading of a lowered graph, reconstructed from its dict."""
    import factory.workflow.primitives as reference

    return reference.Workflow.from_dict(strip_extension_keys(workflow.to_dict()))


def validate_reference(workflow: Any) -> list[str]:
    """Ask the reference whether a lowered graph is well formed."""
    return list(reference_workflow(workflow).validate_graph())


def check_extension_coverage() -> None:
    """Assert every local-only IR field is in EXTENSION_FIELDS, and vice versa.

    Raises AssertionError with a detailed message if:
    - a field exists in the local IR but not in the reference and not in EXTENSION_FIELDS
    - a field in EXTENSION_FIELDS is not actually local-only on any IR class
    """
    import factory.workflow.primitives as reference
    from refactory_dsh import ir

    errors: list[str] = []
    declared_used: set[str] = set()

    for class_name in IR_NAMES:
        local_cls = getattr(ir, class_name)
        if not hasattr(local_cls, "model_fields"):
            continue  # AgentRole is an Enum, not a pydantic model
        ref_cls = getattr(reference, class_name)
        local_fields = set(local_cls.model_fields.keys())
        ref_fields = set(ref_cls.model_fields.keys())
        local_only = local_fields - ref_fields
        for field_name in sorted(local_only):
            if field_name not in EXTENSION_FIELDS:
                errors.append(
                    f"{class_name}.{field_name} is in the local IR but not in the "
                    f"reference and not declared in EXTENSION_FIELDS"
                )
            else:
                declared_used.add(field_name)

    unused = EXTENSION_FIELDS - declared_used
    for field_name in sorted(unused):
        errors.append(
            f"EXTENSION_FIELDS declares {field_name!r} but it is not local-only "
            f"on any IR class (either it was removed or the reference now has it)"
        )

    if errors:
        raise AssertionError(
            "IR extension field coverage is wrong:\n  " + "\n  ".join(errors)
        )
