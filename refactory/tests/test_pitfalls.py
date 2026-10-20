"""Tests for the pitfalls researcher and its mechanical enforcement.

Each test fails without the corresponding production change.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn, pitfalls as pitfalls_mod
from refactory_dsh.design import DesignMode
from refactory_dsh.nn import Verdict
from tests_support import (
    DESIGN_DOC_ANSWER,
    DESIGN_REVIEW_ANSWER,
    FakeModel,
    PITFALLS_ANSWER,
    RESEARCH_ANSWER,
    REVIEW_ANSWERS,
    render,
)


# ── validation ─────────────────────────────────────────────────────────────

def test_validate_pitfalls_accepts_valid():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "pinning_tests",
        "evidence": "tests/test_thing.py:10",
        "prescription": "keep this assertion",
    }])
    assert pitfalls_mod.validate_pitfalls(raw) == []


def test_validate_pitfalls_catches_invalid_json():
    assert pitfalls_mod.validate_pitfalls("not json")[0].startswith("pitfalls.json is not valid JSON")


def test_validate_pitfalls_catches_empty_array():
    assert "non-empty" in pitfalls_mod.validate_pitfalls("[]")[0]


def test_validate_pitfalls_catches_bad_id():
    raw = json.dumps([{
        "id": "BAD", "risk": "high", "category": "pinning_tests",
        "evidence": "f.py:1", "prescription": "fix it",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("PIT-NNN" in p for p in problems)


def test_validate_pitfalls_catches_bad_risk():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "critical", "category": "pinning_tests",
        "evidence": "f.py:1", "prescription": "fix it",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("risk" in p for p in problems)


def test_validate_pitfalls_catches_bad_category():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "unknown_cat",
        "evidence": "f.py:1", "prescription": "fix it",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("category" in p for p in problems)


def test_validate_pitfalls_catches_empty_prescription():
    raw = json.dumps([{
        "id": "PIT-001", "risk": "high", "category": "pinning_tests",
        "evidence": "f.py:1", "prescription": "",
    }])
    problems = pitfalls_mod.validate_pitfalls(raw)
    assert any("prescription" in p for p in problems)


# ── coverage ───────────────────────────────────────────────────────────────

def _pit(pid: str = "PIT-001", risk: str = "high") -> dict:
    return {"id": pid, "risk": risk, "category": "pinning_tests",
            "evidence": "f.py:1", "prescription": "keep it"}


def _contract(body: str = "", anti: str = "") -> str:
    base = (
        "# Do the thing\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Failure trace\n\nN/A — this is a new feature.\n\n"
        "## Ask mapping\n\n"
        "| Ask (quoted from brief) | Design section | Test |\n"
        "|---|---|---|\n"
        '| "do a thing" | § Changes | test.py |\n\n'
        "## Rule and sites\n\n"
        "- `file.py:1` — **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- file.py\n"
    )
    if body:
        base += f"\n{body}\n"
    if anti:
        base += f"\n## Anti-patterns to avoid\n\n{anti}\n"
    return base


def test_pitfall_coverage_missing_when_no_anti_patterns_section():
    addressed, set_aside, missing = pitfalls_mod.pitfall_coverage(
        _contract(), [_pit()])
    assert "PIT-001" in missing


def test_pitfall_coverage_set_aside_with_reason():
    design = _contract(anti="- PIT-001: [set-aside: not applicable because the change does not touch this path]")
    addressed, set_aside, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in set_aside
    assert "PIT-001" not in missing


def test_pitfall_in_summary_only_fails():
    """Correction test: a design mentioning PIT-001 only in its Summary fails."""
    design = (
        "# Do the thing\n\n"
        "## Summary\n\nPIT-001 is important.\n\n"
        "## Rule and sites\n\n"
        "- `file.py:1` — **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- file.py\n"
    )
    _, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in missing


def test_anti_patterns_entry_with_valid_site_passes():
    """Correction test: an Anti-patterns entry naming a site that exists passes."""
    design = _contract(anti="- PIT-001: addressed at `file.py:1`")
    addressed, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in addressed
    assert "PIT-001" not in missing


def test_anti_patterns_entry_with_invalid_heading_fails():
    """Correction test: an entry naming a heading that does not exist fails."""
    design = _contract(anti="- PIT-001: see § Nonexistent section")
    _, _, missing = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in missing


def test_set_aside_bold_marker_recognised():
    design = _contract(anti="- PIT-001: **[set-aside: out of scope]**")
    _, set_aside, _ = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in set_aside


def test_set_aside_backtick_marker_recognised():
    design = _contract(anti="- PIT-001: `[set-aside: out of scope]`")
    _, set_aside, _ = pitfalls_mod.pitfall_coverage(design, [_pit()])
    assert "PIT-001" in set_aside


# ── gate_design_review pitfall check ───────────────────────────────────────

def test_gate_design_review_refuses_missing_high_pitfall(tmp_path):
    """The contract refuses a design that omits a high pitfall id."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "has_pitfalls_step": True,
    })
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md",
                   "All confirmed.\n\nVERDICT: PASS\n")
    # A contract-compliant design with NO Anti-patterns section.
    ctx.write_text("design.md", _contract())
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    ctx.write_json("pitfalls.json", [_pit()])
    import refactory_dsh.design_ops as ops
    verdict = ops.gate_design_review(ctx)
    assert verdict == "reloop"
    assert "PIT-001" in verdict.reason


