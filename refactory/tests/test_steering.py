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
from refactory_dsh.steering import Ledger, SteeringError

ROOT = Path(__file__).resolve().parent.parent.parent
#: A mode with agent nodes, so the transform has something to reach.
MODE = "meta_pack/create"


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
def test_the_annotated_workflow_is_still_valid_ir(tmp_path):
    """A transform that produced an unloadable graph would be worse than none."""
    from refactory_dsh import lowering

    workflow = steering.interactive(
        _state(), tmp_path, request="build a mode for circle packing"
    )[0]
    assert lowering.validate_ir(workflow) == []


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
    from refactory_dsh.loop import Loop, LoopConfig

    root = tmp_path / "run"
    config = LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=5, runs_root=root,
                        intent="the first request")
    Loop("circle_packing/adaptive", config, project=ROOT).run()
    Ledger(root).note("constraint", "no new dependencies")

    resumed = LoopConfig(epochs=1, batch_size=2, optimizer="random", seed=5, runs_root=root,
                         intent="a different request entirely", resume=True)
    Loop("circle_packing/adaptive", resumed, project=ROOT).init()

    assert Ledger(root).entries()[0]["text"] == "the first request", (
        "resuming rewrote the origin of the intent"
    )
    assert Ledger(root).constraints() == ["no new dependencies"], (
        "resuming dropped a constraint that was given while the run was going"
    )


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
