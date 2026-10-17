"""Steering a run while using it: the ledger, and the transform that carries it.

Structure is the outer loop's job. Steering is the person's, and the graph does not
change -- what changes is what the agents know. These assert the three properties that
make that safe:

- the layer is **inert when unused**, so a run nobody steered is the run it would have
  been and the interactive and headless paths cannot silently disagree;
- a note **reaches every agent node**, so a mode author cannot be the thing that makes
  steering work;
- seeding **refuses a default**, which is BUG-05: a ledger holding a placeholder is
  one a fidelity gate cannot check, and it looks identical to a filled-in one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refactory_dsh import registry, steering
from refactory_dsh import authoring_evidence as evidence
from refactory_dsh import nn
from refactory_dsh.steering import Ledger, SteeringError

from reference_ir import needs_factory, validate_reference

ROOT = Path(__file__).resolve().parent.parent.parent
#: A mode with agent nodes, so the transform has something to reach.
MODE = "default_pack/create"


def _state() -> dict:
    return registry.resolve(ROOT, MODE)[1]().state_dict()


def _agent_prompts(workflow) -> list[str]:
    return [
        node.prompt_template
        for node in workflow.nodes.values()
        if node.__class__.__name__ == "AgentNode"
    ]


# ── the layer is inert when unused ─────────────────────────────────────────
def test_an_unsaid_run_is_the_run_it_would_have_been(tmp_path):
    """The property that lets the interactive and headless paths be compared.

    An empty ledger renders nothing, so `annotate` is a no-op and the workflow is the
    object it was handed. Without this the two paths could disagree about one mode with
    no way to tell which was wrong.
    """
    from refactory_dsh import lowering

    plain = lowering.to_workflow(_state(), name="m")
    before = _agent_prompts(plain)

    ledger = Ledger(tmp_path)
    assert ledger.render() == "", "an empty ledger rendered something"
    annotated = steering.annotate(plain, ledger)

    assert annotated is plain, "an empty ledger produced a different workflow"
    assert _agent_prompts(annotated) == before, "an unsaid run's prompts changed"


def test_the_headless_lowering_is_untouched_by_this_module(tmp_path):
    """Steering is a layer over the IR, not a branch inside the lowering."""
    import inspect

    from refactory_dsh import lowering

    for name in ("to_workflow", "write_mode_graph"):
        source = inspect.getsource(getattr(lowering, name))
        assert "steering" not in source, (
            f"`{name}` knows about steering, so a mode is now lowered differently "
            "depending on who is going to run it"
        )


# ── a note reaches every agent node ────────────────────────────────────────
def test_a_note_reaches_every_agent_node(tmp_path):
    """Every one, not the node an author remembered to wire."""
    workflow = steering.interactive(
        _state(), tmp_path, request="build a mode for circle packing"
    )[0]
    prompts = _agent_prompts(workflow)
    assert len(prompts) >= 2, "the fixture has too few agent nodes to prove anything"
    for prompt in prompts:
        assert "build a mode for circle packing" in prompt, (
            "an agent node did not receive the ledger, so steering is per-node and a "
            "mode author can forget it"
        )


def test_the_three_kinds_are_recorded_and_distinguishable(tmp_path):
    """`constraint` is not `info`: one has to be respected, the other only known."""
    ledger = Ledger.seed(tmp_path, "build a mode")
    ledger.note("info", "the task is circle packing")
    ledger.note("correction", "it is 26 circles, not 25")
    ledger.note("constraint", "do not add a numpy dependency")

    kinds = [entry["kind"] for entry in ledger.entries()]
    assert kinds == ["info", "info", "correction", "constraint"] or kinds[1:] == [
        "info", "correction", "constraint",
    ], kinds
    assert ledger.constraints() == ["do not add a numpy dependency"]
    assert "do not add a numpy dependency" in ledger.render()


def test_an_unknown_kind_is_refused(tmp_path):
    ledger = Ledger.seed(tmp_path, "build a mode")
    with pytest.raises(SteeringError, match="unknown note kind"):
        ledger.note("shout", "do the thing")


def test_an_empty_note_is_refused(tmp_path):
    ledger = Ledger.seed(tmp_path, "build a mode")
    with pytest.raises(SteeringError, match="records nothing"):
        ledger.note("info", "   ")


# ── the ledger is a run artifact ───────────────────────────────────────────
def test_the_ledger_survives_being_reopened(tmp_path):
    """Resume must replay what you said, not ask you again."""
    Ledger.seed(tmp_path, "build a mode")
    Ledger(tmp_path).note("constraint", "no new dependencies")

    reopened = Ledger(tmp_path)
    assert [entry["kind"] for entry in reopened.entries()] == ["Initial Idea", "constraint"]
    assert reopened.constraints() == ["no new dependencies"]


def test_past_multiline_text_survives_round_trip_and_console_reads(tmp_path):
    from refactory_dsh.console import _steer

    original = "Build the workflow.\n\nAcceptance:\n  - Keep upstream portable\n  - Keep the gears"
    correction = "Use this shape:\n\n```python\ndef run():\n    return 42\n```\n\nPreserve all past notes."
    ledger = Ledger.seed(tmp_path, original)
    ledger.note("correction", correction)
    ledger.note("constraint", "Never replace the original brief.")
    entries = _steer(tmp_path)["entries"]
    assert [entry["text"] for entry in entries] == [original, correction, "Never replace the original brief."]
    assert [entry["kind"] for entry in entries] == ["Initial Idea", "correction", "constraint"]
    assert original in ledger.render() and correction in ledger.render()


def test_full_history_keeps_gate_decisions_without_replaying_a_used_answer(tmp_path):
    ledger = Ledger.seed(tmp_path, "Review this workflow.\n\nKeep the original request.")
    review = ledger.visit("review", [])
    ledger.verdict("review", "proceed")
    assert ledger.take_verdict("review", review, ["proceed", "halt"]) == "proceed"
    assert ledger.take_verdict("review", review, ["proceed", "halt"]) == ""
    entries = Ledger(tmp_path).entries()
    assert [entry["kind"] for entry in entries] == ["Initial Idea", "gate-visit", "verdict", "verdict-use"]
    assert "verdict: proceed" in entries[2]["text"]
    assert "result: used" in entries[3]["text"]


def test_seeding_twice_is_refused(tmp_path):
    """A second seed would put two origins in one ledger."""
    Ledger.seed(tmp_path, "build a mode")
    with pytest.raises(SteeringError, match="seeded once"):
        Ledger.seed(tmp_path, "something else entirely")


# ── seeding, which is where BUG-05 lived ───────────────────────────────────
def test_seeding_refuses_an_empty_request(tmp_path):
    with pytest.raises(SteeringError, match="empty intent"):
        Ledger.seed(tmp_path, "")


def test_seeding_refuses_the_placeholder(tmp_path):
    """BUG-05: `REFACTORY_INTENT` never arrived and the ledger held this instead.

    The fidelity gate then had nothing to check against, checked nothing, and reported
    a pass. Refusing it here is what stops that being reintroduced by a caller.
    """
    never = tmp_path / "never-written"
    with pytest.raises(SteeringError, match="placeholder"):
        Ledger.seed(never, steering.PLACEHOLDER)
    assert not never.exists(), "it wrote before refusing"


# ── the fidelity gate checks the precondition, not the output ──────────────
def test_the_gate_reports_a_missing_ledger(tmp_path):
    problems = steering.fidelity(Ledger(tmp_path))
    assert problems and "no intent ledger" in problems[0]


def test_the_gate_passes_a_real_ledger(tmp_path):
    assert steering.fidelity(Ledger.seed(tmp_path, "build a mode for circle packing")) == []


def test_the_gate_reports_a_defaulted_ledger(tmp_path):
    """The BUG-05 shape: the file exists, and the gate used to pass it anyway."""
    (tmp_path / steering.LEDGER).write_text(
        f"# User Intent Ledger\n\n## [2026-01-01T00:00:00+00:00] Initial Idea\n"
        f"{steering.PLACEHOLDER}\n"
    )
    problems = steering.fidelity(Ledger(tmp_path))
    assert problems, "a ledger holding the placeholder was reported as fine"
    assert "placeholder" in problems[0]


# ── what the transform produces is still a valid workflow ──────────────────
@pytest.mark.toolchain("factory")
@needs_factory
def test_the_annotated_workflow_is_still_valid_ir(tmp_path):
    """A transform that produced an unloadable graph would be worse than none."""
    workflow = steering.interactive(
        _state(), tmp_path, request="build a mode for circle packing"
    )[0]
    assert validate_reference(workflow) == []


# ── the ledger as a run artifact ───────────────────────────────────────────
def test_a_run_seeds_the_ledger_from_its_intent(tmp_path):
    """The CLI flag and the run agree about where the ledger lives."""
    from refactory_dsh.loop import Loop, LoopConfig

    root = tmp_path / "run"
    Loop(
        "circle_packing/adaptive",
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=5, runs_root=root,
                   intent="make circle packing better without new dependencies"),
        project=ROOT,
    ).run()

    ledger = Ledger(root)
    assert ledger.exists(), "the run did not leave a ledger"
    assert ledger.entries()[0]["text"].startswith("make circle packing better")
    assert steering.fidelity(ledger) == []


def test_a_run_nobody_steered_writes_no_ledger(tmp_path):
    """No ledger beats an empty one that looks like a statement."""
    from refactory_dsh.loop import Loop, LoopConfig

    root = tmp_path / "run"
    Loop(
        "circle_packing/adaptive",
        LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=5, runs_root=root),
        project=ROOT,
    ).run()
    assert not Ledger(root).exists(), (
        "a run with no intent wrote a ledger, which a fidelity check would then read"
    )


def test_a_resumed_run_keeps_what_it_was_told(tmp_path):
    """Re-seeding on resume would put a second origin in the ledger."""
    from refactory_dsh.loop import Loop, LoopConfig, LoopError

    root = tmp_path / "run"
    config = LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=5, runs_root=root,
                        intent="the first request")
    Loop("circle_packing/adaptive", config, project=ROOT).run()
    Ledger(root).note("constraint", "no new dependencies")

    resumed = LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=5, runs_root=root,
                         intent="the first request", resume=True)
    Loop("circle_packing/adaptive", resumed, project=ROOT).init()

    assert Ledger(root).entries()[0]["text"] == "the first request", (
        "resuming rewrote the origin of the intent"
    )
    assert Ledger(root).constraints() == ["no new dependencies"], (
        "resuming dropped a constraint that was given while the run was going"
    )

    # A different request is a different evaluation context, so the resume is
    # refused rather than reusing cached scores for a program it no longer describes.
    changed = LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=5, runs_root=root,
                         intent="a different request entirely", resume=True)
    with pytest.raises(LoopError, match="evaluation context changed"):
        Loop("circle_packing/adaptive", changed, project=ROOT).init()


def test_the_console_can_add_a_note_mid_run(tmp_path):
    """What a person does while using a mode: add context, correct it, forbid it."""
    from refactory_dsh import console

    root = tmp_path / "run"
    root.mkdir()
    Ledger.seed(root, "build a mode for circle packing")

    payload = console._steer(  # noqa: SLF001 - the console verb, without a process
        root, note="do not add a numpy dependency", kind="constraint"
    )
    assert payload["constraints"] == ["do not add a numpy dependency"]
    assert payload["problems"] == []


def test_the_console_reports_a_run_with_no_ledger(tmp_path):
    from refactory_dsh import console

    root = tmp_path / "run"
    root.mkdir()
    payload = console._steer(root)  # noqa: SLF001
    assert payload["exists"] is False
    assert payload["problems"], "a run with no ledger was reported as fine"


# ── a gate a person answers ────────────────────────────────────────────────
#
# The half of #36 that was missed the first time and closed as if it had landed:
# `lowering` hardcoded `evaluator_type="fn"` and `nn.Node` had no gate kind, so a
# mode could not declare a pause point at all. These are the tests that would have
# caught that.


def _gated_mode():
    """A mode whose one gate is answered by a person."""
    from refactory_dsh import nn

    class Gated(nn.Mode):
        name = "gated"

        def build(self) -> nn.Graph:
            graph = nn.Graph()
            graph.add(nn.Node("ask", "gate", gate_kind="user", notes="ask the person"))
            graph.add(nn.Node(
                "done", "op", call="refactory_packages.sympy_pack.ops:load_problems",
                writes=("problems.json",), produces=("problem_count",),
            ))
            graph.connect("ask", "done", condition="proceed")
            return graph

        def score(self, run):
            return 1.0

    return Gated


def test_a_mode_can_declare_a_gate_a_person_answers(tmp_path):
    from refactory_dsh import lowering

    state = _gated_mode()().state_dict()
    workflow = lowering.to_workflow(state, name="gated")
    gate = workflow.nodes["ask"]
    assert gate.evaluator_type == "user", (
        f"the declared gate kind did not reach the IR: {gate.evaluator_type!r}"
    )
    assert not gate.evaluator_command, (
        "a user gate was given a command, so a program and a person would both answer it"
    )


def test_a_headless_run_refuses_a_gate_meant_for_a_person(tmp_path):
    """A run that pauses cannot quietly decide for you.

    This is the point of the gate kind: inventing a verdict would produce a result
    nobody can account for, and it would be indistinguishable from one where the
    person actually said something.
    """
    from refactory_dsh.nn.executor import ExecutionError

    mode = _gated_mode()()
    result = mode.evaluate(tmp_path / "run")
    assert result.exit_ok is False
    assert "answered by 'user'" in (result.error or ""), result.error


def test_a_supplied_verdict_lets_it_proceed(tmp_path):
    """An interactive run collects the answer; a resumed run replays it."""
    from refactory_dsh.nn.executor import execute

    graph = _gated_mode()().build()
    result = execute(graph, tmp_path / "run", verdicts={"ask": "proceed"})
    assert result.exit_ok, result.error


def test_an_unknown_verdict_is_still_refused(tmp_path):
    """A verdict that is not one of the gate's outcomes is not an answer."""
    from refactory_dsh.nn.executor import execute

    graph = _gated_mode()().build()
    result = execute(graph, tmp_path / "run", verdicts={"ask": "nonsense"})
    assert result.exit_ok is False
    assert "nonsense" in (result.error or ""), result.error


