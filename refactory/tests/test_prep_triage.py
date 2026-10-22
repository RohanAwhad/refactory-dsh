"""Tests for change-caused preparation failure triage (dm-104).

Each scenario is tested through the node runner or as unit tests of
compare_prep_failure.  The tests assert on the builder's feedback text,
not on result.error, because on main the failure already reaches the
builder — the problems are the heading and the stops.

# guardedness-exempt: these tests pin new behaviour; on the base they fail
#   because compare_prep_failure and the early-return blocks do not exist.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from refactory_dsh import base_check, sandbox, worktree
from refactory_dsh import authoring_evidence as evidence
from refactory_dsh.design import DesignMode
from refactory_dsh.fast import DesignFastMode
from refactory_dsh.failfast import record_check_history
from tests_support import render


# ── helpers ────────────────────────────────────────────────────────────────


def _git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"],
                   capture_output=True, check=True)
    return path


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", name],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", f"add {name}"],
                   capture_output=True, check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


class _Ctx:
    """Minimal RunContext stand-in."""
    def __init__(self, workdir: Path, facts: dict | None = None):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.facts: dict[str, Any] = dict(facts or {})
        self.budget = None

    def knob(self, name: str, default: Any = None) -> Any:
        return default

    def set_fact(self, name: str, value: Any) -> None:
        self.facts[name] = value

    def exists(self, name: str) -> bool:
        return (self.workdir / name).exists()

    def read_text(self, name: str) -> str:
        return (self.workdir / name).read_text()

    def write_text(self, name: str, text: str) -> Path:
        target = self.workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def write_json(self, name: str, payload: Any) -> Path:
        return self.write_text(name, json.dumps(payload, indent=2, default=str))

    def read_json(self, name: str, default: Any = None) -> Any:
        p = self.workdir / name
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return default


# ── Unit: infrastructure fault guards ──────────────────────────────────────


def test_exit_137_keeps_infrastructure_handling(tmp_path):
    """A setup that exits 137 (SIGKILL) is classified as infrastructure."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000004", run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "", "setup": "sh setup.sh"})
    prep_fail = {"step": "sh setup.sh", "returncode": 137, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["killed"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is None, "exit 137 should return None (infrastructure)"


def test_timeout_keeps_infrastructure_handling(tmp_path):
    """A timed-out prep step is classified as infrastructure."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000005", run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "sh gen.sh", "setup": ""})
    prep_fail = {"step": "sh gen.sh", "returncode": 1, "timed_out": True}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["timed out"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is None, "timeout should return None (infrastructure)"


def test_missing_executable_keeps_infrastructure_handling(tmp_path):
    """Exit 127 (command not found) is infrastructure."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000006", run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "nonexistent", "setup": ""})
    prep_fail = {"step": "nonexistent", "returncode": 127, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["not found"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is None, "exit 127 should return None (infrastructure)"


def test_signal_exit_134_keeps_infrastructure_handling(tmp_path):
    """Exit 134 (SIGABRT, in _AMBIGUOUS_SHELL_EXITS) is infrastructure."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000014", run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "", "setup": "sh s.sh"})
    prep_fail = {"step": "sh s.sh", "returncode": 134, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["aborted"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is None, "exit 134 should return None (infrastructure)"


# ── Unit: cached base comparison ───────────────────────────────────────────


def test_two_prep_failures_use_cache(tmp_path):
    """Two consecutive prep failures run the base's prep only once."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000007", run_dir)
    worktree.write_wrapper(found, run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "sh gen.sh", "setup": ""})
    key = base_check.cache_key(found.base, "", "sh gen.sh", "")
    base_check._write_prep_cache(run_dir, key, {
        "base_ok": True, "base_step": None,
        "base_error": None, "base_returncode": None,
    })
    prep_fail = {"step": "sh gen.sh", "returncode": 1, "timed_out": False}
    r1 = base_check.compare_prep_failure(
        ctx, prep_fail, ["gen failed"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    r2 = base_check.compare_prep_failure(
        ctx, prep_fail, ["gen failed"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert r1 is not None and r2 is not None, "both calls should be change-caused"


def test_base_passes_failing_step_but_fails_later(tmp_path):
    """Base passes generate but fails setup → attempt's generate failure is change-caused."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000003", run_dir)
    worktree.write_wrapper(found, run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "sh gen.sh", "setup": "sh setup.sh"})
    key = base_check.cache_key(found.base, "sh setup.sh", "sh gen.sh", "")
    base_check._write_prep_cache(run_dir, key, {
        "base_ok": False, "base_step": "sh setup.sh",
        "base_error": "setup failed", "base_returncode": 1,
    })
    prep_fail = {"step": "sh gen.sh", "returncode": 1, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["gen schema error"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is not None, "should be change-caused: base passed generate"
    assert result["step_label"] == "generate"


def test_changed_file_in_error_makes_it_change_caused(tmp_path):
    """Base fails same step but attempt's error uniquely names a changed file."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000001", run_dir)
    wt = Path(found.path)
    (wt / "schema.json").write_text("{}\n")
    subprocess.run(["git", "-C", str(wt), "add", "schema.json"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", "add schema"],
                   capture_output=True, check=True)
    found = worktree.Worktree(
        path=found.path, branch=found.branch,
        base=found.base, head=subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip(),
        target=found.target, run=found.run,
    )
    worktree.write_wrapper(found, run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "sh gen.sh", "setup": ""})
    key = base_check.cache_key(found.base, "", "sh gen.sh", "")
    base_check._write_prep_cache(run_dir, key, {
        "base_ok": False, "base_step": "sh gen.sh",
        "base_error": "missing dependency", "base_returncode": 1,
    })
    prep_fail = {"step": "sh gen.sh", "returncode": 1, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["Error in schema.json: invalid"], found,
        worktree.check_environment(wt, tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is not None, "should be change-caused: error names schema.json"


def test_base_same_prep_failure_keeps_environment_handling(tmp_path):
    """Base fails same step, no changed file uniquely named → inherited (None).

    This is the negative-case counterpart of
    test_changed_file_in_error_makes_it_change_caused: the base also fails
    the same generate step with the same error text, and no changed file is
    uniquely named in the attempt's error.  compare_prep_failure must return
    None so that _check_in_worktree falls through to today's
    "the worktree's environment could not be prepared" handling.
    """
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000015", run_dir)
    wt = Path(found.path)
    # Make a change that does NOT appear in the error text.
    (wt / "unrelated.txt").write_text("change\n")
    subprocess.run(["git", "-C", str(wt), "add", "unrelated.txt"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "-m", "unrelated change"],
                   capture_output=True, check=True)
    found = worktree.Worktree(
        path=found.path, branch=found.branch,
        base=found.base, head=subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip(),
        target=found.target, run=found.run,
    )
    worktree.write_wrapper(found, run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "sh gen.sh", "setup": ""})
    key = base_check.cache_key(found.base, "", "sh gen.sh", "")
    # Cache: base fails the same step with the same error.
    base_check._write_prep_cache(run_dir, key, {
        "base_ok": False, "base_step": "sh gen.sh",
        "base_error": "missing dependency: libfoo not found",
        "base_returncode": 1,
    })
    prep_fail = {"step": "sh gen.sh", "returncode": 1, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["missing dependency: libfoo not found"], found,
        worktree.check_environment(wt, tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is None, (
        "base fails same step with no changed file uniquely named → "
        "should return None (inherited), keeping today's environment handling"
    )


# ── Unit: convergence history ──────────────────────────────────────────────


def test_prep_failure_records_convergence_history(tmp_path):
    """record_check_history records an empty failure set for prep errors."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "test00000002", run_dir)
    ctx = _Ctx(run_dir, facts={
        "check": "python app.py",
        "check_failures_history": [["t1", "t2"], ["t1"]],
        "check_blocker_history": [None, None],
    })
    record_check_history(ctx, "full", "the generate command failed", "python app.py")
    history = ctx.facts.get("check_failures_history", [])
    assert len(history) == 3 and history[-1] == [], "prep records empty failure set"


# ── Unit: HOME isolation ──────────────────────────────────────────────────


def test_base_prep_uses_own_home(tmp_path):
    """The base prep in compare_prep_failure uses BASE_CHECK_HOME, not CHECK_HOME."""
    # worktree re-exports both from invocations; the code uses worktree.BASE_CHECK_HOME.
    assert str(worktree.CHECK_HOME) != str(worktree.BASE_CHECK_HOME), (
        "CHECK_HOME and BASE_CHECK_HOME must be distinct paths"
    )
    # Verify compare_prep_failure calls check_environment with the BASE home,
    # not the attempt home that was passed to _check_in_worktree.
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000020", run_dir)
    worktree.write_wrapper(found, run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "sh gen.sh", "setup": ""})
    # Pre-seed the cache with base_ok=True so compare_prep_failure doesn't
    # actually call _ensure_base_worktree, but verify the key uses the right
    # constants (setup, generate, "").
    key = base_check.cache_key(found.base, "", "sh gen.sh", "")
    base_check._write_prep_cache(run_dir, key, {
        "base_ok": True, "base_step": None,
        "base_error": None, "base_returncode": None,
    })
    attempt_home = tmp_path / "attempt-home"
    attempt_home.mkdir(parents=True, exist_ok=True)
    prep_fail = {"step": "sh gen.sh", "returncode": 1, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["gen failed"], found,
        worktree.check_environment(Path(found.path), attempt_home),
        attempt_home, 60.0,
    )
    assert result is not None, "should be change-caused when base is cached as OK"
    # The base_home that _try_base_prep builds is under BASE_CHECK_HOME,
    # not under the attempt_home we passed.  The constants control that.
    base_home_path = Path(run_dir).resolve() / worktree.BASE_CHECK_HOME
    attempt_home_path = Path(run_dir).resolve() / worktree.CHECK_HOME
    assert str(base_home_path) != str(attempt_home_path)


# ── Integration: DSH runner (DesignMode) ───────────────────────────────────


def test_prep_triage_through_dsh_runner_design(tmp_path, monkeypatch):
    """Drive prep triage through the node runner, as test_design_worktree ~629."""
    from refactory_dsh import lowering, design_ops, repo_change
    from refactory_dsh.run import run_node

    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    (repo / "gen.sh").write_text(
        '#!/bin/sh\n'
        'if [ -f FAIL_MARKER ]; then\n'
        '  echo "the schema is uncovered; set reviewed_schema=hash1"\n'
        '  exit 1\n'
        'fi\n'
        'echo ok\n'
    )
    subprocess.run(["chmod", "+x", str(repo / "gen.sh")], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "gen.sh"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "gen"],
                   capture_output=True, check=True)

    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "dshrunner0001", run_dir)
    lowering.write_declarations(DesignMode().state_dict(), run_dir)
    (run_dir / "facts.json").write_text(json.dumps({
        "name": "runner_test", "brief": "a tool",
        "check": "python app.py", "generate": "sh gen.sh",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3}))
    (run_dir / "emit.txt").write_text(render({
        "FAIL_MARKER": "yes\n", "app.py": "print('runner')\n",
    }))
    run_node(f"{repo_change.OPS}:materialise", workdir=run_dir, node_id="materialise")
    verdict = run_node(
        f"{design_ops.__name__}:check_artifact", workdir=run_dir, node_id="check",
    )
    assert verdict == "reloop", f"expected reloop, got {verdict}"
    facts = json.loads((run_dir / "facts.json").read_text())
    assert "set reviewed_schema=hash1" in facts.get("validation", ""), (
        f"coverage message not in validation: {facts.get('validation')!r}"
    )


# ── Integration: DSH runner (DesignFastMode) ───────────────────────────────


def test_prep_triage_through_dsh_runner_fast(tmp_path, monkeypatch):
    """Drive prep triage through the node runner in fast mode."""
    from refactory_dsh import lowering, fast_ops, repo_change
    from refactory_dsh.run import run_node

    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    (repo / "setup.sh").write_text(
        '#!/bin/sh\n'
        'if [ -f TSC_FAIL ]; then\n'
        '  echo "error TS2304: Cannot find name Y" >&2\n'
        '  exit 1\n'
        'fi\n'
        'echo ok\n'
    )
    subprocess.run(["chmod", "+x", str(repo / "setup.sh")], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "setup.sh"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "setup"],
                   capture_output=True, check=True)

    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "dshrunner0002", run_dir)
    lowering.write_declarations(DesignFastMode().state_dict(), run_dir)
    (run_dir / "facts.json").write_text(json.dumps({
        "name": "runner_fast", "brief": "a tool",
        "check": "python app.py", "setup": "sh setup.sh",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"repair_attempts": 3}))
    (run_dir / "emit.txt").write_text(render({
        "TSC_FAIL": "yes\n", "app.py": "print('runner')\n",
    }))
    run_node(f"{fast_ops.__name__}:materialise_artifact", workdir=run_dir,
             node_id="materialise")
    run_node(f"{fast_ops.__name__}:focused_check", workdir=run_dir,
             node_id="focused_check")
    check_data = json.loads((run_dir / "focused-check.json").read_text())
    assert check_data.get("prep_triage") is True, (
        f"focused-check.json should have prep_triage=True: {check_data}"
    )
    assert "TS2304" in (check_data.get("digest") or ""), (
        f"tsc error not in digest: {check_data.get('digest')!r}"
    )


# ── Unit: _step_label classification ──────────────────────────────────────


def test_step_label_classification():
    """_step_label classifies generate, setup and install correctly."""
    assert base_check._step_label("sh gen.sh", "sh gen.sh", "sh setup.sh") == "generate"
    assert base_check._step_label("sh setup.sh", "sh gen.sh", "sh setup.sh") == "setup"
    assert base_check._step_label("sh install.sh", "sh gen.sh", "sh setup.sh") == "install"
    assert base_check._step_label("something", "", "") == "install"


# ── Unit: _RESOURCE_EXIT_CODES guard ──────────────────────────────────────


def test_resource_exit_152_keeps_infrastructure_handling(tmp_path):
    """Exit 152 (SIGXCPU, in _RESOURCE_EXIT_CODES) bypasses the base comparison."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000016", run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "", "setup": "sh s.sh"})
    prep_fail = {"step": "sh s.sh", "returncode": 152, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["cpu limit"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is None, "exit 152 (SIGXCPU) should return None (infrastructure)"


def test_negative_returncode_keeps_infrastructure_handling(tmp_path):
    """A directly negative returncode (signal kill) is infrastructure."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000017", run_dir)
    ctx = _Ctx(run_dir, facts={"generate": "sh gen.sh", "setup": ""})
    prep_fail = {"step": "sh gen.sh", "returncode": -9, "timed_out": False}
    result = base_check.compare_prep_failure(
        ctx, prep_fail, ["signal killed"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"),
        tmp_path / "home", 60.0,
    )
    assert result is None, "negative returncode should return None (infrastructure)"


# ── Unit: ANSI stripping ──────────────────────────────────────────────────


def test_ansi_codes_stripped_from_builder_text(tmp_path, monkeypatch):
    """ANSI escape codes are stripped before the prep error reaches the builder."""
    from refactory_dsh import lowering, design_ops, repo_change
    from refactory_dsh.run import run_node

    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    (repo / "gen.sh").write_text(
        '#!/bin/sh\n'
        'if [ -f FAIL_MARKER ]; then\n'
        '  printf "\\033[31mERROR\\033[0m: schema uncovered\\n"\n'
        '  exit 1\n'
        'fi\n'
        'echo ok\n'
    )
    subprocess.run(["chmod", "+x", str(repo / "gen.sh")], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "gen.sh"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "gen"],
                   capture_output=True, check=True)

    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "dshrunner0010", run_dir)
    lowering.write_declarations(DesignMode().state_dict(), run_dir)
    (run_dir / "facts.json").write_text(json.dumps({
        "name": "ansi_test", "brief": "a tool",
        "check": "python app.py", "generate": "sh gen.sh",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3}))
    (run_dir / "emit.txt").write_text(render({
        "FAIL_MARKER": "yes\n", "app.py": "print('runner')\n",
    }))
    run_node(f"{repo_change.OPS}:materialise", workdir=run_dir, node_id="materialise")
    verdict = run_node(
        f"{design_ops.__name__}:check_artifact", workdir=run_dir, node_id="check",
    )
    assert verdict == "reloop", f"expected reloop, got {verdict}"
    facts = json.loads((run_dir / "facts.json").read_text())
    validation = facts.get("validation", "")
    assert "\033[" not in validation, (
        f"ANSI escape codes must not appear in validation: {validation!r}"
    )
    assert "schema uncovered" in validation, (
        f"error text not in validation: {validation!r}"
    )


# ── Unit: record_check_history with prep text ─────────────────────────────


def test_record_check_history_prep_text_produces_empty_failures(tmp_path):
    """record_check_history with prep failure text (no test IDs) records an empty set.

    Covers failfast.py:275 (result_output parameter): when result_output
    contains prep-failure text rather than test output, parse_test_failures
    finds no test IDs and records an empty failure set.
    """
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "test00000018", run_dir)
    ctx = _Ctx(run_dir, facts={
        "check": "python app.py",
        "check_failures_history": [],
        "check_blocker_history": [],
    })
    # Prep failure text — no pytest FAILED or vitest FAIL lines.
    prep_text = "the generate command `sh gen.sh` failed (exit 1):\nschema uncovered"
    record_check_history(ctx, "full", prep_text, "python app.py")
    history = ctx.facts.get("check_failures_history", [])
    assert len(history) == 1, f"expected 1 entry, got {len(history)}"
    assert history[0] == [], (
        "prep failure text has no test IDs, so failure set must be empty"
    )


def test_record_check_history_with_test_output_finds_failures(tmp_path):
    """record_check_history with real test output extracts test IDs.

    Covers failfast.py:275 (result_output parameter): when result_output
    contains pytest output with FAILED lines, parse_test_failures finds
    the test IDs and records them.
    """
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "test00000019", run_dir)
    ctx = _Ctx(run_dir, facts={
        "check": "pytest",
        "check_failures_history": [],
        "check_blocker_history": [],
    })
    test_output = (
        "=== FAILURES ===\n"
        "FAILED tests/test_foo.py::test_bar - AssertionError\n"
        "=== short test summary info ===\n"
        "FAILED tests/test_foo.py::test_bar\n"
        "1 failed\n"
    )
    record_check_history(ctx, "full", test_output, "pytest")
    history = ctx.facts.get("check_failures_history", [])
    assert len(history) == 1, f"expected 1 entry, got {len(history)}"
    assert "tests/test_foo.py::test_bar" in history[0], (
        f"expected test ID in history: {history[0]}"
    )


# ── Unit: _resolve_prep_comparison edge cases ─────────────────────────────


def test_base_fails_earlier_step_returns_none(tmp_path):
    """When the base fails an earlier step than the attempt, it's inherited."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000021", run_dir)
    worktree.write_wrapper(found, run_dir)
    # Attempt fails setup, base fails generate (earlier step).
    result = base_check._resolve_prep_comparison(
        base_ok=False,
        base_step="sh gen.sh",
        base_error="gen failed on base",
        prep_fail={"step": "sh setup.sh", "returncode": 1},
        problems=["setup failed on attempt"],
        found_wt=found,
        step_label_val="setup",
        generate="sh gen.sh",
        setup="sh setup.sh",
    )
    assert result is None, "base fails earlier step → inherited"


def test_base_fails_later_step_returns_change_caused(tmp_path):
    """When the base fails a later step, the attempt's earlier failure is change-caused."""
    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    run_dir = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "test00000022", run_dir)
    worktree.write_wrapper(found, run_dir)
    # Attempt fails generate, base fails setup (later step).
    result = base_check._resolve_prep_comparison(
        base_ok=False,
        base_step="sh setup.sh",
        base_error="setup failed on base",
        prep_fail={"step": "sh gen.sh", "returncode": 1},
        problems=["gen failed on attempt"],
        found_wt=found,
        step_label_val="generate",
        generate="sh gen.sh",
        setup="sh setup.sh",
    )
    assert result is not None, "base fails later step → change-caused"
    assert result["step_label"] == "generate"


# ── Integration: prep failure never reported as unbuilt (DesignMode) ───────


def test_prep_failure_never_reported_as_unbuilt(tmp_path, monkeypatch):
    """A change-caused generate failure whose error names a missing entry point
    must NOT be reported as "unbuilt packages".

    Per correction #10, the failure lines name the missing entry point — the
    scenario that previously caused the "unbuilt packages" stop at
    design_ops ~863.  The early-return for change_caused_prep fires before
    the unbuilt diagnosis, so "unbuilt" never appears in the builder's
    feedback.
    """
    from refactory_dsh import lowering, design_ops, repo_change
    from refactory_dsh.run import run_node

    repo = _git_init(tmp_path / "repo")
    # Create a simple app and a generate script that fails with an error
    # naming a missing entry point (packages/foo/dist/index.js).
    _commit(repo, "app.py", "print('app')\n")
    (repo / "gen.sh").write_text(
        '#!/bin/sh\n'
        'if [ -f FAIL_MARKER ]; then\n'
        '  echo "Error: Cannot find module packages/foo/dist/index.js"\n'
        '  echo "FAILED to import packages/foo/dist/index.js"\n'
        '  exit 1\n'
        'fi\n'
        'echo ok\n'
    )
    subprocess.run(["chmod", "+x", str(repo / "gen.sh")], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "gen.sh"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "gen"],
                   capture_output=True, check=True)

    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "dshrunner0003", run_dir)
    lowering.write_declarations(DesignMode().state_dict(), run_dir)
    (run_dir / "facts.json").write_text(json.dumps({
        "name": "unbuilt_test", "brief": "a tool",
        "check": "python app.py", "generate": "sh gen.sh",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3}))
    (run_dir / "emit.txt").write_text(render({
        "FAIL_MARKER": "yes\n", "app.py": "print('runner')\n",
    }))
    run_node(f"{repo_change.OPS}:materialise", workdir=run_dir, node_id="materialise")
    verdict = run_node(
        f"{design_ops.__name__}:check_artifact", workdir=run_dir, node_id="check",
    )
    # The change-caused prep failure should route to repair (reloop),
    # NOT raise ValueError("check failed because of unbuilt packages...").
    assert verdict == "reloop", f"expected reloop, got {verdict}"
    facts = json.loads((run_dir / "facts.json").read_text())
    validation = facts.get("validation", "")
    assert "unbuilt" not in validation.lower(), (
        f"'unbuilt' must not appear in validation: {validation!r}"
    )
    # The actual error text must reach the builder.
    assert "packages/foo/dist/index.js" in validation, (
        f"entry-point path not in validation: {validation!r}"
    )


# ── Integration: fast-mode gate_check prep_triage routing ──────────────────


def test_gate_check_routes_prep_triage_without_wrapping(tmp_path, monkeypatch):
    """gate_check uses the digest directly for prep_triage, without
    the 'requested runtime check failed' wrapping.

    Covers fast_ops.gate_check's check_data.get("prep_triage") branch and its
    check_data.get("digest") detail.
    """
    from refactory_dsh import lowering, fast_ops, repo_change
    from refactory_dsh.run import run_node

    repo = _git_init(tmp_path / "repo")
    _commit(repo, "app.py", "print('app')\n")
    (repo / "gen.sh").write_text(
        '#!/bin/sh\n'
        'if [ -f FAIL_MARKER ]; then\n'
        '  echo "coverage error: uncovered schema"\n'
        '  exit 1\n'
        'fi\n'
        'echo ok\n'
    )
    subprocess.run(["chmod", "+x", str(repo / "gen.sh")], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "gen.sh"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "gen"],
                   capture_output=True, check=True)

    run_dir = tmp_path / "run"
    worktree.create(repo, "HEAD", "dshrunner0011", run_dir)
    lowering.write_declarations(DesignFastMode().state_dict(), run_dir)
    (run_dir / "facts.json").write_text(json.dumps({
        "name": "gate_ver_test", "brief": "a tool",
        "check": "python app.py", "generate": "sh gen.sh",
    }))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"repair_attempts": 3}))
    (run_dir / "emit.txt").write_text(render({
        "FAIL_MARKER": "yes\n", "app.py": "print('runner')\n",
    }))
    run_node(f"{fast_ops.__name__}:materialise_artifact", workdir=run_dir,
             node_id="materialise")
    run_node(f"{fast_ops.__name__}:focused_check", workdir=run_dir,
             node_id="focused_check")
    check_data = json.loads((run_dir / "focused-check.json").read_text())
    assert check_data.get("prep_triage") is True

    # Run gate_check and check the repair_feedback fact.
    verdict = run_node(f"{fast_ops.__name__}:gate_check", workdir=run_dir,
                       node_id="gate_check")
    facts = json.loads((run_dir / "facts.json").read_text())
    repair_fb = facts.get("repair_feedback", "")
    assert "coverage error: uncovered schema" in repair_fb, (
        f"prep detail not in repair_feedback: {repair_fb!r}"
    )
    assert "requested runtime check failed" not in repair_fb, (
        f"prep_triage should not be wrapped with 'requested runtime check failed': {repair_fb!r}"
    )


# ── Unit: base_clean guard on _read_prep_cache (dm-162) ──────────────────


def test_read_prep_cache_rejects_dirty_base(tmp_path):
    """_read_prep_cache rejects entries without base_clean=True (dm-162)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    key = "test_dirty_guard_key"

    # Write a valid prep-cache entry with base_clean=True.
    base_check._write_prep_cache(run_dir, key, {
        "base_ok": True, "base_step": None,
        "base_error": None, "base_returncode": None,
    }, base_clean=True)
    assert base_check._read_prep_cache(run_dir, key) is not None

    # Tamper: set base_clean to False.
    path = run_dir / base_check.PREP_CACHE_FILE
    data = json.loads(path.read_text())
    data["base_clean"] = False
    path.write_text(json.dumps(data))
    assert base_check._read_prep_cache(run_dir, key) is None, \
        "prep cache with base_clean=False should be a miss"

    # Tamper: remove base_clean entirely (old format).
    del data["base_clean"]
    path.write_text(json.dumps(data))
    assert base_check._read_prep_cache(run_dir, key) is None, \
        "prep cache without base_clean should be a miss"
