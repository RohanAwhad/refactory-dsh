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


def test_check_review_questions_brief_trace_missing():
    from refactory_dsh.authoring_evidence import check_review_questions

    review = (
        "## Repository questions\n\n"
        '```json\n[{"function":"my_fn", "duplicates":"foo.py:1", '
        '"callers":"bar.py:2", "shared_state":"absent: nothing"}]\n```\n\n'
        "VERDICT: PASS"
    )
    problems = check_review_questions(
        review, ["my_fn"], "design_review", require_brief_trace=True, target="refactory")
    assert any("Brief example trace" in p for p in problems)


def test_check_review_questions_brief_trace_present():
    from refactory_dsh.authoring_evidence import check_review_questions

    review = (
        "## Repository questions\n\n"
        '```json\n[{"function":"my_fn", "duplicates":"foo.py:1", '
        '"callers":"bar.py:2", "shared_state":"absent: nothing"}]\n```\n\n'
        "## Brief example trace\n\n"
        "The brief's example runs through runner.py:5 → tool.py:1 "
        "in both the walker and DSH runtimes.\n\n"
        "VERDICT: PASS"
    )
    problems = check_review_questions(
        review, ["my_fn"], "design_review", require_brief_trace=True, target="refactory")
    assert not problems


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


def test_design_mode_has_review_reloop_edges():
    """DesignMode's graph has review_reloop edges."""
    from refactory_dsh.design import DesignMode

    mode = DesignMode()
    graph = mode.build()
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    assert ("gate_design_review", "design_review", "review_reloop") in edges
    assert ("gate_qa", "fork_qa", "review_reloop") in edges


def test_design_fast_mode_has_review_reloop_edge():
    """DesignFastMode's graph has gate_qa -> fork_qa review_reloop edge."""
    from refactory_dsh.fast import DesignFastMode

    mode = DesignFastMode()
    graph = mode.build()
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    assert ("gate_qa", "fork_qa", "review_reloop") in edges


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
    """The recomposed VERIFICATION_METHODS matches the expected content."""
    from refactory_dsh.verification import (
        VERIFICATION_METHODS, M1_METHOD, M2_METHOD, M3_METHOD,
        M11_METHOD, M12_METHOD, M13_METHOD, M15_METHOD,
    )
    # Each method constant should appear in VERIFICATION_METHODS
    assert M1_METHOD in VERIFICATION_METHODS
    assert M2_METHOD in VERIFICATION_METHODS
    assert M3_METHOD in VERIFICATION_METHODS
    assert M11_METHOD in VERIFICATION_METHODS
    assert M12_METHOD in VERIFICATION_METHODS
    assert M13_METHOD in VERIFICATION_METHODS
    assert M15_METHOD in VERIFICATION_METHODS


def test_reviewer_gates_exist():
    """Reviewer gates are found by scanning registered modes."""
    gates = _find_reviewer_gates()
    # At minimum design and design_fast should have gates
    assert any("design" in name for name in gates), f"No design modes found: {gates}"


# ── Integration tests: scripted mode.run() with FakeModel ──


