"""Our IR against the reference's, over every mode this checkout ships.

`refactory_dsh.ir` is a copy, and a copy drifts. For every mode of every package
under `refactory_packages/`, one lowering runs twice -- once building our classes,
once building the reference's, with only the IR classes swapped -- and the two
`to_dict()` results must serialize to the same bytes. The reference's NetworkX
validator must also accept the graph our copy serialized.

The reference is a dev-only test oracle, so every test here skips with a reason
when `factory` is not importable, and the `toolchain` marker makes
`--toolchain-gate` report that skip as unverified rather than as a pass.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from refactory_dsh import lowering, registry

from reference_ir import needs_factory, reference_lowering, reference_workflow

pytestmark = [pytest.mark.toolchain("factory"), needs_factory]


def _shipped_packages() -> list[str]:
    """Every package installed from `refactory_packages/`, not the workspace."""
    return registry.installed_names(None)


def _modes() -> list[tuple[str, dict[str, Any]]]:
    """Every shipped mode as ``(identifier, state)``, built at collection time."""
    corpus: list[tuple[str, dict[str, Any]]] = []
    for package_name in _shipped_packages():
        package = registry.load(package_name)
        for mode_class in package.modes:
            corpus.append((f"{package_name}/{mode_class.name}", mode_class().state_dict()))
    return corpus


CORPUS = _modes()
IDS = [identifier for identifier, _state in CORPUS]


def _serialized(workflow: Any) -> bytes:
    """The exact bytes a written graph carries, without the pretty-printing."""
    return json.dumps(workflow.to_dict(), default=str).encode()


#: The shipped modes the reference validator refuses, with the issue it reports.
#:
#: Both are limits of that validator rather than defects in the lowering, which
#: the byte-parity test above shows is identical to the reference's:
#:
#: - `sympy_pack/exact` is a read-modify-write loop. `derive` reads and writes
#:   `state.json`, and the reference's data-dependency check unions only the
#:   writes of a node's ancestors, so a node's own writes never count.
#: - `java_build_pack/build` writes `build.sh` inside the `items` data node's
#:   subgraph. The reference adds `data_node -> entry` edges only, so a subgraph
#:   write is never seen as available to the data node's successor.
#:
#: Our `nn.validate_state` accepts both and both runtimes execute both. A mode
#: outside this map being refused fails the test, and a mapped mode being
#: accepted fails too, so the exceptions cannot drift unnoticed.
REFERENCE_REFUSALS = {
    "sympy_pack/exact": "node 'derive' reads {'state.json'} but no predecessor writes them",
    "java_build_pack/build": (
        "node 'finish' reads {'build.sh'} but no predecessor writes them"
    ),
}


@pytest.mark.parametrize(("identifier", "state"), CORPUS, ids=IDS)
def test_a_shipped_mode_serializes_byte_identically_to_the_reference(identifier, state):
    ours = lowering.to_workflow(state, name=identifier)
    reference = reference_lowering(state, name=identifier)
    assert _serialized(reference) == _serialized(ours), (
        f"{identifier} serializes differently from the reference IR"
    )


@pytest.mark.parametrize(("identifier", "state"), CORPUS, ids=IDS)
def test_the_reference_validator_accepts_a_shipped_mode(identifier, state):
    ours = lowering.to_workflow(state, name=identifier)
    issues = reference_workflow(ours).validate_graph()
    recorded = REFERENCE_REFUSALS.get(identifier)
    if recorded is None:
        assert issues == [], f"the reference validator refused {identifier}"
        return
    assert issues, (
        f"the reference validator now accepts {identifier}; remove it from "
        "REFERENCE_REFUSALS"
    )
    assert issues == [recorded], (
        f"{identifier} is refused for a different reason than the recorded one"
    )
