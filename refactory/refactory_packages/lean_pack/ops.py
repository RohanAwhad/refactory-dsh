"""The operations the Lean mode calls.

Each op reads the run directory, writes files it declares, and returns the facts it
declares. Two of them shell out to the Lean toolchain, and that is deliberate: the
whole point of this package is that the verdict comes from a proof checker and not
from Python. Nothing here asserts a theorem; ``check_with_lean`` runs ``lake`` or
``lean`` over a per-problem project and records what came back.

The subprocess is not run under ``refactory_dsh.sandbox``. That containment exists
for code nobody reviewed -- a generated package -- and it scrubs the environment to
an allowlist, which would strip ``ELAN_HOME`` and ``MATHLIB_PATH`` and make the
toolchain unfindable. This package ships and was read; the containment it needs is
the timeout the ``timeout_s`` knob sets.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from refactory_dsh import toolchain
from refactory_dsh.nn import Verdict

from . import problems

#: This module is ``refactory_packages.<package>.ops``, so the package it belongs to
#: is the second-to-last component of its own dotted name. Deriving it rather than
#: repeating the name keeps the ops correct under a rename and under the workspace
#: root, where a package is imported through the same ``refactory_packages`` path.
PACKAGE = __name__.split(".")[-2]

PROBLEM_FILE = "problem.json"
TOOLCHAIN_FILE = "toolchain.json"
STATEMENT_CHECK = "statement_check.json"
ATTEMPT_FILE = "attempt.json"
VERIFY_FILE = "verify.json"
RESULT_FILE = "result.json"

#: The per-problem Lean project, inside the run directory.
PROJECT = "project"
STATEMENT = f"{PROJECT}/Problem.lean"
SOLUTION = f"{PROJECT}/Solution.lean"
LAKEFILE = f"{PROJECT}/lakefile.toml"

#: How much of Lean's output is kept. Enough to read the first error, which is the
#: only one that matters, and small enough that a run's artifacts stay readable.
_OUTPUT_LIMIT = 4000


# ── the project ────────────────────────────────────────────────────────────
def _lakefile(problem: problems.Problem, library: str) -> str:
    """The per-problem Lake project: a library, and mathlib when the problem needs it.

    No ``lean-toolchain`` file is written. Pinning one would make ``elan`` download
    that exact toolchain, and a run that installs a compiler as a side effect of
    evaluation is the thing this package refuses to do; the project uses whichever
    toolchain the machine already has. ``path`` rather than a git dependency for
    mathlib, for the same reason: a checkout the user already built, never a fetch.
    """
    lines = [
        "# Written by refactory_packages.lean_pack. One project per problem,",
        "# because the statement, the solution and the library they need are",
        "# properties of the problem rather than of the checkout.",
        'name = "problem"',
        'defaultTargets = ["Problem"]',
        "",
        "[[lean_lib]]",
        'name = "Problem"',
    ]
    if library == "mathlib":
        mathlib = Path(toolchain.require(PACKAGE, "mathlib").tool.resolved()).resolve()
        lines += [
            "",
            "[[require]]",
            'name = "mathlib"',
            f'path = "{mathlib}"',
        ]
    return "\n".join(lines) + "\n"


def _checker_command() -> list[str]:
    """``lake env lean`` when lake is here, plain ``lean`` when it is not.

    ``lake`` is what puts a project's library path in front of the compiler, which
    is why the mathlib problem cannot be checked without it. The core problem has
    nothing to import, so a machine with only ``lean`` can still run it.
    """
    if shutil.which("lake"):
        return ["lake", "env", "lean"]
    return ["lean"]


def _run_checker(workdir: Path, filename: str, timeout_s: int) -> dict[str, Any]:
    """Run the checker over one file of the project, and record what it said."""
    command = [*_checker_command(), filename]
    started = time.time()
    try:
        completed = subprocess.run(
            command, cwd=str(workdir), capture_output=True, text=True, timeout=timeout_s
        )
        output = f"{completed.stdout or ''}{completed.stderr or ''}"
        exit_code: int | None = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        output = f"the checker exceeded {timeout_s}s\n{exc}"
        exit_code, timed_out = None, True
    except OSError as exc:  # the binary vanished between the check and the run
        output = f"the checker could not be started: {type(exc).__name__}: {exc}"
        exit_code, timed_out = None, False
    return {
        "command": " ".join(command),
        "file": filename,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": round(time.time() - started, 4),
        "output": output[-_OUTPUT_LIMIT:],
    }


# ── the pipeline ───────────────────────────────────────────────────────────
def load_problem(ctx: Any) -> dict[str, Any]:
    """Write the theorem this run will attempt, and the proofs it may try."""
    library = str(ctx.knob("library", "core"))
    problem = problems.by_library(library)
    ctx.write_json(PROBLEM_FILE, problem.to_state())
    return {"problem_name": problem.name}


def check_toolchain(ctx: Any) -> dict[str, Any]:
    """Refuse the run here if the proof checker is not on this machine.

    The first statement of the first node that could consume anything, so the
    failure names the missing tool and its install command instead of surfacing as a
    ``FileNotFoundError`` from a subprocess several steps later.
    """
    library = str(ctx.knob("library", "core"))
    lean = toolchain.require(PACKAGE, "lean")
    used = [lean.to_dict()]
    if library == "mathlib":
        used.append(toolchain.require(PACKAGE, "mathlib").to_dict())
    ctx.write_json(TOOLCHAIN_FILE, {"package": PACKAGE, "library": library, "tools": used})
    return {"lean_version": lean.version or "unknown"}


def _project_toolchain(workdir: Path, timeout_s: int) -> str:
    """The version of the toolchain that will check this project, once it exists.

    Not the same fact as ``check_toolchain``'s ``lean_version``, and the difference
    is not academic. That one probes ``lean`` on the PATH. A project that requires
    mathlib gets a ``lean-toolchain`` from the dependency, so ``lake env lean`` runs
    a *different* compiler than the one on the PATH -- on this machine 4.34.0-rc2
    from mathlib against 4.33.1 on the PATH. A record that names the PATH version
    names a compiler that did not decide anything.
    """
    command = [*_checker_command(), "--version"]
    try:
        completed = subprocess.run(
            command, cwd=str(workdir), capture_output=True, text=True, timeout=timeout_s
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"unknown ({type(exc).__name__})"
    text = f"{completed.stdout or ''}{completed.stderr or ''}".strip()
    return text.splitlines()[0] if text else "unknown"


def scaffold_project(ctx: Any) -> dict[str, Any]:
    """Write the project and check the statement on its own.

    The statement is checked before any proof is attempted, because a statement
    that does not elaborate cannot be proved by any tactic and the run should say so
    once rather than once per strategy. The proof is left ``sorry`` here: this file
    is the spec, and a candidate edits only the solution.
    """
    problem = problems.Problem.from_state(ctx.read_json(PROBLEM_FILE) or {})
    library = str(ctx.knob("library", "core"))
    timeout_s = int(ctx.knob("timeout_s", 60))

    ctx.write_text(LAKEFILE, _lakefile(problem, library))
    ctx.write_text(STATEMENT, problems.statement_source(problem))
    checked = _run_checker(ctx.path(PROJECT), "Problem.lean", timeout_s)
    # Asked here because this is the first moment the project exists, and the whole
    # point of the record is to name the checker that decided.
    version = _project_toolchain(ctx.path(PROJECT), timeout_s)
    ctx.write_json(
        STATEMENT_CHECK,
        {**checked, "ok": checked["exit_code"] == 0, "checker_version": version},
    )
    return {"statement_ok": 1 if checked["exit_code"] == 0 else 0, "checker_version": version}


def write_solution(ctx: Any) -> dict[str, Any]:
    """Write the next proof into the solution file.

    Which strategy comes next is the previous attempt's count, so the retry gate
    walks the catalogue deterministically: the run never consults a clock, and the
    same state produces the same proof.
    """
    problem = problems.Problem.from_state(ctx.read_json(PROBLEM_FILE) or {})
    previous = ctx.read_json(VERIFY_FILE, {}) or {}
    attempts = int(previous.get("attempt", 0))
    strategy = problem.rotated(str(ctx.knob("strategy", "omega")), attempts)

    ctx.write_text(SOLUTION, problems.solution_source(problem, strategy))
    ctx.write_json(ATTEMPT_FILE, {"attempt": attempts + 1, "strategy": strategy})
    return {"strategy_used": strategy}


def check_with_lean(ctx: Any) -> dict[str, Any]:
    """Ask Lean whether the solution is a proof, and record its answer.

    Two conditions, and both come from the checker. The exit code says the file
    elaborated; the axiom dump says what the proof assumed. A ``sorry`` closes a
    goal and elaborates cleanly, so the exit code alone would accept an unfinished
    proof -- ``sorryAx`` in ``#print axioms`` is Lean reporting the hole itself,
    rather than this module pattern-matching on the tactic text.
    """
    timeout_s = int(ctx.knob("timeout_s", 60))
    attempt = ctx.read_json(ATTEMPT_FILE, {}) or {}
    checked = _run_checker(ctx.path(PROJECT), "Solution.lean", timeout_s)
    sorry = "sorryAx" in checked["output"]
    verified = checked["exit_code"] == 0 and not sorry
    ctx.write_json(VERIFY_FILE, {
        **checked,
        "attempt": int(attempt.get("attempt", 1)),
        "strategy": str(attempt.get("strategy", "")),
        "used_sorry": sorry,
        "verified": verified,
    })
    return {"verified": 1 if verified else 0}


def retry_gate(ctx: Any) -> str:
    """Gate: reloop while a strategy remains untried, then proceed."""
    problem = problems.Problem.from_state(ctx.read_json(PROBLEM_FILE) or {})
    verify = ctx.read_json(VERIFY_FILE, {}) or {}
    attempts = int(verify.get("attempt", 0))
    allowed = min(int(ctx.knob("attempts", 4)), len(problem.proofs))
    ctx.set_fact("attempts_used", attempts)
    if verify.get("verified") or attempts >= allowed:
        return "proceed"
    # Lean's own error says why this strategy failed; its tail carries it.
    why = "it used `sorry`" if verify.get("used_sorry") else "Lean rejected it"
    reason = f"attempt {attempts} of {allowed} ({verify.get('strategy', '?')}) did not verify: {why}"
    output = str(verify.get("output", "")).strip()
    return Verdict("reloop", f"{reason}\n{output[-1500:]}" if output else reason)


def finish(ctx: Any) -> dict[str, Any]:
    """Close the run and publish what the checker decided."""
    problem = ctx.read_json(PROBLEM_FILE, {}) or {}
    verify = ctx.read_json(VERIFY_FILE, {}) or {}
    statement = ctx.read_json(STATEMENT_CHECK, {}) or {}
    verified = bool(verify.get("verified"))
    ctx.write_json(RESULT_FILE, {
        "problem": problem.get("name"),
        "library": problem.get("library"),
        "statement": problem.get("statement"),
        # The compiler that decided, which is the project's and not necessarily the
        # one on the PATH.
        "checker_version": statement.get("checker_version"),
        "verified": verified,
        "attempts": int(verify.get("attempt", 0)),
        "strategy": verify.get("strategy"),
        "checker": verify.get("command"),
        "exit_code": verify.get("exit_code"),
        "used_sorry": bool(verify.get("used_sorry")),
        "output": str(verify.get("output", "")),
    })
    return {
        "verified": 1 if verified else 0,
        "attempts_used": max(1, int(verify.get("attempt", 1))),
    }
