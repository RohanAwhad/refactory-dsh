"""The walker's prompts and run files, as the spine's (dm-123, #436 gaps 5, 13, 14, 18, 20).

`test_runtime_equivalence.py` compares one design run on both runtimes. These hold the
same rules on small graphs of any shape: a node's prompt is its rendered template with a
reloop's feedback said once and its inputs in name order, never its notes; the run leaves
`facts.json` and `.refactory/spine-status.json` as the spine does, with the nodes
completed by the spine's rules; a gate's verdict is the run's record, not a fact; and the
readers that ask whether a walked run is still alive keep asking its own progress.
"""
from __future__ import annotations

import json
import subprocess
import sys

from refactory_dsh import nn
from refactory_dsh.nn.executor import PROGRESS


class FakeModel:
    """A model client that records what it was asked."""

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def complete(self, prompt: str, *, system: str = "") -> str:
        self.prompts.append((system, prompt))
        return "ok"


def _reloop_to_agent(gate: str, template: str) -> nn.Graph:
    """An agent, a gate that sends it back twice and says why, and an op after."""
    graph = nn.Graph()
    graph.add(nn.Node("write", "agent", prompt_template=template, writes=("answer.txt",),
                      notes="write the answer"))
    graph.add(nn.Node("gate", "gate", evaluator=gate, max_iterations=4, produces=("passes", "why")))
    graph.add(nn.Node("done", "op", call="loop_ops:noop"))
    graph.connect("write", "gate")
    graph.connect("gate", "write", condition="retry")
    graph.connect("gate", "done", condition="proceed")
    return graph


def _status(run) -> dict:
    return json.loads((run / ".refactory/spine-status.json").read_text())


# ── the prompt ────────────────────────────────────────────────────────────────


def test_a_nodes_notes_are_not_part_of_its_prompt(tmp_path):
    """The spine's worker sends the rendered template alone; so does the walker."""
    model = FakeModel()
    result = nn.execute(_reloop_to_agent("loop_ops:three_passes", "Write it."), tmp_path, model=model)
    assert result.exit_ok, result.error
    assert [system for system, _ in model.prompts] == ["", "", ""]
    assert not any("write the answer" in prompt for _, prompt in model.prompts)


def test_a_reloop_reason_reaches_the_node_it_sends_back_once(tmp_path):
    """A template that does not carry the gate's reason gets it under the feedback heading,
    for the one visit it was given for, as the spine's worker shows it."""
    model = FakeModel()
    result = nn.execute(_reloop_to_agent("loop_ops:explained_passes", "Write it."), tmp_path, model=model)
    assert result.exit_ok, result.error
    first, second, third = (prompt for _, prompt in model.prompts)
    assert "## Feedback for this visit" not in first
    assert second.count("## Feedback for this visit\n\npass 1 of 3\n\nWrite it.") == 1
    assert "pass 2 of 3" in third and "pass 1 of 3" not in third


def test_feedback_is_for_the_visit_it_was_given_for(tmp_path):
    """Spent once the node ran: the next item's pass of the same node does not repeat the
    reason the last item's gate gave (`Spine.completeDue` drops an agent's feedback)."""
    from refactory_dsh.nn.executor import CURRENT_ITEM

    graph = nn.Graph()
    graph.add(nn.Node("items", "data", subgraph_entry="write", subgraph_exit="next",
                      writes=(CURRENT_ITEM,), inline_items=({"id": "a"}, {"id": "b"})))
    graph.add(nn.Node("write", "agent", prompt_template="Write it.", reads=(CURRENT_ITEM,), writes=("answer.txt",)))
    graph.add(nn.Node("gate", "gate", evaluator="loop_ops:retry_each_item_once", max_iterations=4,
                      writes=("tried.json",)))
    graph.add(nn.Node("next", "op", call="loop_ops:noop"))
    graph.connect("write", "gate")
    graph.connect("gate", "write", condition="retry")
    graph.connect("gate", "next", condition="proceed")
    model = FakeModel()
    result = nn.execute(graph, tmp_path, model=model)
    assert result.exit_ok, result.error
    prompts = [prompt for _, prompt in model.prompts]
    assert ["item a" in prompt for prompt in prompts] == [False, True, False, False]
    assert ["item b" in prompt for prompt in prompts] == [False, False, False, True]


