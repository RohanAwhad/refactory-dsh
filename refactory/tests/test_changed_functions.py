"""Tests for the changed_functions module.

Each test validates that the ast-based Python span mapper and the TypeScript hunk
range reporter produce correct results.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from refactory_dsh.changed_functions import (
    changed_functions,
    compute_and_write,
    format_changed_functions,
    _parse_diff,
    _python_spans,
    _python_top_level_names,
)


def test_python_function_matching_not_hunk_header(tmp_path):
    """A diff whose hunk header says _report but the actual change is in _repair."""
    source = textwrap.dedent("""\
        def _report():
            print("report")

        def _repair():
            print("repair old")
    """)
    (tmp_path / "mod.py").write_text(source.replace("repair old", "repair new"))

    diff = textwrap.dedent("""\
        diff --git a/mod.py b/mod.py
        --- a/mod.py
        +++ b/mod.py
        @@ -3,3 +3,3 @@ def _report():
         
         def _repair():
        -    print("repair old")
        +    print("repair new")
    """)
    result = changed_functions(diff, tmp_path, "HEAD~1")
    functions = [e["function"] for e in result]
    assert any("_repair" in f for f in functions), f"Expected _repair, got {functions}"
    assert not any("_report" in f for f in functions), f"Should not match _report: {functions}"


def test_typescript_hunk_range_entries(tmp_path):
    """A diff changing a TypeScript file returns file:first-last entries."""
    source = "export function hello() {\n  return 'world';\n}\n"
    (tmp_path / "index.ts").write_text(source)

    diff = textwrap.dedent("""\
        diff --git a/index.ts b/index.ts
        --- a/index.ts
        +++ b/index.ts
        @@ -1,3 +1,3 @@
         export function hello() {
        -  return 'world';
        +  return 'hello world';
         }
    """)
    result = changed_functions(diff, tmp_path, "HEAD~1")
    assert len(result) >= 1
    assert any("index.ts:" in e["function"] for e in result)


def test_class_method_naming(tmp_path):
    """A change inside a class method is reported as ClassName.method_name."""
    source = textwrap.dedent("""\
        class Parser:
            def parse(self):
                return "parsed"

            def validate(self):
                return True
    """)
    (tmp_path / "parser.py").write_text(source)

    diff = textwrap.dedent("""\
        diff --git a/parser.py b/parser.py
        --- a/parser.py
        +++ b/parser.py
        @@ -2,2 +2,2 @@ class Parser:
             def parse(self):
        -        return "parsed"
        +        return "parsed value"
    """)
    result = changed_functions(diff, tmp_path, "HEAD~1")
    functions = [e["function"] for e in result]
    assert any("Parser.parse" in f for f in functions)


def test_top_level_assignment_naming(tmp_path):
    """A changed top-level CONSTANT = ... is reported as CONSTANT."""
    source = "CONSTANT = 42\n\ndef func():\n    pass\n"
    (tmp_path / "config.py").write_text(source)

    diff = textwrap.dedent("""\
        diff --git a/config.py b/config.py
        --- a/config.py
        +++ b/config.py
        @@ -1 +1 @@
        -CONSTANT = 41
        +CONSTANT = 42
    """)
    result = changed_functions(diff, tmp_path, "HEAD~1")
    functions = [e["function"] for e in result]
    assert any("CONSTANT" in f for f in functions)


def test_annotated_assignment_target(tmp_path):
    """A changed top-level CONSTANT: str = ... is reported as CONSTANT."""
    source = 'CONSTANT: str = "hello"\n'
    (tmp_path / "types.py").write_text(source)

    diff = textwrap.dedent("""\
        diff --git a/types.py b/types.py
        --- a/types.py
        +++ b/types.py
        @@ -1 +1 @@
        -CONSTANT: str = "old"
        +CONSTANT: str = "hello"
    """)
    result = changed_functions(diff, tmp_path, "HEAD~1")
    functions = [e["function"] for e in result]
    assert any("CONSTANT" in f for f in functions)


def test_compute_and_write_empty(tmp_path):
    """compute_and_write with empty diff writes []."""
    formatted = compute_and_write("", tmp_path, tmp_path, "HEAD")
    assert formatted == "(none)"
    data = json.loads((tmp_path / "changed-functions.json").read_text())
    assert data == []


def test_format_changed_functions():
    """format_changed_functions produces readable output."""
    entries = [
        {"function": "mod.py::MyClass.method", "file": "mod.py", "line": 10, "test": False},
    ]
    result = format_changed_functions(entries)
    assert "mod.py::MyClass.method" in result
    assert "(none)" != result


def test_python_spans_with_decorators():
    """A decorator counts as part of the function: the span starts at the decorator."""
    source = textwrap.dedent("""\
        @decorator
        def my_func():
            pass
    """)
    spans = _python_spans(source)
    assert len(spans) >= 1
    start, end, name = spans[0]
    assert start == 1  # starts at the decorator
    assert name == "my_func"


def _git(root: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_tests_and_generated_files_need_no_repository_questions(tmp_path):
    """Entries are flagged test/generated, and only product code is listed for review."""
    from refactory_dsh.changed_functions import review_functions

    (tmp_path / "tests").mkdir()
    (tmp_path / "tool.py").write_text("def build():\n    return 2\n")
    (tmp_path / "tests" / "test_tool.py").write_text("def test_build():\n    assert True\n")
    (tmp_path / "help.ts").write_text(
        "// Generated offline by tools.gen. Do not edit.\nexport const A = 2\n")
    diff = textwrap.dedent("""\
        diff --git a/tool.py b/tool.py
        --- a/tool.py
        +++ b/tool.py
        @@ -1,2 +1,2 @@
         def build():
        -    return 1
        +    return 2
        diff --git a/tests/test_tool.py b/tests/test_tool.py
        --- a/tests/test_tool.py
        +++ b/tests/test_tool.py
        @@ -1,2 +1,2 @@
         def test_build():
        -    assert False
        +    assert True
        diff --git a/help.ts b/help.ts
        --- a/help.ts
        +++ b/help.ts
        @@ -1,2 +1,2 @@
         // Generated offline by tools.gen. Do not edit.
        -export const A = 1
        +export const A = 2
    """)
    entries = changed_functions(diff, tmp_path, "HEAD")
    flags = {e["function"]: (e["test"], e["generated"]) for e in entries}
    assert flags == {"tool.py::build": (False, False),
                     "tests/test_tool.py::test_build": (True, False),
                     "help.ts:2-2": (False, True)}
    assert review_functions(entries) == ["tool.py::build"]
    formatted = format_changed_functions(entries)
    assert "tool.py::build" in formatted and "test_build" not in formatted
    assert "2 more changed in tests or generated files" in formatted


def test_changes_merged_in_from_the_base_are_not_the_runs(tmp_path):
    """A run that merged its base in after it began: only its own functions and hunks count."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    ts_lines = [f"export const l{i} = {i}" for i in range(1, 21)]
    (repo / "a.py").write_text("def fa():\n    return 1\n")
    (repo / "b.py").write_text("def fb():\n    return 1\n")
    (repo / "c.ts").write_text("\n".join(ts_lines) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    # The base branch moves on: it changes fa and the top of c.ts.
    (repo / "a.py").write_text("def fa():\n    return 2\n")
    (repo / "c.ts").write_text("\n".join(["export const l1 = 100", *ts_lines[1:]]) + "\n")
    _git(repo, "commit", "-q", "-am", "main moves on")
    _git(repo, "checkout", "-q", "-b", "run", base)
    # The run changes fb and the bottom of c.ts, then merges the base in.
    (repo / "b.py").write_text("def fb():\n    return 2\n")
    (repo / "c.ts").write_text("\n".join([*ts_lines[:-1], "export const l20 = 200"]) + "\n")
    _git(repo, "commit", "-q", "-am", "the run's change")
    _git(repo, "merge", "-q", "--no-edit", "main")

    diff = _git(repo, "diff", base, "HEAD") + "\n"
    functions = {e["function"] for e in changed_functions(diff, repo, base)}
    assert "a.py::fa" in functions  # the raw diff includes what main brought in

    workdir = tmp_path / "run"
    workdir.mkdir()
    compute_and_write(diff, repo, workdir, base)
    own = {e["function"] for e in json.loads((workdir / "changed-functions.json").read_text())}
    assert own == {"b.py::fb", "c.ts:20-20"}
