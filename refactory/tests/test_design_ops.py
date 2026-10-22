"""Tests for the steer → pick_gate_corrections → apply_corrections pipeline.

Each test that pins a fix fails without its corresponding change:
- T1: one-call verdict+correction through ``_steer`` is collected.
- T3b: two-call form (verdict first, note second) still works.
- T4: process-scoped, CEO-source, info-kind and another-gate corrections are
  not collected.
- T5: the card's command (no ``--kind``) auto-derives constraint via
  ``NOTE_KINDS`` and is collected.
- Bridge: bridge ``answer`` with feedback is collected (passes on main).
- Empty note: an empty ``--note`` in one call records neither verdict nor note.
- Two corrections: two correction notes before resume are both collected.
- Multi-line: a correction containing a ``## `` heading survives whole.
- Escaped boundary: a ``\\## [`` line reaches apply_corrections unescaped.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────


class _Ctx:
    """Minimal stand-in for the gate evaluator's ctx argument."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.facts: dict[str, str] = {}

    def set_fact(self, name: str, value: str) -> None:
        self.facts[name] = value


def _setup_run(tmp_path: Path) -> Path:
    """Create a minimal run directory with seeded ledger and gate visit."""
    run = tmp_path / "run"
    run.mkdir()
    # Create the files gate_strategy reads so the review hash is stable.
    (run / "design.md").write_text("# placeholder\n")
    (run / "delivery.md").write_text("delivery placeholder\n")
    from refactory_dsh.steering import Ledger
    ledger = Ledger.seed(run, "Build a tool.")
    ledger.visit("gate_strategy", ["design.md", "delivery.md"])
    return run


# ── T1: single steer call with verdict + correction ─────────────────────


def test_single_steer_call_correction_collected(tmp_path):
    """One _steer call with verdict and correction collects the correction.

    Fails without the reorder in _steer: the note lands before the verdict,
    so pick_gate_corrections never sees it after the proceed entry.
    """
    from refactory_dsh.console import _steer
    from refactory_dsh.design_ops import pick_gate_corrections

    run = _setup_run(tmp_path)
    _steer(run, verdicts=["gate_strategy=proceed"], kind="correction", note="X")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    assert "X" in ctx.facts["gate_corrections"]
    assert "1" in ctx.facts["design_corrections_applied"]


# ── T3b: two-call form ──────────────────────────────────────────────────


def test_two_call_correction_collected(tmp_path):
    """A verdict in one call, then a correction in a later call, is collected.

    Passes on main: this pins the existing two-call behaviour.
    """
    from refactory_dsh.console import _steer
    from refactory_dsh.design_ops import pick_gate_corrections

    run = _setup_run(tmp_path)
    # First call: verdict only.
    _steer(run, verdicts=["gate_strategy=proceed"])
    # Second call: correction only.
    _steer(run, kind="constraint", note="use websockets")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    assert "use websockets" in ctx.facts["gate_corrections"]
    assert "1" in ctx.facts["design_corrections_applied"]


# ── T4: negative cases ──────────────────────────────────────────────────


