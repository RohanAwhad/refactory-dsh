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
