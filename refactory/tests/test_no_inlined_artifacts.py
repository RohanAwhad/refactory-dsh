"""Guard against reintroducing inlined large artifacts in agent prompts.

Issue #320: agent-node prompt templates must not inline large run artifacts.
Workers read these files from their declared reads paths instead.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from refactory_dsh.authoring_evidence import INLINE_LIMIT, requirements
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


class FakeContext:
    """The parts of a run context ``requirements`` reads, over in-memory files."""

    def __init__(self, files, brief="build a widget"):
        self.facts = {"brief": brief}
        self._files = files

    @property
    def workdir(self):
        return Path(tempfile.gettempdir())

    def exists(self, name):
        return name in self._files

    def read_text(self, name):
        return self._files[name]


@pytest.mark.parametrize("mode", [DesignMode, CreateMode], ids=["design", "create"])
@pytest.mark.parametrize("var", sorted(LARGE_ARTIFACT_VARS))
def test_no_large_artifact_in_agent_prompts(mode, var):
    for node_id, node in _agent_nodes(mode().build()):
        if (node_id, var) in ALLOWED:
            continue
        assert var not in node.prompt_template, (
            f"{mode.__name__} node {node_id!r} still inlines {var}; "
            f"use a file-read instruction instead (issue #320)"
        )


def test_large_requirement_uses_path_reference():
    """A requirement over the inline limit has path+sha256, not text."""
    ctx = FakeContext({
        "strategy.json": '{"chosen": "x", "why": "y", "score": "z", "considered": []}',
        "design.md": "# Design\n" + "x" * (INLINE_LIMIT + 1000),
    })
    by_id = {r["id"]: r for r in requirements(ctx)}
    assert "text" in by_id["brief"]
    assert "text" in by_id["strategy"]
    design_req = by_id["design"]
    assert "path" in design_req
    assert "sha256" in design_req
    assert "text" not in design_req


def test_small_requirement_stays_inline():
    """A requirement under the inline limit has text, not path."""
    ctx = FakeContext({"design.md": "# Small design\nJust a few lines."})
    by_id = {r["id"]: r for r in requirements(ctx)}
    design_req = by_id["design"]
    assert "text" in design_req
    assert "path" not in design_req


def test_runtime_evidence_fact_has_no_oversized_requirements():
    """The serialized runtime_evidence JSON must not contain oversized requirement text.

    runtime_evidence is set as a fact via ctx.set_fact("runtime_evidence",
    json.dumps(payload)) and inlined into every reviewer prompt via
    {{runtime_evidence}} in REVIEW_CONTEXT. If a requirement entry
    carries full text instead of a path+sha256 reference, the prompt
    balloons to tens of KB per reviewer — the root cause of #320.
    """
    ctx = FakeContext({
        "strategy.json": json.dumps({
            "chosen": "simple approach", "why": "minimal",
            "score": "line count", "considered": [
                {"approach": "a", "why_not": ""},
                {"approach": "b", "why_not": "complex"},
            ],
            "acceptance_criteria": ["counts lines"],
        }) + "x" * 20000,   # realistic: 20 KB strategy
        "design.md": "# Design\n" + "detailed design " * 2000,  # ~30 KB
    }, brief="write wordcount.py")
    reqs = requirements(ctx)
    serialized = json.dumps({"schema": 1, "requirements": reqs})

    # Before the fix it was 50-60 KB; with path+sha256 refs it's ~2-3 KB.
    MAX_EVIDENCE_KB = 8
    assert len(serialized) < MAX_EVIDENCE_KB * 1024, (
        f"runtime_evidence payload is {len(serialized)} bytes "
        f"(>{MAX_EVIDENCE_KB} KB); large requirements must use "
        f"path+sha256 references, not inline text (issue #320)"
    )
    for req in reqs:
        text = req.get("text", "")
        assert len(text) <= INLINE_LIMIT, (
            f"requirement {req['id']!r} has {len(text)}-char inline text; "
            f"inputs over {INLINE_LIMIT} chars must use path+sha256 (issue #320)"
        )
