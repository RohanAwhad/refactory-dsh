"""A design run works in its own worktree of its target, end to end (#351).

One test per path a person takes: a new idea with no repository, a change to a
repository they have checked out, the same idea again, a base other than HEAD, an
attempt that fails its check and is redone, and a run with its worktree turned off.
Then the edits an attempt makes (#330 I4): a small change to a long file, a retry that
edits what the last attempt wrote, and an emission materialise cannot apply, which goes
back to emit rather than ending the run. Last, design_fast, which takes the same steps
(`repo_change`) and delivers nothing. A fake model answers; the git repositories, the
worktrees, the check and the registry are real.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

import pytest

from refactory_dsh import (cli, console, design_ops, interactive, lowering, nn, projects, registry, repo_change,
                           worktree)
from refactory_dsh import run as node_runner
from refactory_dsh.design import DesignMode
from refactory_dsh.fast import DesignFastMode
from refactory_dsh.paths import REPO_ROOT
from refactory_dsh.projects import projects_dir
from tests_support import (RESEARCH_ANSWER, REVIEW_ANSWERS, FakeModel, StaleEmitModel, git, git_repo,
                           render, stub)

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


def _edit(name: str, *pairs: tuple[str, str]) -> str:
    """An ``===EDIT:`` block: each pair is the lines to find and the lines put in their place."""
    body = "".join(f"{repo_change.SEARCH}\n{search}{repo_change.DIVIDER}\n{replacement}{repo_change.REPLACE}\n"
                   for search, replacement in pairs)
    return f"===EDIT: {name}===\n{body}"


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"add {name}")


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


def test_an_attempt_that_fails_its_check_is_redone_as_a_second_commit_on_top_of_it(tmp_path):
    """The retry changes only what it needs to; a file of the first attempt goes when it is deleted."""
    failing = render({"tool.py": "raise SystemExit(1)\n", "draft.py": "x = 1\n", "keep.py": "y = 2\n"})
    fixed = render({"tool.py": "print('fixed')\n"}) + "===DELETE: draft.py===\n"
    result = _design(tmp_path / "run", FakeModel(_answers(failing, fixed)))
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    project = Path(payload["target"])
    assert git(project, "log", "--format=%s", f"{payload['base']}..{payload['branch']}").splitlines() == [
        f"{NAME}: attempt 2", f"{NAME}: attempt 1"]
    assert git(project, "ls-tree", "--name-only", payload["branch"]).split() == ["keep.py", "tool.py"]
    assert "draft.py" not in (tmp_path / "run" / "changes.diff").read_text()
    assert payload["files"] == ["keep.py", "tool.py"], "the report names what the branch delivers, not one attempt"


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
    assert "the check changed tracked files in the run's worktree; they were put back" in warned and "app.py" in warned
    assert "are not part of its branch" not in warned, "put back after the check, so the report finds none"


# ── edits, and an emission materialise cannot apply (#330 I4, E5) ──────────
def test_an_edit_changes_only_the_lines_it_names_in_a_long_file(repo, tmp_path):
    """A one-line change to a long file is a one-line diff: nothing else is retyped."""
    _commit(repo, "long.py", "".join(f"value_{index} = {index}\n" for index in range(300)))
    emission = (render({"tool.py": "import long\nassert long.value_150 == 'changed'\n"})
                + _edit("long.py", ("value_150 = 150\n", "value_150 = 'changed'\n")))
    result = _design(tmp_path / "run", FakeModel(_answers(emission)), target=str(repo))
    assert result.exit_ok, result.error
    branch = _report(tmp_path / "run")["branch"]
    assert git(repo, "diff", "--numstat", f"main..{branch}", "--", "long.py").split()[:2] == ["1", "1"]


def test_a_retry_edits_the_file_its_last_attempt_wrote(tmp_path):
    failing = render({"tool.py": "x = 1\nraise SystemExit('not yet')\n"})
    fixed = _edit("tool.py", ("raise SystemExit('not yet')\n", "print('fixed', x)\n"))
    result = _design(tmp_path / "run", FakeModel(_answers(failing, fixed)))
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    assert git(Path(payload["target"]), "show", f"{payload['branch']}:tool.py") == "x = 1\nprint('fixed', x)"


@pytest.mark.parametrize("emission, reason", [
    (_edit("app.py", ("print('not there')\n", "print('x')\n")), "is not in the file the edit applies to"),
    (render({"tool.py": "x = 'long'  # ... (line truncated to 2000 chars)\n"}), "the read tool's note that it cut"),
    ("===EDIT: app.py===\nprint('no pairs')\n", "has text outside its"),
    ("===FILE: ../outside.py===\nx = 1\n", "is not a file inside the artifact directory"),
], ids=["edit-not-found", "truncated-read", "malformed-edit", "outside-path"])
def test_an_emission_materialise_cannot_apply_goes_back_to_emit_with_the_reason(repo, tmp_path, emission, reason):
    """It once ended the run (#330 E5); now the retry is told why, and nothing of it is committed."""
    model = FakeModel(_answers(emission, render({"tool.py": "print('built')\n"})))
    result = _design(tmp_path / "run", model, target=str(repo))
    assert result.exit_ok, result.error
    retry = [prompt for prompt in model.prompts if "Write the artifact `" in prompt][1]
    assert reason in retry
    payload = _report(tmp_path / "run")
    assert git(repo, "log", "--format=%s", f"{payload['base']}..{payload['branch']}").splitlines() == [
        f"{NAME}: attempt 1"]


def test_emissions_that_never_apply_end_the_run_once_its_attempts_are_spent(repo, tmp_path):
    bad = _edit("app.py", ("print('not there')\n", "x\n"))
    result = _design(tmp_path / "run", FakeModel(_answers(bad, bad, bad)), target=str(repo))
    assert not result.exit_ok
    assert "materialise could not apply emit.txt" in str(result.error)


def test_the_same_edits_given_again_apply_to_what_they_first_applied_to(repo, tmp_path):
    """An unchanged answer on a retry, or materialise redone, must not find its own edits gone."""
    _commit(repo, "old.py", "x = 1\n")
    _commit(repo, "notes.py", "a = 1\nb = 2\nc = 3\n")
    tool = ("import os, pathlib\n"
            "count = pathlib.Path(os.environ['HOME']) / 'attempts'\n"
            "seen = len(count.read_text()) if count.exists() else 0\n"
            "count.write_text('x' * (seen + 1))\n"
            "if seen < 2:\n"
            "    raise SystemExit('this attempt is refused on purpose')\n")
    # A removal leaves nothing that shows it happened, so only the record of the emission
    # can tell this one is a repeat.
    emission = (render({"tool.py": tool}) + _edit("app.py", ("print('app')\n", "print('edited')\n"))
                + _edit("notes.py", ("b = 2\n", "")) + "===DELETE: old.py===\n")
    result = _design(tmp_path / "run", FakeModel(_answers(emission, emission, emission)), _mode(check_attempts=3),
                     target=str(repo))
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    assert git(repo, "log", "--format=%s", f"{payload['base']}..{payload['branch']}").splitlines() == [
        f"{NAME}: attempt 3", f"{NAME}: attempt 2", f"{NAME}: attempt 1"]
    assert git(repo, "show", f"{payload['branch']}:app.py") == "print('edited')"
    assert git(repo, "show", f"{payload['branch']}:notes.py") == "a = 1\nc = 3"
    assert git(repo, "ls-tree", "--name-only", payload["branch"]).split() == ["app.py", "notes.py", "tool.py"]


def test_an_edit_sent_again_is_skipped_rather_than_applied_twice(repo, tmp_path, capsys):
    """Models re-send an earlier attempt's edits beside new work; an insertion must not land twice."""
    tool = ("import os, pathlib\n"
            "marker = pathlib.Path(os.environ['HOME']) / 'second-attempt'\n"
            "if not marker.exists():\n"
            "    marker.write_text('')\n"
            "    raise SystemExit('the first attempt is refused on purpose')\n")
    insert = _edit("app.py", ("print('app')\n", "print('app')\nprint('inserted')\n"))
    first = render({"tool.py": tool}) + insert
    second = render({"tool.py": tool, "extra.py": "x = 1\n"}) + insert
    result = _design(tmp_path / "run", FakeModel(_answers(first, second)), target=str(repo))
    assert result.exit_ok, result.error
    branch = _report(tmp_path / "run")["branch"]
    assert git(repo, "show", f"{branch}:app.py") == "print('app')\nprint('inserted')"
    assert "re-sent edits the last attempt already holds, so they were skipped" in capsys.readouterr().err


@pytest.mark.parametrize("text, pair", [
    ("a\nnew\nb\n", ("a\n", "a\nnew\n")),
    ("new\na\nb\n", ("a\n", "new\na\n")),
    ("a\nx = 2\nb\n", ("x = 1\n", "x = 2\n")),
], ids=["inserted-after", "inserted-before", "changed"])
def test_an_edit_whose_result_is_already_in_place_is_counted_as_applied(text, pair):
    assert repo_change._edited(Path("a.py"), text, [pair], "git show HEAD:a.py") == (text, 1)


@pytest.mark.parametrize("pair", [("x\n", ""), ("x\n", "\n")], ids=["removed", "blanked"])
def test_an_edit_that_removes_lines_is_never_taken_as_already_applied(pair):
    """Nothing shows a removal happened -- a blank line proves nothing -- so it is refused, not skipped."""
    with pytest.raises(worktree.AttemptRefused, match="not in the file"):
        repo_change._edited(Path("a.py"), "a\n\nb\n", [pair], "git show HEAD:a.py")


def test_without_a_worktree_a_run_takes_whole_files_only(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(repo))
    model = FakeModel(_answers(_edit("tool.py", ("x\n", "y\n")), render({"tool.py": "print('built')\n"})))
    result = _design(tmp_path / "run", model, _mode(worktree=False))
    assert result.exit_ok, result.error
    emits = [prompt for prompt in model.prompts if "Write the artifact `" in prompt]
    assert "===FILE: <relative path>===" in emits[0] and "===EDIT:" not in emits[0]
    assert "has no worktree, so its artifact directory holds only whole files" in emits[1]


@pytest.mark.parametrize("answer, reason", [
    ("print('no blocks')\n", "holds no ===FILE:"),
    ("===EDIT: a path with spaces===\n", "has a block header that is not"),
    ("===FILE: a.py===\nx\n" + _edit("a.py", ("x\n", "y\n")), "more than one block"),
    ("===DELETE: a.py===\nleftover\n", "a deletion takes none"),
    (f"===EDIT: a.py===\n{repo_change.SEARCH}\nx\n{repo_change.DIVIDER}\ny\n", "missing its"),
    (_edit("a.py", ("\n", "y\n")), "nothing to search for"),
], ids=["no-blocks", "bad-header", "two-blocks", "delete-with-text", "unclosed-edit", "empty-search"])
def test_an_emission_whose_blocks_are_malformed_is_refused_with_the_reason(answer, reason):
    with pytest.raises(worktree.AttemptRefused, match=reason):
        repo_change._emitted(answer, edits=True)


def test_an_edit_whose_text_is_in_its_file_twice_is_refused_rather_than_guessed():
    with pytest.raises(worktree.AttemptRefused, match="appear 2 times"):
        repo_change._edited(Path("a.py"), "x = 1\nx = 1\n", [("x = 1\n", "x = 3\n")], "git show HEAD:a.py")


def test_an_edit_whose_lines_hold_a_divider_is_refused_rather_than_cut_short():
    """A reStructuredText title's underline is a line of `=======`, which once ended the search early."""
    with pytest.raises(worktree.AttemptRefused, match="unclear"):
        repo_change._emitted(_edit("README.rst", ("License\n=======\n", "Licence\n=======\n")), edits=True)
    with pytest.raises(worktree.AttemptRefused, match="missing; put one"):
        repo_change._emitted(f"===EDIT: a.py===\n{repo_change.SEARCH}\nx\n{repo_change.REPLACE}\n", edits=True)


@pytest.mark.parametrize("text, pairs, edited", [
    ("max = 1\nx = 1\n", [("x = 1\n", "x = 2\n")], "max = 1\nx = 2\n"),
    ("a\r\nb\r\n", [("b\n", "c\n")], "a\r\nc\r\n"),
    ("a\nb", [("b\n", "c\n")], "a\nc"),
    ("a\nb\nc\n", [("b\n", "")], "a\nc\n"),
], ids=["whole-lines-only", "crlf-kept", "no-final-newline-kept", "lines-removed"])
def test_an_edit_matches_whole_lines_as_the_read_tool_shows_them_and_keeps_the_files_endings(text, pairs, edited):
    """The read tool strips `\\r` and ends every line, so that is the text a search is copied from."""
    assert repo_change._edited(Path("a.py"), text, pairs, "git show HEAD:a.py") == (edited, 0)


def test_a_mid_line_search_applies_when_unique():
    """A search that starts inside a line, not at position 0 or after a newline, matches when unique."""
    result, skipped = repo_change._edited(
        Path("a.py"), "hello world\n", [("world\n", "earth\n")], "git show HEAD:a.py")
    assert result == "hello earth\n" and skipped == 0


def test_a_search_inside_a_line_longer_than_2000_characters_applies():
    """An edit can reach text past the 2000-char point the read tool truncates at."""
    line = "x" * 2500 + "MARKER" + "y" * 100 + "\n"
    result, skipped = repo_change._edited(
        Path("a.py"), line, [("MARKER", "REPLACED")], "git show HEAD:a.py")
    assert result == "x" * 2500 + "REPLACED" + "y" * 100 + "\n" and skipped == 0


def test_a_search_without_leading_spaces_applies_when_unique():
    """A search that omits the line's leading whitespace matches when the content is unique."""
    result, skipped = repo_change._edited(
        Path("a.py"), "    def foo():\n        pass\n",
        [("def foo():\n        pass\n", "def bar():\n        pass\n")],
        "git show HEAD:a.py")
    assert result == "    def bar():\n        pass\n" and skipped == 0


def test_a_substring_that_appears_twice_is_refused():
    """A search text appearing twice as a substring is refused, even when neither occurrence starts a line."""
    with pytest.raises(worktree.AttemptRefused, match="appears 2 times"):
        repo_change._edited(
            Path("a.py"), "abXYZcd\nefXYZgh\n",
            [("XYZ", "NEW")], "git show HEAD:a.py")


def test_an_absent_search_with_short_replacement_mid_line_is_refused_not_skipped():
    """An edit whose search text is absent and whose short replacement appears once mid-line is refused."""
    with pytest.raises(worktree.AttemptRefused, match="is not in the file"):
        repo_change._edited(
            Path("a.py"), "abREPLcd\n",
            [("GONE\n", "REPL")], "git show HEAD:a.py")


def test_the_read_tools_paging_note_is_refused_too():
    """What a model retyping a long file is likeliest to copy: the note where the read stopped."""
    with pytest.raises(worktree.AttemptRefused, match="cut text short"):
        repo_change._emitted(render({"a.py": "x = 1\n(Showing lines 1-2000 of 2637. Use offset=2001 to continue.)\n"}),
                            edits=True)


def test_a_worktree_a_prune_removed_is_added_back_before_an_edit_applies(repo, tmp_path):
    run = tmp_path / "run"
    found = worktree.create(repo, "HEAD", "abcdef123456", run)
    git(repo, "worktree", "remove", "--force", found.path)
    ctx = nn.RunContext(run, model=FakeModel([]), task={})
    ctx.facts.update({"name": NAME})
    ctx.write_text("emit.txt", _edit("app.py", ("print('app')\n", "print('edited')\n")))
    facts = repo_change.materialise(ctx)
    assert "emission_refused" not in facts and facts["files"] == ["app.py"]
    assert (Path(found.path) / "app.py").read_text() == "print('edited')\n"


def test_on_the_spines_route_a_refusal_reaches_the_check_and_a_later_attempt_clears_it(repo, tmp_path, monkeypatch):
    """The spine runs each op in its own process; the refusal has to survive in facts.json."""
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef654321", run)
    lowering.write_declarations(DesignMode().state_dict(), run)
    (run / "facts.json").write_text(json.dumps({"name": NAME, "brief": "a tool", "check": "true"}))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3}))
    (run / "emit.txt").write_text(_edit("app.py", ("print('not there')\n", "x\n")))
    node_runner.run_node(f"{repo_change.OPS}:materialise", workdir=run, node_id="materialise")
    verdict = node_runner.run_node(f"{design_ops.__name__}:check_artifact", workdir=run, node_id="check")
    assert verdict == "reloop" and "not in the file the edit applies to" in verdict.reason
    (run / "emit.txt").write_text(render({"tool.py": "print(1)\n"}))
    node_runner.run_node(f"{repo_change.OPS}:materialise", workdir=run, node_id="materialise")
    assert json.loads((run / "facts.json").read_text())["emission_refused"] == ""


