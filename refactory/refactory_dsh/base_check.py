"""Base-comparison gate: decide which check failures the attempt caused.

Before a failed check spends a code attempt, compare with the unchanged base in the
same environment. A failure the base has too is inherited, not the attempt's, only if
all three conditions hold:

(a) the same test identifier fails on both base and attempt;
(b) its test file is not one the attempt changes;
(c) its test id or leaf name appears in neither the brief text nor design.md.

When the check cannot identify individual tests, no failure is excused (conservative
fallback), and an excusal stands only if the identified failures are the whole of what
failed and nothing was left unrun behind them (`excusal_blocker`). When the base
environment cannot be prepared, the run stops as unavailable.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from refactory_dsh import sandbox, worktree
from refactory_dsh.worktree import _json as _read_json, NO_HOOKS, _isolate_hooks

# ── parse test failures ──────────────────────────────────────────────────

_PYTEST_FAILED = re.compile(r"^FAILED\s+([\w/.:\[\]-]+(?:::[\w\[\]-]+)+)", re.MULTILINE)
_VITEST_FAIL = re.compile(r"^\s*(?:FAIL|×)\s+(.+)$", re.MULTILINE)
_TEST_FILE_PATTERNS = re.compile(
    r"(?:^|/)(?:test_[\w]+\.py|[\w]+_test\.py|[\w]+\.test\.[tj]sx?|[\w]+\.spec\.[tj]sx?|test_[\w]+\.[tj]s)$"
)
_PYTEST_ERROR = re.compile(
    r"^(?:ERROR|ERRORS?)\s+([\w/.:\[\]-]+(?:::[\w\[\]-]+)*)\s*[-–—]", re.MULTILINE
)

CACHE_FILE = Path(".refactory") / "base-check.json"
BASE_WORKTREE_PREFIX = "base-"


def parse_test_failures(output: str) -> set[str]:
    """Extract individual test identifiers from check output.

    Returns empty set when no recognisable test names are found (conservative fallback).
    """
    found: set[str] = set()
    for m in _PYTEST_FAILED.finditer(output):
        found.add(m.group(1).strip())
    # An error (a module that does not import, a fixture that fails) is a failure too.
    for m in _PYTEST_ERROR.finditer(output):
        found.add(m.group(1).strip())
    for m in _VITEST_FAIL.finditer(output):
        candidate = m.group(1).strip()
        if " > " in candidate:
            found.add(candidate)
    return found


#: A flag that stops a runner at its first failure, so nothing after it runs: pytest's
#: ``-x`` (alone or in a cluster such as ``-xq``; ``-r`` takes report letters instead),
#: ``--exitfirst`` and ``--maxfail``, and vitest's and jest's ``--bail``.
_EXIT_FIRST = re.compile(r"^(?:-(?!r)[a-zA-Z]*x[a-zA-Z]*|--exitfirst|--maxfail(?:=.*)?|--bail(?:=.*)?)$")
_PYTEST_SUMMARY = re.compile(r"^=*\s*(\d+ (?:failed|passed|errors?|skipped)\b.*\bin [\d.]+s\b.*)$", re.MULTILINE)


def _steps(command: str) -> list[str]:
    """The shell steps ``command`` chains with ``&&``, ``||``, ``;``, ``|``, ``&`` or newlines.

    Quotes, backslashes and parentheses are respected; a step inside a subshell stays
    part of the step that opens it.
    """
    steps, current, depth, quote, i = [], "", 0, "", 0
    while i < len(command):
        char = command[i]
        if quote:
            current += char
            if char == "\\" and quote == '"' and i + 1 < len(command):
                current += command[i + 1]
                i += 1
            elif char == quote:
                quote = ""
        elif char == "\\" and i + 1 < len(command):
            current += command[i:i + 2]
            i += 1
        elif char in "'\"":
            quote = char
            current += char
        elif char == "(":
            depth += 1
            current += char
        elif char == ")":
            depth -= 1
            current += char
        elif char == "&" and (current.endswith((">", "<")) or command[i + 1:i + 2] == ">"):
            current += char  # a redirection (2>&1, &>log), not a step
        elif depth == 0 and (char in ";|&\n"):
            steps.append(current.strip())
            current = ""
            if command[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            current += char
        i += 1
    steps.append(current.strip())
    return [step for step in steps if step]


def _runner(step: str) -> str | None:
    """``pytest`` or ``vitest`` when ``step`` names that runner (jest reports like vitest)."""
    import shlex

    try:
        words = shlex.split(step)
    except ValueError:
        return None
    names = {Path(word).name for word in words}
    if "pytest" in names or "py.test" in names:
        return "pytest"
    if "vitest" in names or "jest" in names:
        return "vitest"
    return None


def _accounted(runner: str, output: str, failures: set[str]) -> bool:
    """Whether the runner's own summary counts exactly the failures that were identified."""
    if runner == "pytest":
        summaries = _PYTEST_SUMMARY.findall(output)
        if not summaries:
            return False
        last = summaries[-1]
        counted = sum(int(n) for n in re.findall(r"(\d+) (?:failed|errors?)\b", last))
        return counted == len(failures)
    tests = re.findall(r"^\s*Tests\s+(\d+) failed", output, re.MULTILINE)
    files = re.findall(r"^\s*Test Files\s+(\d+) failed", output, re.MULTILINE)
    if not tests:
        return False
    named_files = {failure.split(" > ")[0].strip() for failure in failures}
    return int(tests[-1]) == len(failures) and (not files or int(files[-1]) <= len(named_files))