def test_a_reloop_reason_the_template_renders_is_not_repeated(tmp_path):
    """A template that renders the reason already says it: no second copy (gap 5)."""
    model = FakeModel()
    result = nn.execute(_reloop_to_agent("loop_ops:explained_passes_in_a_fact", "Write it. Last time: {{why}}"),
                        tmp_path, model=model)
    assert result.exit_ok, result.error
    second = model.prompts[1][1]
    assert "## Feedback for this visit" not in second
    assert second.count("pass 1 of 3") == 1


def test_a_nodes_inputs_are_listed_in_name_order(tmp_path):
    """The lowered graph keeps reads as a set, sorted; the walker lists them so too."""
    for name in ("b.txt", "a.txt"):
        (tmp_path / name).write_text(name)
    (tmp_path / "c").mkdir()
    graph = nn.Graph()
    graph.add(nn.Node("ask", "agent", prompt_template="go", reads=("b.txt", "c/", "a.txt"), writes=("x.txt",)))
    model = FakeModel()
    assert nn.execute(graph, tmp_path, model=model).exit_ok
    inputs = json.loads(model.prompts[0][1].split("<!-- refactory worker execution -->\n", 1)[1]
                        .split("\n", 1)[1].split("\n<!-- end refactory worker execution -->", 1)[0])["inputs"]
    assert [path.rsplit("/", 1)[-1] for path in inputs] == ["a.txt", "b.txt", "c"]


def test_a_gate_visit_records_its_reads_in_name_order(tmp_path):
    """The ledger's gate visit lists the reads its review fingerprint is taken over, sorted."""
    from refactory_dsh.steering import Ledger

    ledger = Ledger(tmp_path)
    ledger.visit("gate", ["design.md", "delivery.md"])
    assert 'reads: ["delivery.md", "design.md"]' in ledger.path.read_text()


# ── the run's files ──────────────────────────────────────────────────────────


def test_the_walker_leaves_the_spines_facts_and_status(tmp_path):
    """`facts.json` holds the run's facts, and the status file the nodes completed by the
    spine's rules: a reloop un-completes what it goes back over, which completes again
    after the gate (`Spine.uncompleteBetween`)."""
    model = FakeModel()
    result = nn.execute(_reloop_to_agent("loop_ops:explained_passes", "Write it."), tmp_path, model=model)
    assert result.exit_ok, result.error
    assert json.loads((tmp_path / "facts.json").read_text()) == {"passes": 3}
    status = _status(tmp_path)
    assert (status["status"], status["nodes"], status["running_nodes"]) == ("done", [], [])
    assert status["completed"] == ["gate", "write", "done"]
    assert status["run_id"] == str(tmp_path.resolve()) and status["at"].endswith("Z")


def test_a_gates_verdict_is_the_runs_record_not_a_fact(tmp_path):
    """No `<gate>_verdict` fact: the verdicts are kept where the spine keeps its own."""
    result = nn.execute(_reloop_to_agent("loop_ops:explained_passes", "Write it."), tmp_path, model=FakeModel())
    assert "gate_verdict" not in result.facts
    assert result.gate_verdicts == {"gate": "proceed"}
    assert json.loads((tmp_path / PROGRESS).read_text())["gate_verdicts"] == {"gate": "proceed"}


