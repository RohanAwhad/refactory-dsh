"""Guard against reintroducing inlined large artifacts in agent prompts.

Issue #320: agent-node prompt templates must not inline large run artifacts.
Workers read these files from their declared reads paths instead.
"""
from __future__ import annotations

import pytest

from refactory_dsh.create import CreateMode
from refactory_dsh.design import DesignMode


# Known large-artifact template variables that must not appear in agent prompts.
# Short scalar facts (brief, check, name, etc.) are intentionally kept inline.
LARGE_ARTIFACT_VARS = frozenset({"{{design}}", "{{emit}}", "{{strategy}}", "{{qa}}"})


def _agent_nodes(graph):
    """Yield (node_id, node) for every agent node in a graph."""
    for node_id, node in graph.nodes.items():
        if node.kind == "agent":
            yield node_id, node


# Nodes that are allowed to inline specific large artifacts because they
# produce those artifacts or use them before the artifact exists as a file.
ALLOWED = {
    # design_doc produces the design from the strategy — it needs {{strategy}} inline
    ("design_doc", "{{strategy}}"): "DesignMode",
    # synthesize_strategy compiles perspectives into strategy — no large artifacts
    # design node in create produces the design from strategy
    ("design", "{{strategy}}"): "CreateMode",
}


class TestDesignModeNoInlinedArtifacts:
    """DesignMode agent templates must not inline large artifacts."""

    @pytest.fixture
    def graph(self):
        return DesignMode().build()

    @pytest.mark.parametrize("var", sorted(LARGE_ARTIFACT_VARS))
    def test_no_large_artifact_in_agent_prompts(self, graph, var):
        for node_id, node in _agent_nodes(graph):
            if (node_id, var) in ALLOWED:
                continue
            assert var not in node.prompt_template, (
                f"DesignMode node {node_id!r} still inlines {var}; "
                f"use a file-read instruction instead (issue #320)"
            )


class TestCreateModeNoInlinedArtifacts:
    """CreateMode agent templates must not inline large artifacts."""

    @pytest.fixture
    def graph(self):
        return CreateMode().build()

    @pytest.mark.parametrize("var", sorted(LARGE_ARTIFACT_VARS))
    def test_no_large_artifact_in_agent_prompts(self, graph, var):
        for node_id, node in _agent_nodes(graph):
            if (node_id, var) in ALLOWED:
                continue
            assert var not in node.prompt_template, (
                f"CreateMode node {node_id!r} still inlines {var}; "
                f"use a file-read instruction instead (issue #320)"
            )