def test_a_gate_cannot_have_two_answerers():
    """An evaluator AND a person would make the answer depend on the runtime."""
    from refactory_dsh.nn.graph import GraphError, Node

    with pytest.raises(GraphError, match="two things would answer it"):
        Node("g", "gate", gate_kind="user", evaluator="pkg:fn")


# ── the verdict is recorded, so a resume replays it (#41) ──────────────────


def test_a_verdict_is_recorded_and_read_back(tmp_path):
    ledger = Ledger.seed(tmp_path, "build a mode for circle packing")
    assert ledger.verdicts() == {}
    ledger.verdict("ask", "proceed", approval=True)
    ledger.verdict("confirm", "halt", approval=True)
    assert ledger.verdicts() == {"ask": "proceed", "confirm": "halt"}
    body = ledger.path.read_text()
    assert "gate: ask" in body and "verdict: proceed" in body


def test_the_last_verdict_for_a_gate_wins(tmp_path):
    """A person may change their mind while the run is paused."""
    ledger = Ledger.seed(tmp_path, "build a mode")
    ledger.verdict("ask", "proceed", approval=True)
    ledger.verdict("ask", "halt", approval=True)
    assert ledger.verdicts() == {"ask": "halt"}


def test_a_verdict_needs_a_gate_and_a_value(tmp_path):
    ledger = Ledger.seed(tmp_path, "build a mode")
    with pytest.raises(SteeringError, match="single-line gate"):
        ledger.verdict("", "proceed")
    with pytest.raises(SteeringError, match="nonempty value"):
        ledger.verdict("ask", "   ")


