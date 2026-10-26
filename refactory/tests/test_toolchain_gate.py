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
NEEDS = {"needs_lean", "needs_mathlib", "needs_sympy", "needs_factory", "needs_model_credential"}


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
    # The model-credential tests run against a fake configured home now (dm-257), so no
    # test waits on this machine's own credential.
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


def test_the_gate_sets_a_failing_exit_status(monkeypatch):
    """The counting above is the substance; this holds the path that acts on it.

    `_unverified()` reporting a problem and `--toolchain-gate` actually exiting
    nonzero are two facts, and only the first was covered: #44 proved the session
    status by hand with a temporary probe, so nothing kept the gate itself honest.
    A gate that quietly stops failing is the exact regression it exists to prevent.
    """
    monkeypatch.setattr(gate, "_TOOLCHAIN", {"t.py::a": "mathlib"})
    monkeypatch.setattr(
        gate, "_TOOLCHAIN_RESULT", {"t.py::a": ("skipped", "mathlib is not installed")}
    )

    class Session:
        class config:  # noqa: N801 - mirrors pytest's own attribute shape
            @staticmethod
            def getoption(name: str, default: object = None) -> object:
                return name == "--toolchain-gate"

        exitstatus = 0

    session = Session()
    gate.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == 1, "a skipped marked test left the session green"

    # And a verified run keeps the status it earned.
    monkeypatch.setattr(gate, "_TOOLCHAIN_RESULT", {"t.py::a": ("passed", "")})
    passed = Session()
    gate.pytest_sessionfinish(passed, 0)  # type: ignore[arg-type]
    assert passed.exitstatus == 0, "a run that verified every tool was failed anyway"


def test_the_gate_is_inert_without_the_flag(monkeypatch):
    """`--toolchain-gate` is the opt-in; a plain run must not fail on a skip."""
    monkeypatch.setattr(gate, "_TOOLCHAIN", {"t.py::a": "mathlib"})
    monkeypatch.setattr(
        gate, "_TOOLCHAIN_RESULT", {"t.py::a": ("skipped", "mathlib is not installed")}
    )

    class Session:
        class config:  # noqa: N801 - mirrors pytest's own attribute shape
            @staticmethod
            def getoption(name: str, default: object = None) -> object:
                return False

        exitstatus = 0

    session = Session()
    gate.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == 0, "a plain run failed on a skip the flag never asked about"
