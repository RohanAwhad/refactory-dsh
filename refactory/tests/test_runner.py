"""The node runner: what a node command does on the DSH path.

The runner is what makes a lowered graph executable: it builds the same context the
walking executor builds, enforces the same declarations, and prints a gate's
verdict on the last stdout line, which is how the spine routes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from refactory_dsh import lowering
from refactory_dsh.run import FACTS, NODE_ENV, declarations, run_node

OPS = "refactory_packages.circle_packing.ops"


def _env(tmp_path: Path, node_id: str, *, knobs: dict | None = None) -> dict[str, str]:
    return {
        **{k: v for k, v in __import__("os").environ.items()},
        lowering.KNOBS_ENV: json.dumps(knobs or {"budget": 2, "grid": 10, "order": "desc",
                                                 "acceptance_mode": "strict",
                                                 "selector": "best", "seed": 7}),
        "REFACTORY_WORKDIR": str(tmp_path),
        NODE_ENV: json.dumps({"id": node_id, "type": "FnNode", "reads": [], "writes": []}),
    }


def _declare(tmp_path: Path, node_id: str, **decl) -> None:
    path = tmp_path / lowering.DECLARATIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = lowering.read_declarations(tmp_path)
    existing[node_id] = {"reads": [], "writes": [], "produces": [], **decl}
    path.write_text(json.dumps(existing))


def test_a_node_writes_its_files_and_its_facts(tmp_path):
    _declare(tmp_path, "task_input", writes=["task.json", "budget_state.json"],
             produces=["circles"])
    run_node(f"{OPS}:task_input", workdir=tmp_path, node_id="task_input")
    assert (tmp_path / "task.json").exists()
    facts = json.loads((tmp_path / FACTS).read_text())
    assert facts["circles"] == 26


def test_a_second_node_sees_the_first_nodes_facts(tmp_path):
    _declare(tmp_path, "task_input", writes=["task.json", "budget_state.json"],
             produces=["circles"])
    _declare(tmp_path, "seed_run", reads=["task.json"],
             writes=["population.json", "best_solution.json", "genealogy.json",
                     "accepted_history.json", "rejection_history.json"],
             produces=["seed_score"])
    run_node(f"{OPS}:task_input", workdir=tmp_path, node_id="task_input")
    run_node(f"{OPS}:seed_run", workdir=tmp_path, node_id="seed_run")
    facts = json.loads((tmp_path / FACTS).read_text())
    assert facts["seed_score"] == 0.875, "facts must accumulate across node processes"


def test_a_gate_returns_its_verdict_to_the_caller(tmp_path):
    _declare(tmp_path, "task_input", writes=["task.json", "budget_state.json"],
             produces=["circles"])
    _declare(tmp_path, "budget_check", reads=["budget_state.json"], produces=["spent"])
    run_node(f"{OPS}:task_input", workdir=tmp_path, node_id="task_input")
    verdict = run_node(f"{OPS}:budget_check", workdir=tmp_path, node_id="budget_check")
    assert verdict == "reloop", "the budget is not spent yet, so the gate must reloop"


def test_a_node_that_writes_what_it_did_not_declare_is_refused(tmp_path):
    from refactory_dsh.nn import ExecutionError

    _declare(tmp_path, "generate", writes=["nothing.json"])
    with pytest.raises(ExecutionError, match="candidate.json"):
        run_node(f"{OPS}:generate", workdir=tmp_path, node_id="generate")


def test_a_node_that_produces_an_undeclared_fact_is_refused(tmp_path):
    from refactory_dsh.nn import ExecutionError

    _declare(tmp_path, "task_input", writes=["task.json", "budget_state.json"],
             produces=["something_else"])
    with pytest.raises(ExecutionError, match="circles"):
        run_node(f"{OPS}:task_input", workdir=tmp_path, node_id="task_input")


def test_declarations_fall_back_to_the_manifest(tmp_path):
    """Without the run directory the manifest still covers reads and writes."""
    found = declarations(tmp_path, "task_input")
    assert found == {"reads": [], "writes": [], "produces": []}


def test_a_runner_with_no_node_id_refuses_to_guess(tmp_path, monkeypatch):
    """Guessing the target would silently stop enforcing the declarations."""
    from refactory_dsh.run import RunnerError

    monkeypatch.delenv(NODE_ENV, raising=False)
    with pytest.raises(RunnerError, match="no node id"):
        run_node(f"{OPS}:task_input", workdir=tmp_path)
    (tmp_path / lowering.DECLARATIONS).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / lowering.DECLARATIONS).write_text(json.dumps({
        "task_input": {"reads": [], "writes": ["task.json"], "produces": ["circles"]}
    }))
    found = declarations(tmp_path, "task_input")
    assert found["writes"] == ["task.json"] and found["produces"] == ["circles"]


# ── the command line the spine actually invokes ────────────────────────────
def _python() -> str:
    return sys.executable


def test_the_command_line_prints_a_verdict_as_its_last_line(tmp_path):
    _declare(tmp_path, "task_input", writes=["task.json", "budget_state.json"],
             produces=["circles"])
    _declare(tmp_path, "budget_check", reads=["budget_state.json"], produces=["spent"])
    run = lambda node: subprocess.run(  # noqa: E731
        [_python(), "-m", "refactory_dsh.run", f"{OPS}:{node}"],
        env=_env(tmp_path, node if node == "budget_check" else "task_input"),
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert run("task_input").returncode == 0
    result = run("budget_check")
    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[-1] == "reloop", f"the verdict must be the last line: {result.stdout!r}"


def test_a_node_in_a_workspace_package_runs_in_its_own_process(tmp_path):
    """A package created into `.factory/packages` must run on the DSH path.

    The driver makes a workspace package importable by extending
    ``refactory_packages.__path__`` in its own process. A node command is a new
    process, so unless the same root is applied there the import fails and the
    node exits nonzero — which is how a created mode came to score nothing on DSH
    while scoring normally under the walker.
    """
    package = tmp_path / ".factory" / "packages" / "probe_pack"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "ops.py").write_text(
        "def noop(ctx):\n"
        "    ctx.write_json('probe.json', {'ok': True})\n"
        "    return {'ok': True}\n"
    )
    workdir = tmp_path / "work"
    _declare(workdir, "noop", writes=["probe.json"], produces=["ok"])
    env = {**_env(workdir, "noop"), "REFACTORY_PROJECT": str(tmp_path)}
    result = subprocess.run(
        [_python(), "-m", "refactory_dsh.run", "refactory_packages.probe_pack.ops:noop"],
        env=env, capture_output=True, text=True, cwd=str(workdir),
    )
    assert result.returncode == 0, result.stderr
    assert (workdir / "probe.json").exists()


def test_the_command_line_refuses_a_bad_target(tmp_path):
    result = subprocess.run(
        [_python(), "-m", "refactory_dsh.run", "not-a-target"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_the_command_line_fails_loudly_when_a_node_fails(tmp_path):
    _declare(tmp_path, "generate", writes=["whatever.json"])
    result = subprocess.run(
        [_python(), "-m", "refactory_dsh.run", f"{OPS}:generate"],
        env=_env(tmp_path, "generate"), capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 1
    assert "ExecutionError" in result.stderr