def test_a_run_whose_graph_predates_the_refusal_fact_still_materialises(repo, tmp_path):
    """A spine run started before this fact existed has it in no declaration; succeeding must not need it."""
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef112233", run)
    lowering.write_declarations(DesignMode().state_dict(), run)
    declarations = json.loads((run / lowering.DECLARATIONS).read_text())
    declarations["materialise"]["produces"].remove("emission_refused")
    (run / lowering.DECLARATIONS).write_text(json.dumps(declarations))
    (run / "facts.json").write_text(json.dumps({"name": NAME, "check": "python tool.py"}))
    (run / "emit.txt").write_text(render({"tool.py": "print(1)\n"}))
    node_runner.run_node(f"{repo_change.OPS}:materialise", workdir=run, node_id="materialise")
    assert json.loads((run / "facts.json").read_text())["files"] == ["tool.py"]


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


def test_a_refused_emit_is_redone_from_a_fresh_emit_txt(repo, tmp_path):
    """Left in the run, the refused emit.txt read to the redo's worker as the work done."""
    run = tmp_path / "run"
    refused = _design(run, _EditingModel(_answers("All changes are already applied."),
                                         lambda: Path(worktree.recorded(run).path)), target=str(repo))
    assert "changed while agent 'emit' ran" in (refused.error or "")
    git(worktree.recorded(run).path, "checkout", "--", "app.py")

    redone = DesignMode.resume(run, model=StaleEmitModel([render({"tool.py": "print('redone')\n"}),
                                                          *REVIEW_ANSWERS]))
    assert redone.error is None
    assert (run / "emit.txt").read_text() == render({"tool.py": "print('redone')\n"})
    assert git(repo, "show", f"{_report(run)['branch']}:tool.py") == "print('redone')"


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


