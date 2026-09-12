"""Regression tests for `create-v2`'s emitted mode IR (`_emit_basic_loop`).

Each test pins one defect that the approved repair removed from the emitter, so a
re-emission that reverts any single repair fails here instead of failing inside a
science run. The tests read the emitter's *output* and assert the per-defect
property; none of them snapshots the whole `graph.json`.

Pre-repair proof: run this file against `git show HEAD:refactory/refactory_dsh/ops/scaffold.py`
copied over a package copy, from a NEUTRAL working directory:

    cd /tmp && REFACTORY_EMITTER_ROOT=/tmp/prerepair \\
      PYTHONPATH=/tmp/prerepair /path/to/.venv/bin/python -m pytest <this file> -q

The neutral `cd` and `REFACTORY_EMITTER_ROOT` both matter: `python -m pytest` puts the
current directory first on `sys.path`, so running the same command from `refactory/`
resolves the worktree package, proves the repaired emitter against itself, and reports
every test as passed for a pre-repair emitter. The cross-check in
`test_the_suite_is_testing_the_emitter_it_resolved` turns that silent self-certification
into a failure.

The topology assertions below replicate `spine.ts`'s `unconditionalTarget`,
`advance` and `uncompleteBetween` rather than calling them: they are a model of the
walk the artifact is pinned against, and the executable form of the same properties
lives in `.factory/verification/dup_report_replay.mjs` and `drain_replay.mjs`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from refactory_dsh.ops import scaffold

# The `generate` brief quoted verbatim in the approved specification. The
# pre-repair emitter shipped `system_prompt: ""`, which `spine.ts:285` does not
# fall through, so the model received an empty task.
GENERATE_BRIEF = (
    "Write candidate.py: a complete Python module defining solve(n: int, radii: list[float]) "
    "-> list[tuple[float, float]] that returns exactly n (x, y) centres inside the unit square. "
    "Start from the parent program in selected_parent.json and change it to score higher than that "
    "parent's recorded score. The module must import cleanly and must not import os or subprocess, "
    "open files, or run shell commands."
)

LOOP_STATE = [
    "accepted_history.json", "best_solution.py", "genealogy.json",
    "gepa_state.json", "population.json", "rejection_history.json",
]

# `accept` rewrites the loop state and appends the two JSONL traces on entry
# (`acceptance.py:44-45`).
ACCEPT_WRITES = LOOP_STATE + ["candidates.jsonl", "evaluations.jsonl"]

SEED_COMMAND = (
    "python -m srf.ops.common.seed accepted_history.json best_solution.py "
    "genealogy.json gepa_state.json population.json rejection_history.json"
)

SELECT_PARENT_COMMAND = "python -m srf.ops.run srf.ops.gepa.population:select_parent"
ACCEPT_COMMAND = "python -m srf.ops.run srf.ops.gepa.acceptance:accept_or_reject"


@pytest.fixture()
def graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """The emitted IR, produced by the emitter itself in a scratch work dir."""
    monkeypatch.chdir(tmp_path)
    scaffold._emit_basic_loop()
    emitted = tmp_path / ".factory" / "modes" / "circle_packing_loop.graph.json"
    return json.loads(emitted.read_text())


def _outgoing(graph: dict, node_id: str) -> list[dict]:
    return [edge for edge in graph["edges"] if edge["source"] == node_id]


def _run_eval_result_name(writes: list[str]) -> str:
    """A verbatim model of `run_eval`'s result-name selection (`sandbox.py:50-55`).

    The rule the op executes, not the op: the executed form lives in the Track-1
    harness and in `.factory/verification/drain_replay.mjs`.
    """
    result_name = "eval_result.json"
    for name in sorted(writes):
        if name.endswith(".json"):
            result_name = name
            break
    return result_name


def _unconditional_target(graph: dict, node_id: str) -> str | None:
    """A model of `Spine.unconditionalTarget` (`spine.ts:216-218`)."""
    for edge in _outgoing(graph, node_id):
        if edge["condition"] is None:
            return edge["target"]
    return None


def _uncomplete_between(graph: dict, target: str, gate: str) -> set[str]:
    """A model of `Spine.uncompleteBetween` (`spine.ts:234-257`): `target` plus its
    unconditional downstream, stopping at `gate`. These are the nodes a reloop
    un-completes, i.e. the nodes that re-run on the next iteration."""
    seen: set[str] = set()
    queue = [target]
    while queue:
        node = queue.pop()
        if node in seen or node == gate:
            continue
        seen.add(node)
        for edge in _outgoing(graph, node):
            if edge["condition"] is None and edge["target"] != gate and edge["target"] not in seen:
                queue.append(edge["target"])
    return seen


def test_the_suite_is_testing_the_emitter_it_resolved() -> None:
    """A pre-repair proof run from `refactory/` resolves the worktree package first, because
    `python -m pytest` puts the current directory on `sys.path` ahead of `PYTHONPATH` — and
    would then certify the repaired emitter against itself. Set `REFACTORY_EMITTER_ROOT` to
    make the suite refuse to run against any other copy (see the module docstring)."""
    expected = os.environ.get("REFACTORY_EMITTER_ROOT")
    if expected is None:
        pytest.skip("REFACTORY_EMITTER_ROOT not set — no cross-check requested")
    assert Path(scaffold.__file__).resolve().is_relative_to(Path(expected).resolve())


def test_eval_declares_exactly_one_json_result(graph: dict) -> None:
    """`run_eval` picks its result file with `sorted(writes)`; a second `.json`
    (pre-repair: `budget_state.json`) clobbers the budget counter and exits 1. Eval also
    produces `budget.jsonl` (`budget.py:42-43`) and `budget_state.json`
    (`sandbox.py:88-89`); the `.jsonl` is declared because the selection rule ignores it,
    the `.json` cannot be — it sorts before `eval_result.json` and would be selected.
    The counter the op reads (`budget.py:38`) is declared as a read instead."""
    node = graph["nodes"]["eval"]
    assert node["writes"] == ["budget.jsonl", "eval_result.json"]
    assert [f for f in node["writes"] if f.endswith(".json")] == ["eval_result.json"]
    assert {"candidate.py", "task.yaml"} <= set(node["reads"])
    # `record_eval` reads the counter the same node rewrites; `task_input` writes it
    # and always precedes this node, so declaring the read is safe and true.
    assert "budget_state.json" in node["reads"]
    # The rule that makes the declaration load-bearing, asserted as the run applies it.
    assert _run_eval_result_name(node["writes"]) == "eval_result.json"
    # Declaring the budget counter would select it and raise `KeyError: 'eval_count'`.
    assert "budget_state.json" not in node["writes"]
    assert _run_eval_result_name(node["writes"] + ["budget_state.json"]) == "budget_state.json"


def test_accept_is_an_fnnode_with_the_reference_read_and_write_sets(graph: dict) -> None:
    """As a `GateNode` the op prints no verdict, so the cursor froze on a
    non-idempotent step; `writes: []` also hid six artifacts (F3/F9). The two JSONL
    traces it appends on entry (`acceptance.py:44-45`) are declared too, and neither
    ends in `.json`, so the `run_eval` selection rule ignores them."""
    node = graph["nodes"]["accept"]
    assert node["_type"] == "FnNode"
    assert node["command"] == ACCEPT_COMMAND
    assert "evaluator_type" not in node and "evaluator_command" not in node
    assert {"candidate.py", "eval_result.json", "genealogy.json", "gepa_state.json",
            "population.json", "selected_parent.json"} <= set(node["reads"])
    assert node["writes"] == ACCEPT_WRITES
    assert {"candidates.jsonl", "evaluations.jsonl"} <= set(node["writes"])
    # The selection rule only ever sees `.json`; the two added names are `.jsonl`.
    assert not [f for f in ("candidates.jsonl", "evaluations.jsonl") if f.endswith(".json")]


def test_seed_run_writes_the_loop_state_before_the_cycle(graph: dict) -> None:
    """`accept` reads loop state that nothing produced before it (F6)."""
    node = graph["nodes"]["seed_run"]
    assert node["_type"] == "FnNode"
    assert node["command"] == SEED_COMMAND
    assert node["writes"] == LOOP_STATE
    assert _outgoing(graph, "seed_run") == [
        {"source": "seed_run", "target": "select_parent", "condition": None},
    ]


def test_select_parent_supplies_the_live_incumbent(graph: dict) -> None:
    """Without this node `accept` compares against `parent_score = 0.0` (F5). It also
    rewrites `gepa_state.json` (`population.py:28-30` → `state.py:33-34`), which the
    pre-reloop emitter left undeclared."""
    node = graph["nodes"]["select_parent"]
    assert node["_type"] == "FnNode"
    assert node["command"] == SELECT_PARENT_COMMAND
    assert node["reads"] == ["gepa_state.json", "population.json"]
    assert node["writes"] == ["gepa_state.json", "selected_parent.json"]
    assert "selected_parent.json" in node["writes"]
    assert {"source": "select_parent", "target": "generate", "condition": None} in graph["edges"]


def test_generate_carries_the_brief_and_the_incumbent_reads(graph: dict) -> None:
    """The empty `system_prompt` reached the model as an empty task; the reads
    that make a rejection consequential were missing."""
    node = graph["nodes"]["generate"]
    assert node["system_prompt"] == GENERATE_BRIEF
    assert node["instance_prompt"] == ""
    assert node["temperature"] == 0.7  # node field, never a knob
    assert {"task.yaml", "initial.py", "selected_parent.json",
            "rejection_history.json"} <= set(node["reads"])


def test_budget_gate_declares_its_cap_and_owns_the_loop(graph: dict) -> None:
    """Only `budget_check` may carry conditional edges; an implicit cap burned
    500 reloops (F8). The gate's only two outcomes are the reloop into `eval` and the
    exit from the cycle."""
    assert graph["nodes"]["budget_check"]["max_iterations"] == 500
    conditional_owners = {edge["source"] for edge in graph["edges"] if edge["condition"] is not None}
    assert conditional_owners == {"budget_check"}
    assert _outgoing(graph, "budget_check") == [
        {"source": "budget_check", "target": "eval", "condition": "reloop"},
        {"source": "budget_check", "target": "exit", "condition": "proceed"},
    ]


def test_the_gate_is_the_model_nodes_only_successor(graph: dict) -> None:
    """`generate` is the only node the driver is ever handed, so a repeated completion
    report of it re-advances the walk onto its unconditional successor. With the gate
    there, that successor is a pure read of `budget_state.json` (`budget.py:62-76`); with
    `eval` there (the pre-reposition wiring) the same report evaluated one candidate a
    second time and overshot `--budget`."""
    assert _outgoing(graph, "generate") == [
        {"source": "generate", "target": "budget_check", "condition": None},
    ]
    assert _unconditional_target(graph, "generate") == "budget_check"


def test_every_reloop_target_reruns_select_parent_and_the_model_node(graph: dict) -> None:
    """The reloop lands on `eval`; `uncompleteBetween(eval, budget_check)`
    (`spine.ts:234-257`) un-completes `eval`, `accept`, `select_parent` and `generate`,
    so the next iteration re-selects the incumbent before generating again. Landing the
    back-edge on `generate` instead would freeze `selected_parent.json` at its
    first-iteration content and re-create the `parent_score = 0.0` defect."""
    reloop = [edge for edge in graph["edges"] if edge["condition"] == "reloop"]
    assert len(reloop) == 1
    target = reloop[0]["target"]
    assert target == "eval"
    rerun = _uncomplete_between(graph, target, "budget_check")
    assert rerun == {"eval", "accept", "select_parent", "generate"}
    assert "select_parent" in rerun and "generate" in rerun
    # `select_parent` precedes `generate` on the re-run path, so the model always reads
    # the incumbent the last `accept` left behind.
    assert _unconditional_target(graph, "select_parent") == "generate"


def test_every_non_gate_node_has_one_unconditional_successor(graph: dict) -> None:
    """`advance()` replaces a completed node with its `unconditionalTarget`
    (`spine.ts:357-361`); a non-gate node whose only out-edges are conditional has none
    and ends the walk `done` after one iteration (F11)."""
    for node_id, node in graph["nodes"].items():
        if node["_type"] == "GateNode":
            continue
        outgoing = _outgoing(graph, node_id)
        if node_id == "exit":
            assert outgoing == []
            continue
        assert len(outgoing) == 1, node_id
        assert outgoing[0]["condition"] is None, node_id
        assert _unconditional_target(graph, node_id) is not None, node_id


def test_every_forward_edge_is_unconditional_and_the_dead_edge_is_gone(graph: dict) -> None:
    """A completed non-gate node is replaced by its unconditional successor; a
    conditional-only `accept` ends the walk `done` after one iteration (F11)."""
    assert graph["edges"] == [
        {"source": "task_input", "target": "seed_run", "condition": None},
        {"source": "seed_run", "target": "select_parent", "condition": None},
        {"source": "select_parent", "target": "generate", "condition": None},
        {"source": "generate", "target": "budget_check", "condition": None},
        {"source": "eval", "target": "accept", "condition": None},
        {"source": "accept", "target": "select_parent", "condition": None},
        {"source": "budget_check", "target": "eval", "condition": "reloop"},
        {"source": "budget_check", "target": "exit", "condition": "proceed"},
    ]
    assert _outgoing(graph, "accept") == [
        {"source": "accept", "target": "select_parent", "condition": None},
    ]
    assert graph["nodes"]["exit"]["command"] == "true"


def test_knob_block_declares_only_live_knobs(graph: dict) -> None:
    """`temperature` has no consumer on this path; `off` is implemented and is
    the strongest ablation (E1/E4)."""
    assert "temperature" not in graph["knob_values"]
    assert "temperature" not in graph["knob_bounds"]
    assert "temperature" not in graph["knob_specs"]
    assert graph["knob_values"] == {"acceptance_mode": "strict"}
    assert graph["knob_bounds"]["acceptance_mode"] == ["strict", "lenient", "off"]
    spec = graph["knob_specs"]["acceptance_mode"]
    assert spec["bounds"] == ["strict", "lenient", "off"]
    assert spec["node_id"] == "accept"
    assert "--knobs" in spec["description"]
