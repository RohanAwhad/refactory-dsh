"""Walker recovery: environment overlay, FnNode retry, timeout, fork propagation.

Each test exercises the real walker executor (``nn.executor.execute``) and must
fail without its corresponding fix.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from refactory_dsh import nn
from refactory_dsh.nn.executor import OpTimeoutError


# ---------------------------------------------------------------------------
# Test 1: environment overlay from target_workdir
# ---------------------------------------------------------------------------
def test_worktree_agent_sees_target_interpreter(tmp_path, monkeypatch):
    """_environment() derives the overlay from target_workdir, not REFACTORY_WORKDIR."""
    from refactory_dsh import model

    overlay_calls: list[str] = []

    def mock_overlay(workdir: Path) -> dict[str, str] | None:
        overlay_calls.append(str(workdir))
        return {
            "PATH": "/mock/target/bin:" + os.environ.get("PATH", ""),
            "VIRTUAL_ENV": "/mock/target/venv",
        }

    monkeypatch.setattr(model, "_environment_overlay", mock_overlay)
    monkeypatch.delenv("REFACTORY_WORKDIR", raising=False)

    client = model.DshModelClient()

    # Without target_workdir: the overlay is NOT called (env var is empty).
    env_without = client._environment()
    assert not overlay_calls, "overlay called without target_workdir"

    # With target_workdir: the overlay IS called.
    env_with = client._environment(target_workdir=tmp_path)
    assert len(overlay_calls) == 1
    assert overlay_calls[0] == str(tmp_path)
    assert env_with["PATH"].startswith("/mock/target/bin:")
    assert env_with.get("VIRTUAL_ENV") == "/mock/target/venv"


# ---------------------------------------------------------------------------
# Test 2: transient op failure retried, exit_ok stays True
# ---------------------------------------------------------------------------
def test_transient_op_failure_retried_exit_ok_true(tmp_path):
    graph = nn.Graph()
    graph.add(nn.Node(
        "retry_op", "op",
        call="walker_recovery_ops:transient_op",
        produces=("success",),
    ))

    ctx = nn.RunContext(tmp_path)
    executor = nn.Executor(graph, ctx, enable_recovery=True)
    result = executor.run()

    assert result.exit_ok is True, result.error
    assert result.facts.get("success") is True

    retried = [s for s in executor.steps if s.verdict == "retried"]
    assert len(retried) == 1
    assert retried[0].error is None

    # The op was called twice (file counter is 2).
    counter = int((tmp_path / "_attempt_counter.txt").read_text().strip())
    assert counter == 2


# ---------------------------------------------------------------------------
# Test 3: op exceeding timeout stops the run with a message naming it
# ---------------------------------------------------------------------------
def test_op_exceeding_timeout_stops_run_with_message(tmp_path):
    from refactory_dsh.invocations import CHECK_PRODUCES

    graph = nn.Graph()
    graph.add(nn.Node(
        "slow_check", "op",
        call="walker_recovery_ops:hanging_op",
        produces=(*CHECK_PRODUCES,),
        knobs=("check_timeout_s",),
    ))

    result = nn.execute(
        graph, tmp_path,
        knobs={"check_timeout_s": 2},
        enable_recovery=True,
        resource_limits={"max_elapsed_s": 10},
    )

    assert result.exit_ok is False
    assert "slow_check" in (result.error or "")
    assert "timed out" in (result.error or "").lower()

    # Verify the walker's raw formula matches lowering's.
    from refactory_dsh.lowering import command_timeout_s

    node = graph.nodes["slow_check"]
    state = {"knobs": {"check_timeout_s": {"value": 2}}}
    raw = command_timeout_s(node, state)
    assert raw is not None
    assert raw > 0


# ---------------------------------------------------------------------------
# Test 4: a hung fn-gate times out (never retried)
# ---------------------------------------------------------------------------
def test_hung_fn_gate_times_out(tmp_path):
    from refactory_dsh.invocations import CHECK_PRODUCES

    graph = nn.Graph()
    graph.add(nn.Node(
        "gate_hang", "gate", gate_kind="fn",
        evaluator="walker_recovery_ops:hanging_gate",
        produces=(*CHECK_PRODUCES,),
        knobs=("check_timeout_s",),
    ))
    graph.add(nn.Node("after", "op",
                       call="walker_recovery_ops:succeed_op",
                       produces=("done",)))
    graph.connect("gate_hang", "after", condition="proceed")

    result = nn.execute(
        graph, tmp_path,
        knobs={"check_timeout_s": 2},
        enable_recovery=True,
        resource_limits={"max_elapsed_s": 10},
    )

    assert result.exit_ok is False
    assert "gate_hang" in (result.error or "")
    assert "timed out" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Test 5: fork-branch op under the same timeout limit
# ---------------------------------------------------------------------------
def test_fork_branch_op_under_timeout_limit(tmp_path):
    from refactory_dsh.invocations import CHECK_PRODUCES

    graph = nn.Graph()
    graph.add(nn.Node("f", "fork", targets=("branch_op",)))
    graph.add(nn.Node(
        "branch_op", "op",
        call="walker_recovery_ops:hanging_op",
        produces=(*CHECK_PRODUCES,),
        knobs=("check_timeout_s",),
    ))
    barrier = "j"
    graph.add(nn.Node(barrier, "join", sources=("branch_op",)))
    graph.connect("f", barrier)
    graph.connect("branch_op", barrier)

    result = nn.execute(
        graph, tmp_path,
        knobs={"check_timeout_s": 2},
        enable_recovery=True,
        resource_limits={"max_elapsed_s": 15},
    )

    assert result.exit_ok is False
    assert "branch_op" in (result.error or "")
    assert "timed out" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Test 6: resume after timeout reruns the node (outcome='failed' not 'unknown')
# ---------------------------------------------------------------------------
def test_resume_after_timeout_outcome_is_failed(tmp_path):
    from refactory_dsh.invocations import CHECK_PRODUCES
    from refactory_dsh.nn.executor import PROGRESS

    graph = nn.Graph()
    graph.add(nn.Node(
        "timeout_op", "op",
        call="walker_recovery_ops:hanging_op",
        produces=(*CHECK_PRODUCES,),
        knobs=("check_timeout_s",),
    ))

    nn.execute(
        graph, tmp_path,
        knobs={"check_timeout_s": 2},
        enable_recovery=True,
        resource_limits={"max_elapsed_s": 10},
    )

    progress = json.loads((tmp_path / PROGRESS).read_text())
    assert progress["outcome"] == "failed", f"expected 'failed', got {progress['outcome']!r}"
    assert progress["node"] == "timeout_op"


# ---------------------------------------------------------------------------
# Test 7: wiring at all CLI sites
# ---------------------------------------------------------------------------
def test_wiring_at_all_cli_sites():
    """Every walker run/resume site passes enable_recovery=True."""
    from refactory_dsh import create, design, edit

    # Every CLI main() must pass enable_recovery=True at both the run and resume
    # paths.  Library functions (create.create, edit.edit) accept it as a
    # parameter so tests can run without recovery; the CLI is what wires True.
    for module in (design, create, edit):
        source = inspect.getsource(module.main)
        assert "enable_recovery=True" in source, (
            f"{module.__name__}.main does not pass enable_recovery=True"
        )

    # Check WalkerEvaluator.
    from refactory_dsh.evaluators import WalkerEvaluator
    source = inspect.getsource(WalkerEvaluator.evaluate)
    assert "enable_recovery=True" in source, (
        "WalkerEvaluator.evaluate does not pass enable_recovery=True"
    )


# ---------------------------------------------------------------------------
# Test 8: gate sees declared_reads, op sees memory
# ---------------------------------------------------------------------------
def test_enable_recovery_gate_sees_declared_reads_op_sees_memory(tmp_path):
    """Under enable_recovery the child process builds the same context DSH does."""
    graph = nn.Graph()
    graph.add(nn.Node(
        "g", "gate", gate_kind="fn",
        evaluator="walker_recovery_ops:check_declared_reads_gate",
        reads=("some_read.txt",),
    ))
    graph.add(nn.Node(
        "op1", "op",
        call="walker_recovery_ops:check_memory_op",
        produces=("has_memory",),
    ))
    graph.connect("g", "op1", condition="proceed")

    result = nn.execute(graph, tmp_path, enable_recovery=True)
    assert result.exit_ok is True, result.error


# ---------------------------------------------------------------------------
# Test 9: fake model through prepare never calls model_readiness
# ---------------------------------------------------------------------------
def test_enable_recovery_fake_model_no_readiness(tmp_path):
    """A non-DSH model in the child is neither None nor DshModelClient."""

    class FakeModel:
        def complete(self, prompt: str, *, system: str = "") -> str:
            return "ok"

    graph = nn.Graph()
    graph.add(nn.Node(
        "op1", "op",
        call="walker_recovery_ops:succeed_op",
        produces=("done",),
    ))

    # Run with a fake model and enable_recovery.  The child gets a stand-in
    # that is not None, so prepare/create_ops skip readiness exactly as they
    # do in-process.
    result = nn.execute(graph, tmp_path, model=FakeModel(), enable_recovery=True)
    assert result.exit_ok is True, result.error


# ---------------------------------------------------------------------------
# Test 10: child exception roundtrip
# ---------------------------------------------------------------------------
def test_child_exception_roundtrip(tmp_path):
    graph = nn.Graph()
    graph.add(nn.Node(
        "exc_op", "op",
        call="walker_recovery_ops:custom_exception_op",
        produces=("never",),
    ))

    result = nn.execute(graph, tmp_path, enable_recovery=True)
    assert result.exit_ok is False
    # The error should name the exception type and message.
    assert "custom message from child" in (result.error or "")


# ---------------------------------------------------------------------------
# Test 12: the executor's op context uses forkserver with refactory preloaded
# ---------------------------------------------------------------------------
def test_forkserver_context_with_preload(tmp_path):
    """Structural: the executor uses forkserver, not spawn, with refactory preloaded."""
    from refactory_dsh.nn import executor as ex_mod

    # The module-level context must be a ForkServerContext.
    ctx = ex_mod._forkserver_ctx
    assert ctx.get_start_method() == "forkserver", (
        f"expected forkserver, got {ctx.get_start_method()!r}"
    )

    # After ensuring preload for a graph that uses a refactory_dsh op,
    # the preload modules must include 'refactory_dsh.nn.executor' and
    # 'refactory_dsh.run'.
    graph = nn.Graph()
    graph.add(nn.Node(
        "op1", "op",
        call="walker_recovery_ops:succeed_op",
        produces=("done",),
    ))
    ex_mod._ensure_forkserver_preload(graph)
    preloaded = ex_mod._forkserver_modules
    assert "refactory_dsh.nn.executor" in preloaded
    assert "refactory_dsh.run" in preloaded
    assert "walker_recovery_ops" in preloaded


# ---------------------------------------------------------------------------
# Test 11: outer-loop evaluation — hung op stops, next trial starts clean
# ---------------------------------------------------------------------------
def test_outer_loop_evaluation_hung_op_stops_next_trial_starts_clean(tmp_path):
    from refactory_dsh.invocations import CHECK_PRODUCES
    from refactory_dsh.evaluators import WalkerEvaluator

    class _MiniMode(nn.Mode):
        name = "test_recovery_mode"

        @classmethod
        def declare(cls):
            return {"check_timeout_s": nn.Knob(2, "int")}

        def build(self):
            g = nn.Graph()
            g.add(nn.Node(
                "t_op", "op",
                call="walker_recovery_ops:outer_loop_transient_op",
                produces=("ol_done",),
            ))
            g.add(nn.Node(
                "h_op", "op",
                call="walker_recovery_ops:outer_loop_hanging_op",
                produces=(*CHECK_PRODUCES,),
                knobs=("check_timeout_s",),
            ))
            g.connect("t_op", "h_op")
            return g

        def score(self, run):
            return 1.0 if run.facts.get("ol_done") else 0.0

    mode = _MiniMode()
    evaluator = WalkerEvaluator(project=tmp_path)

    from refactory_dsh.budgets import RunBudget, ResourceLimits
    budget = RunBudget.create(tmp_path / "budget", ResourceLimits(max_elapsed_s=15))
    budget.resume()
    evaluator.budget = budget

    trial_dir = tmp_path / "trial"
    trial_dir.mkdir(parents=True, exist_ok=True)

    result = evaluator.evaluate(mode, mode.state_dict(), trial_dir)

    assert result.exit_ok is False, "expected the hung op to fail the run"
    assert "h_op" in (result.error or ""), f"expected h_op in error, got: {result.error}"
    assert "timed out" in (result.error or "").lower()

    budget.pause()

    import multiprocessing
    leaked = [c for c in multiprocessing.active_children() if c.is_alive()]
    assert not leaked, f"leaked child processes: {leaked}"


# ---------------------------------------------------------------------------
# A check's own tools die at the op's limit, not only the op's child
# ---------------------------------------------------------------------------
def test_a_checks_own_tools_die_at_the_limit(tmp_path):
    from refactory_dsh.invocations import CHECK_PRODUCES

    graph = nn.Graph()
    graph.add(nn.Node(
        "slow_tools", "op",
        call="walker_recovery_ops:sandbox_sleep_op",
        produces=(*CHECK_PRODUCES,),
        knobs=("check_timeout_s",),
    ))
    result = nn.execute(graph, tmp_path, knobs={"check_timeout_s": 2},
                        enable_recovery=True, resource_limits={"max_elapsed_s": 8})

    assert result.exit_ok is False
    assert "timed out" in (result.error or "").lower()
    pid = int((tmp_path / "_sleep_pid.txt").read_text())
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        os.kill(pid, 9)
        pytest.fail(f"the check's sleep (pid {pid}) outlived the op's time limit")
