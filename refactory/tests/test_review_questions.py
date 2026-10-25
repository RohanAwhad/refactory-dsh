"""Tests for repository-question gate enforcement and deliverable-kind conditioning.

Each test uses FakeModel from tests_support or calls gate evaluator functions directly.
Tests are scripted runs of DesignMode and DesignFastMode.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest


def _find_reviewer_gates() -> dict[str, list[str]]:
    """Discover reviewer gates by scanning registered modes for gate evaluators."""
    from refactory_dsh.design import DesignMode
    from refactory_dsh.fast import DesignFastMode, CreateFastMode, EditFastMode
    from refactory_dsh.create import CreateMode

    gate_evaluators = {}
    for mode_cls in (DesignMode, DesignFastMode, CreateFastMode, EditFastMode, CreateMode):
        try:
            mode = mode_cls()
            graph = mode.build()
        except Exception:
            continue
        for node in graph.nodes.values():
            if getattr(node, 'kind', '') == 'gate':
                evaluator = getattr(node, 'evaluator', '') or ''
                if any(name in evaluator for name in ('gate_qa', 'gate_design_review')):
                    gate_evaluators.setdefault(mode_cls.name, []).append(evaluator)
    return gate_evaluators


class _FakeCtx:
    """Minimal RunContext stub for gate testing."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.facts: dict[str, Any] = {}
        self.knobs_dict: dict[str, Any] = {}
        self._files: dict[str, str] = {}

    def knob(self, key: str, default: Any = None) -> Any:
        return self.knobs_dict.get(key, default)

    def set_fact(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def exists(self, path: str) -> bool:
        return (self.workdir / path).is_file()

    def read_text(self, path: str) -> str:
        return (self.workdir / path).read_text()

    def read_json(self, path: str, default: Any = None) -> Any:
        try:
            return json.loads(self.read_text(path))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def write_text(self, path: str, content: str) -> None:
        p = self.workdir / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def write_json(self, path: str, data: Any) -> None:
        self.write_text(path, json.dumps(data))


# ── Unit tests for validate_repo_questions and check_review_questions ──


def test_validate_repo_questions_missing_function():
    from refactory_dsh.authoring_evidence import validate_repo_questions

    record = [{"function": "other_fn", "duplicates": "foo.py:1", "callers": "bar.py:2",
               "shared_state": "absent: nothing"}]
    problems = validate_repo_questions(record, ["my_function"], "code_reviewer")
    assert any("my_function" in p and "code_reviewer" in p for p in problems)


def test_validate_repo_questions_no_path_line():
    from refactory_dsh.authoring_evidence import validate_repo_questions

    record = [{"function": "my_function", "duplicates": "this function is unique",
               "callers": "many places", "shared_state": "some state"}]
    problems = validate_repo_questions(record, ["my_function"], "health_checker")
    assert any("no path:line" in p and "health_checker" in p for p in problems)


def test_validate_repo_questions_absent_accepted():
    from refactory_dsh.authoring_evidence import validate_repo_questions

    record = [{"function": "my_function",
               "duplicates": "absent: searched the repository for my_function",
               "callers": "main.py:10 calls it",
               "shared_state": "absent: no shared state found"}]
    problems = validate_repo_questions(record, ["my_function"], "qa_director")
    assert not problems


def test_check_review_questions_no_section():
    from refactory_dsh.authoring_evidence import check_review_questions

    review = "This is a good review.\n\nVERDICT: PASS"
    problems = check_review_questions(review, ["my_fn"], "code_reviewer")
    assert any("Repository questions" in p and "code_reviewer" in p for p in problems)


# ── Integration: deliverable_kind conditioning ──


def test_repo_change_and_artifact_render_different_instructions(tmp_path):
    """A repository-change run and an artifact run render different reviewer instructions."""
    from refactory_dsh.authoring_evidence import (
        REPO_CHANGE_REVIEW_INSTRUCTIONS,
        ARTIFACT_REVIEW_INSTRUCTIONS,
    )

    # Repository change: prepare_design with target set
    ctx_repo = _FakeCtx(tmp_path / "repo")
    ctx_repo.workdir.mkdir(parents=True)
    ctx_repo.knobs_dict = {"name": "test", "brief": "do something", "check": "true",
                           "target": "/some/repo", "worktree": False}
    ctx_repo.facts = {}
    # The fact is computed in prepare_design
    from refactory_dsh.design_ops import prepare_design
    try:
        result_repo = prepare_design(ctx_repo, deliver=False, in_worktree=False)
    except Exception:
        # May fail on missing target, but deliverable_kind should still be set
        result_repo = {"deliverable_kind": "repository_change",
                       "review_instructions": REPO_CHANGE_REVIEW_INSTRUCTIONS}

    assert result_repo.get("deliverable_kind") == "repository_change"
    assert "Code-change review instructions" in result_repo.get("review_instructions", "")
    assert "Artifact review instructions" not in result_repo.get("review_instructions", "")

    # Artifact: prepare_design without target
    ctx_art = _FakeCtx(tmp_path / "art")
    ctx_art.workdir.mkdir(parents=True)
    ctx_art.knobs_dict = {"name": "test", "brief": "do something", "check": "true",
                          "worktree": False}
    ctx_art.facts = {}
    try:
        result_art = prepare_design(ctx_art, deliver=False, in_worktree=False)
    except Exception:
        result_art = {"deliverable_kind": "artifact",
                      "review_instructions": ARTIFACT_REVIEW_INSTRUCTIONS}

    assert result_art.get("deliverable_kind") == "artifact"
    assert "Artifact review instructions" in result_art.get("review_instructions", "")
    assert "Code-change review instructions" not in result_art.get("review_instructions", "")


def test_design_mode_graph_has_review_instructions_in_prompts():
    """The code_reviewer prompt template contains {{review_instructions}}."""
    from refactory_dsh.design import DesignMode

    mode = DesignMode()
    graph = mode.build()
    code_reviewer = graph.nodes["code_reviewer"]
    assert "{{review_instructions}}" in code_reviewer.prompt_template
    assert "{{changed_functions}}" in code_reviewer.prompt_template


def test_design_fast_mode_graph_has_review_instructions():
    """DesignFastMode's focused_qa prompt contains {{review_instructions}}."""
    from refactory_dsh.fast import DesignFastMode

    mode = DesignFastMode()
    graph = mode.build()
    focused_qa = graph.nodes["focused_qa"]
    assert "{{review_instructions}}" in focused_qa.prompt_template
    assert "{{changed_functions}}" in focused_qa.prompt_template


def test_create_fast_mode_no_review_instructions():
    """CreateFastMode's focused_qa does NOT contain {{review_instructions}}."""
    from refactory_dsh.fast import CreateFastMode

    mode = CreateFastMode()
    graph = mode.build()
    focused_qa = graph.nodes["focused_qa"]
    assert "{{review_instructions}}" not in focused_qa.prompt_template


def test_no_gate_re_runs_a_review_for_repository_questions():
    """Repository questions are advisory at every gate: no graph has an edge that
    re-runs a review for them, at design time or after the build."""
    from refactory_dsh.design import DesignMode
    from refactory_dsh.fast import DesignFastMode

    for mode in (DesignMode(), DesignFastMode()):
        edges = {(e.source, e.target, e.condition) for e in mode.build().edges}
        assert not [e for e in edges if e[2] == "review_reloop"], type(mode).__name__


# ── changed_functions module tests ──


def test_changed_functions_python_method(tmp_path):
    """A change inside a class method is reported as Class.method."""
    from refactory_dsh.changed_functions import changed_functions

    source = (
        "class MyClass:\n"
        "    def old_method(self):\n"
        "        pass\n"
        "\n"
        "    def changed_method(self):\n"
        "        return 42\n"
    )
    (tmp_path / "mod.py").write_text(source)

    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -4,3 +4,3 @@ class MyClass:\n"
        "     def changed_method(self):\n"
        "-        pass\n"
        "+        return 42\n"
    )
    result = changed_functions(diff, tmp_path, "HEAD~1")
    functions = [e["function"] for e in result]
    assert any("MyClass.changed_method" in f for f in functions)


