"""Drift guard: the checked-in design-graph fixture must match what lowering produces.

The workflow-spine tests drive design mode's graph from this fixture
(packages/workflow/workflow-spine/tests/fixtures/design-graph.json). When design.py
changes the graph's topology, this test fails until the fixture is regenerated with
`python -m refactory_dsh.tools.export_design_graph`.
"""
import json
from pathlib import Path

from refactory_dsh.tools.export_design_graph import export_design_graph

FIXTURE = (Path(__file__).resolve().parents[2] / "packages" / "workflow" / "workflow-spine"
           / "tests" / "fixtures" / "design-graph.json")


def test_the_design_graph_fixture_matches_what_lowering_produces():
    assert json.loads(FIXTURE.read_text()) == export_design_graph(), (
        "the design graph fixture is stale; regenerate it with "
        "`python -m refactory_dsh.tools.export_design_graph`"
    )
