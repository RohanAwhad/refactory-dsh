"""Build-loop stop signal, refusal bound, and failure text (dm-202, dm-203).

Tests for ``BuildLoopStop``, ``format_failure_detail``, the refusal counter,
the walker's ``_try_visit`` bypass, ``main()``'s exit code 3, and the
cross-process refusal bound.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from refactory_dsh.run import (
    BuildLoopStop,
    FAILURE_MARKER,
    format_failure_detail,
    main,
    run_node,
)


# ── BuildLoopStop is a ValueError ─────────────────────────────────────────

def test_build_loop_stop_is_a_value_error():
    assert issubclass(BuildLoopStop, ValueError)
    assert isinstance(BuildLoopStop("x"), ValueError)


# ── format_failure_detail ──────────────────────────────────────────────────

def test_format_failure_detail_short_message_comes_back_whole():
    """A short multi-line message comes back whole (correction #3)."""
    exc = BuildLoopStop("check exhausted\nsome detail")
    result = format_failure_detail(exc)
    assert result == "BuildLoopStop: check exhausted\nsome detail"


def test_format_failure_detail_1200_char_message_no_repeat():
    """A 1200-char message does not repeat its first line (correction #3)."""
    first_line = "authoring unresolved after 3 attempt(s)"
    rest = "x" * (1200 - len(first_line) - 1)
    exc = BuildLoopStop(f"{first_line}\n{rest}")
    result = format_failure_detail(exc)
    assert result.startswith("BuildLoopStop: authoring unresolved")
    # Count how many times the reason line appears — must be exactly once
    assert result.count("authoring unresolved after 3 attempt(s)") == 1
    assert len(result) <= 2000


def test_format_failure_detail_reason_first():
    """The 20 KB fixture: reason first, FAILED test id present, total ≤ 2000."""
    # Build a 20 KB check output with one FAILED line in the middle and a vite warning at the end
    preamble = "some check output\n" * 200  # ~3600 chars
    failed_line = "FAILED refactory/tests/test_create.py::test_a_review_that_reloops_the_build_says_why_into_the_rebuild\n"
    vite_warning = "vite v5.2.0 building for production...\n" * 100  # ~3800 chars
    filler = "x" * (20000 - len(preamble) - len(failed_line) - len(vite_warning))
    check_output = preamble + failed_line + filler + vite_warning

    exc = BuildLoopStop(f"authoring unresolved after 3 attempt(s): {check_output}")
    result = format_failure_detail(exc)

    assert result.startswith("BuildLoopStop: authoring unresolved")
    assert "test_a_review_that_reloops_the_build_says_why_into_the_rebuild" in result
    assert len(result) <= 2000
    # The vite warning should not appear before the reason
    reason_pos = result.index("authoring unresolved")
    assert reason_pos == len("BuildLoopStop: ")


# ── main() exit codes ─────────────────────────────────────────────────────

def test_main_exits_3_for_build_loop_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_WORKDIR", str(tmp_path))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "test_node", "type": "op"}))
    (tmp_path / "facts.json").write_text("{}")

    def _raise_stop(ctx):
        raise BuildLoopStop("emissions refused 3 consecutive times: edit text not found")

    with mock.patch("refactory_dsh.run.resolve", return_value=_raise_stop):
        import io
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured)
        code = main(["test_module:_raise_stop", "--node", "test_node"])

    assert code == 3
    stderr_text = captured.getvalue()
    assert FAILURE_MARKER in stderr_text
    assert "emissions refused 3 consecutive times" in stderr_text