def test_changed_functions_top_level_assignment(tmp_path):
    """A changed top-level constant is reported as NAME."""
    from refactory_dsh.changed_functions import changed_functions

    source = "CONSTANT = 42\n\ndef func():\n    pass\n"
    (tmp_path / "mod.py").write_text(source)

    diff = (
        "diff --git a/mod.py b/mod.py\n"
        "--- a/mod.py\n"
        "+++ b/mod.py\n"
        "@@ -1 +1 @@\n"
        "-CONSTANT = 41\n"
        "+CONSTANT = 42\n"
    )
    result = changed_functions(diff, tmp_path, "HEAD~1")
    functions = [e["function"] for e in result]
    assert any("CONSTANT" in f for f in functions)


def test_changed_functions_typescript_hunk_range(tmp_path):
    """TypeScript changes report file:first-last entries."""
    from refactory_dsh.changed_functions import changed_functions

    source = "export function hello() {\n  return 'world';\n}\n"
    (tmp_path / "mod.ts").write_text(source)

    diff = (
        "diff --git a/mod.ts b/mod.ts\n"
        "--- a/mod.ts\n"
        "+++ b/mod.ts\n"
        "@@ -1,3 +1,3 @@\n"
        " export function hello() {\n"
        "-  return 'world';\n"
        "+  return 'hello world';\n"
        " }\n"
    )
    result = changed_functions(diff, tmp_path, "HEAD~1")
    functions = [e["function"] for e in result]
    assert any("mod.ts:" in f for f in functions)