def test_the_console_records_a_verdict_so_the_runtime_can_write_it(tmp_path):
    """The graph profile's answerer records a person's choice through this verb."""
    from refactory_dsh import console

    # A public steer answer is scoped to the reviewed visit, so the run has to have
    # reached the gate: the visit is what records the declaration the answer cites.
    Ledger.seed(tmp_path, "build a mode")
    Ledger(tmp_path).visit("ask", ["draft.md"])
    payload = console._steer(tmp_path, verdicts=("ask=proceed",))
    assert payload["verdicts"] == {"ask": "proceed"}
    assert "verdict: proceed" in (tmp_path / "intent-ledger.md").read_text()


def test_a_gate_a_person_answered_is_replayed_on_the_next_run(tmp_path):
    """A resumed run is the same run, not a second question.

    The first execution has nobody's answer and refuses; the second finds the
    verdict in the ledger and completes on it. `RunContext.verdicts` is no longer
    the only way in -- which is what "a resumed run replays the answer" means.
    """
    from refactory_dsh.nn.executor import execute

    work = tmp_path / "run"
    Ledger.seed(work, "build a mode for circle packing")
    first = execute(_gated_mode()().build(), work)
    assert first.exit_ok is False
    assert "answered by 'user'" in (first.error or ""), first.error

    Ledger(work).verdict("ask", "proceed")
    replayed = execute(_gated_mode()().build(), work)
    assert replayed.exit_ok, replayed.error


