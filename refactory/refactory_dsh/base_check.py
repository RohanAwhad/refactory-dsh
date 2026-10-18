"""Base-comparison gate: decide which check failures the attempt caused.

Before a failed check spends a code attempt, compare with the unchanged base in the
same environment. A failure the base has too is inherited, not the attempt's, only if
all three conditions hold:

(a) the same test identifier fails on both base and attempt;
(b) its test file is not one the attempt changes;
(c) its test id or leaf name appears in neither the brief text nor design.md.

When the check cannot identify individual tests, no failure is excused (conservative
fallback). When the base environment cannot be prepared, the run stops as unavailable.
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
from refactory_dsh.worktree import _json as _read_json, NO_HOOKS

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
    for m in _VITEST_FAIL.finditer(output):
        candidate = m.group(1).strip()
        if " > " in candidate:
            found.add(candidate)
    return found


def _parse_collection_errors(output: str) -> set[str]:
    """Extract test files that had collection or import errors."""
    files: set[str] = set()
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
    return files


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
    """Extract the test file path from a test identifier."""
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
    _, problems = worktree.ensure_environment(base_found, ctx.workdir, budget)
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
        return BaseCheckResult(
            ok=data["ok"],
            failures=set(data.get("failures", [])),
            output=data.get("output", ""),
            exit_code=data.get("exit_code", 1),
            environment_failure=data.get("environment_failure"),
        )
    return None


def _write_cache(run: Path, key: str, result: BaseCheckResult) -> None:
    cache_dir = run / CACHE_FILE.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "cache_key": key,
        "ok": result.ok,
        "failures": sorted(result.failures),
        "output": result.output[-10000:],
        "exit_code": result.exit_code,
        "environment_failure": result.environment_failure,
    }
    (run / CACHE_FILE).write_text(json.dumps(data, indent=2) + "\n")


def run_base_check(ctx: Any, command: str, timeout: float) -> BaseCheckResult:
    """Run the check on the unchanged base in a separate worktree, caching the result."""
    found = worktree.recorded(ctx.workdir)
    if found is None:
        return BaseCheckResult(ok=True, failures=set(), output="", exit_code=0)

    setup = str(ctx.facts.get("setup") or "").strip()
    generate = str(ctx.facts.get("generate") or "").strip()
    key = cache_key(found.base, setup, generate, command)

    cached = _read_cache(ctx.workdir, key)
    if cached is not None:
        return cached

    # Create and prepare the base worktree on demand
    try:
        base_path, base_found = _ensure_base_worktree(ctx)
    except ValueError as exc:
        result = BaseCheckResult(
            ok=False, failures=set(), output="", exit_code=-1,
            environment_failure=str(exc),
        )
        _write_cache(ctx.workdir, key, result)
        return result

    home = Path(ctx.workdir).resolve() / worktree.CHECK_HOME
    home.mkdir(parents=True, exist_ok=True)
    budget = getattr(ctx, "budget", None)
    env = worktree.check_environment(base_path, home)

    # Generate, then setup, exactly as the attempt's check prepares its worktree
    # (repo_change.prepare_commands), without amending anything on the base.
    from refactory_dsh.repo_change import prepare_commands

    _, problems = prepare_commands(ctx, base_found, env, home, timeout, budget, amend=False)
    if problems:
        detail = "on the base: " + "\n".join(problems)
        result = BaseCheckResult(
            ok=False, failures=set(), output=detail, exit_code=-1,
            environment_failure=detail,
        )
        _write_cache(ctx.workdir, key, result)
        return result

    # Run the check command on the base
    check_result = sandbox.run_command(
        command, workdir=base_path, timeout_s=timeout,
        budget=budget, env=env, home=home, exact_env=True,
    )
    output = "\n".join(
        (check_result.stdout or "", check_result.stderr or "")
    ).strip()
    failures = parse_test_failures(output)

    result = BaseCheckResult(
        ok=check_result.ok, failures=failures,
        output=output, exit_code=check_result.returncode or 0,
    )
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
    home = Path(ctx.workdir).resolve() / worktree.CHECK_HOME
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
    collection_errors = _parse_collection_errors(test_output)

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

    # Build detail strings
    inherited_detail = ""
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

    return FilteredCheckResult(
        all_excused=comparison.all_excused,
        inherited=comparison.inherited,
        new_failures=comparison.new_failures,
        new_failure_detail=new_failure_detail,
        inherited_detail=inherited_detail,
        base_environment_failure=None,
        guardedness=guardedness,
    )
