"""Drift guard: the checked-in design-graph fixture must match what lowering produces.

Run with: pytest refactory/tests/test_design_graph_fixture.py
"""
import json
from pathlib import Path

from refactory_dsh.design import DesignMode
from refactory_dsh.lowering import to_workflow


TOPOLOGY_NODE_FIELDS = {
    '_type', 'id', 'targets', 'sources', 'subgraph_entry', 'subgraph_exit',
    'evaluator_type', 'max_iterations', 'role', 'expand',
}


def test_design_fixture_matches():
    """Regenerate the design graph and compare against the fixture."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    fixture_path = (
        repo_root / 'packages' / 'workflow' / 'workflow-spine'
        / 'tests' / 'fixtures' / 'design-graph.json'
    )
    assert fixture_path.exists(), f"Fixture not found: {fixture_path}"
    checked_in = json.loads(fixture_path.read_text())

    # Regenerate
    mode = DesignMode()
    state = mode.state_dict()
    workflow = to_workflow(state, name="design")
    full = workflow.to_dict()

    regen_nodes = {}
    for nid, node in full['nodes'].items():
        regen_nodes[nid] = {k: v for k, v in node.items() if k in TOPOLOGY_NODE_FIELDS}
    regen_edges = [
        {k: v for k, v in edge.items() if k in ('source', 'target', 'condition')}
        for edge in full['edges']
    ]
    regen = {
        'name': full['name'],
        'start_node': full['start_node'],
        'nodes': regen_nodes,
        'edges': regen_edges,
    }

    assert checked_in == regen, (
        f"Design graph fixture is stale. Regenerate with:\n"
        f"  python -m refactory_dsh.tools.export_design_graph"
    )