def test_a_check_in_the_worktree_is_bounded_by_its_timeout_not_by_memory(repo, tmp_path):
    """A Node check that starts worker threads, as vitest does, failed under a 2 GB address-space cap (#325)."""
    import resource

    tool = ("import json, resource\n"
            "print(json.dumps([resource.getrlimit(resource.RLIMIT_AS)[0], "
            "resource.getrlimit(resource.RLIMIT_CPU)[0]]))\n")
    result = _design(tmp_path / "run", FakeModel(_answers(render({"tool.py": tool}))), _mode(check_timeout_s=30),
                     target=str(repo))
    assert result.exit_ok, result.error
    evidence = json.loads((tmp_path / "run" / "authoring-evidence.json").read_text())
    assert json.loads(evidence["validation"]["stdout"]) == [resource.getrlimit(resource.RLIMIT_AS)[0],
                                                            30 * (os.cpu_count() or 1)]


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


def test_a_missing_installer_stops_the_run_without_spending_its_repairs(repo, tmp_path, monkeypatch):
    """No code change can install uv; the run says so instead of asking emit to try again."""
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(next(Path(entry) / "git" for entry in os.environ["PATH"].split(os.pathsep)
                                       if (Path(entry) / "git").is_file()))
    monkeypatch.setenv("PATH", str(only_git))
    (repo / "pyproject.toml").write_text("[project]\nname = 'app'\n")
    git(repo, "add", "pyproject.toml")
    git(repo, "commit", "-q", "-m", "manifest")
    emit = render({"tool.py": "print('built')\n"})
    result = _design(tmp_path / "run", FakeModel(_answers(emit, emit)), target=str(repo))
    assert result.exit_ok is False
    assert "UNAVAILABLE: uv is not installed" in (result.error or "")
    found = worktree.recorded(tmp_path / "run")
    assert git(repo, "rev-list", "--count", f"{found.base}..{found.branch}") == "1", "no repair was attempted"


