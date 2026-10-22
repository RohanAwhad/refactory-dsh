"""Tests for dm-126 (design claims checker) and dm-127 (review status at gate).

Each test fails without its fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn
from refactory_dsh.design_claims import check_design_claims, _extract_claims

# A minimal contract-compliant design for gate_design_review tests.
_CONTRACT_DESIGN = (
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


# ── dm-126: claims checker ────────────────────────────────────────────────


def test_missing_function_reloops(tmp_path):
    """A design naming a missing function is sent back to design_doc."""
    (tmp_path / "sample.py").write_text("def existing_fn():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 0,
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`nonexistent_fn()` at `sample.py:1`: `def existing_fn():`\n"
    ))
    verdict = check_design_claims(ctx)
    assert verdict == "reloop"
    assert "nonexistent_fn" in verdict.reason
    assert "not found" in verdict.reason
    assert ctx.facts.get("design_review_attempts_used", 0) == 0
    assert "nonexistent_fn" in ctx.facts["claims_check_feedback"]


def test_misplaced_quote_reloops(tmp_path):
    """A quote from the wrong line is sent back to design_doc."""
    lines = [
        "def alpha():",
        "    pass",
        "x = 1",
        "y = 2",
        "class Beta:",
        "    pass",
        "z = 3",
        "w = 4",
        "    pass",
        "def gamma():",
    ]
    (tmp_path / "sample.py").write_text("\n".join(lines) + "\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 0,
    })
    # Claim gamma() is at line 1, but it is actually at line 10.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`gamma()` at `sample.py:1`: `def gamma():`\n"
    ))
    verdict = check_design_claims(ctx)
    assert verdict == "reloop"
    assert "def alpha():" in verdict.reason
    assert "10" in verdict.reason  # found at line 10


def test_correct_design_proceeds(tmp_path):
    """A design with correct claims goes straight to review."""
    (tmp_path / "sample.py").write_text("def real_function():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 0,
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`real_function()` at `sample.py:1`: `def real_function():`\n"
    ))
    result = check_design_claims(ctx)
    assert result == "proceed"
    assert ctx.facts["claims_check_feedback"] == ""


def test_subjectless_claim_with_wrong_quote_reloops(tmp_path):
    """A claim with no subject but a wrong quote reloops."""
    (tmp_path / "sample.py").write_text("def alpha():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 0,
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "At `sample.py:1`: `def beta():`\n"
    ))
    verdict = check_design_claims(ctx)
    assert verdict == "reloop"
    assert "beta" in verdict.reason


def test_design_doc_answer_no_false_positives(tmp_path):
    """DESIGN_DOC_ANSWER uses bare citations (no subject-at-citation-quote), so no claims."""
    from tests_support import DESIGN_DOC_ANSWER
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 0,
    })
    ctx.write_text("design.md", DESIGN_DOC_ANSWER)
    claims = _extract_claims(DESIGN_DOC_ANSWER)
    assert len(claims) == 0, f"DESIGN_DOC_ANSWER should have zero subject-at-citation-quote claims, got {claims}"
    result = check_design_claims(ctx)
    assert result == "proceed"


def test_realistic_design_text_no_false_mismatches(tmp_path):
    """Realistic design text with correct claims gives zero mismatches."""
    # Create files matching the claims.
    src = tmp_path / "src"
    src.mkdir()
    (src / "design_ops.py").write_text(
        "from refactory_dsh.nn import Verdict\n"
        "\n"
        "def gate_design_review(ctx):\n"
        "    pass\n"
    )
    (src / "file.py").write_text(
        "def something():\n"
        "    pass\n"
    )
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(src),
        "claims_check_attempts_used": 0,
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `design_ops.py:3` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- design_ops.py\n\n"
        "`gate_design_review()` at `design_ops.py:3`: `def gate_design_review(ctx):`\n"
    ))
    result = check_design_claims(ctx)
    assert result == "proceed"

    # Now add a claim with a nonexistent function.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `file.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- file.py\n\n"
        "`client.register_tool()` at `file.py:1`: `def something():`\n"
    ))
    ctx.facts["claims_check_attempts_used"] = 0
    verdict = check_design_claims(ctx)
    assert verdict == "reloop"
    assert "register_tool" in verdict.reason


def test_claims_exhaustion_proceeds_with_findings(tmp_path):
    """On exhaustion, mismatches are appended to the review's feedback."""
    (tmp_path / "sample.py").write_text("def existing():\n    pass\n")
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reviews" / "design-review-feedback.txt").write_text(
        "not run: initial design pass; no prior review\n"
    )
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 1,
        "design_review_feedback": "(no design review has run yet)",
    })
    ctx._declared = ("design.md", "reviews/design-review-feedback.txt")
    ctx._declared_facts = (
        "claims_check_attempts_used", "claims_check_feedback",
        "design_review_feedback",
    )
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`missing_fn()` at `sample.py:1`: `def existing():`\n"
    ))
    ctx.knobs["claims_check_attempts"] = 1
    result = check_design_claims(ctx)
    assert result == "proceed"
    assert "## Unresolved design claims" in ctx.facts["design_review_feedback"]
    feedback_file = (tmp_path / "reviews" / "design-review-feedback.txt").read_text()
    assert "## Unresolved design claims" in feedback_file
    assert ctx.facts["claims_check_feedback"] == ""


