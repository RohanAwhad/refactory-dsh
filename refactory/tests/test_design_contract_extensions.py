"""R4 tests: Failure trace and Ask mapping sections in the design-document contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh.design import DesignMode
from refactory_dsh.design_contract import prompt_contract


def test_prompt_contract_includes_failure_trace():
    text = prompt_contract()
    assert "## Failure trace" in text


def test_prompt_contract_includes_ask_mapping():
    text = prompt_contract()
    assert "## Ask mapping" in text


def test_prompt_contract_includes_guardedness_exemptions():
    text = prompt_contract()
    assert "## Test guardedness exemptions" in text


def test_prompt_contract_section_order():
    """The sections appear in the specified order."""
    text = prompt_contract()
    ft_pos = text.index("Failure trace")
    am_pos = text.index("Ask mapping")
    rs_pos = text.index("Rule and sites")
    assert ft_pos < am_pos < rs_pos


def test_design_review_prompt_mentions_failure_trace():
    graph = DesignMode().build()
    prompt = graph.nodes["design_review"].prompt_template
    assert "Failure trace" in prompt
    assert "Ask mapping" in prompt


class FakeCtx:
    """Minimal context for gate_design_review mechanical checks."""
    def __init__(self, files=None, facts=None):
        self._files = dict(files or {})
        self.facts = dict(facts or {})
        self.workdir = Path(".")
        self._knobs = {"design_review_attempts": 2}
        self.knobs = self._knobs

    def exists(self, name):
        return name in self._files

    def read_text(self, name):
        return self._files.get(name, "")

    def read_json(self, name, default=None):
        text = self._files.get(name)
        if text is None:
            return default
        return json.loads(text)

    def write_text(self, name, text):
        self._files[name] = text

    def set_fact(self, name, value):
        self.facts[name] = value

    def knob(self, name, default=""):
        return self._knobs.get(name, default)


def _make_design(*, failure_trace=True, ask_mapping=True):
    """Build a design.md with or without the new sections."""
    parts = ["# Fix the thing\n", "## Summary\n\nFix it.\n"]
    if failure_trace:
        parts.append("## Failure trace\n\nN/A — this is a new feature.\n")
    if ask_mapping:
        parts.append(
            "## Ask mapping\n\n"
            "| Ask (quoted from brief) | Design section | Test |\n"
            "|---|---|---|\n"
            '| "a tool" | § Changes | test_tool.py |\n'
        )
    parts.extend([
        "## Rule and sites\n\n- `tool.py:1` — **[changed]**\n",
        "## Docs\n\n- docs/models.md\n",
        "## Changes\n\n- tool.py\n",
    ])
    return "\n".join(parts)


def test_gate_rejects_missing_failure_trace():
    """gate_design_review should flag a missing Failure trace section."""
    from refactory_dsh.design_ops import gate_design_review
    design = _make_design(failure_trace=False, ask_mapping=True)
    review = "No issues found.\n\nVERDICT: PASS"
    ctx = FakeCtx(
        files={
            "reviews/design-review.md": review,
            "design.md": design,
            "brief.json": json.dumps({"brief": "a tool"}),
        },
        facts={"name": "test_r4", "has_pitfalls_step": False},
    )
    result = gate_design_review(ctx)
    # Should detect missing failure trace; the gate may proceed or reloop
    # but the problem must be recorded
    feedback = ctx.facts.get("design_review_feedback", "")
    assert "Failure trace" in feedback or "failure trace" in feedback


def test_gate_rejects_missing_ask_mapping():
    """gate_design_review should flag a missing Ask mapping section."""
    from refactory_dsh.design_ops import gate_design_review
    design = _make_design(failure_trace=True, ask_mapping=False)
    review = "No issues found.\n\nVERDICT: PASS"
    ctx = FakeCtx(
        files={
            "reviews/design-review.md": review,
            "design.md": design,
            "brief.json": json.dumps({"brief": "a tool"}),
        },
        facts={"name": "test_r4", "has_pitfalls_step": False},
    )
    result = gate_design_review(ctx)
    feedback = ctx.facts.get("design_review_feedback", "")
    assert "Ask mapping" in feedback or "ask mapping" in feedback


def test_gate_accepts_both_sections_present():
    """When both sections are present with valid content, no section problems are raised."""
    from refactory_dsh.design_ops import gate_design_review
    design = _make_design(failure_trace=True, ask_mapping=True)
    review = "No issues found.\n\nVERDICT: PASS"
    ctx = FakeCtx(
        files={
            "reviews/design-review.md": review,
            "design.md": design,
            "brief.json": json.dumps({"brief": "a tool"}),
        },
        facts={"name": "test_r4", "has_pitfalls_step": False},
    )
    result = gate_design_review(ctx)
    feedback = ctx.facts.get("design_review_feedback", "")
    # Should not contain failure trace or ask mapping problems
    assert "missing the required ## Failure trace" not in feedback
    assert "missing the required ## Ask mapping" not in feedback