def test_a_forks_branches_complete_in_branch_order(tmp_path):
    """The spine completes a fork's branches boundary by boundary in target order, whatever
    finishes first."""
    graph = nn.Graph()
    graph.add(nn.Node("fork", "fork", targets=("slow", "fast")))
    graph.add(nn.Node("slow", "op", call="loop_ops:pause"))
    graph.add(nn.Node("fast", "op", call="loop_ops:noop"))
    graph.add(nn.Node("join", "join", sources=("slow", "fast")))
    graph.add(nn.Node("end", "op", call="loop_ops:noop"))
    graph.connect("fork", "join")
    graph.connect("slow", "join")
    graph.connect("fast", "join")
    graph.connect("join", "end")
    result = nn.execute(graph, tmp_path)
    assert result.exit_ok, result.error
    assert _status(tmp_path)["completed"] == ["fork", "slow", "fast", "join", "end"]


def _user_gate_graph() -> nn.Graph:
    graph = nn.Graph()
    graph.add(nn.Node("first", "op", call="loop_ops:noop"))
    graph.add(nn.Node("gate", "gate", gate_kind="user", notes="Go on?"))
    graph.add(nn.Node("last", "op", call="loop_ops:noop"))
    graph.connect("first", "gate")
    graph.connect("gate", "last", condition="proceed")
    return graph


def test_a_walk_stopped_at_a_user_gate_says_so_and_resumes_with_its_record(tmp_path):
    """Stopped at the gate, the status is the spine's `refused` naming the gate; resumed,
    the run goes on with the nodes it completed."""
    stopped = nn.execute(_user_gate_graph(), tmp_path)
    assert stopped.gate_stop == "gate"
    status = _status(tmp_path)
    assert (status["status"], status["nodes"], status["completed"]) == ("refused", ["gate"], ["first"])
    assert status["wait_reason"] == "waiting for user gate: gate (gate)"
    assert "waiting for a person" in status["error"]
    resumed = nn.execute(_user_gate_graph(), tmp_path, start="gate", verdicts={"gate": "proceed"})
    assert resumed.exit_ok, resumed.error
    assert _status(tmp_path)["completed"] == ["first", "gate", "last"]
    assert resumed.gate_verdicts == {"gate": "proceed"}


def test_a_failed_node_is_named_in_the_status(tmp_path):
    graph = nn.Graph()
    graph.add(nn.Node("first", "op", call="loop_ops:noop"))
    graph.add(nn.Node("broken", "op", call="loop_ops:boom"))
    graph.connect("first", "broken")
    result = nn.execute(graph, tmp_path)
    assert result.exit_ok is False
    status = _status(tmp_path)
    assert (status["status"], status["nodes"], status["completed"]) == ("failed", ["broken"], ["first"])
    assert status["last_failure"]["node"] == "broken"


# ── readers that ask whether a walked run is alive ──────────────────────────


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def _walked_run_left_running(run):
    """A walked run whose process died mid-node: its status still says it is executing."""
    (run / ".refactory").mkdir(parents=True)
    (run / PROGRESS).write_text(json.dumps({"node": "emit", "error": None, "outcome": "running"}))
    (run / ".refactory/spine-status.json").write_text(json.dumps({"status": "executing", "nodes": ["emit"]}))


def test_a_walked_run_whose_process_died_has_ended_for_its_worktree(tmp_path):
    from refactory_dsh import worktree

    _walked_run_left_running(tmp_path)
    assert worktree.state(tmp_path, _dead_pid()) == "ended"
    # A driven run's status is the spine's word, whoever claimed it.
    (tmp_path / ".refactory/spine-checkpoint.json").write_text("{}")
    assert worktree.state(tmp_path, _dead_pid()) == "active"


def test_a_walked_run_whose_process_died_no_longer_holds_its_artifact(tmp_path):
    from refactory_dsh import repo_change

    _walked_run_left_running(tmp_path)
    assert repo_change._still_open(tmp_path, _dead_pid()) is None


def test_the_console_says_a_walked_run_whose_process_died_died(tmp_path):
    from refactory_dsh import console

    _walked_run_left_running(tmp_path)
    (tmp_path / "console.json").write_text(json.dumps({"pid": _dead_pid(), "mode": "default_pack/design"}))
    assert console.status(tmp_path)["state"] == "died"