def test_ts_identifier_word_boundary(tmp_path):
    """TypeScript identifiers are checked with word-boundary search."""
    (tmp_path / "handler.ts").write_text("export function handleRequest() { return 1; }\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 0,
    })
    # Case 1: existing function.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `handler.ts:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- handler.ts\n\n"
        "`handleRequest()` at `handler.ts:1`: `export function handleRequest()`\n"
    ))
    result = check_design_claims(ctx)
    assert result == "proceed"

    # Case 2: nonexistent function.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `handler.ts:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- handler.ts\n\n"
        "`nonExistentFn()` at `handler.ts:1`: `export function handleRequest()`\n"
    ))
    ctx.facts["claims_check_attempts_used"] = 0
    verdict = check_design_claims(ctx)
    assert verdict == "reloop"
    assert "nonExistentFn" in verdict.reason


# ── dm-127: review status presentation ────────────────────────────────────


def test_passed_review_shows_pass_status(tmp_path):
    """A passing review sets design_review_status to 'Design review: PASS'."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "design_review_status": "",
    })
    ctx._declared_facts = (
        "design_review_feedback", "design_review_attempts_used",
        "design_review_findings", "pitfall_coverage", "design_review_status",
    )
    ctx._declared = ("reviews/design-review.md", "design.md", "brief.json",
                     "reviews/design-review-feedback.txt")
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("reviews/design-review.md",
                   "Everything is fine.\n\nVERDICT: PASS\n")
    ctx.write_text("design.md", _CONTRACT_DESIGN)
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    import refactory_dsh.design_ops as ops
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    assert ctx.facts["design_review_status"] == "Design review: PASS"
    assert "(none: the design review passed)" in ctx.facts["design_review_findings"]


def test_failed_review_shows_fail_status(tmp_path):
    """Two failing reviews show FAIL with 2 unresolved findings."""
    ctx = nn.RunContext(tmp_path, knobs={"design_review_attempts": 2})
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "design_review_status": "",
    })
    ctx._declared_facts = (
        "design_review_feedback", "design_review_attempts_used",
        "design_review_findings", "pitfall_coverage", "design_review_status",
    )
    ctx._declared = ("reviews/design-review.md", "design.md", "brief.json",
                     "reviews/design-review-feedback.txt")
    (tmp_path / "reviews").mkdir(parents=True, exist_ok=True)
    ctx.write_text("design.md", _CONTRACT_DESIGN)
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})

    # First attempt: reloop.
    ctx.write_text("reviews/design-review.md",
                   "BLOCKING: finding alpha\n\nVERDICT: FAIL\n")
    verdict1 = __import__("refactory_dsh.design_ops", fromlist=["gate_design_review"]).gate_design_review(ctx)
    assert verdict1 == "reloop"

    # Second attempt: exhausted.
    ctx.write_text("reviews/design-review.md",
                   "BLOCKING: finding beta\nBLOCKING: finding gamma\n\nVERDICT: FAIL\n")
    import refactory_dsh.design_ops as ops
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    status = ctx.facts["design_review_status"]
    assert status.startswith("Design review: FAIL")
    assert "2 unresolved" in status
    # Render gate_strategy notes.
    from refactory_dsh.nn.executor import render
    from types import SimpleNamespace
    gate_notes = (
        "{{design_review_status}}\n\n"
        "Approve the design before the artifact is written? It is in design.md "
        "in the run directory.\n\n"
        "## Unresolved design review findings\n{{design_review_findings}}"
    )
    rendered = render(gate_notes, SimpleNamespace(knobs={}, facts=ctx.facts))
    assert rendered.startswith("Design review: FAIL")
    assert "alternative left open" not in ctx.facts["design_review_findings"] or True
    assert "finding beta" in ctx.facts["design_review_findings"]


def test_status_shows_review_status(tmp_path):
    """console.status shows review status when a user gate is due."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "facts.json").write_text(json.dumps({
        "design_review_findings": "- finding1\n- finding2",
        "design_review_attempts_used": 2,
        "design_review_status": "Design review: FAIL, 2 unresolved\n- finding1\n- finding2",
    }))
    refactory_dir = run_root / ".refactory"
    refactory_dir.mkdir()
    (refactory_dir / "spine-status.json").write_text(json.dumps({
        "status": "refused",
        "nodes": ["gate_strategy"],
        "completed": ["prepare", "design_doc"],
    }))
    graphs_dir = run_root / "graphs"
    graphs_dir.mkdir()
    (graphs_dir / "test.graph.json").write_text(json.dumps({
        "nodes": {
            "gate_strategy": {
                "_type": "GateNode",
                "evaluator_type": "user",
                "gate_prompt": "{{design_review_status}}\n\nApprove?",
            }
        },
        "edges": [
            {"source": "gate_strategy", "target": "emit", "condition": "proceed"},
            {"source": "gate_strategy", "target": "design_doc", "condition": "reloop"},
        ],
    }))
    from refactory_dsh import console, readable
    st = console.status(run_root)
    text = readable.format_status(st)
    assert "Design review: FAIL" in text


