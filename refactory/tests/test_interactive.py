"""Running one mode with a person in the loop (level 3).

The outer loop searches a mode; this runs one for a person to *use*. What these
hold: the intent ledger is folded into the graph before the run (seeding it
afterwards would produce a run nobody steered), the command names the graph the
profile loads, and a run that stopped at a gate is a failure naming the gate
rather than a score of 0.
"""

from __future__ import annotations

import json
from pathlib import Path

from refactory_dsh import dsh_runtime, interactive, registry, steering

ROOT = Path(__file__).resolve().parent.parent.parent
#: A mode with agent nodes, so "the ledger reaches the agents" has something to reach.
WITH_AGENTS = "default_pack/create"
#: A mode with a gate a person answers and a deterministic gate.
WITH_A_GATE = "review_pack/review"


def test_prepare_seeds_the_ledger_then_folds_it_into_the_graph(tmp_path):
    """Seeded **before** the lowering: the lowering is what reaches the agents."""
    name, graphs = interactive.prepare(
        WITH_AGENTS, workdir=tmp_path / "run", intent="write a counter mode", project=ROOT
    )
    assert (tmp_path / "run" / "intent-ledger.md").exists()

    graph = json.loads((graphs / f"{name}.graph.json").read_text())
    prompts = " ".join(
        node.get("prompt_template") or ""
        for node in graph["nodes"].values()
        if node.get("_type") == "AgentNode"
    )
    assert "write a counter mode" in prompts, (
        "the graph the profile loads does not carry the intent, so nobody steered it"
    )


def test_prepare_leaves_the_mode_unchanged(tmp_path):
    """D15's property for the interactive path: the mode is the author's."""
    source = ROOT / "refactory" / "refactory_packages" / "review_pack"
    before = {
        path: path.read_bytes() for path in sorted(source.rglob("*.py"))
    }
    interactive.prepare(WITH_A_GATE, workdir=tmp_path / "run", intent="review this", project=ROOT)
    after = {path: path.read_bytes() for path in sorted(source.rglob("*.py"))}
    assert after == before, "lowering for a run rewrote the mode it lowered"


def test_the_command_names_the_graph_the_profile_loads(tmp_path):
    name, graphs = interactive.prepare(
        WITH_A_GATE, workdir=tmp_path / "run", intent="review this", project=ROOT
    )
    argv = interactive.command(WITH_A_GATE, workdir=tmp_path / "run", graphs=graphs, name=name)
    assert argv[argv.index("--mode") + 1] == name
    assert argv[argv.index("--graphs") + 1] == str(graphs)
    assert (graphs / f"{name}.graph.json").exists()


def test_the_command_hands_the_knobs_the_node_runner_can_read(tmp_path):
    """The flag is the knob JSON itself, not a JSON string containing it.

    The node runner accepts `--knobs` only when the value parses to an object; a
    double-encoded string leaves every node on its op's fallback default, so the
    run under DSH is not the candidate the loop scored on the walker.
    """
    name, graphs = interactive.prepare(
        WITH_A_GATE, workdir=tmp_path / "run", intent="review this", project=ROOT
    )
    argv = interactive.command(WITH_A_GATE, workdir=tmp_path / "run", graphs=graphs, name=name)
    knobs = json.loads(argv[argv.index("--knobs") + 1])
    assert isinstance(knobs, dict), f"--knobs must carry the values, got {knobs!r}"
    expected = registry.resolve(ROOT, WITH_A_GATE)[1]().state_dict()["knobs"]
    assert knobs == {key: entry["value"] for key, entry in expected.items()}


def test_a_run_that_refused_a_gate_is_a_failure_naming_it(tmp_path, monkeypatch):
    """The refusal must not read as a run that merely scored 0."""
    mode = registry.resolve(ROOT, WITH_A_GATE)[1]()
    work = tmp_path / "run"
    (work / ".refactory").mkdir(parents=True)
    (work / ".refactory" / "spine-status.json").write_text(
        json.dumps({"status": "refused", "error": "gate approve is answered by a person"})
    )

    def _fake_run(state, workdir, **kwargs):
        return dsh_runtime.DshRun(facts={"topic": "x"}, workdir=Path(workdir), returncode=0)

    monkeypatch.setattr(dsh_runtime, "run_on_dsh", _fake_run)
    result = dsh_runtime.evaluate_on_dsh(mode, mode.state_dict(), work)
    assert result.exit_ok is False
    assert "gate approve" in (result.error or ""), result.error
    assert "did not finish" in (result.error or "")


def test_a_run_the_spine_finished_is_scored(tmp_path, monkeypatch):
    """The other side: a `done` status scores normally."""
    mode = registry.resolve(ROOT, WITH_A_GATE)[1]()
    work = tmp_path / "run"
    (work / ".refactory").mkdir(parents=True)
    (work / ".refactory" / "spine-status.json").write_text(json.dumps({"status": "done"}))

    def _fake_run(state, workdir, **kwargs):
        return dsh_runtime.DshRun(
            facts={"published_chars": 42}, workdir=Path(workdir), returncode=0
        )

    monkeypatch.setattr(dsh_runtime, "run_on_dsh", _fake_run)
    result = dsh_runtime.evaluate_on_dsh(mode, mode.state_dict(), work)
    assert result.exit_ok is True, result.error
    assert result.score == 42.0