def test_an_explicit_verdict_still_wins_over_the_ledger(tmp_path):
    """A caller can override the recorded answer, which is what a test needs."""
    from refactory_dsh.nn.executor import execute

    work = tmp_path / "run"
    Ledger.seed(work, "build a mode")
    Ledger(work).verdict("ask", "proceed", approval=True)
    result = execute(_gated_mode()().build(), work, verdicts={"ask": "nonsense"})
    assert result.exit_ok is False
    assert "nonsense" in (result.error or ""), result.error


# ── the chain: a node that ignored the ledger is caught (#43) ──────────────


def test_the_ledger_instruction_forbids_appending_citations(tmp_path):
    """The node is told to read the ledger, not to litter its artifact with proofs.

    A citation appended to a code or JSON artifact is not evidence the constraint was
    followed, and the runtime keeps supplied context and artifact hashes in its own
    receipt instead.
    """
    ledger = Ledger.seed(tmp_path, "build a mode for circle packing")
    workflow = steering.interactive(_state(), tmp_path)[0]
    prompts = " ".join(_agent_prompts(workflow))
    assert "Do not append steering citations" in prompts, (
        "agent nodes were not told to keep citations out of their artifacts"
    )
    assert ledger.entries()[0]["at"] in prompts, "the entries to read were not listed"