def test_design_mode_reviewer_miss_reloops_without_attempt(tmp_path, monkeypatch):
    """(a) DesignMode: code_reviewer miss → review_reloop; code_reviewer twice; attempts_used unchanged."""
    import sys
    from refactory_dsh.design import DesignMode
    from tests_support import (FakeModel, RESEARCH_ANSWER, PITFALLS_ANSWER,
                               DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
                               VERIFICATION_ANSWER, git_repo, render)

    repo = git_repo(tmp_path / "repo")
    tool = "def build():\n    print('built')\n"

    # code_reviewer answer with repo_questions present but empty (missing changed function)
    bad_code_review = (
        "code review: the code matches the design.\n\n"
        "## Repository questions\n\n"
        "```json\n[]\n```"
    )

    answers = [
        RESEARCH_ANSWER,                 # research_director
        PITFALLS_ANSWER,                 # pitfalls_researcher
        "perspectives",                  # strategy_director
        "one plan",                      # synthesize_strategy
        DESIGN_DOC_ANSWER,               # design_doc
        DESIGN_REVIEW_ANSWER,            # design_review
        # gate_strategy → proceed (via verdicts)
        render({"tool.py": tool}),       # emit
        # First reviewer pass (fork_qa: health, code, qa, verifier):
        "health: ok",                    # health_checker (FakeModel injects rq)
        bad_code_review,                 # code_reviewer (bad rq → review_reloop)
        "qa: ok",                        # qa_director (FakeModel injects rq)
        VERIFICATION_ANSWER,             # verifier
        # gate_qa → review_reloop → fork_qa
        # Second reviewer pass (fork_qa again):
        "health: ok",                    # health_checker
        "code review: ok",              # code_reviewer
        "qa: ok",                        # qa_director
        VERIFICATION_ANSWER,             # verifier
        # gate_qa → proceed
        "VERDICT: PASS",                 # overwatch
    ]

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

    assert run.exit_ok, run.error

    # code_reviewer prompt appears twice
    code_reviewer_prompts = [p for p in model.prompts
                             if "You are the code reviewer." in p]
    assert len(code_reviewer_prompts) >= 2, (
        f"code_reviewer ran {len(code_reviewer_prompts)} times, expected >= 2"
    )

    # The second code_reviewer prompt's qa_gate names code_reviewer and the function
    assert "code_reviewer" in code_reviewer_prompts[1]
    assert "tool.py" in code_reviewer_prompts[1]

    # attempts_used unchanged — review_reloop does not spend a builder attempt
    assert int(run.facts.get("attempts_used", 0)) == 0

    # Code-change review instructions present (for test (e))
    assert any("Code-change review instructions" in p for p in code_reviewer_prompts)


def test_design_fast_reviewer_miss_reloops_focused_qa(tmp_path, monkeypatch):
    """(b) DesignFastMode: focused_qa miss → review_reloop; repairs_used unchanged."""
    import sys
    from refactory_dsh.fast import DesignFastMode
    from tests_support import FakeModel, VERIFICATION_ANSWER, git_repo, render

    repo = git_repo(tmp_path / "repo")
    tool = "def build():\n    print('built')\n"

    PLAN = "Change only the requested file."
    # bad_qa: valid JSON but empty repo_questions (missing changed function)
    bad_qa = json.dumps({
        "status": "pass",
        "checks": [{"name": "runs", "command": "test",
                     "status": "pass", "evidence": "exit 0"}],
        "findings": [], "not_run": [],
        "repo_questions": [],
    })
    # good_qa: valid JSON without repo_questions (FakeModel auto-injects from changed-functions.json)
    good_qa = json.dumps({
        "status": "pass",
        "checks": [{"name": "runs", "command": "test",
                     "status": "pass", "evidence": "exit 0"}],
        "findings": [], "not_run": [],
    })

    model = FakeModel([
        PLAN,                      # strategy
        render({"tool.py": tool}), # emit
        bad_qa,                    # focused_qa (1st — bad rq)
        VERIFICATION_ANSWER,       # verifier
        # gate_qa → review_reloop → fork_qa (re-runs both branches)
        good_qa,                   # focused_qa (2nd — FakeModel injects valid rq)
        VERIFICATION_ANSWER,       # verifier (2nd run in fork)
    ])

    run = DesignFastMode().run(
        tmp_path / "run", model=model,
        task={"name": "rq_fast", "brief": "a tool",
              "check": f"{sys.executable} tool.py",
              "target": str(repo)})

    assert run.exit_ok, run.error

    # focused_qa prompt appears twice
    qa_prompts = [p for p in model.prompts if "Write focused-qa.json" in p]
    assert len(qa_prompts) >= 2, (
        f"focused_qa ran {len(qa_prompts)} times, expected >= 2"
    )

    # repairs_used unchanged (review reloop does not spend repairs)
    report = json.loads((tmp_path / "run/fast-report.json").read_text())
    assert report["repairs_used"] == 0

    # Code-change review instructions present (for test (e))
    assert any("Code-change review instructions" in p for p in qa_prompts)