def excusal_blocker(command: str, output: str, failures: set[str]) -> str | None:
    """Why the check's failures, all inherited, still cannot excuse it; None when they can.

    Matching tests is not enough: the check must have failed only for them, and nothing
    may have gone unrun behind them. So the failing runner has to be the command's last
    step, named so it can be recognised; it must not stop at its first failure; and its
    own summary must count exactly the failures identified. A later step, a module that
    does not import, or a step skipped after a failing one is otherwise unaccounted for.
    """
    steps = _steps(command)
    runner = _runner(steps[-1]) if steps else None
    if runner is None:
        return "the check's last step is not a test runner refactory recognises (pytest, vitest, jest)"
    kind = {"pytest": lambda failure: " > " not in failure, "vitest": lambda failure: " > " in failure}[runner]
    if not all(kind(failure) for failure in failures):
        return f"a failure is not from the check's last step ({runner}), so what follows it never ran"
    import shlex

    if any(_EXIT_FIRST.match(word) for word in shlex.split(steps[-1])):
        return f"{runner} stops at its first failure here, so the tests after it never ran"
    if not _accounted(runner, output, failures):
        return f"{runner}'s summary counts failures that are not identified by name"
    return None


def _parse_collection_errors(output: str) -> tuple[set[str], bool]:
    """Extract test files that had collection or import errors.

    Returns ``(files, has_unlocatable)`` where *has_unlocatable* is True when
    a collection error was detected but the importing file could not be
    determined (e.g. vitest's "does not provide an export named").
    """
    files: set[str] = set()
    has_unlocatable = False
    for m in _PYTEST_ERROR.finditer(output):
        ident = m.group(1).strip()
        # Extract just the file path (before any ::)
        path = ident.split("::")[0] if "::" in ident else ident
        if path.endswith(".py"):
            files.add(path)
    # Also catch "ImportError while importing" and "ModuleNotFoundError" patterns
    for line in output.splitlines():
        if "ImportError" in line or "ModuleNotFoundError" in line or "CollectionError" in line:
            # Try to extract the file from the surrounding context
            m = re.search(r"([\w/.-]+\.py)", line)
            if m:
                files.add(m.group(1))
    # Vitest collection errors — use non-greedy .*? to capture the full file path
    for m in re.finditer(r'Failed to collect\s+([\w/.-]+?\.(?:spec\.ts|ts))', output):
        files.add(m.group(1))
    for m in re.finditer(r'Error:.*?Cannot find module.*?([\w/.-]+?\.(?:spec\.ts|ts))', output):
        files.add(m.group(1))
    # "does not provide an export named" — record the importing file from surrounding output
    for m in re.finditer(r"does not provide an export named\s+'(\w+)'", output):
        found_file = False
        for line in output.splitlines():
            if m.group(0) in line:
                fm = re.search(r'([\w/.-]+?\.(?:spec\.ts|ts))', line)
                if fm:
                    files.add(fm.group(1))
                    found_file = True
                    break
        if not found_file:
            has_unlocatable = True
    # "Failed to load url" pattern
    for line in output.splitlines():
        if "Failed to load url" in line:
            m = re.search(r'([\w/.-]+?\.(?:spec\.ts|ts))', line)
            if m:
                files.add(m.group(1))
            else:
                has_unlocatable = True
    return files, has_unlocatable


