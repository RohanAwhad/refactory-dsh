"""Install-step base failures in compare_prep_failure (dm-104): an install the base fails too keeps today's handling, and a crashed base install is never cached."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from refactory_dsh import base_check, worktree
from tests_support import git_repo

from test_prep_triage import _Ctx


def _broken_install_repo(root: Path) -> Path:
    repo = git_repo(root)
    (repo / "pyproject.toml").write_text("[project\nname = 'broken'\n")
    (repo / "tool.py").write_text("print('ok')\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "broken"], check=True,
                   capture_output=True)
    return repo


@pytest.mark.parametrize("generate,setup", [("", ""), ("python gen.py", ""), ("", "python build.py"),
                                            ("python gen.py", "python build.py")])
def test_install_failing_on_the_base_too_keeps_todays_handling(tmp_path, generate, setup):
    """The base's install fails identically (_ensure_base_worktree raises): inherited, not the change's."""
    repo = _broken_install_repo(tmp_path / "repo")
    run = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "b471reg00001", run)
    ctx = _Ctx(run, facts={"generate": generate, "setup": setup})
    _, problems, prep_fail = worktree.ensure_environment(found, run)
    assert problems and prep_fail and prep_fail["returncode"] not in (126, 127), (problems, prep_fail)
    out = base_check.compare_prep_failure(
        ctx, prep_fail, problems, found,
        worktree.check_environment(Path(found.path), tmp_path / "home"), tmp_path / "home", 60.0)
    assert out is None, f"base fails the install too, yet triaged as the change's: {out}"


def test_a_crashed_base_install_is_never_cached(tmp_path, monkeypatch):
    """A signal-killed base install must not be cached (crashes are never cached)."""
    repo = git_repo(tmp_path / "repo")
    (repo / "tool.py").write_text("print('ok')\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "t"], check=True, capture_output=True)
    run = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "b471reg00002", run)
    ctx = _Ctx(run, facts={"generate": "python gen.py", "setup": "python build.py"})

    def crashed(_ctx):
        raise ValueError("environment preparation failed on the base:\n"
                         "`uv sync --all-groups` killed by SIGKILL: killed by SIGKILL")
    monkeypatch.setattr(base_check, "_ensure_base_worktree", crashed)
    prep_fail = {"step": "python gen.py", "returncode": 1, "timed_out": False}
    base_check.compare_prep_failure(
        ctx, prep_fail, ["the generate command `python gen.py` failed: boom"], found,
        worktree.check_environment(Path(found.path), tmp_path / "home"), tmp_path / "home", 60.0)
    cache = run / base_check.PREP_CACHE_FILE
    assert not cache.exists(), f"a crashed base install was cached: {cache.read_text()}"