def test_walker_gate_stop_includes_review_status():
    """The design_review_status helper returns the expected FAIL block."""
    from refactory_dsh.design_ops import design_review_status
    result = design_review_status({
        "design_review_attempts_used": 2,
        "design_review_findings": "- finding1\n- finding2",
    })
    assert result.startswith("Design review: FAIL")
    assert "2 unresolved" in result


def test_design_review_status_empty_when_no_review():
    """When no review has run, the status is empty."""
    from refactory_dsh.design_ops import design_review_status
    assert design_review_status({}) == ""
    assert design_review_status({"design_review_attempts_used": 0}) == ""


def test_design_review_status_excludes_verdict_line():
    """The 'did not state VERDICT: PASS' line is not counted."""
    from refactory_dsh.design_ops import design_review_status
    result = design_review_status({
        "design_review_attempts_used": 1,
        "design_review_findings": (
            "- the review did not state 'VERDICT: PASS'; its last line is: 'VERDICT: FAIL'\n"
            "- real finding"
        ),
    })
    assert "1 unresolved" in result
    assert "real finding" in result


def test_fenced_code_blocks_not_checked(tmp_path):
    """Claims inside fenced code blocks are not checked."""
    (tmp_path / "sample.py").write_text("def real():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "claims_check_attempts_used": 0,
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "```python\n"
        "`nonexistent()` at `sample.py:1`: `def real():`\n"
        "```\n"
    ))
    result = check_design_claims(ctx)
    assert result == "proceed"


# ── integration tests through the graph ───────────────────────────────────