def cache_key(base: str, setup: str, generate: str, check: str) -> str:
    payload = json.dumps(
        {"base": base, "setup": setup, "generate": generate, "check": check},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── data types ───────────────────────────────────────────────────────────


@dataclass
class BaseCheckResult:
    ok: bool
    failures: set[str]
    output: str
    exit_code: int
    environment_failure: str | None = None


@dataclass
class ComparisonResult:
    inherited: set[str]
    new_failures: set[str]
    all_excused: bool
    base_ok: bool


@dataclass
class FilteredCheckResult:
    all_excused: bool
    inherited: set[str]
    new_failures: set[str]
    new_failure_detail: str
    inherited_detail: str
    base_environment_failure: str | None
    guardedness: dict | None
    base_failures: set[str] | None = None
    base_complete: bool = False
    base_crashed: bool = False


# ── three-condition excusal ──────────────────────────────────────────────


def _test_name_parts(identifier: str) -> list[str]:
    """The parts of a test identifier to search for in the brief/design.

    For "tests/test_a.py::TestClass::test_method", yields:
    ["test_method", "TestClass::test_method", "tests/test_a.py::TestClass::test_method"]
    """
    parts = []
    if "::" in identifier:
        segments = identifier.split("::")
        for i in range(len(segments) - 1, -1, -1):
            parts.append("::".join(segments[i:]))
    elif " > " in identifier:
        segments = identifier.split(" > ")
        for i in range(len(segments) - 1, -1, -1):
            parts.append(" > ".join(segments[i:]))
    else:
        parts.append(identifier)
    return parts


def _test_file(identifier: str) -> str | None:
    """Extract the test file path from a test identifier, or a file-level error's file."""
    if identifier.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
        return identifier
    if "::" in identifier:
        return identifier.split("::")[0]
    if " > " in identifier:
        return identifier.split(" > ")[0].strip()
    return None


def compare_failures(
    base_failures: set[str],
    attempt_failures: set[str],
    changed_files: list[str],
    brief_text: str,
    design_text: str,
) -> ComparisonResult:
    """Classify attempt failures as inherited or new using the three-condition rule.

    A failure is inherited only if ALL THREE hold:
    (a) the same test identifier fails on both base and attempt;
    (b) its test file is not one the attempt changes;
    (c) its test id or leaf name appears in neither the brief text nor design.md.
    """
    if not attempt_failures:
        # No test identifiers parsed: conservative fallback.
        return ComparisonResult(
            inherited=set(), new_failures=set(), all_excused=False,
            base_ok=not bool(base_failures),
        )

    search_text = (brief_text + "\n" + design_text).lower()
    changed_set = set(changed_files)

    inherited: set[str] = set()
    new_failures: set[str] = set()

    for failure in attempt_failures:
        # Condition (a): same test fails on base
        if failure not in base_failures:
            new_failures.add(failure)
            continue
        # Condition (b): test file not changed by the attempt. A runner names the file
        # relative to where it ran (a package directory, say), git relative to the root.
        test_file = _test_file(failure)
        if test_file and any(path == test_file or path.endswith("/" + test_file)
                             for path in changed_set):
            new_failures.add(failure)
            continue
        # Condition (c): test name not mentioned in brief or design.md
        mentioned = any(
            part.lower() in search_text for part in _test_name_parts(failure)
        )
        if mentioned:
            new_failures.add(failure)
            continue
        # All three conditions hold: inherited
        inherited.add(failure)

    return ComparisonResult(
        inherited=inherited,
        new_failures=new_failures,
        all_excused=len(new_failures) == 0 and len(inherited) > 0,
        base_ok=not bool(base_failures),
    )


# ── base worktree lifecycle ──────────────────────────────────────────────


def _base_worktree_path(target: Path, run_id: str) -> Path:
    return worktree.slot(target) / f"{BASE_WORKTREE_PREFIX}{run_id}"


def _ensure_base_worktree(ctx: Any) -> tuple[Path, worktree.Worktree]:
    """Create and prepare the base worktree on demand, caching it per run.

    Returns the base worktree path and a Worktree descriptor for it.
    Raises ValueError with an environment-failure message on failure.
    """
    found = worktree.recorded(ctx.workdir)
    if found is None:
        raise ValueError("no worktree recorded for this run")

    target = Path(found.target)
    run_id = Path(found.path).name.removeprefix("run-")
    base_path = _base_worktree_path(target, run_id)

    # Create the worktree if it does not exist or points to the wrong commit
    if base_path.exists():
        try:
            head = worktree.git(base_path, "rev-parse", "HEAD").strip()
            if head != found.base:
                worktree.git(target, "worktree", "remove", "--force", str(base_path))
                worktree.git(
                    target, *NO_HOOKS, "worktree", "add", "-q", "--detach",
                    str(base_path), found.base,
                )
        except ValueError as exc:
            raise ValueError(
                f"could not verify base worktree for {found.base}: {exc}"
            ) from exc
    else:
        try:
            worktree.git(
                target, *NO_HOOKS, "worktree", "add", "-q", "--detach",
                str(base_path), found.base,
            )
        except ValueError as exc:
            raise ValueError(
                f"could not create base worktree for {found.base}: {exc}"
            ) from exc

    _isolate_hooks(target, base_path)

    # Build a Worktree descriptor for the base; use a synthetic branch name so
    # ensure_environment's record path does not collide with the run's own.
    base_found = worktree.Worktree(
        path=str(base_path),
        branch=f"{worktree.BRANCH_PREFIX}base-{run_id}",
        base=found.base,
        head=found.base,
        target=found.target,
        run=found.run,
    )

    # Prepare the environment (installs)
    budget = getattr(ctx, "budget", None)
    _, problems, _ = worktree.ensure_environment(base_found, ctx.workdir, budget)
    if problems:
        unavailable = [p for p in problems if "UNAVAILABLE:" in p]
        if unavailable:
            raise ValueError("\n".join(unavailable))
        raise ValueError(
            "environment preparation failed on the base:\n" + "\n".join(problems)
        )

    return base_path, base_found


def remove_base_worktree(target: Path, run_id: str) -> None:
    """Remove the base worktree when the run ends."""
    path = _base_worktree_path(target, run_id)
    if path.exists():
        try:
            worktree.git(target, "worktree", "remove", "--force", str(path))
        except ValueError:
            # Best effort — the worktree may already be gone
            shutil.rmtree(path, ignore_errors=True)


# ── identify changed files ───────────────────────────────────────────────


def _changed_test_files(path: Path, base: str) -> list[str]:
    try:
        changed = worktree.git(
            path, "diff", "--name-only", "--diff-filter=d", base, "HEAD"
        )
    except ValueError:
        return []
    return [f for f in changed.splitlines() if _TEST_FILE_PATTERNS.search(f)]


def _all_changed_files(path: Path, base: str) -> list[str]:
    try:
        changed = worktree.git(path, "diff", "--name-only", base, "HEAD")
    except ValueError:
        return []
    return [f.strip() for f in changed.splitlines() if f.strip()]


def _changed_files_in_test_dirs(path: Path, base: str, test_files: list[str]) -> list[str]:
    """All new or changed files in the same directories as the test files.

    Covers helpers, fixtures and other files the tests import (correction 3).
    The project root is excluded: a test at the root imports production code,
    and copying that code into the base worktree defeats the guardedness check.
    Only proper test subdirectories are considered.
    """
    if not test_files:
        return []
    test_dirs = {str(Path(tf).parent) for tf in test_files}
    # A test file at the project root (parent == ".") means every changed file
    # in the root would be copied — including production code the test is meant
    # to exercise.  Exclude the root so only actual test subdirectories supply
    # helpers and fixtures.
    test_dirs.discard(".")
    if not test_dirs:
        return []
    all_changed = _all_changed_files(path, base)
    return [
        f for f in all_changed
        if f not in test_files and any(
            f.startswith(d + "/") or str(Path(f).parent) == d
            for d in test_dirs
        )
    ]


# ── run base check ───────────────────────────────────────────────────────


def _read_cache(run: Path, key: str) -> BaseCheckResult | None:
    data = _read_json(run / CACHE_FILE)
    if data.get("cache_key") == key:
        result = BaseCheckResult(
            ok=data["ok"],
            failures=set(data.get("failures", [])),
            output=data.get("output", ""),
            exit_code=data.get("exit_code", 1),
            environment_failure=data.get("environment_failure"),
        )
        # Attach base_complete as a transient attribute for filter_check (R5).
        result._base_complete = data.get("base_complete", False)  # type: ignore[attr-defined]
        return result
    return None


def _write_cache(run: Path, key: str, result: BaseCheckResult) -> None:
    # Never cache a crash result — R2: later checks get a fresh attempt.
    # Deterministic (non-crash) environment failures ARE cached so filter_check
    # and the next attempt do not re-prepare the base (v5 correction 2).
    if getattr(result, '_crashed', False):
        return  # pragma: no cover — guarded by run_base_check
    cache_dir = run / CACHE_FILE.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Compute completeness for R5/R6.
    base_complete = getattr(result, '_base_complete', False)
    data = {
        "cache_key": key,
        "ok": result.ok,
        "failures": sorted(result.failures),
        "output": result.output[-10000:],
        "exit_code": result.exit_code,
        "environment_failure": result.environment_failure,
        "base_complete": base_complete,
    }
    (run / CACHE_FILE).write_text(json.dumps(data, indent=2) + "\n")


def run_base_check(ctx: Any, command: str, timeout: float) -> BaseCheckResult:
    """Run the check on the unchanged base in a separate worktree, caching the result.

    When any step crashes (a signal that survives ``run_command``'s retry),
    the base's HOME, worktree and cache are discarded and rebuilt from scratch
    (R2).  On double failure the result is returned with ``environment_failure``
    set but never cached, so later checks get a fresh attempt.  Deterministic
    (non-crash) failures are cached as before.
    """
    found = worktree.recorded(ctx.workdir)
    if found is None:
        return BaseCheckResult(ok=True, failures=set(), output="", exit_code=0)

    setup = str(ctx.facts.get("setup") or "").strip()
    generate = str(ctx.facts.get("generate") or "").strip()
    key = cache_key(found.base, setup, generate, command)

    cached = _read_cache(ctx.workdir, key)
    if cached is not None:
        return cached

    def _try_base() -> tuple[BaseCheckResult, bool]:
        """Attempt to prepare the base worktree and run the check.

        Returns ``(result, is_crash)``.  ``is_crash`` is True when a signal
        killed a preparation step and the base environment should be rebuilt.
        """
        try:
            base_path, base_found = _ensure_base_worktree(ctx)
        except ValueError as exc:
            err_str = str(exc)
            # Classify: use structured result from ensure_environment.
            # prep_fail returned by ensure_environment carries returncode.
            is_crash = any(tok in err_str for tok in ("killed by SIG", "killed by signal"))
            r = BaseCheckResult(
                ok=False, failures=set(), output=err_str, exit_code=-1,
                environment_failure=err_str,
            )
            r._crashed = is_crash  # type: ignore[attr-defined]
            return r, is_crash

        home = Path(ctx.workdir).resolve() / worktree.BASE_CHECK_HOME
        home.mkdir(parents=True, exist_ok=True)
        budget_inner = getattr(ctx, "budget", None)
        env = worktree.check_environment(base_path, home)

        from refactory_dsh.repo_change import prepare_commands as _prep

        _, problems, prep_fail = _prep(
            ctx, base_found, env, home, timeout, budget_inner, amend=False)
        if problems:
            # Check if any problem is a signal crash.
            is_crash = False
            if prep_fail is not None and isinstance(prep_fail.get("returncode"), int):
                prc = prep_fail["returncode"]
                is_crash = prc < 0 or prc in sandbox._AMBIGUOUS_SHELL_EXITS
            detail = "on the base: " + "\n".join(problems)
            r = BaseCheckResult(
                ok=False, failures=set(), output=detail, exit_code=-1,
                environment_failure=detail,
            )
            r._crashed = is_crash  # type: ignore[attr-defined]
            return r, is_crash

        check_result = sandbox.run_command(
            command, workdir=base_path, timeout_s=timeout,
            budget=budget_inner, env=env, home=home, exact_env=True,
        )
        crashed_chk, sig_chk = sandbox.is_signal_crash(check_result)
        if crashed_chk:
            r = BaseCheckResult(
                ok=False, failures=set(), output=f"base check command {sig_chk}",
                exit_code=-1, environment_failure=f"base check command {sig_chk}",
            )
            r._crashed = True  # type: ignore[attr-defined]
            return r, True

        output = "\n".join(
            (check_result.stdout or "", check_result.stderr or "")
        ).strip()
        failures = parse_test_failures(output)
        result = BaseCheckResult(
            ok=check_result.ok, failures=failures,
            output=output, exit_code=check_result.returncode or 0,
        )
        # Compute completeness for R5.
        blocker = excusal_blocker(command, output, failures) if failures else None
        result._base_complete = (blocker is None) if failures else check_result.ok  # type: ignore[attr-defined]
        return result, False

    result, is_crash = _try_base()
    if is_crash:
        # Discard and rebuild from scratch — R2.
        home = Path(ctx.workdir).resolve() / worktree.BASE_CHECK_HOME
        shutil.rmtree(home, ignore_errors=True)
        if found is not None:
            run_id = Path(found.path).name.removeprefix("run-")
            remove_base_worktree(Path(found.target), run_id)
        (ctx.workdir / CACHE_FILE).unlink(missing_ok=True)
        result, is_crash = _try_base()
        if is_crash or result.environment_failure:
            # Double crash or deterministic failure after rebuild.
            # Discard HOME and the base worktree so the NEXT check gets a
            # fresh start too (R2: never reuse a base environment whose
            # preparation crashed — the b27a rerun failure).
            home = Path(ctx.workdir).resolve() / worktree.BASE_CHECK_HOME
            shutil.rmtree(home, ignore_errors=True)
            if found is not None:
                run_id = Path(found.path).name.removeprefix("run-")
                remove_base_worktree(Path(found.target), run_id)
            (ctx.workdir / CACHE_FILE).unlink(missing_ok=True)
            result._crashed = is_crash  # type: ignore[attr-defined]
            return result

    _write_cache(ctx.workdir, key, result)
    return result


# ── test guardedness ─────────────────────────────────────────────────────


def _infer_test_runner(check_command: str) -> str | None:
    if "pytest" in check_command:
        return "pytest"
    if "vitest" in check_command or "jest" in check_command:
        return "vitest"
    return None


def compute_test_guardedness(
    ctx: Any, command: str, timeout: float,
) -> dict | None:
    """Run the attempt's new/changed test files against the base source.

    Returns a guardedness record dict; when the tests cannot be run on the base, the
    record says why under ``unavailable``. None only when the run has no worktree. Computed per attempt (never cached). Creates the base worktree on
    demand if it does not already exist.
    """
    found = worktree.recorded(ctx.workdir)
    if found is None:
        return None

    attempt_path = Path(found.path)
    new_test_files = _changed_test_files(attempt_path, found.base)
    if not new_test_files:
        return {
            "new_test_files": [],
            "results": [],
            "summary": {
                "guarding": 0, "not_guarding": 0, "total_new_tests": 0,
            },
        }

    runner = _infer_test_runner(command)
    if runner is None:
        return {"new_test_files": sorted(new_test_files),
                "unavailable": "the check command runs neither pytest nor vitest, so the new tests "
                               "could not be run on the base"}

    # Ensure the base worktree exists (on demand, correction 1)
    try:
        base_path, _ = _ensure_base_worktree(ctx)
    except ValueError as exc:
        return {"new_test_files": sorted(new_test_files),
                "unavailable": f"the base worktree could not be prepared: {exc}"}

    # Copy the attempt's new/changed test files AND their helpers/fixtures
    # into the base worktree (correction 3)
    helper_files = _changed_files_in_test_dirs(attempt_path, found.base, new_test_files)
    all_to_copy = new_test_files + helper_files
    copied: list[Path] = []
    for tf in all_to_copy:
        src = attempt_path / tf
        dst = base_path / tf
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)

    # Run the test files against the base source
    home = Path(ctx.workdir).resolve() / worktree.BASE_CHECK_HOME
    env = worktree.check_environment(base_path, home)
    budget = getattr(ctx, "budget", None)

    if runner == "pytest":
        test_cmd = f"python -m pytest -q {' '.join(new_test_files)}"
    else:
        test_cmd = f"npx vitest run {' '.join(new_test_files)}"

    test_result = sandbox.run_command(
        test_cmd, workdir=base_path, timeout_s=timeout,
        budget=budget, env=env, home=home, exact_env=True,
    )
    test_output = "\n".join(
        (test_result.stdout or "", test_result.stderr or "")
    ).strip()
    base_failures = parse_test_failures(test_output)
    collection_errors, _ = _parse_collection_errors(test_output)

    # Restore the base worktree: remove the copied files
    for dst in copied:
        dst.unlink(missing_ok=True)

    # Build the guardedness record (correction 4: distinguish import errors)
    guarding = 0
    not_guarding = 0
    cannot_run = 0
    results = []
    for tf in new_test_files:
        tests_in_file = sorted(
            f for f in base_failures
            if f.startswith(tf + "::") or f.startswith(tf + " > ")
        )
        has_collection_error = tf in collection_errors or any(
            e.startswith(tf) for e in collection_errors
        )
        if has_collection_error and not tests_in_file:
            # The test cannot be collected or imported on the base (correction 4):
            # reported as "cannot run on the base", not evidence of guarding.
            cannot_run += 1
            results.append({
                "file": tf, "tests": [], "file_level": "cannot_run_on_base",
                "guarding": False,
            })
        elif tests_in_file:
            # Assertion-level failures: real evidence of guarding
            guarding += len(tests_in_file)
            results.append({
                "file": tf,
                "tests": [
                    {"name": t, "on_base": "fail", "guarding": True}
                    for t in tests_in_file
                ],
            })
        elif not test_result.ok and not has_collection_error:
            # The test file failed but we can't parse which tests
            # Fall back to file-level assessment
            guarding += 1
            results.append({
                "file": tf, "tests": [], "file_level": "fail", "guarding": True,
            })
        else:
            not_guarding += 1
            results.append({
                "file": tf, "tests": [], "file_level": "pass", "guarding": False,
            })

    return {
        "new_test_files": sorted(new_test_files),
        "results": results,
        "summary": {
            "guarding": guarding,
            "not_guarding": not_guarding,
            "cannot_run_on_base": cannot_run,
            "total_new_tests": guarding + not_guarding + cannot_run,
        },
    }


