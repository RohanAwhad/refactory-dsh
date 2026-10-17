"""New projects and the registry that finds them again (#351).

Ported from remote-factory's `~/factory-projects` and `tests/test_registry.py`. The
conftest points the projects folder and refactory's home at a temporary directory, so
nothing here touches the real ones.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from refactory_dsh import cli, projects, worktree
from refactory_dsh.projects import projects_dir
from tests_support import git, git_repo


def _spine(run: Path, status: str) -> None:
    (run / ".refactory").mkdir(parents=True, exist_ok=True)
    (run / ".refactory" / "spine-status.json").write_text(json.dumps({"status": status}))


def test_a_new_project_is_a_repository_on_main_whose_first_commit_is_empty():
    """So the run's first pull request holds all of its code (#350)."""
    path = projects.new_project("weather-cli")
    assert path == (projects_dir() / "weather-cli").resolve()
    assert git(path, "branch", "--show-current") == "main"
    assert git(path, "rev-list", "--count", "HEAD") == "1"
    assert git(path, "ls-tree", "--name-only", "HEAD") == ""
    assert git(path, "log", "-1", "--format=%an") == "Refactory"
    [entry] = projects.load()["projects"]
    assert (entry["name"], entry["path"], entry["kind"]) == ("weather-cli", str(path), "new")
    assert sorted(item.name for item in projects_dir().iterdir()) == ["weather-cli"], "no staging left"


def test_a_name_refactory_already_created_is_the_same_project_again():
    first = projects.new_project("weather-cli")
    (first / "kept.py").write_text("x = 1\n")
    assert projects.new_project("weather-cli") == first
    assert (first / "kept.py").exists()
    assert len(projects.load()["projects"]) == 1