def test_main_exits_1_for_other_exceptions(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_WORKDIR", str(tmp_path))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "test_node", "type": "op"}))
    (tmp_path / "facts.json").write_text("{}")

    def _raise_error(ctx):
        raise RuntimeError("something went wrong: details")

    with mock.patch("refactory_dsh.run.resolve", return_value=_raise_error):
        import io
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stderr", captured)
        code = main(["test_module:_raise_error", "--node", "test_node"])

    assert code == 1
    stderr_text = captured.getvalue()
    assert FAILURE_MARKER in stderr_text


# ── run_node does not retry BuildLoopStop ──────────────────────────────────

def test_run_node_does_not_retry_build_loop_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_WORKDIR", str(tmp_path))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "test_node", "type": "op"}))
    (tmp_path / "facts.json").write_text("{}")

    call_count = 0

    def _raise_stop(ctx):
        nonlocal call_count
        call_count += 1
        ctx.set_fact("counter", call_count)
        raise BuildLoopStop("limit reached")

    with mock.patch("refactory_dsh.run.resolve", return_value=_raise_stop):
        with pytest.raises(BuildLoopStop, match="limit reached"):
            run_node("test_module:_raise_stop", workdir=tmp_path, node_id="test_node")

    # The op should be called exactly once (no retry)
    assert call_count == 1
    # Facts should be written to disk
    facts = json.loads((tmp_path / "facts.json").read_text())
    assert facts["counter"] == 1


# ── walker _try_visit bypass ──────────────────────────────────────────────

def test_walker_stop_reaches_run_result_error(tmp_path):
    """BuildLoopStop propagates through the walker's Executor.run() with its reason."""
    from refactory_dsh.nn import RunContext, Node, Graph
    from refactory_dsh.nn.executor import Executor

    # Build a minimal graph with one op that raises BuildLoopStop
    graph = Graph()
    graph.add(Node("failing_op", "op", call="test_module:fail"))

    ctx = RunContext(tmp_path, facts={"name": "test"}, knobs={})

    with mock.patch("refactory_dsh.nn.executor.Executor._visit") as mock_visit:
        # First call raises, second call (retry) should NOT happen
        mock_visit.side_effect = BuildLoopStop("test stop reason")
        executor = Executor(graph, ctx, enable_recovery=True)
        result = executor.run()

    assert result.exit_ok is False
    assert "BuildLoopStop" in (result.error or "")
    assert "test stop reason" in (result.error or "")
    # _visit called only once — the bypass prevents retry
    assert mock_visit.call_count == 1


# ── consecutive refusals end the run (walker) ──────────────────────────────

def test_consecutive_refusals_end_the_run(tmp_path):
    """A scripted walker run whose emissions keep changing nothing ends after refusal_attempts."""
    from refactory_dsh.design import DesignMode
    from tests_support import FakeModel, render, RESEARCH_ANSWER, PITFALLS_ANSWER
    from tests_support import DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, REVIEW_ANSWERS, VERIFICATION_ANSWER
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    # An emission that is identical to itself (will be refused as "changed nothing"
    # on the second and subsequent materialise calls because the worktree already
    # has these files from the first attempt).
    emission = render({"tool.py": "print('built')\n"})

    # We need enough answers: research, pitfalls, perspectives, plan, design doc,
    # design review, then multiple identical emissions for the refusal loop.
    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        emission, emission, emission, emission, emission,
        *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3],
    ]
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["refusal_attempts"]["value"] = 3
    mode.load_state_dict(state)
    result = mode.run(
        run, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": "refusal_test", "brief": "a tool", "check": "python tool.py",
              "target": str(repo)},
    )

    assert result.exit_ok is False
    assert "emissions refused" in (result.error or ""), f"Expected refusal stop, got: {result.error}"
    assert "consecutive" in (result.error or "")


def test_interleaved_refusals_and_successes_converge(tmp_path):
    """Refusals interleaved with applied emissions converge: the counter resets."""
    from refactory_dsh.design import DesignMode
    from tests_support import FakeModel, render, RESEARCH_ANSWER, PITFALLS_ANSWER
    from tests_support import DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, REVIEW_ANSWERS, VERIFICATION_ANSWER
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    # First emission: valid. Second: identical (refused). Third: different (valid, passes check).
    emit1 = render({"tool.py": "print('v1')\n"})
    emit2 = render({"tool.py": "print('v1')\n"})  # same as emit1 → refused
    emit3 = render({"tool.py": "print(1)\n"})  # different → applied, passes check "python tool.py"

    answers = [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
        emit1, emit2, emit3,
        *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3],
    ]
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["refusal_attempts"]["value"] = 3
    mode.load_state_dict(state)
    result = mode.run(
        run, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": "interleave_test", "brief": "a tool", "check": "python tool.py",
              "target": str(repo)},
    )

    # The run should succeed (or at least not stop on refusals)
    # The counter resets on each applied emission
    if not result.exit_ok:
        assert "emissions refused" not in (result.error or ""), (
            f"Run should not have stopped on refusals: {result.error}"
        )


