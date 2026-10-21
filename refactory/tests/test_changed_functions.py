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