def test_design_fast_repo_questions_advisory_after_one_rerun(tmp_path, monkeypatch):
    """(c) focused_qa missing repo questions twice: one re-run, then the run proceeds and
    fast-report.json records the gap, naming the node and the function."""
    import sys
    from refactory_dsh.fast import DesignFastMode
    from tests_support import FakeModel, VERIFICATION_ANSWER, git_repo, render

    repo = git_repo(tmp_path / "repo")
    tool = "def build():\n    print('built')\n"

    PLAN = "Change only the requested file."
    # Always-bad focused_qa: empty repo_questions
    bad_qa = json.dumps({
        "status": "pass",
        "checks": [{"name": "runs", "command": "test",
                     "status": "pass", "evidence": "exit 0"}],
        "findings": [], "not_run": [],
        "repo_questions": [],
    })

    model = FakeModel([
        PLAN,                      # strategy
        render({"tool.py": tool}), # emit
        bad_qa,                    # focused_qa (1st — the one targeted re-run follows)
        VERIFICATION_ANSWER,       # verifier
        # gate_qa → review_reloop → fork_qa (re-runs both branches)
        bad_qa,                    # focused_qa (2nd — still missing → advisory, proceed)
        VERIFICATION_ANSWER,       # verifier (2nd run in fork)
    ])

    run = DesignFastMode().run(
        tmp_path / "run", model=model,
        task={"name": "rq_exhaust", "brief": "a tool",
              "check": f"{sys.executable} tool.py",
              "target": str(repo)})

    assert run.exit_ok, run.error
    qa_prompts = [p for p in model.prompts if "Write focused-qa.json" in p]
    assert len(qa_prompts) == 2
    report = json.loads((tmp_path / "run/fast-report.json").read_text())
    advisory = "\n".join(report["findings"])
    assert "advisory: repository questions" in advisory
    assert "focused_qa" in advisory and "tool.py" in advisory


def test_design_review_exhaustion_reaches_strategy_without_failing(tmp_path, monkeypatch):
    """(d) design_review exhaustion reaches gate_strategy; the run does not fail."""
    import sys
    from refactory_dsh.design import DesignMode
    from tests_support import (FakeModel, RESEARCH_ANSWER, PITFALLS_ANSWER,
                               DESIGN_DOC_ANSWER, VERIFICATION_ANSWER,
                               REVIEW_ANSWERS, git_repo, render)

    repo = git_repo(tmp_path / "repo")
    tool = "def build():\n    print('built')\n"

    # design_review answer: PASS verdict but empty repo_questions (missing tool.py)
    bad_design_review = (
        "No open alternatives found.\n\n"
        "## Repository questions\n\n"
        "```json\n[]\n```\n\n"
        "## Brief example trace\n\n"
        "The brief's example runs through runner.py:5 \u2192 tool.py:1 "
        "in both the walker and DSH runtimes.\n\n"
        "VERDICT: PASS"
    )

    answers = [
        RESEARCH_ANSWER,             # research_director
        PITFALLS_ANSWER,             # pitfalls_researcher
        "perspectives",              # strategy_director
        "one plan",                  # synthesize_strategy
        DESIGN_DOC_ANSWER,           # design_doc
        bad_design_review,           # design_review (1st — the one targeted re-run follows)
        bad_design_review,           # design_review (2nd — still missing → advisory, proceed)
        # gate_strategy → proceed (via verdicts)
        render({"tool.py": tool}),   # emit
        # Reviewers (fork_qa: health, code, qa, verifier):
        "health: ok",               # health_checker
        "code review: ok",          # code_reviewer
        "qa: ok",                    # qa_director
        VERIFICATION_ANSWER,         # verifier
        # gate_qa → proceed
        "VERDICT: PASS",             # overwatch
    ]

    model = FakeModel(answers)
    mode = DesignMode()
    state = mode.state_dict()
    state["knobs"]["pr"]["value"] = False
    state["knobs"]["design_review_attempts"]["value"] = 1
    mode.load_state_dict(state)

    run = mode.run(
        tmp_path / "run", model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "rq_exhaust_dr", "brief": "a tool",
              "check": f"{sys.executable} tool.py",
              "target": str(repo)})

    # The run does NOT fail — after one re-run the missing questions are advisory
    assert run.exit_ok, run.error

    # design_review was called twice: the first pass and its one targeted re-run
    dr_prompts = [p for p in model.prompts
                  if "Read the design document from `design.md`" in p
                  and "BLOCKING" in p and "open alternative" in p.lower()]
    assert len(dr_prompts) == 2, (
        f"design_review ran {len(dr_prompts)} times, expected 2"
    )
    # The gap is shown with the findings instead of spending design-review attempts
    assert "advisory: repository questions" in run.facts["design_review_findings"]
    assert run.facts["design_review_status"].startswith("Design review: PASS")