def test_runs_creating_one_project_at_once_share_it():
    made: list[Path] = []
    threads = [threading.Thread(target=lambda: made.append(projects.new_project("busy"))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(made) == 4 and len(set(made)) == 1
    assert git(made[0], "rev-list", "--count", "HEAD") == "1"
    assert [item.name for item in projects_dir().iterdir()] == ["busy"]


def test_staging_commit_disables_automatic_maintenance():
    """The staging commit must not start background maintenance that races with cleanup."""
    calls: list[tuple[str, ...]] = []
    original_git = worktree.git

    def recording_git(repo, *args, **kwargs):
        calls.append(args)
        return original_git(repo, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(worktree, "git", recording_git)
        projects.new_project("probe")

    commit_calls = [args for args in calls if "commit" in args]
    assert len(commit_calls) == 1, f"expected exactly one commit call, got {len(commit_calls)}"
    commit_args = commit_calls[0]
    joined = " ".join(commit_args)
    assert "gc.auto=0" in joined, f"gc.auto=0 not found in {commit_args}"
    assert "maintenance.auto=false" in joined, f"maintenance.auto=false not found in {commit_args}"


def test_a_creation_that_died_part_way_does_not_refuse_the_next_run():
    projects_dir().mkdir(parents=True)
    (projects_dir() / ".tool.abc123.new").mkdir()
    assert projects.new_project("tool") == (projects_dir() / "tool").resolve()


def test_a_directory_in_the_projects_folder_refactory_did_not_create_is_refused():
    (projects_dir() / "mine").mkdir(parents=True)
    with pytest.raises(ValueError, match="is not a project refactory created"):
        projects.new_project("mine")
    assert list((projects_dir() / "mine").iterdir()) == [], "nothing is written into it"
    assert projects.load()["projects"] == []


def test_a_run_is_noted_once_and_its_later_status_replaces_the_earlier(tmp_path):
    path = projects.new_project("tool")
    run = tmp_path / "run"
    run.mkdir()
    projects.record_run(path, run=run, branch="refactory/run-abc12345", base="b" * 40, status="running")
    projects.record_run(path, run=run, branch="refactory/run-abc12345", base="b" * 40, status="succeeded")
    [entry] = projects.load()["projects"]
    [note] = entry["runs"]
    assert (note["run"], note["branch"], note["status"]) == (str(run.resolve()), "refactory/run-abc12345",
                                                             "succeeded")


def test_two_runs_in_one_run_directory_are_two_notes(tmp_path):
    """A session's second run reuses its directory; the first run's note stays."""
    path = projects.new_project("tool")
    run = tmp_path / "run"
    projects.record_run(path, run=run, branch="refactory/run-aaaa1111", base="b", status="succeeded")
    projects.record_run(path, run=run, branch="refactory/run-bbbb2222", base="b", status="running")
    [entry] = projects.load()["projects"]
    assert [(note["branch"], note["status"]) for note in entry["runs"]] == [
        ("refactory/run-aaaa1111", "succeeded"), ("refactory/run-bbbb2222", "running")]


def test_a_run_that_ended_without_saying_so_is_listed_as_ended(tmp_path):
    """A run notes only that it runs and that it succeeded; a failure is read from its record."""
    repo = git_repo(tmp_path / "repo")
    running, failed = tmp_path / "running", tmp_path / "failed"
    for run, run_id in ((running, "aaaa0000bbbb"), (failed, "cccc0000dddd")):
        run.mkdir()
        found = worktree.create(repo, "HEAD", run_id, run)
        projects.record_run(repo, run=run, branch=found.branch, base=found.base, status="running")
    _spine(running, "ready")
    _spine(failed, "failed")
    [entry] = projects.load()["projects"]
    assert [projects.status(entry, note) for note in entry["runs"]] == ["running", "ended"]
    gone = {**entry["runs"][0], "branch": "refactory/run-ffff0000ffff"}
    assert projects.status(entry, gone) == "ended", "pruning removed its record"
    _spine(running, "halted")
    assert projects.status(entry, entry["runs"][0]) == "halted", "a run that can be resumed says why it waits"


def test_a_run_on_an_existing_repository_lists_it_as_existing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    projects.record_run(repo, run=tmp_path / "run", branch="refactory/run-abc12345", base="b", status="running")
    [entry] = projects.load()["projects"]
    assert (entry["path"], entry["kind"]) == (str(repo.resolve()), "existing")


def test_a_registry_that_cannot_be_written_is_reported_and_the_run_goes_on(tmp_path, monkeypatch, capsys):
    """The registry finds a project again; it is not part of the run's own record."""
    def refuse(path, payload):
        raise PermissionError("read-only")

    monkeypatch.setattr(worktree, "_write_json", refuse)
    projects.record_run(tmp_path, run=tmp_path / "run", branch="refactory/run-abc12345", base="b",
                        status="running")
    assert "the project registry" in capsys.readouterr().err
    assert projects.load()["projects"] == []


def test_runs_ending_at_once_are_all_recorded(tmp_path):
    """Written under a lock as well as atomically, so no run's note overwrites another's."""
    path = projects.new_project("busy")
    runs = [tmp_path / f"run{index}" for index in range(8)]
    threads = [threading.Thread(target=projects.record_run, args=(path,),
                                kwargs={"run": run, "branch": f"refactory/run-{index:08x}", "base": "b",
                                        "status": "succeeded"})
               for index, run in enumerate(runs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    [entry] = projects.load()["projects"]
    assert sorted(note["run"] for note in entry["runs"]) == sorted(str(run.resolve()) for run in runs)


def test_a_registry_that_is_not_json_reads_as_empty_and_is_rewritten():
    projects.registry_path().parent.mkdir(parents=True, exist_ok=True)
    projects.registry_path().write_text("{not json")
    assert projects.load() == {"schema": projects.SCHEMA, "projects": []}
    projects.new_project("fresh")
    assert [entry["name"] for entry in json.loads(projects.registry_path().read_text())["projects"]] == ["fresh"]


# ── `refactory projects` ───────────────────────────────────────────────────
def test_projects_lists_each_project_with_how_its_last_run_stands(tmp_path, capsys):
    assert cli.main(["projects"]) == 0
    assert "no projects yet" in capsys.readouterr().out
    path = projects.new_project("tool")
    projects.record_run(path, run=tmp_path / "run", branch="refactory/run-abc12345", base="b", status="running")
    assert cli.main(["projects", "--json"]) == 0
    note = json.loads(capsys.readouterr().out)["projects"][0]["runs"][0]
    assert (note["status"], note["now"]) == ("running", "ended")
    assert cli.main(["projects"]) == 0
    assert "last run ended on refactory/run-abc12345" in capsys.readouterr().out


def test_projects_prune_cleans_up_ended_runs_and_refuses_an_unknown_name(tmp_path, capsys, monkeypatch):
    path = projects.new_project("tool")
    run = tmp_path / "run"
    run.mkdir()
    found = worktree.create(path, "main", "abcdef012345", run)
    _spine(run, "failed")
    monkeypatch.setenv("REFACTORY_WORKTREE_IDLE_HOURS", "0")
    assert cli.main(["projects", "prune", "tool"]) == 0
    assert "removed the worktree of a run" in capsys.readouterr().out
    assert not Path(found.path).exists()
    kept = worktree.create(path, "main", "abcdef543210", run)
    worktree.record_outcome(kept, "succeeded")
    assert cli.main(["projects", "prune", "tool"]) == 0 and Path(kept.path).exists()
    assert cli.main(["projects", "prune", "tool", "--succeeded"]) == 0 and not Path(kept.path).exists()
    capsys.readouterr()
    assert cli.main(["projects", "prune", "nothing-here"]) == 2
    assert "no project called 'nothing-here'" in capsys.readouterr().err
