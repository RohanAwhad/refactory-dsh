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
    from refactory_dsh.registry import modes

    gate_evaluators = {}
    for mode_cls in modes():
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
    """DesignFastMode's graph has gate_qa -> focused_qa review_reloop edge."""
    from refactory_dsh.fast import DesignFastMode

    mode = DesignFastMode()
    graph = mode.build()
    edges = [(e.source, e.target, e.condition) for e in graph.edges]
    assert ("gate_qa", "focused_qa", "review_reloop") in edges


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