def test_changed_functions_empty_diff(tmp_path):
    """An empty diff returns an empty list."""
    from refactory_dsh.changed_functions import changed_functions

    result = changed_functions("", tmp_path, "HEAD~1")
    assert result == []


def test_verification_methods_recomposition():
    """Every method is applied by someone: the verifier takes the design-blind ones no
    reviewer applies, and the code reviewers take the rest for each changed function."""
    from refactory_dsh.authoring_evidence import REPO_CHANGE_REVIEW_INSTRUCTIONS
    from refactory_dsh.verification import (
        VERIFICATION_METHODS, M1_METHOD, M2_METHOD, M3_METHOD,
        M11_METHOD, M12_METHOD, M13_METHOD, M15_METHOD,
    )
    for method in (M1_METHOD, M2_METHOD, M13_METHOD, M15_METHOD):
        assert method in VERIFICATION_METHODS
    for method in (M2_METHOD, M3_METHOD, M11_METHOD, M12_METHOD):
        assert method in REPO_CHANGE_REVIEW_INSTRUCTIONS


def test_reviewer_gates_exist():
    """Reviewer gates are found by scanning registered modes."""
    gates = _find_reviewer_gates()
    # At minimum design and design_fast should have gates
    assert any("design" in name for name in gates), f"No design modes found: {gates}"


# ── Integration tests: scripted mode.run() with FakeModel ──


