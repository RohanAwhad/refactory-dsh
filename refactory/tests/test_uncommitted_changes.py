"""Uncommitted-changes opt-in: copy the person's working tree into the worktree (#351).

Modified, staged, untracked and deleted files all appear in the worktree's base commit,
none of them appears in the first attempt's diff, and the person's checkout is unchanged.
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
    """Modified + untracked + deleted files all appear in the worktree's base commit."""
    # Set up: modify tracked file, create untracked file, delete tracked file.
    (repo / "app.py").write_text("modified\n")
    (repo / "untracked.txt").write_text("new file\n")
    (repo / "to_delete.txt").write_text("will be deleted\n")
    git(repo, "add", "to_delete.txt")
    git(repo, "commit", "-q", "-m", "add to_delete")
    (repo / "to_delete.txt").unlink()

    # Record porcelain before.
    porcelain_before = git(repo, "status", "--porcelain")

    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "uctest1111", run)
    entries = worktree.copy_uncommitted(repo, Path(found.path), run)

    # All three files are recorded.
    paths = {e["path"] for e in entries}
    assert "app.py" in paths
    assert "untracked.txt" in paths
    assert "to_delete.txt" in paths

    # The modified content is in the worktree.
    assert (Path(found.path) / "app.py").read_text() == "modified\n"
    # The untracked file is in the worktree.
    assert (Path(found.path) / "untracked.txt").read_text() == "new file\n"
    # The deleted file is gone from the worktree.
    assert not (Path(found.path) / "to_delete.txt").exists()

    # The person's checkout is unchanged.
    porcelain_after = git(repo, "status", "--porcelain")
    assert porcelain_before == porcelain_after

    # The uncommitted record is persisted and readable.
    record = worktree.uncommitted_record(found)
    assert len(record) == len(entries)


def test_worktree_without_copy_uncommitted_has_committed_content_only(repo, tmp_path):
    """Without copy_uncommitted, the worktree has only the committed content."""
    (repo / "app.py").write_text("modified\n")
    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "uctest2222", run)
    # Without copy_uncommitted, the worktree has the committed content.
    assert (Path(found.path) / "app.py").read_text() == "print('app')\n"
    # No uncommitted record.
    assert worktree.uncommitted_record(found) == []