def test_a_setup_command_that_fails_fails_the_check_and_says_why(tmp_path):
    emit = render({"tool.py": "print('built')\n"})
    failed_setup = _design(tmp_path / "setup", FakeModel(_answers(emit, emit, emit)), _mode(setup="exit 3"),
                           target=str(git_repo(tmp_path / "plain")))
    assert failed_setup.exit_ok is False
    assert "the setup command `exit 3` failed" in (failed_setup.error or "")


def test_a_lockfile_the_install_writes_is_part_of_the_attempt_the_reviewers_see(repo, tmp_path, monkeypatch):
    """A new Node project has no lockfile: the one its install writes is committed, not refused."""
    stub(tmp_path / "bin", "npm", "echo '{\"lockfileVersion\": 3}' > package-lock.json")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    emit = render({"package.json": "{}\n", "tool.py": "print('built')\n"})
    result = _design(tmp_path / "run", FakeModel(_answers(emit)), target=str(repo))
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    assert "package-lock.json" in git(repo, "ls-tree", "--name-only", payload["branch"]).splitlines()
    assert payload["head"] == git(repo, "rev-parse", payload["branch"]) == result.facts["commit"]
    assert "package-lock.json" in (tmp_path / "run" / "changes.diff").read_text()
    assert "package-lock.json" in result.facts["files"], "the files the branch delivers follow the amendment"