def test_the_instruction_names_the_ledger_path_so_a_mid_run_note_is_read(tmp_path):
    """The inlined graph cannot carry a note added while it runs; the path can.

    The lowered prompt is fixed at boot, so "a note added mid-run reaches the nodes
    that run afterwards" only holds if the node re-reads the file. The instruction
    says where it is and that the newest entry wins.
    """
    ledger = Ledger.seed(tmp_path, "build a mode")
    workflow = steering.interactive(_state(), tmp_path)[0]
    prompts = " ".join(_agent_prompts(workflow))
    assert str(ledger.path) in prompts, "the prompt did not name the live ledger"
    assert "Read the live ledger" in prompts


def test_a_note_added_mid_run_is_live_for_a_later_node(tmp_path):
    """An agent that follows the instruction sees what the person said mid-run."""
    import refactory_dsh.nn as nn
    from refactory_dsh.nn import executor

    work = tmp_path / "run"
    ledger = Ledger.seed(work, "write a counter mode")
    graph = nn.Graph()
    graph.add(nn.Node("first", "agent", prompt_template="A", writes=("a.md",)))
    graph.add(nn.Node("second", "agent", prompt_template="B", writes=("b.md",)))
    graph.connect("first", "second")

    class Reader:
        """A stand-in agent: it reads the live ledger its prompt names."""

        def __init__(self) -> None:
            self.saw: list[bool] = []
            self.prompted_path = False

        def complete(self, prompt: str, system: str = "") -> str:
            self.prompted_path = self.prompted_path or str(ledger.path) in prompt
            if not self.saw:
                # The person adds a constraint while the run is going.
                Ledger(work).note("constraint", "no numpy")
            self.saw.append("no numpy" in ledger.path.read_text())
            return f"{steering.CITATION} {ledger.entries()[0]['at']}"

    reader = Reader()
    result = executor.execute(graph, work, model=reader)
    assert result.exit_ok, result.error
    assert reader.prompted_path, "the agent was not told where the live ledger is"
    assert reader.saw[1] is True, "the node after the note did not see it"