# ── site-revert check ────────────────────────────────────────────────────


def _is_test_file(path: str) -> bool:
    """Whether *path* is a test file (not a code site for revert purposes)."""
    parts = Path(path).parts
    name = Path(path).name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    if name.endswith(".spec.ts"):
        return True
    if "tests" in parts or "test" in parts:
        return True
    return False


def _extract_defined_names(added_lines: list[str]) -> set[str]:
    """Extract def/class/assignment names from ``+`` lines of a hunk."""
    names: set[str] = set()
    for line in added_lines:
        m = re.match(r'\s*(?:def|class)\s+(\w+)', line)
        if m:
            names.add(m.group(1))
        m = re.match(r'\s*(\w+)\s*=', line)
        if m:
            names.add(m.group(1))
    return names


def _is_import_only_failure(output: str, defined_names: set[str]) -> bool:
    """Whether the failure is only NameError/AttributeError/ImportError on defined names.

    Classifies from pytest's ``E   <ErrorType>: <message>`` lines, not the
    truncated short-summary line ("…has no attrib..."), which leaves only the
    module name quoted.  A failure is import-only when every NameError,
    AttributeError or ImportError line names a definition the reverted hunks
    removed.
    """
    if not defined_names:
        return False
    found_errors = False
    all_names_defined = True
    # Extract from pytest's E-lines: ``E   NameError: name 'foo' is not defined``
    # and from full-output error lines.
    error_types = ("NameError", "AttributeError", "ImportError")
    for line in output.splitlines():
        stripped = line.strip()
        # Match pytest E-lines: "E   ErrorType: message"
        # Also match plain "ErrorType: message" lines for non-pytest output.
        for error_type in error_types:
            if error_type not in stripped:
                continue
            m = re.match(rf'(?:E\s+)?({error_type}):?\s+(.*)', stripped)
            if not m:
                continue
            found_errors = True
            snippet = m.group(2)
            quoted = re.findall(r"['\"](\w+)['\"]", snippet)
            if quoted:
                if not any(n in defined_names for n in quoted):
                    all_names_defined = False
            else:
                all_names_defined = False
    return found_errors and all_names_defined