def test_a_relative_target_names_a_repository_beside_the_project_not_the_run_directory(repo, tmp_path,
                                                                                    monkeypatch):
    """A node starts in its run directory; `target=repo` from the GUI means the project's `repo`."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    result = _design(tmp_path / "runs" / "one", FakeModel(_answers(render({"tool.py": "print(1)\n"}))),
                     target="repo")
    assert result.exit_ok, result.error
    assert _report(tmp_path / "runs" / "one")["target"] == str(repo.resolve())


def test_a_base_for_a_project_that_does_not_exist_yet_is_refused_before_it_is_made(tmp_path):
    result = _design(tmp_path / "run", FakeModel(_answers()), base="v1")
    assert result.exit_ok is False
    assert "this run starts a new project, which has only its empty main" in (result.error or "")
    assert not (projects_dir() / NAME).exists()


def test_a_second_run_on_a_project_names_the_earlier_runs_branches(tmp_path, capsys):
    """A new project's main stays empty until #350 delivers, so the work is on the branches."""
    first = _design(tmp_path / "one", FakeModel(_answers(render({"tool.py": "print(1)\n"}))))
    assert first.exit_ok, first.error
    capsys.readouterr()
    second = _design(tmp_path / "two", FakeModel(_answers(render({"tool.py": "print(2)\n"}))))
    assert second.exit_ok, second.error
    warned = capsys.readouterr().err
    assert f"has earlier runs, whose work is on their branches, not on main: {_report(tmp_path / 'one')['branch']}" in warned


def test_an_emit_that_edits_the_checkout_the_run_started_from_is_refused(repo, tmp_path, monkeypatch):
    """Workers start in the run directory inside that checkout, so it is watched too (#319)."""
    launcher = git_repo(tmp_path / "launcher")
    monkeypatch.setenv("REFACTORY_PROJECT", str(launcher))
    result = _design(tmp_path / "run", _EditingModel(_answers(render({"tool.py": "print(1)\n"})), lambda: launcher),
                     target=str(repo))
    assert result.exit_ok is False
    assert f"in {launcher.resolve()}, " in (result.error or "")