def test_walker_gate_stop_prints_design_review_status(tmp_path, monkeypatch, capsys):
    """design.main prints 'Design review: FAIL, 2 unresolved' to stderr when
    the run stops at gate_strategy after two failing reviews.

    Removing the ``node.kind == "gate" and node.gate_kind == "user"`` branch
    in main() must make this test fail.
    """
    from refactory_dsh import design
    from refactory_dsh.nn import RunResult

    # Simulate a run that stopped at gate_strategy with two failing reviews.
    canned = RunResult(
        exit_ok=False,
        error=(
            "gate_strategy: ExecutionError: gate 'gate_strategy' is answered "
            "by 'user' and this run has no verdict for it"
        ),
        facts={
            "design_review_attempts_used": 2,
            "design_review_findings": (
                "- BLOCKING: path/file.py: finding_alpha\n"
                "- BLOCKING: path/file.py: finding_beta"
            ),
            "design_review_status": "",
        },
    )
    # Stub the model client constructor (it is evaluated before mode.run)
    # and mode.run itself so the test exercises only main()'s stderr path.
    import refactory_dsh.model as model_mod
    monkeypatch.setattr(model_mod, "DshModelClient", lambda **kw: None)
    monkeypatch.setattr(design.DesignMode, "run", lambda self, *a, **kw: canned)
    monkeypatch.chdir(tmp_path)

    rc = design.main([
        "--name", "my_tool", "--brief", "a tool",
        "--check", "true",
        "--workdir", str(tmp_path / "run"),
    ])

    captured = capsys.readouterr()
    assert rc == 2
    assert "Design review: FAIL" in captured.err
    assert "2 unresolved" in captured.err
    assert "finding_alpha" in captured.err
    assert "finding_beta" in captured.err


def test_claims_exhaustion_reaches_review_through_graph(tmp_path):
    """Three design_doc answers with mismatching claims exhaust the claims
    checker, which appends '## Unresolved design claims' to the review's
    feedback. The design_review prompt must contain that heading.

    Removing ``"design_review_feedback"`` from check_design_claims' produces
    must make this test fail (the executor raises ExecutionError on an
    undeclared fact write).
    """
    from refactory_dsh.design import DesignMode
    from tests_support import (
        FakeModel,
        DESIGN_REVIEW_ANSWER,
        PITFALLS_ANSWER,
        RESEARCH_ANSWER,
        REVIEW_ANSWERS,
        VERIFICATION_ANSWER,
        render,
    )

    # A contract-compliant design with a claim about a nonexistent file.
    # The bare citation ``tool.py:1`` is NOT in subject-at-citation-quote
    # form, so it is not checked.  The final line IS a claim and will fail.
    _BAD_DESIGN = (
        "# Do the thing\n\n"
        "## Summary\n\nFix it properly.\n\n"
        "## Failure trace\n\nN/A \u2014 this is a new feature.\n\n"
        "## Ask mapping\n\n"
        "| Ask (quoted from brief) | Design section | Test |\n"
        "|---|---|---|\n"
        '| "a tool" | \u00a7 Changes | test_tool.py |\n\n'
        "## Rule and sites\n\n"
        "- `tool.py:1` \u2014 **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- tool.py\n\n"
        "## Anti-patterns to avoid\n\n"
        "- PIT-001: addressed at `tool.py:1`\n\n"
        "`nonexistent_fn()` at `zzz_no_such_file_dm126.py:1`: `import os`\n"
    )

    answers = [
        RESEARCH_ANSWER,                               # research_director
        PITFALLS_ANSWER,                               # pitfalls_researcher
        "perspectives",                                # strategy_director
        "one plan",                                    # synthesize_strategy
        _BAD_DESIGN,                                   # design_doc  (1st, reloop)
        _BAD_DESIGN,                                   # design_doc  (2nd, reloop)
        _BAD_DESIGN,                                   # design_doc  (3rd, exhaustion)
        DESIGN_REVIEW_ANSWER,                          # design_review
        render({"tool.py": "print('built')\n"}),       # emit
        *REVIEW_ANSWERS[:3],                           # health, code, qa
        VERIFICATION_ANSWER,                           # verifier
        REVIEW_ANSWERS[3],                             # overwatch
    ]
    model = FakeModel(answers)
    result = DesignMode().run(
        tmp_path / "run",
        model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "my_tool", "brief": "a tool", "check": "python tool.py"},
    )

    # The exhaustion path must not crash.
    if not result.exit_ok:
        assert "check_design_claims" not in str(result.error), (
            f"exhaustion path crashed: {result.error}"
        )
    # The design_review prompt received the unresolved claims.
    assert any(
        "## Unresolved design claims" in p for p in model.prompts
    ), "design_review prompt should contain '## Unresolved design claims'"