def test_a_node_that_cited_nothing_is_caught(tmp_path):
    ledger = Ledger.seed(tmp_path, "build a mode")
    problems = steering.chain(ledger, {"writer": "I did the work."})
    assert problems and "cites no ledger entry" in problems[0]


def test_a_dangling_citation_is_caught(tmp_path):
    ledger = Ledger.seed(tmp_path, "build a mode")
    problems = steering.chain(
        ledger, {"writer": f"{steering.CITATION} 1999-01-01T00:00:00+00:00"}
    )
    assert problems and "dangles" in problems[0]


def test_a_citation_that_exists_passes(tmp_path):
    ledger = Ledger.seed(tmp_path, "build a mode")
    stamp = ledger.entries()[0]["at"]
    assert steering.chain(ledger, {"writer": f"done.\n{steering.CITATION} {stamp}"}) == []


def test_fidelity_checks_the_chain_only_when_it_is_given_the_outputs(tmp_path):
    """Says which half it is: the precondition alone, or the chain too."""
    ledger = Ledger.seed(tmp_path, "build a mode")
    assert steering.fidelity(ledger) == []
    problems = steering.fidelity(ledger, {"writer": "no citation"})
    assert problems, "the chain half was not checked when the outputs were given"


def test_outputs_in_reads_what_the_agent_nodes_wrote(tmp_path):
    """Read from the declarations beside the run, so either runtime can be checked."""
    from refactory_dsh import lowering

    work = tmp_path / "run"
    work.mkdir()
    lowering.write_declarations(_state(), work)
    declared = lowering.read_declarations(work)
    node_id, entry = next(
        (nid, e)
        for nid, e in declared.items()
        if e.get("kind") == "agent" and e.get("writes")
    )
    target = work / entry["writes"][0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"the work\n{steering.CITATION} x\n")

    outputs = steering.outputs_in(work)
    assert "the work" in outputs[node_id]
    assert all(declared[nid].get("kind") == "agent" for nid in outputs), (
        "a deterministic node was treated as an agent node"
    )


# ── ask 5: verdict consumed display ───────────────────────────────────────


def test_verdict_consumed_after_use(tmp_path):
    """After take_verdict uses a verdict, the original entry shows consumed: yes."""
    ledger = Ledger.seed(tmp_path, "build a mode")
    (tmp_path / "design.md").write_text("the design")
    review = ledger.visit("gate_check", ["design.md"])
    entry = ledger.verdict("gate_check", "proceed")
    verdict_id = entry["id"]
    review = ledger.visit("gate_check", ["design.md"])
    result = ledger.take_verdict("gate_check", review, ["proceed", "reloop"])
    assert result == "proceed"
    text = ledger.path.read_text()
    assert f"id: {verdict_id}" in text
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"id: {verdict_id}":
            found_consumed = False
            for j in range(i, min(i + 10, len(lines))):
                if lines[j].strip() == "consumed: yes":
                    found_consumed = True
                    break
            assert found_consumed, f"verdict {verdict_id} should show consumed: yes"
            break


