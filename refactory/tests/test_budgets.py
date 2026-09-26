from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from refactory_dsh.budgets import BudgetExhausted, ResourceLimits, RunBudget
from refactory_dsh import nn


def test_shared_nested_usage_is_idempotent_and_reserves_validation(tmp_path):
    budget = RunBudget.open(tmp_path, ResourceLimits(max_tokens=100, validation_reserve_tokens=20))
    with ThreadPoolExecutor() as pool:
        list(pool.map(lambda index: RunBudget.from_path(budget.path).record_usage(f"worker-{index}", 10), range(8)))
    budget.record_usage("worker-0", 10)
    assert budget.snapshot()["tokens"] == 80
    with pytest.raises(BudgetExhausted, match="validation reserve"):
        budget.checkpoint("work")
    budget.checkpoint("final_validation")
    budget.record_usage("holdout", 20)
    with pytest.raises(BudgetExhausted, match="token budget"):
        budget.checkpoint()


def test_missing_usage_and_pricing_are_not_zero(tmp_path):
    budget = RunBudget.open(tmp_path, ResourceLimits(max_tokens=100))
    assert budget.snapshot()["tokens"] is None
    with pytest.raises(BudgetExhausted, match="no token telemetry"):
        budget.require_usage_capability(False)
    budget.record_usage("nested", 30)
    budget.record_usage("parent", None)
    summary = budget.snapshot()
    assert summary["tokens"] is None and summary["known_tokens"] == 30
    assert summary["estimated_cost_usd"] is None
    assert summary["token_status"] == summary["cost_status"] == "unavailable"
    with pytest.raises(BudgetExhausted, match="usage unavailable"):
        budget.checkpoint()


def test_elapsed_pause_resume_preserves_consumption_and_explicit_extension(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("refactory_dsh.budgets.time.time", lambda: clock[0])
    budget = RunBudget.open(tmp_path, ResourceLimits(max_elapsed_s=10, validation_reserve_s=2))
    clock[0] = 108
    with pytest.raises(BudgetExhausted, match="elapsed-time"):
        budget.checkpoint()
    budget.pause()
    clock[0] = 1000
    assert budget.snapshot()["elapsed_s"] == 8
    budget.resume(ResourceLimits(max_elapsed_s=20, validation_reserve_s=2))
    clock[0] = 1003
    assert budget.snapshot()["elapsed_s"] == 11
    assert json.loads(budget.path.read_text())["limits"]["max_elapsed_s"] == 20


def test_fresh_owner_cannot_reuse_paused_old_usage(tmp_path):
    budget = RunBudget.create(tmp_path, ResourceLimits(max_tokens=100))
    budget.record_usage("old-worker", 25)
    budget.pause()
    with pytest.raises(ValueError, match="new run directory or explicit resume"):
        RunBudget.create(tmp_path, ResourceLimits(max_tokens=100))
    assert budget.snapshot()["tokens"] == 25


@pytest.mark.parametrize("values", [{"max_tokens": -1}, {"max_elapsed_s": float("inf")},
                                    {"max_tokens": 10, "validation_reserve_tokens": 10},
                                    {"max_tokens": 1.5}])
def test_invalid_limits_refused(values):
    with pytest.raises(ValueError):
        ResourceLimits(**values)


def spend(ctx):
    ctx.append_text("first.txt", "completed once\n")
    ctx.budget.record_usage("first", 10)


def finish(ctx):
    ctx.write_text("second.txt", "complete\n")


class BudgetedMode(nn.Mode):
    name = "budgeted"

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("first", "op", call=f"{__name__}:spend", writes=("first.txt",)))
        graph.add(nn.Node("second", "op", call=f"{__name__}:finish", writes=("second.txt",)))
        graph.connect("first", "second")
        return graph


def test_resume_retains_completed_stage_after_budget_boundary(tmp_path):
    result = BudgetedMode().run(tmp_path, resource_limits=ResourceLimits(max_tokens=10))
    assert not result.exit_ok
    progress = json.loads((tmp_path / ".refactory/progress.json").read_text())
    assert progress["node"] == "second" and progress["outcome"] == "pending"
    resumed = BudgetedMode.resume(tmp_path, resource_limits=ResourceLimits(max_tokens=20))
    assert resumed.exit_ok, resumed.error
    assert (tmp_path / "first.txt").read_text() == "completed once\n"
    assert (tmp_path / "second.txt").exists()
    events = [json.loads(line) for line in (tmp_path / ".refactory/events.jsonl").read_text().splitlines()]
    assert [event["node_id"] for event in events if event["kind"] == "node_completed"] == ["first", "second"]


def test_fresh_graph_run_cannot_reuse_a_prior_budget(tmp_path):
    result = BudgetedMode().run(tmp_path, resource_limits=ResourceLimits(max_tokens=10))
    assert not result.exit_ok
    with pytest.raises(ValueError, match="explicit resume"):
        BudgetedMode().run(tmp_path, resource_limits=ResourceLimits(max_tokens=20))


def test_unknown_inflight_outcome_is_not_replayed(tmp_path):
    (tmp_path / ".refactory").mkdir()
    (tmp_path / ".refactory/progress.json").write_text(json.dumps({"node": "first", "outcome": "unknown"}))
    with pytest.raises(ValueError, match="automatic replay refused"):
        BudgetedMode.resume(tmp_path)


def test_partial_fork_is_not_silently_replayed(tmp_path):
    (tmp_path / ".refactory").mkdir()
    (tmp_path / ".refactory/progress.json").write_text(json.dumps({
        "node": "fork", "branches": {"left": {"status": "completed"}, "right": {"status": "failed"}},
    }))
    with pytest.raises(ValueError, match="partial fork recovery"):
        BudgetedMode.resume(tmp_path)


def test_budgeted_shell_check_stops_owned_children(tmp_path):
    import shlex
    import sys
    import time
    from refactory_dsh.sandbox import run_command

    # The child would publish a marker after one second if only its shell died.
    marker = tmp_path / "leaked.txt"
    code = f"import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text('leaked')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    budget = RunBudget.open(tmp_path, ResourceLimits(max_elapsed_s=0.15))
    with pytest.raises(BudgetExhausted):
        run_command(command, workdir=tmp_path, timeout_s=5, budget=budget)
    time.sleep(1.05)
    assert not marker.exists()


def test_candidate_validation_cannot_release_outer_search_holdout(tmp_path):
    budget = RunBudget.create(tmp_path, ResourceLimits(max_tokens=100, validation_reserve_tokens=20))
    budget.set_validation_owner("search")
    budget.record_usage("training", 80)
    with pytest.raises(BudgetExhausted, match="reserve retained"):
        budget.checkpoint_node("final_validation")
    assert budget.snapshot()["phase"] == "work"
    budget.checkpoint("final_validation")
    assert budget.snapshot()["phase"] == "final_validation"


def test_nested_validation_holds_compose_and_restore(tmp_path):
    budget = RunBudget.create(tmp_path, ResourceLimits(max_tokens=100, validation_reserve_tokens=20))
    with budget.retain_validation_reserve():
        with RunBudget.from_path(budget.path).retain_validation_reserve():
            budget.checkpoint_node("final_validation")
        assert budget.snapshot()["validation_holds"] == 1
        assert budget.snapshot()["phase"] == "work"
    assert budget.snapshot()["validation_holds"] == 0
    budget.checkpoint_node("final_validation")
    assert budget.snapshot()["phase"] == "final_validation"
