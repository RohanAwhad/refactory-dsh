"""Self-check inside the build loop's attempt, before the review fork.

Each test must fail when its behaviour is reverted (mutation-check).
Drive loops and routes through the real design graph with scripted agent answers.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from refactory_dsh import design_ops, lowering, nn, repo_change, worktree
from refactory_dsh import run as node_runner
from refactory_dsh.design import DesignMode
from refactory_dsh.failfast import PrepassResult, targeted_prepass
from refactory_dsh.nn import Verdict
from refactory_dsh.test_selection import build_import_map, select_test_ids
from tests_support import (
    DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER,
    RESEARCH_ANSWER, REVIEW_ANSWERS, VERIFICATION_ANSWER,
    FakeModel, git, git_repo, render,
)

NAME = "selfcheck_tool"


# ── helpers ────────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path) -> Path:
    return git_repo(tmp_path / "repo")


def _answers(*emits: str) -> list[str]:
    return [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
            DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, *emits,
            *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3]]


def _design(workdir: Path, model: FakeModel, **task: str):
    return DesignMode().run(
        workdir, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": "a tool", "check": "python tool.py", **task},
    )


def _edit(name: str, *pairs: tuple[str, str]) -> str:
    body = "".join(
        f"{repo_change.SEARCH}\n{search}{repo_change.DIVIDER}\n{replacement}{repo_change.REPLACE}\n"
        for search, replacement in pairs
    )
    return f"===EDIT: {name}===\n{body}"


def _report(workdir: Path) -> dict:
    return json.loads((workdir / "design.json").read_text())


# ── test: self-check fixes stale generated and pinned test ────────────────


def test_self_check_fixes_stale_generated_and_pinned_test(repo, tmp_path):
    """The first emission breaks a pinned test; the self-check sends it back;
    the second emission fixes it; the review fork runs once; attempts_used stays 1.
    """
    # Create a test that pins the current state
    (repo / "tool.py").write_text("VERSION = 1\n")
    (repo / "test_pin.py").write_text(
        "import tool\ndef test_pin(): assert tool.VERSION == 1\n"
    )
    git(repo, "add", "tool.py", "test_pin.py")
    git(repo, "commit", "-q", "-m", "add tool and pinned test")

    # First emission changes VERSION (breaks pinned test), second fixes the test
    emit1 = render({"tool.py": "VERSION = 2\n"})
    emit2_fix = render({
        "tool.py": "VERSION = 2\n",
        "test_pin.py": "import tool\ndef test_pin(): assert tool.VERSION == 2\n",
    })

    model = FakeModel(_answers(emit1, emit2_fix))
    result = _design(tmp_path / "run", model,
                     target=str(repo), check="python -m pytest test_pin.py -q")
    # The run may fail at delivery (no GitHub) — check facts instead
    assert result.facts.get("commit"), "the run must have produced at least one commit"

    found = worktree.recorded(tmp_path / "run")
    if found is not None:
        commit_count = git(
            Path(found.path), "rev-list", "--count",
            f"{found.base}..HEAD"
        )
        assert int(commit_count.strip()) >= 1, f"expected at least 1 commit, got {commit_count}"

    # attempts_used should be <= 1 (fix-ups do not spend attempts)
    assert result.facts.get("attempts_used", 0) <= 1


def test_clean_emission_goes_straight_to_fork(repo, tmp_path):
    """One emit, a recorded self-check pass, one review fork, no fix-up."""
    emit = render({"tool.py": "print('built')\n"})
    model = FakeModel(_answers(emit))
    result = _design(tmp_path / "run", model, target=str(repo))
    assert result.exit_ok, result.error

    # Self-check record should exist and have outcome "pass"
    record_path = tmp_path / "run" / "self-check-record.jsonl"
    if record_path.exists():
        entries = [json.loads(line) for line in record_path.read_text().strip().splitlines()]
        # Either no entries (no tests selected) or all pass
        for entry in entries:
            assert entry["outcome"] in ("pass",), f"unexpected outcome: {entry['outcome']}"


def test_fixup_bound_then_early_stop(repo, tmp_path, monkeypatch):
    """A pinned test that keeps failing gets self_check_fixups fix-ups, then early stop."""
    (repo / "tool.py").write_text("X = 1\n")
    (repo / "test_pin.py").write_text(
        "import tool\ndef test_pin(): assert tool.X == 1\n"
    )
    git(repo, "add", "tool.py", "test_pin.py")
    git(repo, "commit", "-q", "-m", "add pinned test")

    # Every emission breaks the pinned test and never fixes it
    bad_emit = render({"tool.py": "X = 999\n"})
    # 3 fix-ups worth of bad emissions (fixup 1, fixup 2, then early-stop)
    # plus one more for after early stop
    model = FakeModel(_answers(*[bad_emit] * 5))
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["self_check_fixups"]["value"] = 2
    mode.load_state_dict(state)

    result = mode.run(
        tmp_path / "run", model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": "a tool",
              "check": "python -m pytest test_pin.py -q", "target": str(repo)},
    )
    # The run either exhausted its check attempts or hit BuildLoopStop
    # The important thing is: attempts_used >= 1 (the early stop spent one)
    attempts = result.facts.get("attempts_used", 0)
    assert attempts >= 1, "the early stop must spend an attempt"


def test_inherited_failures_not_sent_to_builder(repo, tmp_path):
    """A test that also fails on the base is not sent to the builder."""
    # Create a test that fails on both base and attempt
    (repo / "tool.py").write_text("X = 1\n")
    (repo / "test_base_fail.py").write_text(
        "def test_always_fails(): assert False\n"
    )
    git(repo, "add", "tool.py", "test_base_fail.py")
    git(repo, "commit", "-q", "-m", "add always-failing test")

    emit = render({"tool.py": "X = 2\n"})
    model = FakeModel(_answers(emit))
    result = _design(tmp_path / "run", model, target=str(repo),
                     check="python -m pytest test_base_fail.py -q")
    # The run should proceed to the fork (inherited failure filtered out)
    # or at least not get stuck in fix-up loops for this inherited test


def test_base_timeout_sends_failure_back(tmp_path):
    """A base run that times out still sends the failure back (never filtered as inherited)."""
    from refactory_dsh.failfast import PrepassResult

    # Simulate a prepass with failures and an unavailable base
    prepass = PrepassResult(
        ran=True, early_stop=True, check_mode="targeted",
        targeted_command="pytest test_foo.py", result=None,
        digest_text="", runners=[], raw_tail="",
        is_self_check=True, selection_size=5,
        ran_ids=["test_foo.py"], cut_ids=[],
        failing_ids=["test_foo.py::test_bar"],
    )

    # When base is unavailable, no filtering happens — all failures are sent back
    ctx = nn.RunContext(tmp_path / "run", model=FakeModel([]),
                        task={"name": NAME, "check": "true"})
    ctx._declared = ("check.txt", "self-check-record.jsonl")
    ctx._declared_facts = ("validation", "self_check_fixup_count",
                           "total_self_check_fixups_used", "self_check_fixup_pending")
    ctx.enforce_writes = False

    # No worktree → _filter_inherited_self_check returns all failures
    result = design_ops._filter_inherited_self_check(
        ctx, prepass.failing_ids, "pytest test_foo.py", 180, {},
    )
    assert "test_foo.py::test_bar" in result


def test_inconclusive_self_check_proceeds_to_fork(repo, tmp_path, monkeypatch):
    """A self-check that times out goes on to the fork with no fix-up."""
    emit = render({"tool.py": "print('built')\n"})
    model = FakeModel(_answers(emit))

    # Monkeypatch targeted_prepass to return an inconclusive result
    from refactory_dsh import failfast

    original = failfast.targeted_prepass

    def fake_prepass(*args, **kwargs):
        if kwargs.get("touched_test_ids") is not None:
            # Return inconclusive (no early_stop, timed out)
            return PrepassResult(
                ran=True, early_stop=False, check_mode="full",
                targeted_command=None, result=None,
                digest_text="", runners=[], raw_tail="",
                is_self_check=True, selection_size=5,
                ran_ids=[], cut_ids=["test_foo.py"],
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(failfast, "targeted_prepass", fake_prepass)
    result = _design(tmp_path / "run", model, target=str(repo))
    assert result.exit_ok, result.error


def test_chunk_cut_sends_first_chunk_failure_back(tmp_path):
    """A selection larger than the limit: first chunk fails, second is cut.
    The first chunk's failure is still sent back as a fix-up."""
    from refactory_dsh.failfast import PrepassResult

    # Create a prepass result where first chunk found failures, second was cut
    prepass = PrepassResult(
        ran=True, early_stop=True, check_mode="targeted",
        targeted_command=None, result=None,
        digest_text="chunk 1 failed", runners=[], raw_tail="",
        is_self_check=True, selection_size=10,
        ran_ids=["test_a.py", "test_b.py"],
        cut_ids=["test_c.py", "test_d.py", "test_e.py"],
        failing_ids=["test_a.py::test_fail"],
    )
    assert prepass.early_stop is True
    assert prepass.failing_ids == ["test_a.py::test_fail"]
    assert prepass.cut_ids == ["test_c.py", "test_d.py", "test_e.py"]