def test_a_worktree_run_does_not_warn_that_the_persons_checkout_moved(repo, tmp_path, capsys):
    """Its base is pinned, so the person working in their checkout meanwhile changes nothing it sees."""
    class Moving(FakeModel):
        def complete(self, prompt: str, *, system: str = "", workdir: object = None) -> str:
            if "You are the research director" in prompt:
                (repo / "app.py").write_text("print('the person kept working')\n")
            return super().complete(prompt, system=system, workdir=workdir)

    result = _design(tmp_path / "run", Moving(_answers(render({"tool.py": "print(1)\n"}))), target=str(repo))
    assert result.exit_ok, result.error
    assert "changed since this run recorded it" not in capsys.readouterr().err


def test_the_agents_are_told_the_worktree_and_the_reviewers_the_diff_and_the_wrapper(repo, tmp_path):
    model = FakeModel(_answers(render({"tool.py": "print('built')\n"})))
    result = _design(tmp_path / "run", model, target=str(repo))
    assert result.exit_ok, result.error
    found = worktree.recorded(tmp_path / "run")
    assert any(f"answerable by reading {found.path}" in prompt for prompt in model.prompts)
    emit = next(prompt for prompt in model.prompts if "Write the artifact `" in prompt)
    assert f"relative to the root of this run's worktree, {found.path}" in emit
    assert "Each attempt builds on the last" in emit and "===EDIT: <relative path>===" in emit
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
    assert Path(_report(tmp_path / "run")["artifact"]) == (repo / repo_change.ARTIFACTS / NAME).resolve()
    assert git(repo, "branch", "--list", "refactory/*") == ""
    assert not worktree.root().exists() and projects.load()["projects"] == []


def test_with_its_worktree_off_a_run_refuses_what_only_a_worktree_can_use(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(repo))
    result = _design(tmp_path / "run", FakeModel(_answers()), _mode(worktree=False), target=str(repo))
    assert result.exit_ok is False
    assert "target need the run's worktree, which is off" in (result.error or "")
    # Following up a pull request is design's own (#350), and needs a worktree as much.
    result = _design(tmp_path / "again", FakeModel(_answers()), _mode(worktree=False), refine="acme/app#3")
    assert result.exit_ok is False
    assert "refine need the run's worktree, which is off" in (result.error or "")


def test_a_design_run_lowered_before_materialise_moved_still_finds_it():
    """A run waiting at its approval gate across an upgrade calls the name its graph was lowered with."""
    from refactory_dsh.nn.resolve import resolve

    assert resolve("refactory_dsh.design_ops:materialise_artifact") is repo_change.materialise


def test_a_run_without_a_worktree_never_inherits_one_an_earlier_run_left_in_its_directory(
        repo, tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(repo))
    run = tmp_path / "run"
    for knobs, target in (({}, str(repo)), ({"worktree": False}, "")):
        ctx = nn.RunContext(run, model=FakeModel([]), knobs=knobs,
                            task={"name": NAME, "brief": "a tool", "check": "python tool.py", "target": target})
        ctx.facts.update(design_ops.prepare_design(ctx))
    assert worktree.recorded(run) is None
    assert ctx.facts["worktree"] == repo_change.NO_WORKTREE


# ── design_fast, on the same steps (`repo_change`) ─────────────────────────────
#: What design_fast's strategist and focused QA answer; the coder's answers are each test's own.
FAST_PLAN = "Change only the requested file."
FAST_QA = json.dumps({"status": "pass", "checks": [{"name": "runs", "command": "python tool.py",
                                                    "status": "pass", "evidence": "exit 0"}],
                      "findings": [], "not_run": []})


def _fast(workdir: Path, *emits: str, refused: int = 0, knobs: dict | None = None, **task: str):
    """A design_fast run: the strategist, then each emission and, once one applies, its focused QA.

    The first ``refused`` emissions are ones materialise cannot apply, so no QA follows them.
    """
    mode = DesignFastMode()
    state = mode.state_dict()
    for name, value in (knobs or {}).items():
        state["knobs"][name]["value"] = value
    mode.load_state_dict(state)
    answers = [FAST_PLAN] + [answer for index, emit in enumerate(emits)
                             for answer in ((emit,) if index < refused else (emit, FAST_QA))]
    return mode.run(workdir, model=FakeModel(answers),
                    task={"name": NAME, "brief": "a tool", "check": "python tool.py", **task})


