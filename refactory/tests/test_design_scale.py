"""The design phase scales with the change, and its review blocks only on what changes the build.

A scoped change (a refine, or a brief that names files) gets one research question, at most
five pitfalls, one strategy perspective and one design-review round. At every scale the
design-review gate sends a design back only for an unmet ask, a false claim the design relies
on, a missing high-risk pitfall or a blocking finding from the review agent; everything else
is listed as advisory at the person's gate and costs no round.
"""
from __future__ import annotations

import json

from refactory_dsh.design import DesignMode
from refactory_dsh.design_ops import design_review_status, gate_design_review
from test_design_contract_extensions import FakeCtx, _make_design

PASSING_REVIEW = "No issues found.\n\nVERDICT: PASS"


def _gate(design: str, *, brief: str = "a tool", scope: str = "full", source: str = "",
          review: str = PASSING_REVIEW, pitfalls: list | None = None):
    files = {"reviews/design-review.md": review, "design.md": design,
             "brief.json": json.dumps({"brief": brief})}
    if pitfalls is not None:
        files["pitfalls.json"] = json.dumps(pitfalls)
    ctx = FakeCtx(
        files=files,
        facts={"name": "scale_test", "has_pitfalls_step": pitfalls is not None,
               "research_scope": scope, "source": source},
    )
    return ctx, gate_design_review(ctx)


# ── what blocks ────────────────────────────────────────────────────────────────

def test_a_missing_ask_mapping_still_sends_the_design_back():
    ctx, verdict = _gate(_make_design(ask_mapping=False))
    assert verdict == "reloop"
    assert "Ask mapping" in verdict.reason


def test_a_citation_past_the_end_of_its_file_still_blocks(tmp_path):
    (tmp_path / "tool.py").write_text("x = 1\ny = 2\n")
    design = _make_design().replace("## Changes", "`x` at `tool.py:40`: `x = 1`\n\n## Changes")
    ctx, verdict = _gate(design, source=str(tmp_path))
    assert verdict == "reloop"
    assert "tool.py has 2 lines, not 40" in verdict.reason


def test_a_misquote_of_the_brief_still_blocks():
    design = _make_design() + '\nThe brief says "remove the flag from the command".\n'
    ctx, verdict = _gate(design, brief="Add the flag to the command.")
    assert verdict == "reloop"
    assert "does not appear verbatim in the brief" in verdict.reason


def test_a_misquote_that_drops_a_negation_still_blocks():
    # "able" is inside "unable": a word-level match must not accept the inverted quote.
    design = _make_design() + '\nThe brief says "Able to overwrite files".\n'
    ctx, verdict = _gate(design, brief="The tool is unable to overwrite files.")
    assert verdict == "reloop"
    assert "does not appear verbatim in the brief" in verdict.reason


def test_a_missing_high_risk_pitfall_still_blocks():
    ctx, verdict = _gate(_make_design(), pitfalls=[
        {"id": "PIT-001", "risk": "high", "text": "the cache key ignores the base"}])
    assert verdict == "reloop"
    assert "PIT-001" in verdict.reason


# ── what is advisory ──────────────────────────────────────────────────────────

def test_a_quote_differing_only_in_punctuation_and_case_is_advisory():
    design = _make_design() + '\nThe brief says "add the **flag** to the command".\n'
    ctx, verdict = _gate(design, brief="Add the flag to the command.")
    assert verdict == "proceed"
    assert "differs from the brief only in punctuation" in ctx.facts["design_review_advisory"]
    assert ctx.facts["design_review_status"] == "Design review: PASS, 1 advisory"


def test_paperwork_items_are_listed_and_cost_no_round():
    """b35a's gate read 'FAIL, 33 unresolved' on items like these; now it reads PASS."""
    design = (_make_design(failure_trace=False)
              .replace("# Fix the thing", "# scale_test")
              .replace("## Rule and sites\n\n- `tool.py:1` — **[changed]**",
                       "## Rule and sites\n\n- `tool.py:1` — **[changed]**\n- `other.py:3` — the caller"))
    ctx, verdict = _gate(design)
    assert verdict == "proceed"
    advisory = ctx.facts["design_review_advisory"]
    assert "Failure trace" in advisory
    assert "title is the run name" in advisory
    assert "other.py:3" in advisory
    assert ctx.facts["design_review_status"] == "Design review: PASS, 3 advisory"
    assert "## Advisory" in ctx.facts["design_review_findings"]


def test_an_unmentioned_medium_pitfall_is_not_called_missing():
    ctx, verdict = _gate(_make_design(), pitfalls=[
        {"id": "PIT-002", "risk": "medium", "text": "a slower path on large inputs"}])
    assert verdict == "proceed"
    assert "PIT-002 (medium): not mentioned" in ctx.facts["pitfall_coverage"]
    assert "MISSING" not in ctx.facts["pitfall_coverage"]


def test_the_review_agents_notes_reach_the_person():
    review = ("The design meets the brief.\n\n## Notes\n"
              "- docs/cli.md describes the flag; the Docs section leaves it out\n\n"
              "VERDICT: PASS")
    ctx, verdict = _gate(_make_design(), review=review)
    assert verdict == "proceed"
    assert ("review note: docs/cli.md describes the flag; the Docs section leaves it out"
            in ctx.facts["design_review_advisory"])
    assert ctx.facts["design_review_status"] == "Design review: PASS, 1 advisory"


def test_the_fail_count_counts_only_blocking_findings():
    # The findings text a person sees can carry other bulleted lines; the count comes
    # from the blocking list alone.
    facts = {"design_review_attempts_used": 1,
             "design_review_findings": "- a blocking finding\n- a listed note\n- another note",
             "design_review_blocking": "- a blocking finding",
             "design_review_advisory": "- a note\n- another note"}
    assert design_review_status(facts) == (
        "Design review: FAIL, 1 unresolved, 2 advisory\n- a blocking finding")


# ── rounds ────────────────────────────────────────────────────────────────────

def test_a_scoped_change_gets_one_design_review_round():
    design = _make_design(ask_mapping=False)
    full_ctx, full_verdict = _gate(design, scope="full")
    scoped_ctx, scoped_verdict = _gate(design, scope="scoped")
    assert full_verdict == "reloop"
    # The scoped design goes to the person after one round, with its finding shown.
    assert scoped_verdict == "proceed"
    assert scoped_ctx.facts["design_review_status"].startswith("Design review: FAIL, 1 unresolved")
    assert "Ask mapping" in scoped_ctx.facts["design_review_findings"]


# ── the prompts scale with the change ─────────────────────────────────────────

def test_the_design_phase_prompts_scale_with_the_change():
    nodes = DesignMode().build().nodes
    assert "one focused question for a scoped change" in nodes["research_director"].prompt_template
    assert "{{research_scope}}" in nodes["pitfalls_researcher"].prompt_template
    assert "list at most five" in nodes["pitfalls_researcher"].prompt_template
    # A scoped change has no perspectives step: route_strategy sends it past
    # strategy_director (test_design_short_path.py).
    graph = DesignMode().build()
    assert graph.out_edges("route_strategy", condition="scoped")[0].target == "synthesize_strategy"


def test_the_design_does_not_account_for_every_finding():
    nodes = DesignMode().build().nodes
    assert "Carry every finding id forward" not in nodes["synthesize_strategy"].prompt_template
    design_doc = nodes["design_doc"].prompt_template
    assert "neither addressed nor set aside is blocking" not in design_doc
    assert "where it changes a decision" in design_doc
    review = nodes["design_review"].prompt_template
    assert "## Notes" in review
    assert "Notes do not send the design back" in review