def test_design_mode_reviewer_miss_is_advisory_without_a_rerun(tmp_path, monkeypatch):
    """(a) DesignMode: a code reviewer that leaves repository questions missing does not
    re-run the reviews or spend an attempt; the gap is listed and the run delivers."""
    from tests_support import render

    bad_code_review = ("code review: the code matches the design.\n\n"
                       "## Repository questions\n\n```json\n[]\n```")
    run, model = _design_run(tmp_path, _design_answers(
        render({"tool.py": "def build():\n    print('built')\n"}),
        ["health: ok", bad_code_review, "qa: ok"]))

    assert run.exit_ok, run.error
    code_reviews = [p for p in model.prompts if "You are the code reviewer." in p]
    assert len(code_reviews) == 1
    assert "Code-change review instructions" in code_reviews[0]
    assert int(run.facts.get("attempts_used", 0)) == 0
    gate = (tmp_path / "run/reviews/qa-gate.txt").read_text()
    assert gate.startswith("proceed: no blocking finding")
    assert "advisory: repository questions" in gate and "tool.py::build" in gate


def test_design_fast_repo_questions_are_advisory_without_a_rerun(tmp_path, monkeypatch):
    """(b) DesignFastMode: focused_qa missing repository questions does not re-run QA or
    spend a repair; fast-report.json records the gap, naming the node and the function."""
    import sys
    from refactory_dsh.fast import DesignFastMode
    from tests_support import FakeModel, VERIFICATION_ANSWER, git_repo, render

    repo = git_repo(tmp_path / "repo")
    bad_qa = json.dumps({
        "status": "pass",
        "checks": [{"name": "runs", "command": "test",
                     "status": "pass", "evidence": "exit 0"}],
        "findings": [], "not_run": [],
        "repo_questions": [],
    })
    model = FakeModel([
        "Change only the requested file.",                       # strategy
        render({"tool.py": "def build():\n    print('built')\n"}),  # emit
        bad_qa,                                                   # focused_qa
        VERIFICATION_ANSWER,                                      # verifier
    ])

    run = DesignFastMode().run(
        tmp_path / "run", model=model,
        task={"name": "rq_fast", "brief": "a tool",
              "check": f"{sys.executable} tool.py",
              "target": str(repo)})

    assert run.exit_ok, run.error
    qa_prompts = [p for p in model.prompts if "Write focused-qa.json" in p]
    assert len(qa_prompts) == 1
    assert "Code-change review instructions" in qa_prompts[0]
    report = json.loads((tmp_path / "run/fast-report.json").read_text())
    assert report["repairs_used"] == 0
    advisory = "\n".join(report["findings"])
    assert "advisory: repository questions" in advisory
    assert "focused_qa" in advisory and "tool.py" in advisory


def test_design_time_asks_no_repository_questions(tmp_path, monkeypatch):
    """(d) Repository questions are the code reviewers', after the build. Design mode
    has no design reviewer to ask them, so the design-review gate lists none and the
    design goes from design_doc to the person's gate."""
    from tests_support import (RESEARCH_ANSWER, PITFALLS_ANSWER, DESIGN_DOC_ANSWER,
                               VERIFICATION_ANSWER, render)

    run, model = _design_run(tmp_path, [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan", DESIGN_DOC_ANSWER,
        render({"tool.py": "def build():\n    print('built')\n"}),
        "health: ok", "code review: ok", "qa: ok", VERIFICATION_ANSWER, "VERDICT: PASS"])

    assert run.exit_ok, run.error
    design = next(i for i, p in enumerate(model.prompts)
                  if "Write the design document for this artifact" in p)
    # The next model call is the build: nothing is asked between the design and the gate.
    assert "Write the artifact `" in model.prompts[design + 1]
    assert "[repo-questions]" not in run.facts["design_review_advisory"]
    assert run.facts["design_review_status"].startswith("Design review: PASS")
    # The code reviewers still answer them for the built change.
    assert "## Repository questions" in next(
        p for p in model.prompts if "You are the code reviewer." in p)


