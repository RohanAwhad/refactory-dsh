"""Optimizers: proposal, refusal with reasons, and the validating loop.

Phase 5 gate of ``docs/plans/pythonic-modes.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refactory_dsh import nn, optim, registry
from refactory_dsh.optim import Candidate, Optimizer, Proposal, ProposalContext

MODE = "circle_packing/adaptive"


def _mode() -> nn.Mode:
    _package, mode_class = registry.resolve(Path("."), MODE)
    return mode_class()


def _context(mode: nn.Mode | None = None, *, preset: str = "broad") -> ProposalContext:
    mode = mode or _mode()
    described = mode.describe()
    return ProposalContext(
        epoch=1,
        generation=1,
        mode_name="circle_packing/adaptive",
        best_score=0.875,
        mean_score=0.875,
        knobs=[{"name": name, **spec} for name, spec in described["knobs"].items()],
        nodes=described["nodes"],
        edges=described["edges"],
        leaderboard=[
            {"id": "seed", "parent": None, "score": 0.875, "structural": False, "edits": []}
        ],
        traces=[{"knob_attribution": {"order": {"asc": 1.04, "desc": 0.875}}}],
        catalogue=nn.edits.catalogue(preset),
        insertable=[node.to_state() for node in mode.insertable()],
        preset=preset,
    )


class ScriptedOptimizer(Optimizer):
    """Proposes a fixed list of edits and records the feedback it was given."""

    name = "scripted"

    def __init__(self, edits: list[dict], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._edits = list(edits)
        self.feedback_seen: list[list[str]] = []

    def propose(self, ctx, n, parents):  # type: ignore[no-untyped-def]
        self.feedback_seen.append(list(ctx.rejections))
        return [
            Proposal(edit=edit, parent=parents[0].id, rationale="scripted")
            for edit in self._edits[:n]
        ]


# ── proposal ───────────────────────────────────────────────────────────────
def test_the_random_optimizer_proposes_valid_candidates():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = optim.RandomOptimizer(seed=1)
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 4, parents=parents, invariants=mode.invariants()
    )
    assert candidates, [r.reasons for r in rejections]
    for candidate in candidates:
        assert candidate.parent == parents[0].id
        assert nn.validate_state(
            candidate.state, invariants=mode.invariants()
        ) == []


def test_the_random_optimizer_respects_the_batch_size():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = optim.RandomOptimizer(seed=3)
    candidates, _ = optimizer.propose_valid(
        _context(mode), 2, parents=parents, invariants=mode.invariants()
    )
    assert len(candidates) <= 2


def test_the_random_optimizer_is_reproducible():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    first, _ = optim.RandomOptimizer(seed=11).propose_valid(
        _context(mode), 3, parents=parents, invariants=mode.invariants()
    )
    second, _ = optim.RandomOptimizer(seed=11).propose_valid(
        _context(mode), 3, parents=parents, invariants=mode.invariants()
    )
    assert [c.id for c in first] == [c.id for c in second]


def test_a_candidate_id_is_a_function_of_the_state():
    """Two routes to the same state are the same candidate, and are not run twice."""
    mode = _mode()
    seed = mode.state_dict()
    once = nn.edits.apply({"op": "set_knob", "name": "order", "value": "asc"}, seed)
    twice = nn.edits.apply({"op": "set_knob", "name": "order", "value": "asc"}, seed)
    assert optim.candidate_id(once) == optim.candidate_id(twice)


# ── the loop ───────────────────────────────────────────────────────────────
def test_an_invalid_proposal_becomes_a_rejection_with_reasons():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = ScriptedOptimizer([{"op": "remove_node", "id": "exit"}], k=1)
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert candidates == []
    assert len(rejections) == 1
    assert any("best_score" in reason for reason in rejections[0].reasons)


def test_a_frozen_knob_is_refused_with_a_reason():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = ScriptedOptimizer([{"op": "set_knob", "name": "seed", "value": 9}], k=1)
    _candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert rejections
    assert "frozen" in rejections[0].reasons[0]


def test_an_unknown_operation_is_refused_with_the_catalogue():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = ScriptedOptimizer([{"op": "levitate", "target": "eval"}], k=1)
    _candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert "unknown edit" in rejections[0].reasons[0]
    assert "set_knob" in rejections[0].reasons[0]


def test_the_loop_regenerates_up_to_k_times():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = ScriptedOptimizer([{"op": "remove_node", "id": "exit"}], k=3)
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert candidates == []
    assert [r.attempt for r in rejections] == [1, 2, 3]


def test_the_loop_feeds_refusal_reasons_back():
    """The second attempt is told exactly what was wrong with the first."""
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = ScriptedOptimizer([{"op": "remove_node", "id": "exit"}], k=3)
    optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert optimizer.feedback_seen[0] == []
    assert optimizer.feedback_seen[1], "the second attempt got no feedback"
    assert "best_score" in optimizer.feedback_seen[1][0]


def test_the_loop_stops_as_soon_as_enough_candidates_are_valid():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = ScriptedOptimizer([{"op": "set_knob", "name": "order", "value": "asc"}], k=3)
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert len(candidates) == 1
    assert rejections == []
    assert len(optimizer.feedback_seen) == 1


def test_a_valid_edit_after_a_refused_one_still_produces_a_candidate():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = ScriptedOptimizer(
        [{"op": "remove_node", "id": "exit"},
         {"op": "set_knob", "name": "order", "value": "asc"}],
        k=1,
    )
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 2, parents=parents, invariants=mode.invariants()
    )
    assert len(candidates) == 1
    assert len(rejections) == 1


def test_rejection_codes_group_by_rule():
    rejection = optim.Rejection(
        attempt=1,
        edit={"op": "x"},
        reasons=["V5 [gate]: a gate needs at least two conditioned out-edges, has 1",
                 "V7 [rounds]: knob is declared but no node consumes it"],
    )
    assert rejection.codes() == ["V5", "V7"]


def test_an_operator_frozen_knob_is_refused_for_the_run():
    """Freezing is a property of the run: no candidate can edit it away."""
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    ctx = _context(mode)
    ctx.frozen = ["order"]
    optimizer = ScriptedOptimizer([{"op": "set_knob", "name": "order", "value": "asc"}], k=1)
    candidates, rejections = optimizer.propose_valid(
        ctx, 1, parents=parents, invariants=mode.invariants()
    )
    assert candidates == []
    assert "E-frozen" in rejections[0].codes()[0]
    assert "frozen for this run" in rejections[0].reasons[0]


def test_a_structural_edit_marks_the_candidate():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    block = {node.id: node for node in mode.insertable()}["observe"].to_state()
    optimizer = ScriptedOptimizer(
        [{"op": "insert_after", "target": "eval", "node": block}], k=1
    )
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert not rejections, [r.reasons for r in rejections]
    assert candidates[0].is_structural() is True
    assert parents[0].is_structural() is False


# ── the model optimizer ────────────────────────────────────────────────────
class FakeModel:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, system: str = "") -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "{}"


def test_the_autoresearch_optimizer_parses_a_fenced_answer():
    answer = (
        "Here is my decision.\n```json\n"
        '{"diagnosis": "the generator repeats itself", "proposals": ['
        '{"edit": {"op": "set_knob", "name": "order", "value": "asc"},'
        ' "rationale": "asc places more circles", "prediction": "score rises",'
        ' "confidence": 0.6}]}\n```\n'
    )
    model = FakeModel([answer])
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = optim.AutoresearchOptimizer(model, k=1)
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert not rejections, [r.reasons for r in rejections]
    assert candidates[0].state["knobs"]["order"]["value"] == "asc"
    assert optimizer.last_diagnosis == "the generator repeats itself"


def test_the_autoresearch_prompt_carries_the_gradient_and_the_blocks():
    model = FakeModel(["{}"])
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optim.AutoresearchOptimizer(model, k=1).propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    prompt = model.prompts[0]
    assert "Parameters you may move" in prompt
    assert "The operations you may use" in prompt
    assert "Blocks you may insert" in prompt
    assert "restart_on_stall" in prompt
    assert "set_knob" in prompt
    assert "Answer" in prompt


def test_the_autoresearch_prompt_lists_the_refusals_on_a_retry():
    model = FakeModel([
        '{"proposals": [{"edit": {"op": "remove_node", "id": "exit"}}]}',
        '{"proposals": [{"edit": {"op": "set_knob", "name": "order", "value": "asc"}}]}',
    ])
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = optim.AutoresearchOptimizer(model, k=2)
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert candidates
    assert len(rejections) == 1
    assert "Why your last proposals were refused" in model.prompts[1]
    assert "best_score" in model.prompts[1]


def test_a_model_that_returns_nothing_is_a_failed_round_not_a_crash():
    mode = _mode()
    parents = [Candidate.from_state(mode.state_dict())]
    optimizer = optim.AutoresearchOptimizer(FakeModel(["no json here at all"]), k=2)
    candidates, rejections = optimizer.propose_valid(
        _context(mode), 1, parents=parents, invariants=mode.invariants()
    )
    assert candidates == []
    assert rejections == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 2}\n```', {"a": 2}),
        ('prose before {"a": 3} prose after', {"a": 3}),
        ('{"a": {"b": "}"}}', {"a": {"b": "}"}}),
    ],
)
def test_json_extraction(text, expected):
    assert optim.extract_json(text) == expected


def test_an_unknown_optimizer_name_is_refused():
    with pytest.raises(ValueError, match="unknown optimizer"):
        optim.create("nonsense")
    assert optim.names() == ["autoresearch", "random"]


# ── the model client the optimizer is handed in production ─────────────────
def test_the_model_client_reports_a_timeout_instead_of_hanging(monkeypatch):
    import subprocess

    from refactory_dsh.model import DshModelClient

    def explode(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="dsh", timeout=1)

    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(RuntimeError, match="timed out"):
        DshModelClient(timeout_s=1).complete("hello")


def test_the_model_client_reports_a_failed_run(monkeypatch):
    import subprocess

    from refactory_dsh.model import DshModelClient

    class Failed:
        returncode = 3
        stdout = ""
        stderr = "no credentials"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(RuntimeError, match="exit 3"):
        DshModelClient().complete("hello")


def test_the_model_client_returns_stdout(monkeypatch):
    import subprocess

    from refactory_dsh.model import DshModelClient

    class Ok:
        returncode = 0
        stdout = '{"proposals": []}\n'
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Ok())
    assert DshModelClient().complete("hello") == '{"proposals": []}\n'
