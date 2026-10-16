"""A design run works in its own worktree of its target, end to end (#351).

One test per path a person takes: a new idea with no repository, a change to a
repository they have checked out, the same idea again, a base other than HEAD, an
attempt that fails its check and is redone, and a run with its worktree turned off. A
fake model answers; the git repositories, the worktrees, the check and the registry
are real.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

import pytest

from refactory_dsh import cli, design_ops, interactive, nn, projects, registry, worktree
from refactory_dsh.design import DesignMode
from refactory_dsh.fast import DesignFastMode
from refactory_dsh.paths import REPO_ROOT, projects_dir
from tests_support import RESEARCH_ANSWER, REVIEW_ANSWERS, FakeModel, git, git_repo, render, stub

NAME = "worktree_tool"


@pytest.fixture
def repo(tmp_path) -> Path:
    return git_repo(tmp_path / "repo")


def _answers(*emits: str) -> list[str]:
    """Every answer a design run needs, in the order its graph asks."""
    return [RESEARCH_ANSWER, "perspectives", "one plan", "the design document", *emits, *REVIEW_ANSWERS]


def _mode(**knobs) -> DesignMode:
    """Design mode with some of its knobs set, as a person sets them for one run."""
    mode = DesignMode()
    state = mode.state_dict()
    for name, value in knobs.items():
        state["knobs"][name]["value"] = value
    mode.load_state_dict(state)
    return mode


def _design(workdir: Path, model: FakeModel, mode: DesignMode | None = None, **task: str):
    return (mode or DesignMode()).run(
        workdir, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": "a tool", "check": "python tool.py", **task},
    )


def _report(workdir: Path) -> dict:
    return json.loads((workdir / "design.json").read_text())


# ── the paths a person takes ───────────────────────────────────────────────
def test_a_run_with_no_target_starts_a_new_project_and_commits_its_work_on_a_branch(tmp_path):
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": "print('built')\n"}))))
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    project = (projects_dir() / NAME).resolve()
    assert payload["target"] == str(project)
    assert payload["base"] == git(project, "rev-parse", "main")
    assert git(project, "rev-list", "--count", "main") == "1", "main keeps only its empty first commit"
    assert git(project, "rev-list", "--count", f"{payload['base']}..{payload['branch']}") == "1"
    assert git(project, "ls-tree", "--name-only", payload["branch"]) == "tool.py"
    assert "tool.py" in payload["diff_stat"]
    [entry] = projects.load()["projects"]
    assert (entry["kind"], entry["runs"][-1]["branch"], entry["runs"][-1]["status"]) == (
        "new", payload["branch"], "succeeded")


def test_the_same_idea_again_is_a_new_branch_of_the_same_project_from_main(tmp_path):
    first = _design(tmp_path / "one", FakeModel(_answers(render({"tool.py": "print(1)\n"}))))
    second = _design(tmp_path / "two", FakeModel(_answers(render({"tool.py": "print(2)\n"}))))
    assert first.exit_ok and second.exit_ok, (first.error, second.error)
    one, two = _report(tmp_path / "one"), _report(tmp_path / "two")
    assert one["target"] == two["target"] and one["branch"] != two["branch"]
    assert one["base"] == two["base"], "the second run starts from main, not from the first run's branch"
    [entry] = projects.load()["projects"]
    assert [note["status"] for note in entry["runs"]] == ["succeeded", "succeeded"]


def test_a_run_on_a_repository_leaves_the_persons_checkout_and_uncommitted_work_alone(repo, tmp_path, capsys):
    """Its work is a branch of their repository; their checkout, edits included, is untouched (#330 I1)."""
    (repo / "app.py").write_text("print('mine, not committed')\n")
    (repo / "notes.txt").write_text("untracked\n")
    head = git(repo, "rev-parse", "HEAD")
    # This check passes only where app.py is as committed: the worktree, from its pinned base.
    tool = "import pathlib\nassert pathlib.Path('app.py').read_text() == \"print('app')\\n\"\n"
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": tool}))), target=str(repo))
    assert result.exit_ok, result.error
    assert (git(repo, "branch", "--show-current"), git(repo, "rev-parse", "HEAD")) == ("main", head)
    assert (repo / "app.py").read_text() == "print('mine, not committed')\n"
    assert (repo / "notes.txt").exists() and not (repo / "tool.py").exists()
    payload = _report(tmp_path / "run")
    assert (payload["target"], payload["base"]) == (str(repo.resolve()), head)
    assert git(repo, "ls-tree", "--name-only", payload["branch"]).splitlines() == ["app.py", "tool.py"]
    assert "uncommitted change(s) in" in capsys.readouterr().err
    [entry] = projects.load()["projects"]
    assert entry["kind"] == "existing"


def test_a_run_from_another_base_works_from_that_commit(repo, tmp_path):
    first = git(repo, "rev-parse", "HEAD")
    (repo / "later.py").write_text("x = 1\n")
    git(repo, "add", "later.py")
    git(repo, "commit", "-q", "-m", "later")
    tool = "import pathlib\nassert not pathlib.Path('later.py').exists()\n"
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": tool}))), target=str(repo),
                     base=first)
    assert result.exit_ok, result.error
    assert _report(tmp_path / "run")["base"] == first