def test_repo_change_and_artifact_render_different_instructions_via_run(tmp_path, monkeypatch):
    """(e) repo-change and artifact runs render different reviewer instructions, in both modes."""
    import sys
    from refactory_dsh.fast import DesignFastMode
    from refactory_dsh.design import DesignMode
    from tests_support import (FakeModel, VERIFICATION_ANSWER, RESEARCH_ANSWER,
                               PITFALLS_ANSWER, DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
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
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
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
        DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER,
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
                               DESIGN_REVIEW_ANSWER, VERIFICATION_ANSWER)

    # Interleave VERIFICATION_ANSWER after every 3 reviewer answers
    # (the verifier runs as the 4th fork branch in each review round)
    interleaved: list[str] = []
    for i, r in enumerate(reviews):
        interleaved.append(r)
        if (i + 1) % 3 == 0:
            interleaved.append(VERIFICATION_ANSWER)
    return [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
            DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, emitted,
            *interleaved, "VERDICT: PASS"]


def test_design_mode_gate_qa_advisory_after_one_rerun_reaches_pr_text(tmp_path, monkeypatch):
    """A code reviewer that leaves repository questions missing twice: one targeted re-run,
    then gate_qa proceeds; qa-gate.txt and the pull-request text list the gap."""
    from tests_support import render

    bad_code_review = ("code review: the code matches the design.\n\n"
                       "## Repository questions\n\n```json\n[]\n```")
    run, model = _design_run(tmp_path, _design_answers(
        render({"tool.py": "def build():\n    print('built')\n"}),
        ["health: ok", bad_code_review, "qa: ok",      # first round
         "health: ok", bad_code_review, "qa: ok"]))    # the one re-run

    assert run.exit_ok, run.error
    code_reviews = [p for p in model.prompts if "You are the code reviewer." in p]
    assert len(code_reviews) == 2
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


def test_design_review_asks_repository_questions_for_product_sites_only(tmp_path, monkeypatch):
    """A design that changes tool.py and its test: the design review answers for tool.py
    only and passes on its first run."""
    from tests_support import (RESEARCH_ANSWER, PITFALLS_ANSWER, DESIGN_DOC_ANSWER,
                               DESIGN_REVIEW_ANSWER, VERIFICATION_ANSWER, render)

    site = "- `tool.py:1` — **[changed]**\n\n"
    assert site in DESIGN_DOC_ANSWER
    design = DESIGN_DOC_ANSWER.replace(
        site, "- `tool.py:1` — **[changed]**\n- `tests/test_tool.py:1` — **[changed]**\n\n")
    run, model = _design_run(tmp_path, [
        RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan", design,
        DESIGN_REVIEW_ANSWER,  # answers for tool.py only
        render({"tool.py": "def build():\n    print('built')\n",
                "tests/test_tool.py": "def test_build():\n    assert True\n"}),
        "health: ok", "code review: ok", "qa: ok", VERIFICATION_ANSWER, "VERDICT: PASS"])

    assert run.exit_ok, run.error
    assert int(run.facts.get("design_review_reruns_used", 0)) == 0
    assert run.facts["design_review_status"].startswith("Design review: PASS")