def test_verdict_consumed_after_expiry(tmp_path):
    """An expired verdict (scope mismatch) is also marked consumed: yes."""
    ledger = Ledger.seed(tmp_path, "build a mode")
    (tmp_path / "design.md").write_text("the design")
    review = ledger.visit("gate_check", ["design.md"])
    entry = ledger.verdict("gate_check", "proceed")
    verdict_id = entry["id"]
    (tmp_path / "design.md").write_text("changed design")
    new_review = ledger.visit("gate_check", ["design.md"])
    result = ledger.take_verdict("gate_check", new_review, ["proceed", "reloop"])
    assert result == "", "expired verdict should return empty string"
    text = ledger.path.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"id: {verdict_id}":
            found_consumed = False
            for j in range(i, min(i + 10, len(lines))):
                if lines[j].strip() == "consumed: yes":
                    found_consumed = True
                    break
            assert found_consumed, "expired verdict should also show consumed: yes"
            break


def test_unused_verdict_keeps_consumed_no(tmp_path):
    """A verdict that was never taken keeps consumed: no."""
    ledger = Ledger.seed(tmp_path, "build a mode")
    (tmp_path / "design.md").write_text("the design")
    ledger.visit("gate_check", ["design.md"])
    entry = ledger.verdict("gate_check", "proceed")
    verdict_id = entry["id"]
    text = ledger.path.read_text()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"id: {verdict_id}":
            found = False
            for j in range(i, min(i + 10, len(lines))):
                if lines[j].strip() == "consumed: no":
                    found = True
                    break
            assert found, "untaken verdict should keep consumed: no"
            break


def test_marking_consumed_is_identity_safe(tmp_path):
    """Marking a verdict consumed after evidence is recorded does not trigger
    current_problems, because consumed is excluded from the identity digest."""
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "answer.txt").write_text("42")
    ctx = nn.RunContext(tmp_path, facts={"brief": "Deliver the answer", "artifact": str(root)})
    ctx.write_json("brief.json", {"brief": ctx.facts["brief"]})
    ledger = Ledger.seed(tmp_path, ctx.facts["brief"])
    (tmp_path / "design.md").write_text("the design")
    review = ledger.visit("gate_check", ["design.md"])
    entry = ledger.verdict("gate_check", "proceed")
    evidence.record(ctx, {"status": "pass", "command": "test", "stdout": "42"})
    review = ledger.visit("gate_check", ["design.md"])
    ledger.take_verdict("gate_check", review, ["proceed", "reloop"])
    problems = evidence.current_problems(ctx)
    identity_problem = [p for p in problems if "intent changed" in p]
    assert not identity_problem, (
        f"marking consumed must not change identity: {identity_problem}"
    )


def test_the_dsh_runtime_loads_the_ledger_into_its_agent_nodes(tmp_path, monkeypatch):
    """The runtime that ships gets the ledger too, not only the walker.

    Both runtimes annotate through `steering.for_run`, because a difference between
    them is exactly what nothing would report.
    """
    import json

    from refactory_dsh import dsh_profile
    from refactory_dsh.dsh_runtime import run_on_dsh

    home = tmp_path / "home"
    (home / "profiles" / "graph").mkdir(parents=True)
    (home / "profiles" / "graph" / "package.json").write_text(
        json.dumps({"dsh": {"profile": {"bundles": [dsh_profile.GRAPH_BUNDLE]}}})
    )
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.delenv("REFACTORY_MODEL_DSH_HOME", raising=False)

    work = tmp_path / "run"
    Ledger.seed(work, "build a mode for circle packing")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "stopped"

    monkeypatch.setattr(
        "refactory_dsh.dsh_runtime.subprocess.run", lambda *args, **kwargs: Result()
    )
    run_on_dsh(_state(), work, name="probe")

    graph = json.loads((work / "graphs" / "probe.graph.json").read_text())
    prompts = " ".join(
        node.get("prompt_template") or ""
        for node in graph["nodes"].values()
        if node.get("_type") == "AgentNode"
    )
    assert "build a mode for circle packing" in prompts, (
        "the DSH graph's agent nodes did not carry the ledger"
    )
