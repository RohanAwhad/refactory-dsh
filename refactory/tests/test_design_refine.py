"""Light refine: a design-mode refine skips research and strategy.

Tests that a refine with refine_design=light runs no research, pitfalls or strategy
agent, writes one design, reaches the person's gate, and then builds and delivers;
that the same refine with refine_design=full walks the full phase; that a non-refine
run's node sequence is unchanged; and that a gate_strategy reloop on a light refine
works from the light sentinels.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from refactory_dsh.design import DesignMode
from refactory_dsh.design_ops import (
    LIGHT_REFINE_STRATEGY_PLAN,
    REFINE_SCOPE_REASON,
    route_refine_design,
)
from tests_support import (
    DESIGN_DOC_ANSWER,
    PITFALLS_ANSWER,
    RESEARCH_ANSWER,
    SCOPED_RESEARCH_ANSWER,
    VERIFICATION_ANSWER,
    FakeModel,
    git_repo,
    render,
)

STRATEGY_DIRECTOR = "You are the strategy director."
PLAN_STEP = "Compile the perspectives into one plan"
RESEARCH_DIRECTOR = "You are the research director."
PITFALLS_RESEARCHER = "You are the pitfalls researcher."

BUILD = [
    render({"tool.py": "def build():\n    print('built')\n"}),
    "health: ok",
    "code review: ok",
    "qa: ok",
    VERIFICATION_ANSWER,
    "VERDICT: PASS",
]


def _head_commit(repo):
    """The HEAD commit of a test repository."""
    import subprocess
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _stub_gh_factory(head_oid):
    """Build a _gh stub that returns a fake open PR with the given head commit."""
    def _stub_gh(*args, **kwargs):
        args_str = " ".join(str(a) for a in args)
        if "pr" in args_str and "view" in args_str:
            return json.dumps({
                "url": "https://github.com/test/repo/pull/42",
                "number": 42,
                "state": "OPEN",
                "headRefName": "refactory/run-abc",
                "headRefOid": head_oid,
                "baseRefName": "main",
                "isCrossRepository": False,
                "body": "This PR fixes the widget.\n\n## Changes\n- Fixed widget.py",
            })
        if "api" in args_str:
            return "[]"
        return "{}"
    return _stub_gh


def _refine_run(tmp_path, answers, *, refine_design="light", verdicts=None,
                monkeypatch=None):
    """Run a refine through DesignMode with a fake GitHub."""
    repo = git_repo(tmp_path / "repo")
    head_oid = _head_commit(repo)
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    state["knobs"]["refine_design"]["value"] = refine_design
    mode.load_state_dict(state)

    # Stub out delivery._gh so we don't call real GitHub.
    with mock.patch("refactory_dsh.delivery._gh", side_effect=_stub_gh_factory(head_oid)):
        with mock.patch("refactory_dsh.delivery._remote",
                        return_value=("origin", "test/repo")):
            with mock.patch("refactory_dsh.delivery._present", return_value=True):
                with mock.patch("refactory_dsh.delivery._fetch"):
                    run = mode.run(
                        tmp_path / "run", model=model,
                        verdicts=verdicts or {"gate_strategy": "proceed"},
                        task={
                            "name": "refine_tool",
                            "brief": "Fix the widget rendering",
                            "check": f"{sys.executable} tool.py",
                            "target": str(repo),
                            "refine": "https://github.com/test/repo/pull/42",
                        },
                    )
    return run, model


def test_light_refine_skips_research_and_strategy(tmp_path, monkeypatch):
    """A refine with refine_design=light runs no research, pitfalls or strategy agent."""
    # On a light refine, only design_doc and downstream agents run.
    # Answers: design_doc, then BUILD (emit, reviews, judges).
    run, model = _refine_run(tmp_path, [DESIGN_DOC_ANSWER, *BUILD])

    assert run.exit_ok, run.error
    # No research, pitfalls or strategy agent prompts.
    assert not [p for p in model.prompts if RESEARCH_DIRECTOR in p]
    assert not [p for p in model.prompts if PITFALLS_RESEARCHER in p]
    assert not [p for p in model.prompts if STRATEGY_DIRECTOR in p]
    assert not [p for p in model.prompts if PLAN_STEP in p]

    # The strategy_plan directive appears in the design_doc prompt.
    design_prompts = [p for p in model.prompts if "Write the design document" in p]
    assert len(design_prompts) >= 1
    assert "light refine" in design_prompts[0] or "follow-up" in design_prompts[0].lower() or \
        LIGHT_REFINE_STRATEGY_PLAN[:40] in design_prompts[0]

    # The pitfalls fact contains the light-refine note.
    assert "light refine" in str(run.facts.get("pitfalls", "")).lower()

    # Sentinel files exist on disk.
    run_dir = tmp_path / "run"
    assert (run_dir / "research-plan.json").exists()
    assert (run_dir / "pitfalls.json").exists()
    assert (run_dir / "strategy.json").exists()
    assert (run_dir / "research").is_dir()
    assert (run_dir / "strategy").is_dir()

    # The run reached gate_strategy.
    assert run.facts.get("research_scope_reason") == REFINE_SCOPE_REASON


def test_full_refine_walks_all_agents(tmp_path):
    """A refine with refine_design=full walks the full design phase."""
    # Full refine: research, pitfalls, perspectives, strategy, design_doc, then BUILD.
    answers = [
        SCOPED_RESEARCH_ANSWER,
        PITFALLS_ANSWER,
        "one plan",
        DESIGN_DOC_ANSWER,
        *BUILD,
    ]
    run, model = _refine_run(tmp_path, answers, refine_design="full")

    assert run.exit_ok, run.error
    # Research director was visited.
    assert [p for p in model.prompts if RESEARCH_DIRECTOR in p]
    # Pitfalls researcher was visited.
    assert [p for p in model.prompts if PITFALLS_RESEARCHER in p]


def test_non_refine_unchanged(tmp_path):
    """A non-refine run walks the full design phase regardless of the knob."""
    repo = git_repo(tmp_path / "repo")
    model = FakeModel([
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER, *BUILD,
    ])
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    mode.load_state_dict(state)
    run = mode.run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={
            "name": "short_tool",
            "brief": "a tool",
            "check": f"{sys.executable} tool.py",
            "target": str(repo),
        },
    )

    assert run.exit_ok, run.error
    assert run.facts["research_scope"] == "full"
    # Research director was visited (full path).
    assert len([p for p in model.prompts if STRATEGY_DIRECTOR in p]) == 1


def test_light_refine_reloop_at_gate_strategy(tmp_path):
    """A light refine whose gate_strategy is relooped once reaches design_doc and
    the gate again, runs no research/pitfalls/strategy_director agent, and the
    person's note reaches the new design."""
    # On a reloop at gate_strategy, the run goes to synthesize_strategy (the
    # existing reloop edge) then back to design_doc and gate_strategy.
    # Answers: design_doc (1st), synthesize_strategy (reloop), design_doc (2nd), BUILD.
    answers = [
        DESIGN_DOC_ANSWER,  # first design_doc
        "revised plan with person's note",  # synthesize_strategy after reloop
        DESIGN_DOC_ANSWER,  # second design_doc
        *BUILD,
    ]
    # The first gate_strategy returns "reloop", the second "proceed".
    verdict_seq = iter(["reloop", "proceed"])

    repo = git_repo(tmp_path / "repo")
    head_oid = _head_commit(repo)
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    state["knobs"]["refine_design"]["value"] = "light"
    mode.load_state_dict(state)

    with mock.patch("refactory_dsh.delivery._gh", side_effect=_stub_gh_factory(head_oid)):
        with mock.patch("refactory_dsh.delivery._remote",
                        return_value=("origin", "test/repo")):
            with mock.patch("refactory_dsh.delivery._present", return_value=True):
                with mock.patch("refactory_dsh.delivery._fetch"):
                    run = mode.run(
                        tmp_path / "run", model=model,
                        verdicts={"gate_strategy": "reloop"},
                        task={
                            "name": "refine_tool",
                            "brief": "Fix the widget rendering",
                            "check": f"{sys.executable} tool.py",
                            "target": str(repo),
                            "refine": "https://github.com/test/repo/pull/42",
                        },
                    )

    # The run stopped at gate_strategy on the reloop (since verdicts only has "reloop",
    # it will stop after the first gate_strategy hit).
    # No research, pitfalls or strategy_director agents.
    assert not [p for p in model.prompts if RESEARCH_DIRECTOR in p]
    assert not [p for p in model.prompts if PITFALLS_RESEARCHER in p]
    assert not [p for p in model.prompts if STRATEGY_DIRECTOR in p]

    # design_doc was visited (at least once).
    design_prompts = [p for p in model.prompts if "Write the design document" in p]
    assert len(design_prompts) >= 1