def test_a_base_that_names_nothing_is_refused_before_anything_is_pruned_or_made(repo, tmp_path, monkeypatch):
    ended = tmp_path / "ended"
    ended.mkdir()
    earlier = worktree.create(repo, "HEAD", "0123abcd0123", ended)
    (ended / ".refactory" / "spine-status.json").write_text(json.dumps({"status": "failed"}))
    monkeypatch.setenv("REFACTORY_WORKTREE_IDLE_HOURS", "0")
    result = _design(tmp_path / "run", FakeModel(_answers()), target=str(repo), base="no-such-branch")
    assert result.exit_ok is False
    assert "the base 'no-such-branch' is not a commit" in (result.error or "")
    assert Path(earlier.path).exists() and worktree.recorded(tmp_path / "run") is None


def test_a_redo_of_prepare_keeps_the_base_it_pinned_even_once_that_branch_is_gone(repo, tmp_path):
    git(repo, "branch", "start")
    run = tmp_path / "run"
    (run / ".refactory").mkdir(parents=True)
    (run / ".refactory" / "invocation.json").write_text(json.dumps({"id": "0123456789abcdef"}))
    task = {"name": NAME, "brief": "a tool", "check": "python tool.py", "target": str(repo), "base": "start"}
    first = design_ops.prepare_design(nn.RunContext(run, model=FakeModel([]), task=task))
    git(repo, "branch", "-D", "start")
    again = design_ops.prepare_design(nn.RunContext(run, model=FakeModel([]), task=task))
    assert (again["worktree"], again["base"]) == (first["worktree"], first["base"])


def test_a_target_inside_a_repository_is_refused_before_anything_is_made(repo, tmp_path):
    (repo / "sub").mkdir()
    result = _design(tmp_path / "run", FakeModel(_answers()), target=str(repo / "sub"))
    assert result.exit_ok is False
    assert f"pass its root, {repo.resolve()}" in (result.error or "")
    assert not worktree.slot(repo).exists() and projects.load()["projects"] == []


def test_an_attempt_that_fails_its_check_is_redone_as_a_second_commit_that_replaces_it(tmp_path):
    failing = render({"tool.py": "raise SystemExit(1)\n", "draft.py": "x = 1\n"})
    result = _design(tmp_path / "run", FakeModel(_answers(failing, render({"tool.py": "print('fixed')\n"}))))
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    project = Path(payload["target"])
    assert git(project, "log", "--format=%s", f"{payload['base']}..{payload['branch']}").splitlines() == [
        f"{NAME}: attempt 2", f"{NAME}: attempt 1"]
    assert git(project, "ls-tree", "--name-only", payload["branch"]) == "tool.py", "draft.py went with attempt 1"
    assert "draft.py" not in (tmp_path / "run" / "changes.diff").read_text()


def test_a_check_that_rewrites_a_tracked_file_does_not_wedge_the_run(repo, tmp_path, capsys):
    """The worktree is the run's own: what its check changed is put back, and said so."""
    tool = ("import os, pathlib\n"
            "pathlib.Path('app.py').write_text('rewritten by the check\\n')\n"
            "marker = pathlib.Path(os.environ['HOME']) / 'second-attempt'\n"
            "if not marker.exists():\n"
            "    marker.write_text('')\n"
            "    raise SystemExit('the first attempt is refused on purpose')\n")
    emit = render({"tool.py": tool})
    result = _design(tmp_path / "run", FakeModel(_answers(emit, emit)), target=str(repo))
    assert result.exit_ok, result.error
    assert git(repo, "show", f"{_report(tmp_path / 'run')['branch']}:app.py") == "print('app')"
    warned = capsys.readouterr().err
    assert "were put back before this one: app.py" in warned
    assert "are not part of its branch" in warned