def test_design_fast_changes_a_repository_in_a_worktree_and_delivers_nothing(repo, tmp_path):
    """It works where design works, and its work stays on the run's branch: it has no gate to approve a PR."""
    (repo / "app.py").write_text("print('mine, not committed')\n")
    head = git(repo, "rev-parse", "HEAD")
    # Passes only where app.py is as committed and the setup command has run: the worktree, prepared.
    tool = ("import pathlib\nassert pathlib.Path('app.py').read_text() == \"print('app')\\n\"\n"
            "assert pathlib.Path('built.txt').exists()\n")
    model = FakeModel([FAST_PLAN, render({"tool.py": tool}), FAST_QA])
    result = DesignFastMode().run(tmp_path / "run", model=model, task={
        "name": NAME, "brief": "a tool", "check": "python tool.py", "target": str(repo),
        "setup": "python -c \"open('built.txt', 'w').write('built')\""})
    assert result.exit_ok, result.error
    assert "===EDIT: <relative path>===" in model.prompts[1], "emit is told it may edit the last attempt"
    assert str(worktree.WRAPPER) in model.prompts[2], "focused QA is told how to run a command in the worktree"
    assert (git(repo, "rev-parse", "HEAD"), (repo / "app.py").read_text()) == (head, "print('mine, not committed')\n")
    payload = _report(tmp_path / "run")
    assert (payload["target"], payload["base"]) == (str(repo.resolve()), head)
    assert git(repo, "ls-tree", "--name-only", payload["branch"]).splitlines() == ["app.py", "tool.py"]
    assert "fast" in payload and payload["source_project"] is None
    assert not (tmp_path / "run" / "delivery.json").exists() and not (tmp_path / "run" / "delivery.md").exists()
    [entry] = projects.load()["projects"]
    assert entry["runs"][-1]["status"] == "succeeded"


def test_design_fast_with_no_target_starts_a_new_project(tmp_path):
    result = _fast(tmp_path / "run", render({"tool.py": "print('built')\n"}))
    assert result.exit_ok, result.error
    project = (projects_dir() / NAME).resolve()
    payload = _report(tmp_path / "run")
    assert payload["target"] == str(project)
    assert git(project, "ls-tree", "--name-only", payload["branch"]) == "tool.py"


def test_design_fast_edits_its_last_attempt_and_reviews_the_whole_branch(tmp_path):
    """A repair is the next commit, made with an edit, and focused QA is shown every file the branch changes."""
    failing = render({"tool.py": "raise SystemExit(1)\n", "helper.py": "x = 1\n"})
    fixed = _edit("tool.py", ("raise SystemExit(1)\n", "print('fixed')\n"))
    result = _fast(tmp_path / "run", failing, fixed)
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    project = Path(payload["target"])
    assert git(project, "rev-list", "--count", f"{payload['base']}..{payload['branch']}") == "2"
    assert git(project, "show", f"{payload['branch']}:tool.py") == "print('fixed')"
    assert json.loads(result.facts["review_files"]) == ["helper.py", "tool.py"]
    assert payload["fast"]["repairs_used"] == 1


def test_design_fast_carries_the_lockfile_its_install_writes(repo, tmp_path, monkeypatch):
    """The check's install amends the attempt, and focused QA is shown that file too."""
    stub(tmp_path / "bin", "npm", "echo '{\"lockfileVersion\": 3}' > package-lock.json")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    result = _fast(tmp_path / "run", render({"package.json": "{}\n", "tool.py": "print('built')\n"}),
                   target=str(repo))
    assert result.exit_ok, result.error
    payload = _report(tmp_path / "run")
    assert "package-lock.json" in git(repo, "ls-tree", "--name-only", payload["branch"]).splitlines()
    assert payload["head"] == git(repo, "rev-parse", payload["branch"]) == result.facts["commit"]
    assert "package-lock.json" in json.loads(result.facts["review_files"])

def test_design_fast_sends_an_emission_it_cannot_apply_back_to_emit(tmp_path):
    """Within its repair bound, as a failed check is; with no repair left, the run ends and says why."""
    truncated = "===FILE: tool.py===\nx = 1  # ... (line truncated to 2000 chars)\n"
    result = _fast(tmp_path / "run", truncated, render({"tool.py": "print('whole')\n"}), refused=1)
    assert result.exit_ok, result.error
    assert "the read tool's note that it cut text short" in (tmp_path / "run" / "repair.txt").read_text()
    assert _report(tmp_path / "run")["fast"]["repairs_used"] == 1
    ended = _fast(tmp_path / "ended", truncated, refused=1, knobs={"repair_attempts": 0}, name="other_tool")
    assert not ended.exit_ok
    assert "after 0 repair(s): materialise could not apply emit.txt" in str(ended.error)
    assert "cut text short" in str(ended.error)


def test_design_fast_with_its_worktree_off_keeps_its_own_copy_and_makes_no_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path / "project"))
    result = _fast(tmp_path / "run", "===FILE: tool.py===\nx = 1\n", knobs={"worktree": False})
    assert result.exit_ok, result.error
    assert result.facts["artifact"] == str((tmp_path / "project" / repo_change.ARTIFACTS / NAME).resolve())
    assert not worktree.root().exists() and projects.load()["projects"] == []


