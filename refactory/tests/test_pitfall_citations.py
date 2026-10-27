"""The approval card quotes where the design cites each pitfall, and never judges it (dm-262).

The card called pitfalls unaddressed that the wordcount and b46a designs had answered,
and a word-counting fix then called pitfalls addressed that a design had explicitly left
out. The user's decision (2026-10-26): "the card quotes each pitfall's cited line (no
handled verdict from parsing)". Each pitfall is shown with up to two of the lines that
cite it, under their headings, or as "not mentioned" when the design never cites it.
The probes are the independent review's; the fixtures are the two runs' own designs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn, pitfalls as pitfalls_mod

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HEAD = "# Count lines like wc\n\n## Summary\n\nCounts lines.\n\n## Changes\n\n- `wc.py` — the counter\n\n"
PITS = [{"id": f"PIT-00{n}", "risk": "high"} for n in range(1, 6)]
VERDICTS = ("addressed", "MISSING", "not addressed", "set aside", "not checked", "PASS", "FAIL")

#: The review's probe lines: (body, the line the card must quote for PIT-001, its heading).
PROBES = {
    "list of ids in prose": (
        "## Anti-patterns to avoid\n\nThis design covers the risks listed in PIT-001, PIT-002 and "
        "PIT-003 from the pitfalls step.\n",
        "This design covers the risks listed in PIT-001, PIT-002 and PIT-003 from the pitfalls step.",
        "Anti-patterns to avoid"),
    "bare id list with label": (
        "## Anti-patterns to avoid\n\n- Pitfalls considered: PIT-001, PIT-002, PIT-003.\n",
        "Pitfalls considered: PIT-001, PIT-002, PIT-003.", "Anti-patterns to avoid"),
    "explicitly not addressed": (
        "## Anti-patterns to avoid\n\n- PIT-001: this design does not address it; it is out of scope.\n",
        "PIT-001: this design does not address it; it is out of scope.", "Anti-patterns to avoid"),
    "will NOT address": (
        "## Anti-patterns to avoid\n\n- PIT-001: We will NOT address this in this change at all.\n",
        "PIT-001: We will NOT address this in this change at all.", "Anti-patterns to avoid"),
    "vacuous five words": (
        "## Anti-patterns to avoid\n\n- PIT-001: this is handled elsewhere later.\n",
        "PIT-001: this is handled elsewhere later.", "Anti-patterns to avoid"),
    "restated risk, no remedy": (
        "## Anti-patterns to avoid\n\n- PIT-001 — splitlines drops the final line of a file "
        "without a newline.\n",
        "PIT-001 — splitlines drops the final line of a file without a newline.",
        "Anti-patterns to avoid"),
    "risk table row": (
        "## Risks\n\n| id | risk | what can go wrong |\n|---|---|---|\n"
        "| PIT-001 | high | the counter disagrees with wc on a missing final newline |\n\n"
        "## Anti-patterns to avoid\n\n- none\n",
        "| PIT-001 | high | the counter disagrees with wc on a missing final newline |", "Risks"),
    "open question": (
        "## Open questions\n\n- Should we handle PIT-001 here or leave it to a follow-up later?\n\n"
        "## Anti-patterns to avoid\n\n- none\n",
        "Should we handle PIT-001 here or leave it to a follow-up later?", "Open questions"),
    "follow-up deferral": (
        "## Follow-ups\n\n- PIT-001 is deferred to a later pull request by someone.\n\n"
        "## Anti-patterns to avoid\n\n- none\n",
        "PIT-001 is deferred to a later pull request by someone.", "Follow-ups"),
    "summary heading variant": (
        "## Summary of the change\n\nPIT-001 matters here, because a file without a final newline "
        "loses a line.\n\n## Anti-patterns to avoid\n\n- none\n",
        "PIT-001 matters here, because a file without a final newline loses a line.",
        "Summary of the change"),
    "H1 Summary": (
        "# Summary\n\nPIT-001 matters here, because a file without a final newline loses a line.\n\n"
        "## Anti-patterns to avoid\n\n- none\n",
        "PIT-001 matters here, because a file without a final newline loses a line.", "Summary"),
    "multi-line bullet: remedy on next line": (
        "## Anti-patterns to avoid\n\n- **PIT-001 (splitlines trap)**\n"
        "  Line counting uses `text.count('\\n')`, never `splitlines()`, so a file without a final "
        "newline still counts.\n",
        "PIT-001 (splitlines trap) Line counting uses `text.count('\\n')`, never `splitlines()`, so a "
        "file without a final newline still counts.", "Anti-patterns to avoid"),
    "heading with title, remedy in body": (
        "## Anti-patterns to avoid\n\n### PIT-001 (splitlines trap)\n\n"
        "Line counting uses `text.count('\\n')`, so a file without a final newline counts like wc.\n",
        "PIT-001 (splitlines trap) Line counting uses `text.count('\\n')`, so a file without a final "
        "newline counts like wc.", "Anti-patterns to avoid"),
    "short heading with id": (
        "## Anti-patterns to avoid\n\n### PIT-001\n\nCounted with count, never splitlines, matching "
        "wc on every input.\n",
        "PIT-001 Counted with count, never splitlines, matching wc on every input.",
        "Anti-patterns to avoid"),
    "terse remedy (4 words)": (
        "## Anti-patterns to avoid\n\n- PIT-001: use `count('\\n')`, not `splitlines()`.\n",
        "PIT-001: use `count('\\n')`, not `splitlines()`.", "Anti-patterns to avoid"),
    "remedy pointing at a file without line": (
        "## Anti-patterns to avoid\n\n- PIT-001: guarded in `wc.py`.\n",
        "PIT-001: guarded in `wc.py`.", "Anti-patterns to avoid"),
    "table row with remedy": (
        "## Anti-patterns to avoid\n\n| pitfall | how |\n|---|---|\n"
        "| PIT-001 | count newlines, never splitlines |\n",
        "| PIT-001 | count newlines, never splitlines |", "Anti-patterns to avoid"),
}


def _gate(tmp_path: Path, design: str, pitfalls: list[dict]) -> nn.RunContext:
    import refactory_dsh.design_ops as ops

    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path), "has_pitfalls_step": True})
    ctx.write_text("design.md", design)
    ctx.write_json("pitfalls.json", pitfalls)
    assert ops.gate_design_review(ctx) == "proceed"
    return ctx


def _card_pitfalls(ctx: nn.RunContext) -> list[str]:
    return ctx.facts["design_card"].split("\n\n")[-1].splitlines()[1:]


def test_the_probes_are_all_here():
    assert len(PROBES) == 17


@pytest.mark.parametrize("name", list(PROBES))
def test_the_card_quotes_the_cited_line_and_never_a_verdict(tmp_path, name):
    body, quoted, heading = PROBES[name]
    ctx = _gate(tmp_path, HEAD + body, PITS[:1])
    line = f'- PIT-001 (high): § {heading}: "{quoted}"'
    assert _card_pitfalls(ctx) == [line]
    assert ctx.facts["pitfall_coverage"] == f"Pitfall citations:\n  {line[2:]}"
    card = ctx.facts["design_card"] + ctx.facts["design_review_advisory"]
    for verdict in VERDICTS[:5]:
        assert verdict not in ctx.facts["pitfall_coverage"], verdict
    assert "pitfall" not in ctx.facts["design_review_advisory"]
    assert "Pitfalls: 1" in card


def test_an_id_the_design_never_cites_is_not_mentioned(tmp_path):
    ctx = _gate(tmp_path, HEAD + PROBES["will NOT address"][0], PITS[:2])
    assert _card_pitfalls(ctx) == [
        '- PIT-001 (high): § Anti-patterns to avoid: "PIT-001: We will NOT address this in '
        'this change at all."',
        "- PIT-002 (high): not mentioned"]
    assert [line for line in ctx.facts["design_review_advisory"].splitlines() if "pitfall" in line] == [
        "- high-risk pitfall(s) the design does not mention: PIT-002"]


@pytest.mark.parametrize("run", ["wordcount", "b46a"])
def test_the_runs_designs_have_every_pitfall_quoted(tmp_path, run):
    design = (FIXTURES / f"{run}_design.md").read_text()
    pitfalls = json.loads((FIXTURES / f"{run}_pitfalls.json").read_text())
    ctx = _gate(tmp_path, design, pitfalls)
    lines = _card_pitfalls(ctx)
    assert [line.split(" ", 2)[1] for line in lines] == [p["id"] for p in pitfalls]
    assert not any(line.endswith("not mentioned") for line in lines)
    assert "pitfall" not in ctx.facts["design_review_advisory"]
    for verdict in VERDICTS[:5]:
        assert verdict not in ctx.facts["pitfall_coverage"], verdict


def test_the_wordcount_card_shows_where_each_pitfall_is_cited(tmp_path):
    design = (FIXTURES / "wordcount_design.md").read_text()
    pitfalls = json.loads((FIXTURES / "wordcount_pitfalls.json").read_text())
    lines = _card_pitfalls(_gate(tmp_path, design, pitfalls))
    # PIT-001: its Anti-patterns entry first, then the Tests line (long, so quoted around the id).
    assert lines[0].startswith('- PIT-001 (high): \u00a7 Anti-patterns to avoid: "PIT-001 (file co-location): '
                               'Both files placed')
    assert '; \u00a7 Tests: "\u2026' in lines[0] and lines[0].endswith('repository root (PIT-001)."')
    # PIT-005 is cited only in its Anti-patterns entry.
    assert lines[4] == ('- PIT-005 (medium): § Anti-patterns to avoid: "PIT-005 (no test '
                        'framework): No external dependencies. Check command is sole automated '
                        'gate. Script exits 0 on success — see wordcount.py specification."')
