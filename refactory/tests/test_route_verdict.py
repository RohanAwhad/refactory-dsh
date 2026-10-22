"""Verify that ``route_verdict`` agrees with the shared routing cases."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh.nn.executor import ExecutionError, route_verdict
from refactory_dsh.nn.graph import Edge


CASES_PATH = Path(__file__).resolve().parent.parent / "refactory_dsh" / "data" / "verdict-routing-cases.json"


def _load_cases():
    return json.loads(CASES_PATH.read_text())


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_route_verdict_matches_shared_cases(case):
    edges = [
        Edge(source=case["gate"], target=e["target"], condition=e["condition"])
        for e in case["edges"]
    ]
    expect = case["expect"]
    if "error" in expect:
        with pytest.raises(ExecutionError):
            route_verdict(case["verdict"], edges, case["gate_kind"])
    elif "halt" in expect:
        edge, reason = route_verdict(case["verdict"], edges, case["gate_kind"])
        assert edge is None, f"expected halt but got edge to {edge.target}"
        assert reason == expect["reason"]
    else:
        edge, _reason = route_verdict(case["verdict"], edges, case["gate_kind"])
        assert edge is not None, "expected a target but got halt"
        assert edge.target == expect["target"]