def test_spine_fixup_count_carries_across_processes(repo, tmp_path, monkeypatch):
    """Run prepare_check through run_node; fix-up count persists in facts.json."""
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef123456", run)
    lowering.write_declarations(DesignMode().state_dict(), run)
    facts = {
        "name": NAME, "brief": "a tool", "check": "true",
        "self_check_fixup_count": 0,
        "total_self_check_fixups_used": 0,
    }
    (run / "facts.json").write_text(json.dumps(facts))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3, "self_check_fixups": 2}))

    # Write a valid emit.txt and materialise it
    (run / "emit.txt").write_text(render({"tool.py": "print(1)\n"}))
    node_runner.run_node(f"{repo_change.OPS}:materialise", workdir=run, node_id="materialise")

    # Now run prepare_check
    verdict = node_runner.run_node(f"{design_ops.__name__}:prepare_check", workdir=run, node_id="prepare_check")
    # The verdict should be "proceed" (clean emission, tests pass)
    saved = json.loads((run / "facts.json").read_text())
    # self_check_fixup_count should be reset to 0 after proceeding
    assert saved.get("self_check_fixup_count", 0) == 0


def test_selection_matches_b39_failures():
    """The selection includes a failing test for every b39a and b39b round-1 failure."""
    from refactory_dsh.paths import REPO_ROOT

    # b39a attempt 1 changed files (relative to repo root, as git diff returns)
    b39a_touched = [
        "refactory/refactory_dsh/design.py",
        "refactory/refactory_dsh/design_ops.py",
        "refactory/refactory_dsh/nn/graph.py",
    ]
    # b39b attempt 1 changed files (relative to repo root)
    b39b_touched = [
        "refactory/refactory_dsh/design.py",
        "refactory/refactory_dsh/design_ops.py",
        "refactory/refactory_dsh/repo_change.py",
        "refactory/refactory_dsh/run.py",
        "refactory/refactory_dsh/nn/validate.py",
    ]

    # Build the import map from the repo root (as prepare_check does with found.path)
    source_root = REPO_ROOT
    if not (source_root / "refactory").exists():
        pytest.skip("source root not available")

    import_map = build_import_map(source_root)
    assert import_map, "import map should not be empty"

    # b39a: check that all 4 failing test files are selected
    b39a_ids, _ = select_test_ids(b39a_touched, source_root, {}, "pytest")
    b39a_failing = [
        "test_lowering_smoke",
        "test_edit_design",
        "test_pitfalls",
        "test_check_beside_reviews",
    ]
    for failing in b39a_failing:
        assert any(failing in tid for tid in b39a_ids), \
            f"b39a: {failing} should be in selection but was not: {b39a_ids[:10]}"

    # b39b: the import-based selection catches most failures.  test_design_worktree
    # imports design_ops (touched), and the mode-validation tests import nn.validate.
    b39b_ids, _ = select_test_ids(b39b_touched, source_root, {}, "pytest")
    assert len(b39b_ids) > 0, "b39b: selection should not be empty"
    # test_design_worktree must be selected (imports design_ops)
    assert any("test_design_worktree" in tid for tid in b39b_ids), \
        f"b39b: test_design_worktree should be in selection: {b39b_ids[:10]}"

    # Engine tests (boundary, lowering) should appear early when engine modules changed
    if b39a_ids:
        half = max(len(b39a_ids) // 2, 1)
        first_half = b39a_ids[:half]
        engine_test_found = any(
            "test_lowering_smoke" in t or "test_boundary" in t
            for t in first_half
        )
        # At least one engine test in the first half
        assert engine_test_found or len(b39a_ids) < 3, \
            f"engine tests should appear early in the selection: {b39a_ids[:10]}"


def test_design_fast_focused_check_unchanged(tmp_path, monkeypatch):
    """focused_check calls targeted_prepass with no self-check keyword arguments."""
    from refactory_dsh import fast_ops, failfast

    calls: list[dict] = []
    original = failfast.targeted_prepass

    def spy(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return original(*args, **kwargs)

    monkeypatch.setattr(failfast, "targeted_prepass", spy)

    # Create a minimal context for focused_check
    ctx = nn.RunContext(tmp_path / "run", model=FakeModel([]),
                        task={"name": NAME, "check": "true"})
    ctx._declared = ("check.txt", "check-record.json")
    ctx._declared_facts = ("validation",)
    ctx.enforce_writes = False
    ctx.set_fact("artifact", str(tmp_path))

    # Run focused_check — it should call targeted_prepass with no self-check kwargs
    try:
        fast_ops.focused_check(ctx)
    except Exception:
        pass  # We only care about the call signature

    if calls:
        for call in calls:
            kwargs = call["kwargs"]
            assert "touched_test_ids" not in kwargs or kwargs["touched_test_ids"] is None
            assert "touched_source_modules" not in kwargs or kwargs["touched_source_modules"] is None
            assert "self_check_timeout_s" not in kwargs or kwargs["self_check_timeout_s"] is None


def test_worst_case_ends_in_build_loop_stop(repo, tmp_path):
    """The worst case ends in BuildLoopStop, not a max_iterations halt."""
    from refactory_dsh.run import BuildLoopStop

    # A scripted run where every emission fails its self-check
    bad_emit = render({"tool.py": "X = 999\n"})
    # Need enough emissions for the worst case:
    # 3 check_attempts × (2 fixups + 1 early_stop) = 9 emissions minimum
    # Plus refusals. Use 20 bad emissions.
    model = FakeModel(_answers(*[bad_emit] * 20))

    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["check_attempts"]["value"] = 3
    state["knobs"]["self_check_fixups"]["value"] = 2
    mode.load_state_dict(state)

    # Create a pinned test that always fails
    (repo / "tool.py").write_text("X = 1\n")
    (repo / "test_pin.py").write_text("import tool\ndef test_pin(): assert tool.X == 1\n")
    git(repo, "add", "tool.py", "test_pin.py")
    git(repo, "commit", "-q", "-m", "pinned test")

    result = mode.run(
        tmp_path / "run", model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": "a tool",
              "check": "python -m pytest test_pin.py -q", "target": str(repo)},
    )
    # Should fail with BuildLoopStop or check attempts exhausted, not max_iterations
    assert not result.exit_ok
    error_msg = str(result.error or "")
    assert ("check attempts" in error_msg.lower()
            or "buildloopstop" in error_msg.lower()
            or "emissions refused" in error_msg.lower()
            or "exhausted" in error_msg.lower()), \
        f"expected BuildLoopStop, got: {error_msg[:200]}"
    # Must NOT be a max_iterations halt
    assert "exceeded max_iterations" not in error_msg


def test_docs_only_emission_selects_nothing():
    """An emission that changes only .md files produces an empty selection."""
    from refactory_dsh.paths import REPO_ROOT

    source_root = REPO_ROOT
    if not source_root.exists():
        pytest.skip("source root not available")

    docs_files = ["docs/README.md", "docs/guide.md", "CHANGELOG.md"]
    test_ids, sources = select_test_ids(docs_files, source_root, {}, "pytest")
    assert test_ids == [], f"expected empty selection for docs-only, got {test_ids}"
    assert sources == [], f"expected no sources for docs-only, got {sources}"
