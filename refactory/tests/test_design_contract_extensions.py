"""The design-document contract: a light template, and the optional sections it names.

design_doc must write ## Summary, ## Changes and ## Tests. Failure trace, Ask mapping
(R4) and the rest are optional; gate_design_review does not check one a design leaves
out.
"""

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


def test_prompt_contract_requires_only_summary_changes_and_tests():
    text = prompt_contract()
    required, optional = text.split("Add any of these optional sections")
    for section in ("## Summary", "## Changes", "## Tests"):
        assert section in required, section
    for section in ("## Failure trace", "## Ask mapping", "## Rule and sites",
                    "## Test guardedness exemptions", "## Site revert exemptions",
                    "## Anti-patterns to avoid", "## Docs", "## Runtime coverage"):
        assert section in optional and section not in required, section


def test_the_design_doc_prompt_carries_the_light_template():
    prompt = DesignMode().build().nodes["design_doc"].prompt_template
    assert prompt_contract() in prompt
    assert "fails the contract" not in prompt


class FakeCtx:
    """Minimal context for gate_design_review mechanical checks."""
    def __init__(self, files=None, facts=None):
        self._files = dict(files or {})
        self.facts = dict(facts or {})
        self.workdir = Path(".")
        self._knobs = {"design_review_attempts": 1}
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


def _make_design(*, failure_trace=True, ask_mapping=True, empty_trace=False):
    """Build a design.md with or without the new sections."""
    parts = ["# Fix the thing\n", "## Summary\n\nFix it.\n"]
    if failure_trace:
        if empty_trace:
            parts.append("## Failure trace\n\n")
        else:
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
        "## Tests\n\n- `test_tool.py::test_build` — it builds\n",
    ])
    return "\n".join(parts)


@pytest.mark.parametrize("design", [
    _make_design(failure_trace=False, ask_mapping=True),
    _make_design(failure_trace=True, ask_mapping=False),
    _make_design(failure_trace=True, ask_mapping=True, empty_trace=True),
], ids=["no-failure-trace", "no-ask-mapping", "empty-failure-trace"])
def test_an_optional_section_left_out_is_not_checked(design):
    """Failure trace and Ask mapping are optional: a design without one goes to the
    person with nothing listed about it."""
    from refactory_dsh.design_ops import gate_design_review
    ctx = FakeCtx(
        files={"design.md": design, "brief.json": json.dumps({"brief": "a tool"})},
        facts={"name": "test_r4", "has_pitfalls_step": False},
    )
    assert gate_design_review(ctx) == "proceed"
    assert ctx.facts["design_review_advisory"] == ""
    assert ctx.facts["design_review_status"] == "Design review: PASS"


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
    # Neither section is reported, as a blocking problem or as an advisory one.
    assert "missing the required ## Ask mapping" not in feedback
    assert "Failure trace" not in ctx.facts["design_review_advisory"]
    assert "Ask mapping" not in ctx.facts["design_review_advisory"]
