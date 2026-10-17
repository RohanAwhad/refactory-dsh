"""fix_pack/make_pass, a mode written on `repo_change`'s steps, end to end.

It is the example the mode-author docs point at, so what it shows is tested: a fix
lands as commits on the run's own branch, a failed check is tried again with what it
said, an emission materialise cannot apply goes back to emit, the attempts knob bounds
the tries, and the person's checkout is left as it is. A fake model answers; the
repository, the worktree and the check are real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from refactory_dsh import nn, registry, repo_change, worktree
from refactory_dsh.paths import REPO_ROOT
from tests_support import FakeModel, git, git_repo, render, stub

CHECK = 'python -c "from app import answer; assert answer() == 42, answer()"'


@pytest.fixture
def repo(tmp_path) -> Path:
    """A checkout whose check fails: `answer()` is off by one."""
    root = git_repo(tmp_path / "repo")
    (root / "app.py").write_text("def answer():\n    return 41\n")
    git(root, "commit", "-qam", "off by one")
    return root


def _edit(name: str, search: str, replacement: str) -> str:
    return (f"===EDIT: {name}===\n{repo_change.SEARCH}\n{search}{repo_change.DIVIDER}\n"
            f"{replacement}{repo_change.REPLACE}\n")


def _run(workdir: Path, *emits: str, **knobs):
    mode = registry.resolve(REPO_ROOT, "fix_pack/make_pass")[1]()
    state = mode.state_dict()
    task = {"check": CHECK}
    for name, value in knobs.items():
        if name == "target":
            task["target"] = value
        else:
            state["knobs"][name]["value"] = value
    mode.load_state_dict(state)
    model = FakeModel(list(emits))
    return mode.run(workdir, model=model, task=task), model


def test_the_example_is_a_valid_unscored_mode_on_the_steps():
    mode = registry.resolve(REPO_ROOT, "fix_pack/make_pass")[1]()
    assert nn.validate_state(mode.state_dict(), invariants=mode.invariants(), resolve_calls=True) == []
    assert not mode.is_scored() and mode.unscored_reason
    assert set(repo_change.knob_names()) <= set(mode.declare())
    assert "deliver" not in mode.graph().nodes, "nothing approves a pull request, so none is opened"


def test_a_fix_lands_on_the_runs_branch_and_the_checkout_is_left_alone(repo, tmp_path):
    head = git(repo, "rev-parse", "HEAD")
    result, model = _run(tmp_path / "run", _edit("app.py", "    return 41\n", "    return 43\n"),
                         _edit("app.py", "    return 43\n", "    return 42\n"), target=str(repo))
    assert result.exit_ok, result.error
    payload = json.loads((tmp_path / "run" / "result.json").read_text())
    assert (payload["target"], payload["base"], payload["attempts"]) == (str(repo.resolve()), head, 2)
    assert git(repo, "show", f"{payload['branch']}:app.py") == "def answer():\n    return 42"
    assert git(repo, "rev-list", "--count", f"{head}..{payload['branch']}") == "2"
    assert (git(repo, "rev-parse", "HEAD"), (repo / "app.py").read_text()) == (head, "def answer():\n    return 41\n")
    assert "AssertionError: 43" in model.prompts[1], "the retry is told what the check said"


def test_the_lockfile_an_install_writes_is_part_of_the_fix(repo, tmp_path, monkeypatch):
    """The check's install amends the attempt, so the branch holds what the check ran."""
    stub(tmp_path / "bin", "npm", "echo '{\"lockfileVersion\": 3}' > package-lock.json")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    fix = render({"package.json": "{}\n"}) + _edit("app.py", "    return 41\n", "    return 42\n")
    result, _ = _run(tmp_path / "run", fix, target=str(repo))
    assert result.exit_ok, result.error
    payload = json.loads((tmp_path / "run" / "result.json").read_text())
    assert "package-lock.json" in git(repo, "ls-tree", "--name-only", payload["branch"]).splitlines()
    assert payload["head"] == result.facts["commit"]

def test_an_emission_that_cannot_be_applied_goes_back_to_emit(repo, tmp_path):
    result, model = _run(tmp_path / "run", _edit("app.py", "    return 40\n", "    return 42\n"),
                         _edit("app.py", "    return 41\n", "    return 42\n"), target=str(repo))
    assert result.exit_ok, result.error
    assert "materialise could not apply emit.txt" in model.prompts[1]


def test_the_attempts_knob_bounds_the_tries(repo, tmp_path):
    result, _ = _run(tmp_path / "run", _edit("app.py", "    return 41\n", "    return 43\n"),
                     target=str(repo), attempts=1)
    assert not result.exit_ok
    assert "the check still fails after 1 attempt(s)" in str(result.error)


def test_a_name_that_is_not_one_directory_is_refused_before_anything_is_made(repo, tmp_path):
    """`repo_change.locate` checks the name itself: a new project and an artifact are named by it."""
    result, _ = _run(tmp_path / "run", target=str(repo), name="../escape")
    assert not result.exit_ok
    assert "artifact name must be one directory name" in str(result.error)
    assert not worktree.root().exists()


def test_with_its_worktree_off_it_writes_the_artifact_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path / "project"))
    result, _ = _run(tmp_path / "run", render({"app.py": "def answer():\n    return 42\n"}),
                     name="answer", worktree=False)
    assert result.exit_ok, result.error
    assert result.facts["artifact"] == str((tmp_path / "project" / repo_change.ARTIFACTS / "answer").resolve())
    assert not worktree.root().exists()
