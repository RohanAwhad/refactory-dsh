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
    """CHECK_HOME and BASE_CHECK_HOME are distinct paths."""
    from refactory_dsh.invocations import BASE_CHECK_HOME, CHECK_HOME
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    assert str(Path(run_dir).resolve() / CHECK_HOME) != str(
        Path(run_dir).resolve() / BASE_CHECK_HOME
    )


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