def test_design_fast_copies_a_source_project_and_refuses_a_target_with_it(repo, tmp_path, monkeypatch):
    """`source_project` is the other way to start from existing code, so a run takes one of the two."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path / "project"))
    truncated = "===FILE: tool.py===\nx = 1  # ... (line truncated to 2000 chars)\n"
    copied = _fast(tmp_path / "run", truncated, "===FILE: tool.py===\nx = 1\n", refused=1,
                   source_project=str(repo))
    assert copied.exit_ok, copied.error
    assert "cut text short" in (tmp_path / "run" / "repair.txt").read_text(), "a refusal is repaired here too"
    assert sorted(path.name for path in Path(copied.facts["artifact"]).iterdir()) == ["app.py", "tool.py"]
    assert not worktree.root().exists()
    refused = _fast(tmp_path / "both", source_project=str(repo), target=str(repo))
    assert not refused.exit_ok
    assert "source_project copies a directory into the artifact, and target" in str(refused.error)


def test_design_fast_refuses_to_follow_up_a_pull_request(tmp_path):
    """Following one up updates it, and design_fast delivers nothing, so it is refused, not ignored."""
    result = _fast(tmp_path / "run", refine="acme/app#3")
    assert not result.exit_ok and "design_fast delivers nothing" in str(result.error)


def test_the_direct_entry_point_passes_the_worktree_flags_to_design_fast(monkeypatch, tmp_path, capsys):
    from refactory_dsh import design

    seen: dict = {}

    def run(self, workdir, **kwargs):
        seen.update(knobs=self.values(), task=kwargs["task"])
        return nn.RunResult(exit_ok=True, facts={})

    monkeypatch.setattr(DesignFastMode, "run", run)
    monkeypatch.chdir(tmp_path)
    assert design.main(["--name", NAME, "--brief", "b", "--check", "true", "--fast", "--no-worktree"]) == 0
    assert seen["knobs"]["worktree"] is False
    with pytest.raises(SystemExit) as refused:
        design.main(["--name", NAME, "--brief", "b", "--check", "true", "--fast", "--no-pr"])
    assert refused.value.code == 2 and "--fast does not take --no-pr" in capsys.readouterr().err


def test_ceo_passes_the_worktree_flags_to_design_and_refuses_them_where_they_cannot_apply(
        monkeypatch, tmp_path, capsys):
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "_forward", lambda module, argv: seen.append(argv) or 0)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ceo", "a tool", "--check", "true", "--target", ".", "--base", "v1", "--setup", "make"]) == 0
    assert {f"target={tmp_path.resolve()}", "base=v1", "setup=make"} <= set(seen[0])
    assert cli.main(["ceo", "a tool", "--check", "true", "--no-worktree"]) == 0
    assert "worktree=0" in seen[1]
    assert cli.main(["ceo", "a tool", "--check", "true", "--mode", "design_fast", "--target", ".",
                     "--setup", "make"]) == 0
    assert {f"target={tmp_path.resolve()}", "setup=make"} <= set(seen[2]), "design_fast takes them too"
    for refused in (["--mode", "create", "--single", "--target", "."], ["--no-worktree", "--target", "."],
                    ["--mode", "design_fast", "--source-project", ".", "--target", "."]):
        assert cli.main(["ceo", "a tool", "--check", "true", *refused]) == 2
    err = capsys.readouterr().err
    assert "does not take --target" in err and "--no-worktree cannot take --target" in err
    assert "--source-project cannot take --target" in err
    assert len(seen) == 3, "a refused flag forwards nothing"
    assert cli.main(["ceo", "default_pack/design", "--target", "."]) == 0
    assert f"target={tmp_path.resolve()}" in seen[3], "a named mode takes them as knobs"


def test_the_console_design_verb_takes_a_target_too(monkeypatch, tmp_path):
    """The GUI chat's headless route and the refactory tool's `design` action go through it."""
    seen: list[list[str]] = []
    monkeypatch.setattr(console, "_forward", lambda module, argv: seen.append(argv) or {})
    monkeypatch.chdir(tmp_path)
    console.main(["design", "--name", "x", "--brief", "b", "--check", "true", "--target", ".",
                  "--base", "v1", "--setup", "make"])
    assert seen[0][seen[0].index("--target") + 1] == str(tmp_path.resolve())
    assert {"--base", "v1", "--setup", "make"} <= set(seen[0])


def test_the_design_mode_declares_the_knobs_the_ceo_passes():
    """`--target` reaches a knob the graph consumes, not one the runtime would refuse."""
    state = registry.resolve(REPO_ROOT, "default_pack/design")[1]().state_dict()
    state = interactive.with_knobs(state, ["target=/somewhere", "base=v1", "worktree=0", "setup=make"])
    assert (state["knobs"]["target"]["value"], state["knobs"]["base"]["value"],
            state["knobs"]["worktree"]["value"], state["knobs"]["setup"]["value"]) == (
        "/somewhere", "v1", False, "make")