def test_gate_design_review_passes_set_aside(tmp_path):
    """A design that sets a pitfall aside with a reason passes."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "has_pitfalls_step": True,
    })
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md",
                   "All confirmed.\n\nVERDICT: PASS\n")
    ctx.write_text("design.md",
                   _contract(anti="- PIT-001: [set-aside: not applicable]"))
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    ctx.write_json("pitfalls.json", [_pit()])
    import refactory_dsh.design_ops as ops
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    assert "set aside" in ctx.facts.get("pitfall_coverage", "")


def test_gate_design_review_skips_without_pitfalls_step(tmp_path):
    """A graph without a research stage records why it has no pitfalls."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        # has_pitfalls_step not set → skip
    })
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md",
                   "All confirmed.\n\nVERDICT: PASS\n")
    ctx.write_text("design.md", _contract())
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    import refactory_dsh.design_ops as ops
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    coverage = ctx.facts.get("pitfall_coverage", "")
    assert "skipped" in coverage.lower() or "no pitfalls" in coverage.lower()


# ── design graph structure ─────────────────────────────────────────────────

def test_design_graph_has_pitfalls_nodes():
    graph = DesignMode().build()
    assert "pitfalls_researcher" in graph.nodes
    assert "gate_pitfalls" in graph.nodes
    # Wiring: gate_research → pitfalls_researcher → gate_pitfalls → strategy_director
    proceed_edges = graph.out_edges("gate_research", condition="proceed")
    assert len(proceed_edges) == 1
    assert proceed_edges[0].target == "pitfalls_researcher"
    pit_edges = graph.out_edges("pitfalls_researcher")
    assert pit_edges[0].target == "gate_pitfalls"
    gp_proceed = graph.out_edges("gate_pitfalls", condition="proceed")
    assert gp_proceed[0].target == "strategy_director"
    gp_reloop = graph.out_edges("gate_pitfalls", condition="reloop")
    assert gp_reloop[0].target == "pitfalls_researcher"


def test_design_prompts_contain_pitfalls():
    """The strategy and design prompts contain {{pitfalls}}."""
    graph = DesignMode().build()
    for node_id in ("strategy_director", "synthesize_strategy", "design_doc",
                    "design_review", "code_reviewer"):
        assert "{{pitfalls}}" in graph.nodes[node_id].prompt_template, (
            f"{node_id} prompt must contain {{{{pitfalls}}}}"
        )