# ── the boundaries a run keeps ─────────────────────────────────────────────
class _EditingModel(FakeModel):
    """The #319 worker: edits a checkout directly, then answers that it is already done."""

    def __init__(self, answers: list[str], edit: Callable[[], Path]) -> None:
        super().__init__(answers)
        self.edit = edit

    def complete(self, prompt: str, *, system: str = "", workdir: object = None) -> str:
        answer = super().complete(prompt, system=system, workdir=workdir)
        if "Write the artifact `" in prompt:
            (self.edit() / "app.py").write_text("print('edited in place')\n")
        return answer


@pytest.mark.parametrize("where", ["worktree", "target"])
def test_an_emit_that_edits_the_worktree_or_the_checkout_is_refused(repo, tmp_path, where):
    """The worktree is the run's own, but only materialise writes it (#319)."""
    run = tmp_path / "run"
    edit = (lambda: Path(worktree.recorded(run).path)) if where == "worktree" else (lambda: repo)
    result = _design(run, _EditingModel(_answers(render({"tool.py": "print('built')\n"})), edit),
                     target=str(repo))
    assert result.exit_ok is False
    assert "changed while agent 'emit' ran" in (result.error or "")
    found = worktree.recorded(run)
    assert (f"in {found.path}, " in (result.error or "")) == (where == "worktree")
    assert found.head == found.base, "materialise never ran"


def test_a_check_in_the_worktree_sees_none_of_refactorys_environment(repo, tmp_path, monkeypatch):
    """`REFACTORY_*` and the launcher's PYTHONPATH once failed a target's own tests (#330 E1)."""
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "launcher"))
    monkeypatch.setenv("REFACTORY_PROBE", "1")
    tool = ("import os, pathlib\n"
            "leaked = sorted(n for n in os.environ if n == 'PYTHONPATH' or n.startswith(('REFACTORY_', 'DSH_')))\n"
            "assert not leaked, leaked\n"
            "assert not pathlib.Path(os.environ['HOME']).resolve().is_relative_to(pathlib.Path.cwd().resolve())\n"
            "print(os.environ['HOME'])\n")
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": tool}))), target=str(repo))
    assert result.exit_ok, result.error
    evidence = json.loads((tmp_path / "run" / "authoring-evidence.json").read_text())
    assert evidence["validation"]["stdout"].strip() == str(tmp_path / "run" / ".refactory" / "check-home")


def test_a_check_uses_the_environment_its_worktrees_manifests_install(repo, tmp_path, monkeypatch):
    """The worktree's interpreter, not refactory's, even though refactory's is on the launcher's PATH."""
    stub(tmp_path / "stubs", "uv", "mkdir -p .venv/bin\n"
                                   "printf '#!/bin/sh\\necho the-worktree-python\\n' > .venv/bin/python\n"
                                   "chmod +x .venv/bin/python")
    monkeypatch.setenv("PATH", f"{tmp_path / 'stubs'}{os.pathsep}{os.environ['PATH']}")
    (repo / "pyproject.toml").write_text("[project]\nname = 'app'\n")
    git(repo, "add", "pyproject.toml")
    git(repo, "commit", "-q", "-m", "manifest")
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": "print('refactory python')\n"}))),
                     target=str(repo))
    assert result.exit_ok, result.error
    evidence = json.loads((tmp_path / "run" / "authoring-evidence.json").read_text())
    assert evidence["validation"]["stdout"].strip() == "the-worktree-python"


def test_the_setup_command_runs_in_the_worktree_before_each_check(repo, tmp_path):
    tool = "import pathlib\nassert pathlib.Path('built.txt').read_text() == 'built\\n'\n"
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": tool}))),
                     _mode(setup="echo built > built.txt"), target=str(repo))
    assert result.exit_ok, result.error


def test_an_environment_that_cannot_be_prepared_fails_the_check_and_says_why(repo, tmp_path):
    """Running the check anyway would test it against refactory's interpreter, not the target's."""
    (repo / "package.json").write_text("{}\n")
    git(repo, "add", "package.json")
    git(repo, "commit", "-q", "-m", "js")
    emit = render({"tool.py": "print('built')\n"})
    failed_install = _design(tmp_path / "install", FakeModel(_answers(emit, emit)), target=str(repo))
    assert failed_install.exit_ok is False
    assert "package.json has no lockfile" in (failed_install.error or "")
    failed_setup = _design(tmp_path / "setup", FakeModel(_answers(emit, emit)), _mode(setup="exit 3"),
                           target=str(git_repo(tmp_path / "plain")))
    assert failed_setup.exit_ok is False
    assert "the setup command `exit 3` failed" in (failed_setup.error or "")