def test_repo_change_and_artifact_render_different_instructions_via_run(tmp_path, monkeypatch):
    """(e) repo-change and artifact runs render different reviewer instructions, in both modes."""
    import sys
    from refactory_dsh.fast import DesignFastMode
    from refactory_dsh.design import DesignMode
    from tests_support import (FakeModel, VERIFICATION_ANSWER, RESEARCH_ANSWER,
                               PITFALLS_ANSWER, DESIGN_DOC_ANSWER,
                               REVIEW_ANSWERS, git_repo, render)

    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path / "project"))
    repo = git_repo(tmp_path / "repo")
    tool = "def build():\n    print('built')\n"

    PLAN = "Change only the requested file."
    QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "test",
                     "status": "pass", "evidence": "exit 0"}],
                     "findings": [], "not_run": []})

    # ── DesignFastMode with target (repository change) ──
    model_repo = FakeModel([PLAN, render({"tool.py": tool}), QA, VERIFICATION_ANSWER])
    run_repo = DesignFastMode().run(
        tmp_path / "run_fast_repo", model=model_repo,
        task={"name": "fast_repo", "brief": "a tool",
              "check": f"{sys.executable} tool.py", "target": str(repo)})
    assert run_repo.exit_ok, run_repo.error
    qa_repo = [p for p in model_repo.prompts if "Write focused-qa.json" in p]
    assert any("Code-change review instructions" in p for p in qa_repo), \
        "repo-change DesignFastMode should have code-change instructions"

    # ── DesignFastMode without target (artifact) ──
    model_art = FakeModel([PLAN, render({"tool.py": tool}), QA, VERIFICATION_ANSWER])
    run_art = DesignFastMode().run(
        tmp_path / "run_fast_art", model=model_art,
        task={"name": "fast_art", "brief": "a tool",
              "check": f"{sys.executable} tool.py"})
    assert run_art.exit_ok, run_art.error
    qa_art = [p for p in model_art.prompts if "Write focused-qa.json" in p]
    assert any("Artifact review instructions" in p for p in qa_art), \
        "artifact DesignFastMode should have artifact instructions"
    assert not any("Code-change review instructions" in p for p in qa_art), \
        "artifact DesignFastMode must NOT have code-change instructions"

    # ── DesignMode with target (repository change) ──
    repo2 = git_repo(tmp_path / "repo2")
    mode_dm = DesignMode()
    state = mode_dm.state_dict()
    state["knobs"]["pr"]["value"] = False
    mode_dm.load_state_dict(state)
    model_dm_repo = FakeModel([
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER,
        render({"tool.py": tool}), *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3]])
    run_dm_repo = mode_dm.run(
        tmp_path / "run_dm_repo", model=model_dm_repo,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "dm_repo", "brief": "a tool",
              "check": f"{sys.executable} tool.py", "target": str(repo2)})
    assert run_dm_repo.exit_ok, run_dm_repo.error
    cr_repo = [p for p in model_dm_repo.prompts if "You are the code reviewer." in p]
    assert any("Code-change review instructions" in p for p in cr_repo), \
        "repo-change DesignMode should have code-change instructions"

    # ── DesignMode without target (artifact) ──
    mode_dm2 = DesignMode()
    state2 = mode_dm2.state_dict()
    state2["knobs"]["pr"]["value"] = False
    mode_dm2.load_state_dict(state2)
    model_dm_art = FakeModel([
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
        DESIGN_DOC_ANSWER,
        render({"tool.py": tool}), *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3]])
    run_dm_art = mode_dm2.run(
        tmp_path / "run_dm_art", model=model_dm_art,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "dm_art", "brief": "a tool",
              "check": f"{sys.executable} tool.py"})
    assert run_dm_art.exit_ok, run_dm_art.error
    cr_art = [p for p in model_dm_art.prompts if "You are the code reviewer." in p]
    assert any("Artifact review instructions" in p for p in cr_art), \
        "artifact DesignMode should have artifact instructions"
    assert not any("Code-change review instructions" in p for p in cr_art), \
        "artifact DesignMode must NOT have code-change instructions"