def compute_site_reverts(
    ctx: Any, pairs: list[tuple[str, str, str]],
) -> dict[str, Any]:
    """Revert each site and run its named test to check reachability.

    *pairs* is a list of ``(site_path_line, test_file_and_name, scope)`` tuples.
    Operates on the run's own worktree (the attempt path), applies a reverse
    patch of only the overlapping hunks, runs the named test, and restores
    the file in a ``finally`` block.  Results are cached in a dedicated file.
    """
    found = worktree.recorded(ctx.workdir)
    if found is None:
        return {"no_base": True}

    attempt_path = Path(found.path)
    base = found.base
    head = found.head

    # Load or initialise cache
    cache_file = ctx.workdir / ".refactory" / "site-revert-cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    except (ValueError, OSError):
        cache = {}

    results: list[dict[str, Any]] = []
    summary = {"caught": 0, "not_caught": 0, "collection_error": 0, "unavailable": 0}

    import subprocess as _sp
    import time as _time

    total_timeout = 240  # cap total revert time (< gate DSH timeout)
    start_time = _time.monotonic()
    per_pair_timeout = 60

    for site, test_name, scope in pairs:
        # Time cap
        elapsed = _time.monotonic() - start_time
        if elapsed > total_timeout:
            results.append({
                "site": site, "test": test_name,
                "result": "unavailable", "catches_site": False,
            })
            summary["unavailable"] += 1
            continue

        cache_key_str = f"{head}|{site}|{test_name}"
        if cache_key_str in cache:
            cached = cache[cache_key_str]
            results.append(cached)
            if cached.get("catches_site"):
                summary["caught"] += 1
            elif cached.get("result") == "collection_error":
                summary["collection_error"] += 1
            elif cached.get("result") == "unavailable":
                summary["unavailable"] += 1
            else:
                summary["not_caught"] += 1
            continue

        # Test-file exclusion: a site inside a test file is not a code site
        site_path = site.split(":")[0] if ":" in site else site
        if _is_test_file(site_path):
            entry = {"site": site, "test": test_name,
                     "result": "unavailable", "catches_site": False}
            results.append(entry)
            cache[cache_key_str] = entry
            summary["unavailable"] += 1
            continue

        # Parse site line range
        parts = site.split(":")
        if len(parts) < 2:
            entry = {"site": site, "test": test_name,
                     "result": "not_in_diff", "catches_site": False}
            results.append(entry)
            cache[cache_key_str] = entry
            summary["not_caught"] += 1
            continue
        file_path = parts[0]
        line_spec = parts[1]
        if "-" in line_spec:
            line_start, line_end = line_spec.split("-", 1)
            try:
                line_start, line_end = int(line_start), int(line_end)
            except ValueError:
                line_start = line_end = 0
        else:
            try:
                line_start = line_end = int(line_spec)
            except ValueError:
                line_start = line_end = 0

        # Check if file exists in the worktree
        full_file = attempt_path / file_path
        if not full_file.exists():
            entry = {"site": site, "test": test_name,
                     "result": "not_in_diff", "catches_site": False}
            results.append(entry)
            cache[cache_key_str] = entry
            summary["not_caught"] += 1
            continue

        # Check if file exists on base (new file)
        try:
            _sp.run(
                ["git", "-C", str(attempt_path), "cat-file", "-e", f"{base}:{file_path}"],
                check=True, capture_output=True, timeout=10,
            )
            is_new_file = False
        except _sp.CalledProcessError:
            is_new_file = True
        except Exception:
            is_new_file = False

        if is_new_file:
            entry = {"site": site, "test": test_name,
                     "result": "new_file", "catches_site": False}
            results.append(entry)
            cache[cache_key_str] = entry
            summary["not_caught"] += 1
            continue

        # Get diff hunks for overlapping site
        try:
            diff_output = _sp.run(
                ["git", "-C", str(attempt_path), "diff", "-U0", base, "HEAD", "--", file_path],
                capture_output=True, text=True, timeout=15,
            ).stdout
        except Exception:
            entry = {"site": site, "test": test_name,
                     "result": "unavailable", "catches_site": False}
            results.append(entry)
            cache[cache_key_str] = entry
            summary["unavailable"] += 1
            continue

        # Parse hunks and find overlapping ones
        overlapping_hunks: list[str] = []
        current_hunk_lines: list[str] = []
        hunk_overlaps = False

        for line in diff_output.splitlines():
            if line.startswith("@@"):
                if current_hunk_lines and hunk_overlaps:
                    overlapping_hunks.extend(current_hunk_lines)
                current_hunk_lines = [line]
                # Parse hunk header for new-file line range
                m = re.search(r'\+(\d+)(?:,(\d+))?', line)
                if m:
                    hunk_start = int(m.group(1))
                    hunk_count = int(m.group(2)) if m.group(2) else 1
                    if hunk_count == 0:
                        # Pure deletion hunk: site line is the position or next
                        hunk_overlaps = (line_start <= hunk_start + 1 and line_end >= hunk_start)
                    else:
                        hunk_end = hunk_start + hunk_count - 1
                        hunk_overlaps = (line_start <= hunk_end and line_end >= hunk_start)
                else:
                    hunk_overlaps = False
            elif current_hunk_lines:
                current_hunk_lines.append(line)

        if current_hunk_lines and hunk_overlaps:
            overlapping_hunks.extend(current_hunk_lines)

        if not overlapping_hunks:
            entry = {"site": site, "test": test_name,
                     "result": "not_in_diff", "catches_site": False}
            results.append(entry)
            cache[cache_key_str] = entry
            summary["not_caught"] += 1
            continue

        # Build a reverse patch containing only overlapping hunks
        header_lines = []
        for line in diff_output.splitlines():
            if line.startswith("@@"):
                break
            header_lines.append(line)

        reverse_patch = "\n".join(header_lines + overlapping_hunks) + "\n"

        # Extract defined names from the added lines
        all_added = []
        for line in overlapping_hunks:
            if line.startswith("+") and not line.startswith("+++"):
                all_added.append(line[1:])
        defined_names = _extract_defined_names(all_added)

        # Apply reverse patch and run the test
        try:
            _sp.run(
                ["git", "-C", str(attempt_path), "apply", "--reverse", "--unidiff-zero"],
                input=reverse_patch, capture_output=True, text=True, timeout=10, check=True,
            )
        except Exception as exc:
            entry = {"site": site, "test": test_name,
                     "result": "unavailable", "catches_site": False,
                     "detail": f"patch apply failed: {exc}"}
            results.append(entry)
            cache[cache_key_str] = entry
            summary["unavailable"] += 1
            continue

        try:
            # Pick runner and build command
            test_path = test_name.split("::")[0] if "::" in test_name else test_name
            fn_name = test_name.split("::")[-1] if "::" in test_name else ""

            if test_path.endswith(".py"):
                if fn_name:
                    cmd = f"python -m pytest -x -q {test_name}"
                else:
                    cmd = f"python -m pytest -x -q {test_path}"
            elif test_path.endswith(".ts") or test_path.endswith(".spec.ts"):
                if fn_name:
                    cmd = f"npx vitest run {test_path} -t {fn_name}"
                else:
                    cmd = f"npx vitest run {test_path}"
            else:
                cmd = f"python -m pytest -x -q {test_name}"

            home = Path(ctx.workdir).resolve() / worktree.CHECK_HOME
            env = worktree.check_environment(attempt_path, home)

            test_result = sandbox.run_command(
                cmd, workdir=attempt_path,
                timeout_s=min(per_pair_timeout, total_timeout - (_time.monotonic() - start_time)),
                env=env, home=home, exact_env=True,
            )

            test_output = "\n".join(
                (test_result.stdout or "", test_result.stderr or "")
            ).strip()
            rc = test_result.returncode

            # Classify result
            if getattr(test_result, "timed_out", False):
                result = "unavailable"
            elif rc is not None and rc < 0:
                result = "unavailable"
            elif rc in (4, 5) or "no tests ran" in test_output.lower():
                # pytest exit 4 = no tests collected, 5 = no tests ran
                result = "unavailable"
            else:
                coll_files, has_unlocatable = _parse_collection_errors(test_output)
                test_path_for_coll = test_name.split("::")[0] if "::" in test_name else test_name
                has_collection = (test_path_for_coll in coll_files
                                  or any(test_path_for_coll.startswith(e) for e in coll_files)
                                  or has_unlocatable)
                if has_collection:
                    result = "collection_error"
                elif rc != 0:
                    # Test failed — check if it's import-only
                    if _is_import_only_failure(test_output, defined_names):
                        result = "import_only"
                    else:
                        result = "fail"  # genuine assertion failure = site is caught
                else:
                    result = "pass"  # test still passes = site NOT caught

        except Exception:
            result = "unavailable"
        finally:
            # Restore the worktree
            try:
                _sp.run(
                    ["git", "-C", str(attempt_path), "checkout", "HEAD", "--", file_path],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass

        catches = result == "fail"
        entry = {"site": site, "test": test_name,
                 "result": result, "catches_site": catches}
        results.append(entry)
        cache[cache_key_str] = entry

        if catches:
            summary["caught"] += 1
        elif result == "collection_error":
            summary["collection_error"] += 1
        elif result == "unavailable":
            summary["unavailable"] += 1
        else:
            summary["not_caught"] += 1

    # Write cache
    try:
        cache_file.write_text(json.dumps(cache, indent=2) + "\n")
    except OSError:
        pass

    return {"pairs": results, "summary": summary}


# ── integration function ────────────────────────────────────────────────


def filter_check(
    ctx: Any, result: Any, command: str, timeout: float,
    guardedness: dict | None = None,
) -> FilteredCheckResult:
    """The integration point: run the base check, compare, reuse guardedness.

    ``guardedness`` is passed in (correction 2: computed once per attempt,
    reused here rather than recomputed).
    """
    found = worktree.recorded(ctx.workdir)
    if found is None:
        return FilteredCheckResult(
            all_excused=False, inherited=set(), new_failures=set(),
            new_failure_detail="", inherited_detail="",
            base_environment_failure=None, guardedness=guardedness,
        )

    # Run or load the base check (cached per run)
    base_result = run_base_check(ctx, command, timeout)

    if base_result.environment_failure:
        return FilteredCheckResult(
            all_excused=False, inherited=set(), new_failures=set(),
            new_failure_detail="", inherited_detail="",
            base_environment_failure=base_result.environment_failure,
            guardedness=guardedness,
            base_failures=None,
            base_complete=False,
            base_crashed=getattr(base_result, '_crashed', False),
        )

    # Parse the attempt's failures
    attempt_output = "\n".join(
        (result.stdout or "", result.stderr or "")
    ).strip()
    attempt_failures = parse_test_failures(attempt_output)

    # Get changed files and texts for the three-condition rule
    attempt_path = Path(found.path)
    changed_files = _all_changed_files(attempt_path, found.base)
    brief_text = str(ctx.facts.get("brief") or "")
    design_text = ""
    if ctx.exists("design.md"):
        design_text = ctx.read_text("design.md")

    # Compare
    comparison = compare_failures(
        base_result.failures, attempt_failures, changed_files,
        brief_text, design_text,
    )
    blocker = excusal_blocker(command, attempt_output, attempt_failures) if comparison.all_excused else None
    if blocker:
        comparison = ComparisonResult(inherited=set(), new_failures=set(), all_excused=False,
                                      base_ok=comparison.base_ok)

    # Build detail strings
    inherited_detail = f"no failure was excused: {blocker}" if blocker else ""
    if comparison.inherited:
        inherited_detail = (
            "inherited failures from the base (not the attempt's): "
            + ", ".join(sorted(comparison.inherited))
        )

    new_failure_detail = attempt_output
    if comparison.new_failures and comparison.inherited:
        # Show only new failures to repair, but mention inherited ones
        new_lines = [
            line for line in attempt_output.splitlines()
            if any(f in line for f in comparison.new_failures)
            or not any(f in line for f in comparison.inherited)
        ]
        new_failure_detail = "\n".join(new_lines) if new_lines else attempt_output

    # Populate base_failures and base_complete for R5 (broken guarantees).
    base_complete = getattr(base_result, '_base_complete', False)

    return FilteredCheckResult(
        all_excused=comparison.all_excused,
        inherited=comparison.inherited,
        new_failures=comparison.new_failures,
        new_failure_detail=new_failure_detail,
        inherited_detail=inherited_detail,
        base_environment_failure=None,
        guardedness=guardedness,
        base_failures=base_result.failures,
        base_complete=base_complete,
    )