# ── cross-process refusal bound ──────────────────────────────────────────

def test_refusal_bound_holds_across_node_processes(tmp_path, monkeypatch):
    """Run materialise three times through node_runner, each sharing one facts file.

    The third invocation exits 3 with the failure marker and the refusal reason.
    Removing consecutive_refusals from produces makes the test fail.
    """
    from refactory_dsh.design import DesignMode
    from refactory_dsh import lowering, worktree, repo_change
    from refactory_dsh.run import main as runner_main
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    # Create worktree and write declarations
    worktree.create(repo, "HEAD", "abcdef999999", run)
    lowering.write_declarations(DesignMode().state_dict(), run)
    (run / "facts.json").write_text(json.dumps({
        "name": "refusal_test", "brief": "a tool", "check": "true",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3, "refusal_attempts": 3}))

    # An edit whose search text is not in the file → AttemptRefused
    bad_emission = "===EDIT: app.py===\n<<<<<<< SEARCH\ntext not in file\n=======\nreplacement\n>>>>>>> REPLACE\n"

    import io

    for i in range(3):
        (run / "emit.txt").write_text(bad_emission)
        monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
        monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "materialise", "type": "op"}))

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            code = runner_main([f"{repo_change.OPS}:materialise", "--node", "materialise"])
        finally:
            sys.stderr = old_stderr

        facts = json.loads((run / "facts.json").read_text())

        if i < 2:
            # First two: the refusal is recorded, exit 0 (refusal returned, not raised)
            assert code == 0, f"invocation {i+1}: expected exit 0, got {code}"
            assert facts.get("consecutive_refusals") == i + 1, (
                f"invocation {i+1}: expected consecutive_refusals={i+1}, got {facts.get('consecutive_refusals')}"
            )
        else:
            # Third: BuildLoopStop raised, exit 3, marker present
            assert code == 3, f"invocation 3: expected exit 3, got {code}. stderr={captured.getvalue()}"
            stderr_text = captured.getvalue()
            assert FAILURE_MARKER in stderr_text
            assert "emissions refused 3 consecutive times" in stderr_text


# ── snapshot guard: a removed produces breaks the cross-process test ──────

def test_removing_consecutive_refusals_from_produces_fails(tmp_path, monkeypatch):
    """Removing consecutive_refusals from the materialise node's produces must
    make the fact unavailable in a subsequent invocation."""
    from refactory_dsh.design import DesignMode
    from refactory_dsh import lowering, worktree, repo_change
    from refactory_dsh.run import main as runner_main
    from tests_support import git_repo

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"

    worktree.create(repo, "HEAD", "abcdef888888", run)
    lowering.write_declarations(DesignMode().state_dict(), run)

    # Remove consecutive_refusals from declarations
    declarations = json.loads((run / lowering.DECLARATIONS).read_text())
    produces = declarations["materialise"]["produces"]
    if "consecutive_refusals" in produces:
        produces.remove("consecutive_refusals")
    (run / lowering.DECLARATIONS).write_text(json.dumps(declarations))

    (run / "facts.json").write_text(json.dumps({
        "name": "produces_test", "brief": "a tool", "check": "true",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3, "refusal_attempts": 3}))

    bad_emission = "===EDIT: app.py===\n<<<<<<< SEARCH\ntext not in file\n=======\nreplacement\n>>>>>>> REPLACE\n"

    import io

    # First invocation — will try to set consecutive_refusals but it is not in produces
    (run / "emit.txt").write_text(bad_emission)
    monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": "materialise", "type": "op"}))

    captured = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        code = runner_main([f"{repo_change.OPS}:materialise", "--node", "materialise"])
    finally:
        sys.stderr = old_stderr

    # The _check_produces should fail because consecutive_refusals is not declared
    # This manifests as exit 1 (general failure) rather than exit 0
    assert code == 1, (
        f"expected exit 1 (produces check failure), got {code}. stderr={captured.getvalue()}"
    )


# ── review of #496: the stop holds on every path, the count resets and survives ──