def test_gate_pitfalls_validates_and_reloops(tmp_path):
    """gate_pitfalls reloops when pitfalls.json is invalid."""
    ctx = nn.RunContext(tmp_path, knobs={"pitfalls_attempts": 3})
    ctx.facts.update({"source": str(tmp_path), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", "not json")
    import refactory_dsh.design_ops as ops
    verdict = ops.gate_pitfalls(ctx)
    assert verdict == "reloop"
    assert "not valid JSON" in verdict.reason


def test_gate_pitfalls_stops_when_exhausted(tmp_path):
    """gate_pitfalls raises ValueError when attempts are exhausted."""
    ctx = nn.RunContext(tmp_path, knobs={"pitfalls_attempts": 1})
    ctx.facts.update({"source": str(tmp_path), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", "not json")
    import refactory_dsh.design_ops as ops
    with pytest.raises(ValueError, match="still not valid"):
        ops.gate_pitfalls(ctx)


def test_gate_pitfalls_proceeds_on_valid(tmp_path):
    """gate_pitfalls proceeds when pitfalls.json is valid."""
    ctx = nn.RunContext(tmp_path, knobs={"pitfalls_attempts": 3})
    ctx.facts.update({"source": str(tmp_path), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", PITFALLS_ANSWER)
    import refactory_dsh.design_ops as ops
    assert ops.gate_pitfalls(ctx) == "proceed"


# ── fixture repository with pinning test ───────────────────────────────────

def test_fixture_repo_gets_pitfall_for_pinning_test(tmp_path):
    """A fixture repo whose test pins behaviour gets a pitfall naming that test.

    Creates a repository with a test file containing ``assert timeout == 300``,
    writes a brief that asks to change the timeout, scripts a pitfall answer
    referencing the test file, line and assertion, and validates it through
    gate_pitfalls — matching what a scripted model would produce, as the
    existing research tests do.

    Fails without the pitfalls_researcher node and gate_pitfalls gate:
    there would be no node to write pitfalls.json and no gate to validate it.
    """
    # ── fixture repository with a pinning test ──
    repo = tmp_path / "repo"
    repo.mkdir()
    test_dir = repo / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_config.py"
    test_file.write_text(
        "def test_default_timeout():\n"
        "    timeout = 300\n"
        "    assert timeout == 300\n"
    )

    # ── scripted pitfall answer referencing the pinning test ──
    pitfall_answer = json.dumps([{
        "id": "PIT-001",
        "risk": "high",
        "category": "pinning_tests",
        "evidence": "tests/test_config.py:3",
        "prescription": (
            "The test asserts `timeout == 300`. The change must keep this "
            "assertion or deliberately update it with a reason."
        ),
    }])

    # ── run context pointing at the fixture repo as its source ──
    run = tmp_path / "run"
    ctx = nn.RunContext(run, knobs={"pitfalls_attempts": 3})
    ctx.facts.update({"source": str(repo), "pitfalls_attempts_used": 0})
    ctx.write_text("pitfalls.json", pitfall_answer)

    # gate_pitfalls validates the schema and the evidence file:line.
    import refactory_dsh.design_ops as ops
    assert ops.gate_pitfalls(ctx) == "proceed"

    # Verify the written pitfalls.json has the required fields.
    written = json.loads(ctx.read_text("pitfalls.json"))
    assert len(written) == 1
    p = written[0]
    assert p["category"] == "pinning_tests"
    assert "tests/test_config.py" in p["evidence"]
    assert ":3" in p["evidence"]
    assert pitfalls_mod.PIT_ID_RE.match(p["id"])
    assert p["risk"] == "high"
    assert p["prescription"]

    # The evidence file actually exists in the fixture repo.
    assert (repo / "tests" / "test_config.py").is_file()
    content = (repo / "tests" / "test_config.py").read_text()
    assert "assert timeout == 300" in content