def test_non_gate_corrections_not_collected(tmp_path):
    """Process-scoped, CEO-source, info-kind, and another-gate corrections
    are not collected.

    Sub-cases (a), (b), (c) pass on main (existing filtering).
    Sub-case (d) — another gate — catches a regression and fails on main.
    """
    from refactory_dsh.design_ops import pick_gate_corrections
    from refactory_dsh.steering import Ledger

    # (a) Process-scoped constraint after proceed verdict.
    run_a = tmp_path / "run_a"
    run_a.mkdir()
    (run_a / "design.md").write_text("# placeholder\n")
    (run_a / "delivery.md").write_text("delivery\n")
    ledger_a = Ledger.seed(run_a, "Build a tool.")
    ledger_a.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger_a.verdict("gate_strategy", "proceed", approval=True)
    ledger_a.note("constraint", "process thing", scope="process")
    ctx_a = _Ctx(run_a)
    assert pick_gate_corrections(ctx_a) == "skip"
    assert "0" in ctx_a.facts["design_corrections_applied"]

    # (b) Info note after proceed verdict.
    run_b = tmp_path / "run_b"
    run_b.mkdir()
    (run_b / "design.md").write_text("# placeholder\n")
    (run_b / "delivery.md").write_text("delivery\n")
    ledger_b = Ledger.seed(run_b, "Build a tool.")
    ledger_b.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger_b.verdict("gate_strategy", "proceed", approval=True)
    ledger_b.note("info", "just info")
    ctx_b = _Ctx(run_b)
    assert pick_gate_corrections(ctx_b) == "skip"
    assert "0" in ctx_b.facts["design_corrections_applied"]

    # (c) Correction after a non-gate_strategy verdict.
    run_c = tmp_path / "run_c"
    run_c.mkdir()
    (run_c / "design.md").write_text("# placeholder\n")
    (run_c / "delivery.md").write_text("delivery\n")
    ledger_c = Ledger.seed(run_c, "Build a tool.")
    ledger_c.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger_c.verdict("gate_design_review", "proceed", approval=True)
    ledger_c.note("correction", "fix the review")
    ctx_c = _Ctx(run_c)
    assert pick_gate_corrections(ctx_c) == "skip"
    assert "0" in ctx_c.facts["design_corrections_applied"]

    # (d) Correction written before a proceed verdict (earlier unrelated call).
    run_d = tmp_path / "run_d"
    run_d.mkdir()
    (run_d / "design.md").write_text("# placeholder\n")
    (run_d / "delivery.md").write_text("delivery\n")
    ledger_d = Ledger.seed(run_d, "Build a tool.")
    ledger_d.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger_d.note("correction", "stale correction")
    ledger_d.verdict("gate_strategy", "proceed", approval=True)
    ctx_d = _Ctx(run_d)
    assert pick_gate_corrections(ctx_d) == "skip"
    assert "0" in ctx_d.facts["design_corrections_applied"]


# ── T5: card command (no --kind) auto-derives kind ───────────────────────


def test_card_command_note_collected_without_explicit_kind(tmp_path):
    """The card's command omits --kind; NOTE_KINDS maps proceed→constraint,
    which pick_gate_corrections collects.

    Fails without the reorder: the auto-derived constraint note lands before
    the verdict.
    """
    from refactory_dsh.console import _steer
    from refactory_dsh.design_ops import pick_gate_corrections

    run = _setup_run(tmp_path)
    # kind="info" is the default when --kind is not given.
    _steer(run, verdicts=["gate_strategy=proceed"], kind="info",
           note="use websockets")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    assert "use websockets" in ctx.facts["gate_corrections"]
    assert "1" in ctx.facts["design_corrections_applied"]


# ── Bridge answer test ───────────────────────────────────────────────────


def test_bridge_answer_correction_collected(tmp_path):
    """The bridge's ``answer`` action writes verdict then note; pick collects it.

    Passes on main — the bridge already writes verdict before note.
    """
    from refactory_dsh.design_ops import pick_gate_corrections
    from refactory_dsh.steering import Ledger

    run = tmp_path / "run"
    run.mkdir()
    (run / "design.md").write_text("# placeholder\n")
    (run / "delivery.md").write_text("delivery\n")
    ledger = Ledger.seed(run, "Build a tool.")
    review = ledger.visit("gate_strategy", ["design.md", "delivery.md"])
    # The bridge writes verdict then note, as steering.py main() does.
    ledger.verdict("gate_strategy", "proceed", review=review, answered=True)
    ledger.note("constraint", "use websockets")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    assert "use websockets" in ctx.facts["gate_corrections"]


# ── Empty note guard ─────────────────────────────────────────────────────


def test_empty_note_records_nothing(tmp_path):
    """An empty --note with a verdict records neither the verdict nor the note.

    Fails without the empty-note guard: with the reorder, the verdict would be
    written first and then Ledger.note would raise on the empty body, leaving a
    verdict without its paired note.
    """
    from refactory_dsh.console import _steer
    from refactory_dsh.steering import Ledger, SteeringError

    run = _setup_run(tmp_path)
    ledger = Ledger(run)
    entries_before = len(ledger.entries())

    with pytest.raises(SteeringError):
        _steer(run, verdicts=["gate_strategy=proceed"], kind="correction",
               note="   ")

    # Nothing was written.
    entries_after = len(ledger.entries())
    assert entries_after == entries_before


# ── Two corrections before resume ────────────────────────────────────────