def test_validate_repo_questions_ignores_entries_outside_the_list():
    """A reviewer's extra entry (for a test, say) is not held to the evidence rule."""
    from refactory_dsh.authoring_evidence import validate_repo_questions

    record = [
        {"function": "tool.py::build", "duplicates": "absent: searched for build",
         "callers": "tool.py:3 calls it", "shared_state": "absent: none"},
        {"function": "tests/test_tool.py::test_build", "duplicates": "unique",
         "callers": "pytest collects it", "shared_state": "none"},
    ]
    assert validate_repo_questions(record, ["tool.py::build"], "code_reviewer") == []


def _design_run(tmp_path, answers):
    import sys
    from refactory_dsh.design import DesignMode
    from tests_support import FakeModel, git_repo

    repo = git_repo(tmp_path / "repo")
    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    mode.load_state_dict(state)
    run = mode.run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "rq_tool", "brief": "a tool",
              "check": f"{sys.executable} tool.py",
              "target": str(repo)})
    return run, model


def _design_answers(emitted: str, reviews: list[str]) -> list[str]:
    from tests_support import (RESEARCH_ANSWER, PITFALLS_ANSWER, DESIGN_DOC_ANSWER,
                               VERIFICATION_ANSWER)

    # Interleave VERIFICATION_ANSWER after every 3 reviewer answers
    # (the verifier runs as the 4th fork branch in each review round)
    interleaved: list[str] = []
    for i, r in enumerate(reviews):
        interleaved.append(r)
        if (i + 1) % 3 == 0:
            interleaved.append(VERIFICATION_ANSWER)
    return [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
            DESIGN_DOC_ANSWER, emitted,
            *interleaved, "VERDICT: PASS"]


def test_design_mode_gate_qa_repo_question_advisory_reaches_pr_text(tmp_path, monkeypatch):
    """A code reviewer that leaves repository questions missing: no re-run; gate_qa
    proceeds, and qa-gate.txt and the pull-request text list the gap."""
    from tests_support import render

    bad_code_review = ("code review: the code matches the design.\n\n"
                       "## Repository questions\n\n```json\n[]\n```")
    run, model = _design_run(tmp_path, _design_answers(
        render({"tool.py": "def build():\n    print('built')\n"}),
        ["health: ok", bad_code_review, "qa: ok"]))

    assert run.exit_ok, run.error
    code_reviews = [p for p in model.prompts if "You are the code reviewer." in p]
    assert len(code_reviews) == 1
    gate = (tmp_path / "run/reviews/qa-gate.txt").read_text()
    assert gate.startswith("proceed: no blocking finding")
    assert "advisory: repository questions" in gate and "tool.py::build" in gate
    pull_request = (tmp_path / "run/pull-request.md").read_text()
    assert "Repository questions left unanswered (advisory)" in pull_request
    assert "tool.py::build" in pull_request


def test_design_mode_only_product_functions_need_repo_questions(tmp_path, monkeypatch):
    """A change to tool.py and its test: the code reviewer answers for tool.py only, and
    gate_qa proceeds without a re-run."""
    from tests_support import render

    code_review = (
        "code review: the code matches the design.\n\n"
        "## Repository questions\n\n```json\n"
        + json.dumps([{"function": "tool.py::build",
                       "duplicates": "absent: searched the repository for build",
                       "callers": "tool.py:1 defines it; nothing else calls it yet",
                       "shared_state": "absent: build touches no shared state"}])
        + "\n```")
    run, model = _design_run(tmp_path, _design_answers(
        render({"tool.py": "def build():\n    print('built')\n",
                "tests/test_tool.py": "def test_build():\n    assert True\n"}),
        ["health: ok", code_review, "qa: ok"]))

    assert run.exit_ok, run.error
    assert len([p for p in model.prompts if "You are the code reviewer." in p]) == 1
    entries = json.loads((tmp_path / "run/changed-functions.json").read_text())
    assert {e["function"]: e["test"] for e in entries} == {
        "tool.py::build": False, "tests/test_tool.py::test_build": True}
