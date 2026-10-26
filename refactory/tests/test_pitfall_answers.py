"""A pitfall the design answers is never reported as missing (dm-262).

The approval card called pitfalls unaddressed that the design had answered: the
wordcount design's ``## Anti-patterns to avoid`` covered PIT-001/002/003 in sentences
pointing at its own sections, and b46a's covered PIT-002/004 with the change and the
test that pins it, but the check only accepted a ``path:line`` site or a § heading.
The fixtures are those two designs and their pitfalls, as the runs wrote them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn, pitfalls as pitfalls_mod

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(run: str) -> tuple[str, list[dict]]:
    design = (FIXTURES / f"{run}_design.md").read_text()
    return design, json.loads((FIXTURES / f"{run}_pitfalls.json").read_text())


def _gate(tmp_path: Path, design: str, pitfalls: list[dict]) -> nn.RunContext:
    import refactory_dsh.design_ops as ops

    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path), "has_pitfalls_step": True})
    ctx.write_text("design.md", design)
    ctx.write_json("pitfalls.json", pitfalls)
    assert ops.gate_design_review(ctx) == "proceed"
    return ctx


@pytest.mark.parametrize("run", ["wordcount", "b46a"])
def test_the_runs_designs_have_every_pitfall_addressed(tmp_path, run):
    design, pitfalls = _fixture(run)
    addressed, set_aside, missing = pitfalls_mod.pitfall_coverage(design, pitfalls)
    assert missing == [] and set_aside == []
    assert addressed == [p["id"] for p in pitfalls]
    ctx = _gate(tmp_path, design, pitfalls)
    assert "not addressed" not in ctx.facts["design_review_advisory"]
    assert "MISSING" not in ctx.facts["pitfall_coverage"]
    assert "not mentioned" not in ctx.facts["pitfall_coverage"]


def test_the_wordcount_entries_count_by_their_remedy_alone(tmp_path):
    """Its Anti-patterns entries name no site and no § heading: each says what the design
    does. Stripped to the id and its title, they are missing again."""
    design, pitfalls = _fixture("wordcount")
    head, anti = design.split("## Anti-patterns to avoid", 1)
    # Leave each id only in its Anti-patterns entry, so the entry alone decides.
    head = head.replace("(PIT-001)", "").replace("(PIT-003)", "").replace(
        "(PIT-004)", "").replace(", avoids PIT-002", "")
    bare = "\n".join(line.split(":**")[0] + ":**" if line.startswith("- **PIT-") else line
                     for line in anti.splitlines())
    _, _, missing = pitfalls_mod.pitfall_coverage(
        head + "## Anti-patterns to avoid" + bare, pitfalls)
    assert missing == ["PIT-001", "PIT-002", "PIT-003", "PIT-004", "PIT-005"]
    addressed, _, missing = pitfalls_mod.pitfall_coverage(
        head + "## Anti-patterns to avoid" + anti, pitfalls)
    assert missing == [] and addressed == [p["id"] for p in pitfalls]


def test_the_b46a_entries_count_by_the_change_and_the_test_that_pins_it():
    """PIT-002 and PIT-004 cite no site in their entries: the guard's three values and the
    test that pins it, the fields the revision covers and the test for it."""
    design, pitfalls = _fixture("b46a")
    anti = design.split("## Anti-patterns to avoid", 1)[1].split("\n## ", 1)[0]
    only = [p for p in pitfalls if p["id"] in ("PIT-002", "PIT-004")]
    entries = "\n".join(line for line in anti.splitlines() if "PIT-002" in line or "PIT-004" in line)
    alone = "# Fix run controls\n\n## Changes\n\n- `graph_control.py`\n\n## Anti-patterns to avoid\n\n"
    assert pitfalls_mod.pitfall_coverage(alone + entries + "\n", only) == (
        ["PIT-002", "PIT-004"], [], [])


def test_a_pitfall_answered_outside_the_anti_patterns_section_counts():
    """A design answers a pitfall where it makes the decision: a Changes bullet, a test."""
    design = (
        "# Count lines like wc\n\n## Summary\n\nPIT-003 matters here, because a file "
        "without a final newline loses its last line.\n\n"
        "## Changes\n\n- `wc.py` — line counting uses `count('\\n')`, never "
        "`splitlines()`, so it agrees with wc (avoids PIT-002)\n\n"
        "### PIT-005: one open per file, closed by a with block\n\n"
        "## Tests\n\n- `tests/test_wc.py::test_final_newline` — a file without a final "
        "newline counts one line fewer, which pins PIT-004\n\n"
        "## Anti-patterns to avoid\n\n- PIT-001: [set-aside: the brief names no other path]\n"
    )
    pits = [{"id": f"PIT-00{n}", "risk": "high"} for n in range(1, 6)]
    addressed, set_aside, missing = pitfalls_mod.pitfall_coverage(design, pits)
    assert addressed == ["PIT-002", "PIT-004", "PIT-005"]
    assert set_aside == ["PIT-001"]
    # The Summary states the problem; naming a pitfall there answers nothing.
    assert missing == ["PIT-003"]


@pytest.mark.parametrize("entry, remedy", [
    ("- **PIT-001 (file co-location):** Both files placed at repository root.", True),
    ("- **PIT-001** (weakened guard): The guard is widened to exactly three values.", True),
    ("- PIT-001: guarded in tool.py", False),
    ("- PIT-001: handled by `retry()` and `self.cache`", False),
    ("- **PIT-001** (a long title that says a great deal about it)", False),
    ("- PIT-001: see Changes rule 4", False),
])
def test_a_remedy_is_a_sentence_of_its_own(entry, remedy):
    assert pitfalls_mod._is_remedy(entry) is remedy
