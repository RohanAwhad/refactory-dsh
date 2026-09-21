"""The toolchain gate: a test that did not run has to be a reported fact.

Issue #44: the Lean and mathlib tests skip when the tools are absent, the suite goes
green, and the green says **nothing** about the checker path — which is how two real
defects hid. These hold the two halves: every test gated by a real tool is marked, and
the gate turns a skip (or a deselection) into a failure that names the reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

import conftest as gate

TESTS = Path(__file__).resolve().parent

#: The decorators that gate a test on a real external tool.
NEEDS = {"needs_lean", "needs_mathlib", "needs_sympy"}


def _decorated_functions(path: Path):
    """Every function in a test module with its decorator names dotted out."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names: list[str] = []
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            parts: list[str] = []
            while isinstance(target, ast.Attribute):
                parts.append(target.attr)
                target = target.value
            if isinstance(target, ast.Name):
                parts.append(target.id)
            names.append(".".join(reversed(parts)))
        yield node.name, names


def test_every_test_gated_by_a_real_tool_is_marked_toolchain():
    """The marked set is the gated set, so it cannot drift out of the gate's view."""
    unmarked: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        for name, decorators in _decorated_functions(path):
            if not (NEEDS & set(decorators)):
                continue
            if "pytest.mark.toolchain" not in decorators:
                unmarked.append(f"{path.name}::{name}")
    assert not unmarked, (
        "these tests skip when an external tool is absent but are not marked "
        f"toolchain, so the gate would report a green suite while they never ran: {unmarked}"
    )


def _marked_tools() -> set[str]:
    """The tool names the sources declare, read without collecting the suite."""
    tools: set[str] = set()
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "toolchain"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                tools.add(str(node.args[0].value))
    return tools


def test_every_declared_tool_is_represented_in_the_gated_set():
    """A gate that covers two of the three tools passes for the wrong reason."""
    tools = _marked_tools()
    assert {"lean", "mathlib", "sympy"} <= tools, (
        f"the gate has no test for some tool it is supposed to cover: {sorted(tools)}"
    )


def test_the_gate_counts_a_skip_as_unverified(monkeypatch):
    monkeypatch.setattr(gate, "_TOOLCHAIN", {"t.py::a": "lean", "t.py::b": "mathlib"})
    monkeypatch.setattr(
        gate,
        "_TOOLCHAIN_RESULT",
        {"t.py::a": ("passed", ""), "t.py::b": ("skipped", "mathlib is not installed")},
    )
    problems = gate._unverified()
    assert [problem[0] for problem in problems] == ["t.py::b"]
    assert problems[0][1] == "mathlib"
    assert "mathlib is not installed" in problems[0][3]


def test_a_marked_test_with_no_report_is_unverified(monkeypatch):
    """Collected but never run — a deselection — is not a pass either."""
    monkeypatch.setattr(gate, "_TOOLCHAIN", {"t.py::a": "lean"})
    monkeypatch.setattr(gate, "_TOOLCHAIN_RESULT", {})
    assert gate._unverified(), "a marked test with no report counted as verified"