def test_the_agents_are_told_the_worktree_and_the_reviewers_the_diff_and_the_wrapper(repo, tmp_path):
    model = FakeModel(_answers(render({"tool.py": "print('built')\n"})))
    result = _design(tmp_path / "run", model, target=str(repo))
    assert result.exit_ok, result.error
    found = worktree.recorded(tmp_path / "run")
    assert any(f"answerable by reading {found.path}" in prompt for prompt in model.prompts)
    emit = next(prompt for prompt in model.prompts if "Write the artifact `" in prompt)
    assert f"relative to the root of this run's worktree, {found.path}" in emit
    reviews = [prompt for prompt in model.prompts if "do not infer it from your working directory" in prompt]
    assert reviews
    for prompt in reviews:
        assert f"git -C {found.path} diff {found.base[:12]}" in prompt
        assert str(tmp_path / "run" / worktree.WRAPPER) in prompt


# ── what keeps working as before ───────────────────────────────────────────
def test_with_its_worktree_off_a_run_writes_the_artifact_directory_and_makes_no_branch(repo, tmp_path,
                                                                                    monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(repo))
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": "print('built')\n"}))),
                     _mode(worktree=False))
    assert result.exit_ok, result.error
    assert Path(_report(tmp_path / "run")["artifact"]) == (repo / design_ops.ARTIFACTS / NAME).resolve()
    assert git(repo, "branch", "--list", "refactory/*") == ""
    assert not worktree.root().exists() and projects.load()["projects"] == []


def test_with_its_worktree_off_a_run_refuses_what_only_a_worktree_can_use(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(repo))
    result = _design(tmp_path / "run", FakeModel(_answers()), _mode(worktree=False), target=str(repo))
    assert result.exit_ok is False
    assert "target need the run's worktree, which is off" in (result.error or "")


def test_a_run_without_a_worktree_never_inherits_one_an_earlier_run_left_in_its_directory(
        repo, tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(repo))
    run = tmp_path / "run"
    for knobs, target in (({}, str(repo)), ({"worktree": False}, "")):
        ctx = nn.RunContext(run, model=FakeModel([]), knobs=knobs,
                            task={"name": NAME, "brief": "a tool", "check": "python tool.py", "target": target})
        ctx.facts.update(design_ops.prepare_design(ctx))
    assert worktree.recorded(run) is None
    assert ctx.facts["worktree"] == design_ops.NO_WORKTREE


def test_design_fast_keeps_its_own_copy_and_makes_no_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path / "project"))
    plan = "Change only the requested file."
    qa = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python answer.py",
                                                   "status": "pass", "evidence": "exit 0"}],
                     "findings": [], "not_run": []})
    result = DesignFastMode().run(tmp_path / "run", model=FakeModel([plan, "===FILE: answer.py===\nx = 1\n", qa]),
                                  task={"name": "small", "brief": "x", "check": f"{sys.executable} answer.py"})
    assert result.exit_ok, result.error
    assert not worktree.root().exists() and projects.load()["projects"] == []


def test_ceo_passes_the_worktree_flags_to_design_and_refuses_them_where_they_cannot_apply(
        monkeypatch, tmp_path, capsys):
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "_forward", lambda module, argv: seen.append(argv) or 0)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ceo", "a tool", "--check", "true", "--target", ".", "--base", "v1", "--setup", "make"]) == 0
    assert {f"target={tmp_path.resolve()}", "base=v1", "setup=make"} <= set(seen[0])
    assert cli.main(["ceo", "a tool", "--check", "true", "--no-worktree"]) == 0
    assert "worktree=0" in seen[1]
    for refused in (["--mode", "design_fast", "--target", "."], ["--mode", "create", "--single", "--target", "."],
                    ["--no-worktree", "--target", "."]):
        assert cli.main(["ceo", "a tool", "--check", "true", *refused]) == 2
    err = capsys.readouterr().err
    assert err.count("does not take --target") == 2 and "--no-worktree cannot take --target" in err
    assert len(seen) == 2, "a refused flag forwards nothing"
    assert cli.main(["ceo", "default_pack/design", "--target", "."]) == 0
    assert f"target={tmp_path.resolve()}" in seen[2], "a named mode takes them as knobs"


def test_the_design_mode_declares_the_knobs_the_ceo_passes():
    """`--target` reaches a knob the graph consumes, not one the runtime would refuse."""
    state = registry.resolve(REPO_ROOT, "default_pack/design")[1]().state_dict()
    state = interactive.with_knobs(state, ["target=/somewhere", "base=v1", "worktree=0", "setup=make"])
    assert (state["knobs"]["target"]["value"], state["knobs"]["base"]["value"],
            state["knobs"]["worktree"]["value"], state["knobs"]["setup"]["value"]) == (
        "/somewhere", "v1", False, "make")