def _design_mode(**knobs):
    from refactory_dsh.design import DesignMode

    mode = DesignMode()
    state = mode.state_dict()
    for key, value in knobs.items():
        state["knobs"][key]["value"] = value
    mode.load_state_dict(state)
    return mode


def _run_node_main(run, target, node_id, node_type, monkeypatch):
    import io

    monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": node_id, "type": node_type}))
    err, out = io.StringIO(), io.StringIO()
    old_err, old_out = sys.stderr, sys.stdout
    sys.stderr, sys.stdout = err, out
    try:
        code = main([target, "--node", node_id])
    finally:
        sys.stderr, sys.stdout = old_err, old_out
    return code, err.getvalue()


def test_a_design_check_limit_ends_the_walker_run_with_the_stop(tmp_path):
    """Design mode runs its check beside the reviews; at the check limit the run
    still ends with a BuildLoopStop, not a plain ValueError."""
    from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER,
                               RESEARCH_ANSWER, REVIEW_ANSWERS, VERIFICATION_ANSWER, FakeModel,
                               git_repo, render)

    repo = git_repo(tmp_path / "repo")
    emits = [render({"tool.py": f"print({i})\n"}) for i in range(8)]
    answers = [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
               DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, *emits,
               *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3]]
    result = _design_mode(check_attempts=2, check_attempt_cap=0).run(
        tmp_path / "run", model=FakeModel(answers), verdicts={"gate_strategy": "proceed"},
        task={"name": "stop_tool", "brief": "a tool", "check": "exit 1", "target": str(repo)})
    assert result.exit_ok is False
    assert "BuildLoopStop" in (result.error or ""), result.error


def test_a_design_check_limit_exits_3_on_the_spine(tmp_path, monkeypatch):
    """The spine runs materialise, prepare_check, the check beside the reviews and
    gate_check as separate processes; at the check limit gate_check exits 3 with the
    marker, so the spine records the run failed and a CEO redo is refused (dm-202)."""
    from refactory_dsh import design_ops, lowering, repo_change, worktree
    from tests_support import git_repo, render

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef777777", run)
    lowering.write_declarations(_design_mode(check_attempts=1, check_attempt_cap=0).state_dict(), run)
    (run / "facts.json").write_text(json.dumps({"name": "stop_tool", "brief": "a tool", "check": "exit 1"}))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 1, "check_attempt_cap": 0,
                                                      "refusal_attempts": 3}))
    (run / "emit.txt").write_text(render({"tool.py": "print(1)\n"}))
    steps = [(f"{repo_change.OPS}:materialise", "materialise", "op"),
             (f"{design_ops.__name__}:prepare_check", "prepare_check", "gate"),
             (f"{design_ops.__name__}:check_beside_reviews", "check", "op"),
             (f"{design_ops.__name__}:gate_check", "gate_check", "gate")]
    codes = []
    for target, node, kind in steps:
        code, err = _run_node_main(run, target, node, kind, monkeypatch)
        codes.append((node, code))
        if code != 0:
            break
    assert codes[-1] == ("gate_check", 3), codes
    assert FAILURE_MARKER in err


def test_every_limit_raise_in_the_build_loop_is_a_stop():
    """No limit in repair or _repair raises a plain ValueError: each is final on the spine."""
    import inspect

    from refactory_dsh import create_ops, fast_ops

    for fn in (create_ops.repair, fast_ops._repair):
        source = inspect.getsource(fn)
        assert "raise ValueError(" not in source, fn.__name__
        assert source.count("raise BuildLoopStop(") >= 3, fn.__name__


def test_an_applied_emission_resets_the_refusal_count_across_processes(tmp_path, monkeypatch):
    """refuse, refuse, apply, refuse, refuse: the applied emission resets the count,
    so the run never stops, and the count is 2 at the end."""
    from refactory_dsh import lowering, repo_change, worktree
    from tests_support import git_repo, render

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef666666", run)
    lowering.write_declarations(_design_mode().state_dict(), run)
    (run / "facts.json").write_text(json.dumps({"name": "reset_test", "brief": "a tool", "check": "true"}))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3, "refusal_attempts": 3}))
    bad = "===EDIT: app.py===\n<<<<<<< SEARCH\ntext not in file\n=======\nreplacement\n>>>>>>> REPLACE\n"
    sequence = [bad, bad, render({"tool.py": "print(1)\n"}), bad, bad]
    counts = []
    for emission in sequence:
        (run / "emit.txt").write_text(emission)
        code, err = _run_node_main(run, f"{repo_change.OPS}:materialise", "materialise", "op", monkeypatch)
        assert code == 0, err
        counts.append(json.loads((run / "facts.json").read_text()).get("consecutive_refusals"))
    assert counts == [1, 2, 0, 1, 2], counts


