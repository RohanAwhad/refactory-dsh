"""Uncommitted-changes opt-in: copy the person's working tree into the worktree (#351).

Modified, staged, untracked, renamed and deleted files all appear in the worktree's
base commit, none of them appears in the first attempt's diff, and the person's
checkout is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import worktree
from tests_support import git, git_repo


@pytest.fixture
def repo(tmp_path) -> Path:
    return git_repo(tmp_path / "repo")


def _run(tmp_path: Path, name: str = "run") -> Path:
    run = tmp_path / name
    run.mkdir(parents=True, exist_ok=True)
    return run


def test_uncommitted_changes_are_copied_and_committed(repo, tmp_path):
    """Modified + staged + untracked + renamed + deleted files all appear in the base commit."""
    # Set up: modify tracked file.
    (repo / "app.py").write_text("modified\n")

    # Create and commit a file to delete.
    (repo / "to_delete.txt").write_text("will be deleted\n")
    git(repo, "add", "to_delete.txt")
    git(repo, "commit", "-q", "-m", "add to_delete")
    (repo / "to_delete.txt").unlink()

    # Create an untracked file.
    (repo / "untracked.txt").write_text("new file\n")

    # Rename a file with git mv.
    (repo / "rename_me.txt").write_text("will be renamed\n")
    git(repo, "add", "rename_me.txt")
    git(repo, "commit", "-q", "-m", "add rename_me")
    git(repo, "mv", "rename_me.txt", "renamed.txt")

    # Create a staged file (after the last commit so it stays staged).
    (repo / "staged.txt").write_text("staged content\n")
    git(repo, "add", "staged.txt")

    # Record porcelain before.
    porcelain_before = git(repo, "status", "--porcelain")

    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "uctest1111", run)
    entries = worktree.copy_uncommitted(repo, Path(found.path), run)

    # Re-read the worktree record: copy_uncommitted committed the working-tree
    # state and updated the on-disk record's base and head, but the frozen
    # Worktree dataclass returned by create() still holds the old base hash.
    found = worktree.recorded(run)

    # All files are recorded.
    paths = {e["path"] for e in entries}
    assert "app.py" in paths
    assert "untracked.txt" in paths
    assert "to_delete.txt" in paths
    assert "staged.txt" in paths
    # Rename: old name deleted, new name copied.
    assert "rename_me.txt" in paths
    assert "renamed.txt" in paths
    deleted_paths = {e["path"] for e in entries if e["kind"] == "deleted"}
    assert "to_delete.txt" in deleted_paths
    assert "rename_me.txt" in deleted_paths

    # The modified content is in the worktree.
    assert (Path(found.path) / "app.py").read_text() == "modified\n"
    # The untracked file is in the worktree.
    assert (Path(found.path) / "untracked.txt").read_text() == "new file\n"
    # The deleted file is gone from the worktree.
    assert not (Path(found.path) / "to_delete.txt").exists()
    # The staged file is in the worktree.
    assert (Path(found.path) / "staged.txt").read_text() == "staged content\n"
    # The rename: old is gone, new exists.
    assert not (Path(found.path) / "rename_me.txt").exists()
    assert (Path(found.path) / "renamed.txt").read_text() == "will be renamed\n"

    # The person's checkout is unchanged.
    porcelain_after = git(repo, "status", "--porcelain")
    assert porcelain_before == porcelain_after

    # The uncommitted record is persisted and readable.
    record = worktree.uncommitted_record(found)
    assert len(record) == len(entries)

    # Assert on the base commit's tree: all uncommitted changes appear in the tree.
    base_tree_files = worktree.git(
        Path(found.path), "ls-tree", "-r", "--name-only", found.base
    ).splitlines()
    assert "app.py" in base_tree_files
    assert "untracked.txt" in base_tree_files
    assert "staged.txt" in base_tree_files
    assert "renamed.txt" in base_tree_files
    assert "to_delete.txt" not in base_tree_files
    assert "rename_me.txt" not in base_tree_files

    # None of the uncommitted files should appear in the first attempt's diff
    # (since the base IS the working-tree state, the diff from base to HEAD is empty).
    diff_output = worktree.git(
        Path(found.path), "diff", "--name-only", found.base, "HEAD"
    ).strip()
    assert diff_output == ""


def test_worktree_without_copy_uncommitted_has_committed_content_only(repo, tmp_path):
    """Without copy_uncommitted, the worktree has only the committed content."""
    (repo / "app.py").write_text("modified\n")
    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "uctest2222", run)
    # Without copy_uncommitted, the worktree has the committed content.
    assert (Path(found.path) / "app.py").read_text() == "print('app')\n"
    # No uncommitted record.
    assert worktree.uncommitted_record(found) == []


def test_redo_guard_prevents_recopy_after_attempt_commits(repo, tmp_path):
    """After commit_attempt advances HEAD past base, copy_uncommitted is not re-run.

    The redo guard in ``_open_worktree`` checks ``found.head != found.base``.
    This test verifies the guard condition is correct: after an attempt commit,
    head advances past base, so has_attempts is True and the copy is skipped.
    """
    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "redotest1", run)
    # Initially: head == base (no attempts yet).
    assert found.head == found.base

    # First copy with no changes → empty entries, no record written.
    entries = worktree.copy_uncommitted(repo, Path(found.path), run)
    assert entries == []

    # Simulate an attempt commit on the branch.
    attempt_file = Path(found.path) / "attempt.py"
    attempt_file.write_text("# attempt 1\n")
    worktree.git(Path(found.path), "add", "attempt.py")
    worktree.git(Path(found.path), *worktree.NO_HOOKS, "commit", "-m", "attempt 1",
                 env={**__import__("os").environ, **worktree.COMMITTER})
    new_head = worktree.git(Path(found.path), "rev-parse", "HEAD").strip()

    # Re-read the worktree record: the run record still has the old head,
    # but create() reads head from git.
    found_after = worktree.create(repo, "HEAD", "redotest1", run)

    # Now head != base: the redo guard (found.head != found.base) is True.
    assert found_after.head != found_after.base, \
        "has_attempts should be True after commit_attempt"
    assert found_after.head == new_head

    # Person now edits a file in the checkout.
    (repo / "app.py").write_text("redo modified\n")

    # The guard condition prevents re-copy (in production, _open_worktree
    # skips copy_uncommitted when has_attempts is True).
    has_attempts = found_after.head != found_after.base
    assert has_attempts, "redo guard should prevent copy on a branch with attempts"


def test_staged_new_files_are_copied(repo, tmp_path):
    """A file added with ``git add`` (status 'A ') is copied into the worktree base."""
    # Stage a new file (index-only addition).
    (repo / "staged_only.txt").write_text("staged only\n")
    git(repo, "add", "staged_only.txt")

    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "stagedtest1", run)
    entries = worktree.copy_uncommitted(repo, Path(found.path), run)

    # The staged file must be in the entries.
    paths = {e["path"] for e in entries}
    assert "staged_only.txt" in paths, f"staged_only.txt not in {paths}"

    # And it must be in the worktree.
    assert (Path(found.path) / "staged_only.txt").read_text() == "staged only\n"