def test_two_corrections_both_collected(tmp_path):
    """Two correction notes given between proceed and resume are both collected.

    Fails without the collect-all change: the old collector only grabs the
    first entry after the verdict.
    """
    from refactory_dsh.design_ops import pick_gate_corrections
    from refactory_dsh.steering import Ledger

    run = tmp_path / "run"
    run.mkdir()
    (run / "design.md").write_text("# placeholder\n")
    (run / "delivery.md").write_text("delivery\n")
    ledger = Ledger.seed(run, "Build a tool.")
    ledger.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger.verdict("gate_strategy", "proceed", approval=True)
    ledger.note("correction", "first fix")
    ledger.note("correction", "second fix")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    assert "first fix" in ctx.facts["gate_corrections"]
    assert "second fix" in ctx.facts["gate_corrections"]
    assert "2" in ctx.facts["design_corrections_applied"]


# ── Multi-line correction with heading ───────────────────────────────────


def test_multiline_correction_survives_heading(tmp_path):
    """A correction whose body contains a ``## `` heading is not cut.

    Fails without parse_entries: the raw ``^## `` split treats the heading
    inside the note as a new entry boundary.
    """
    from refactory_dsh.design_ops import pick_gate_corrections
    from refactory_dsh.steering import Ledger

    run = tmp_path / "run"
    run.mkdir()
    (run / "design.md").write_text("# placeholder\n")
    (run / "delivery.md").write_text("delivery\n")
    ledger = Ledger.seed(run, "Build a tool.")
    ledger.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger.verdict("gate_strategy", "proceed", approval=True)
    ledger.note("correction", "line one\n## Heading inside note\nline three")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    assert "line one" in ctx.facts["gate_corrections"]
    assert "## Heading inside note" in ctx.facts["gate_corrections"]
    assert "line three" in ctx.facts["gate_corrections"]


# ── Escaped boundary line ────────────────────────────────────────────────


def test_escaped_boundary_unescaped(tmp_path):
    r"""An escaped ``\## [`` line in a correction reaches gate_corrections unescaped.

    Fails without parse_entries: the raw split returns the escaped text verbatim.
    """
    from refactory_dsh.design_ops import pick_gate_corrections
    from refactory_dsh.steering import Ledger

    run = tmp_path / "run"
    run.mkdir()
    (run / "design.md").write_text("# placeholder\n")
    (run / "delivery.md").write_text("delivery\n")
    ledger = Ledger.seed(run, "Build a tool.")
    ledger.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger.verdict("gate_strategy", "proceed", approval=True)
    ledger.note("correction", "fix this\n## [looks like a boundary] but is not\nand more")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    # The escaped line should be unescaped by parse_entries.
    assert "## [looks like a boundary] but is not" in ctx.facts["gate_corrections"]


# ── CEO-source correction not collected ──────────────────────────────────


def test_ceo_source_correction_not_collected(tmp_path):
    """A CEO-source correction is not a gate correction.

    Passes on main — tests the parse_entries-based collector's source filter.
    """
    from refactory_dsh.design_ops import pick_gate_corrections
    from refactory_dsh.steering import Ledger

    run = tmp_path / "run"
    run.mkdir()
    (run / "design.md").write_text("# placeholder\n")
    (run / "delivery.md").write_text("delivery\n")
    ledger = Ledger.seed(run, "Build a tool.")
    ledger.visit("gate_strategy", ["design.md", "delivery.md"])
    ledger.verdict("gate_strategy", "proceed", approval=True)
    ledger.note("correction", "ceo wants this", source="ceo")

    ctx = _Ctx(run)
    assert pick_gate_corrections(ctx) == "skip"
    assert "0" in ctx.facts["design_corrections_applied"]


# ── Fact count on proceed path (DSH-level) ───────────────────────────────


def test_count_fact_survives_on_proceed(tmp_path):
    """pick_gate_corrections sets '1 correction(s)' and apply_corrections
    does not overwrite it (produces entry removed).

    Fails without removing produces from apply_corrections: the executor
    overwrites the count with the full design.md text.
    """
    from refactory_dsh.console import _steer
    from refactory_dsh.design_ops import pick_gate_corrections

    run = _setup_run(tmp_path)
    _steer(run, verdicts=["gate_strategy=proceed"], kind="correction",
           note="fix the API")

    ctx = _Ctx(run)
    result = pick_gate_corrections(ctx)
    assert result == "proceed"
    assert ctx.facts["design_corrections_applied"] == "1 correction(s)"