def test_route_refine_design_unit(tmp_path):
    """Unit test for the route_refine_design gate evaluator."""
    from refactory_dsh.nn.executor import RunContext

    # Non-refine → full.
    ctx_full = RunContext(
        tmp_path / "full", knobs={}, task={},
        facts={"research_scope_reason": ""},
    )
    result = route_refine_design(ctx_full)
    assert result == "full"
    assert "strategy" not in ctx_full.facts

    # Refine with full knob → full.
    ctx_refine_full = RunContext(
        tmp_path / "refine_full", knobs={"refine_design": "full"}, task={},
        facts={"research_scope_reason": REFINE_SCOPE_REASON},
    )
    result = route_refine_design(ctx_refine_full)
    assert result == "full"

    # Refine with light knob → light, writes sentinels.
    run_dir = tmp_path / "refine_light"
    ctx_light = RunContext(
        run_dir, knobs={"refine_design": "light"}, task={},
        facts={
            "research_scope_reason": REFINE_SCOPE_REASON,
            "delivery_plan": {
                "refine": {"body": "PR description", "into": "main"},
            },
        },
    )
    # Declare the writes and produces so RunContext enforcement passes.
    ctx_light._declared = (
        "research-plan.json", "pitfalls.json", "pitfalls.md",
        "strategy-plan.json", "strategy.json",
        "research-review.txt", "pitfalls-review.txt",
        "research/", "strategy/",
    )
    ctx_light._declared_facts = (
        "research", "findings_recorded", "research_review",
        "research_attempts_used", "research_advisory",
        "pitfalls", "pitfalls_review", "pitfalls_attempts_used",
        "pitfalls_advisory", "strategy_plan", "strategy",
    )
    result = route_refine_design(ctx_light)
    assert result == "light"
    assert ctx_light.facts["research"] == "(light refine: research was skipped)"
    assert ctx_light.facts["strategy_plan"] == LIGHT_REFINE_STRATEGY_PLAN
    assert "light refine" in ctx_light.facts["pitfalls"].lower()
    assert (run_dir / "research-plan.json").exists()
    assert (run_dir / "pitfalls.json").exists()
    assert (run_dir / "strategy.json").exists()
    assert (run_dir / "research").is_dir()
    assert (run_dir / "strategy").is_dir()