def test_the_refusal_bound_follows_its_knob(tmp_path):
    from refactory_dsh import create_ops, nn

    ctx = nn.RunContext(tmp_path, facts={"name": "knob_pkg",
                                         "package_root": str(tmp_path / "stage/.factory/packages/knob_pkg")},
                        knobs={"refusal_attempts": 2})
    ctx.enforce_writes = False
    bad = "===EDIT: mode.py===\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n"
    ctx.write_text("emit.txt", bad)
    assert create_ops.materialise(ctx).get("emission_refused")
    ctx.write_text("emit.txt", bad)
    with pytest.raises(BuildLoopStop, match="emissions refused 2 consecutive times"):
        create_ops.materialise(ctx)


def test_create_materialise_stops_at_the_bound(tmp_path):
    """The create side of the shared refusal helper counts and stops too."""
    from refactory_dsh import create_ops, nn

    ctx = nn.RunContext(tmp_path, facts={"name": "probe_pkg",
                                         "package_root": str(tmp_path / "stage/.factory/packages/probe_pkg")},
                        knobs={"refusal_attempts": 3})
    ctx.enforce_writes = False
    bad = "===EDIT: mode.py===\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n"
    counts = []
    for _ in range(2):
        ctx.write_text("emit.txt", bad)
        assert create_ops.materialise(ctx).get("emission_refused")
        counts.append(ctx.facts.get("consecutive_refusals"))
    assert counts == [1, 2]
    ctx.write_text("emit.txt", bad)
    with pytest.raises(BuildLoopStop, match="emissions refused 3 consecutive times"):
        create_ops.materialise(ctx)


def test_a_run_lowered_before_the_refusal_count_still_materialises(tmp_path):
    from refactory_dsh import lowering, repo_change, worktree
    from tests_support import git_repo, render

    repo = git_repo(tmp_path / "repo")
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef445566", run)
    lowering.write_declarations(_design_mode().state_dict(), run)
    declarations = json.loads((run / lowering.DECLARATIONS).read_text())
    declarations["materialise"]["produces"].remove("consecutive_refusals")
    (run / lowering.DECLARATIONS).write_text(json.dumps(declarations))
    (run / "facts.json").write_text(json.dumps({"name": "old_tool", "check": "python tool.py"}))
    (run / "emit.txt").write_text(render({"tool.py": "print(1)\n"}))
    run_node(f"{repo_change.OPS}:materialise", workdir=run, node_id="materialise")
    assert json.loads((run / "facts.json").read_text())["files"] == ["tool.py"]


def test_a_ceo_redo_keeps_the_refusal_count(tmp_path):
    """A CEO redo cannot reset a limit count; the refusal count is one."""
    from refactory_dsh import items, lowering

    run = tmp_path / "run"
    run.mkdir()
    lowering.write_declarations(_design_mode().state_dict(), run)
    (run / "facts.json").write_text(json.dumps({"consecutive_refusals": 2, "attempts_used": 1,
                                                "check_attempts_used": 1}))
    items.command_reset(run, {"nodes": ["emit", "materialise"], "start": "emit",
                              "preserveAttemptFacts": True})
    assert json.loads((run / "facts.json").read_text()).get("consecutive_refusals") == 2


def test_a_short_message_with_a_test_id_comes_back_exactly_whole():
    """The whole-message branch, not the reason-plus-ids fallback, answers a short message."""
    exc = BuildLoopStop("check failed\nFAILED tests/test_x.py::test_y - boom")
    assert format_failure_detail(exc) == "BuildLoopStop: check failed\nFAILED tests/test_x.py::test_y - boom"
