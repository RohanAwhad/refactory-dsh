#!/usr/bin/env python3
"""Generate the design graph fixture for the TypeScript test suite.

Usage:
    python -m refactory_dsh.tools.export_design_graph

Writes packages/workflow/workflow-spine/tests/fixtures/design-graph.json.
"""
import json
import sys
from pathlib import Path

from refactory_dsh.design import DesignMode
from refactory_dsh.lowering import to_workflow


TOPOLOGY_NODE_FIELDS = {
    '_type', 'id', 'targets', 'optional_targets', 'sources', 'subgraph_entry', 'subgraph_exit',
    'evaluator_type', 'max_iterations', 'role', 'expand',
}


def export_design_graph() -> dict:
    """Build the design graph and return the topology-only dict."""
    mode = DesignMode()
    state = mode.state_dict()
    workflow = to_workflow(state, name="design")
    full = workflow.to_dict()

    stripped_nodes = {}
    for nid, node in full['nodes'].items():
        stripped = {k: v for k, v in node.items() if k in TOPOLOGY_NODE_FIELDS}
        stripped_nodes[nid] = stripped

    stripped_edges = [
        {k: v for k, v in edge.items() if k in ('source', 'target', 'condition')}
        for edge in full['edges']
    ]

    return {
        'name': full['name'],
        'start_node': full['start_node'],
        'nodes': stripped_nodes,
        'edges': stripped_edges,
    }


def main():
    fixture = export_design_graph()

    # Find repo root by walking up from this file
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    out_path = repo_root / 'packages' / 'workflow' / 'workflow-spine' / 'tests' / 'fixtures' / 'design-graph.json'
    content = json.dumps(fixture, indent=2) + '\n'

    if '--check' in sys.argv:
        if not out_path.exists():
            print(f"export-design-graph: {out_path} does not exist; "
                  "run `python -m refactory_dsh.tools.export_design_graph`")
            sys.exit(1)
        if out_path.read_text() != content:
            print(f"export-design-graph: {out_path} is stale; "
                  "run `python -m refactory_dsh.tools.export_design_graph`")
            sys.exit(1)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    print(f"Wrote {out_path}")


if __name__ == '__main__':
    main()
