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


def test_untracked_in_subdirectory_and_names_with_spaces(repo, tmp_path):
    """Subdirectory untracked files and filenames with spaces are handled correctly.

    Without ``--untracked-files=all``, ``git status`` shows ``?? newdir/`` for a new
    directory and the individual files inside are silently skipped.  Without ``-z``,
    git quotes filenames containing spaces and the parse breaks.
    """
    # 1. Untracked file inside a new subdirectory.
    subdir = repo / "newdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("nested content\n")

    # 2. Tracked file whose name contains a space: commit it, then modify.
    space_file = repo / "my file.txt"
    space_file.write_text("original\n")
    git(repo, "add", "my file.txt")
    git(repo, "commit", "-q", "-m", "add file with space")
    space_file.write_text("modified with space\n")

    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "edgetest01", run)
    entries = worktree.copy_uncommitted(repo, Path(found.path), run)
    found = worktree.recorded(run)

    paths = {e["path"] for e in entries}

    # Subdirectory untracked file must be present.
    assert "newdir/nested.txt" in paths, f"newdir/nested.txt not in {paths}"
    assert (Path(found.path) / "newdir" / "nested.txt").read_text() == "nested content\n"

    # File with space in its name must be present and have the right content.
    assert "my file.txt" in paths, f"'my file.txt' not in {paths}"
    assert (Path(found.path) / "my file.txt").read_text() == "modified with space\n"

    # Both appear in the base commit's tree.
    base_tree = worktree.git(
        Path(found.path), "ls-tree", "-r", "--name-only", found.base
    ).splitlines()
    assert "newdir/nested.txt" in base_tree
    assert "my file.txt" in base_tree


def test_symlink_retarget_leaves_old_target_untouched(repo, tmp_path):
    """A retargeted tracked symlink leaves its old target untouched.

    Before the fix, ``copy_uncommitted`` used ``read_bytes``/``write_bytes``
    which followed the existing symlink in the worktree and overwrote the old
    target file outside the worktree.  After the fix, symlinks are recreated
    with ``os.readlink`` + ``os.symlink``.
    """
    import os as _os

    # Create an external file that the symlink points to.
    ext_dir = tmp_path / "external"
    ext_dir.mkdir()
    old_target = ext_dir / "old_target.txt"
    old_target.write_text("precious data\n")
    new_target = ext_dir / "new_target.txt"
    new_target.write_text("new data\n")

    # Commit a tracked symlink pointing to old_target.
    link_path = repo / "tracked_link"
    _os.symlink(str(old_target), str(link_path))
    git(repo, "add", "tracked_link")
    git(repo, "commit", "-q", "-m", "add tracked symlink")

    # Retarget the symlink (the person's uncommitted change).
    link_path.unlink()
    _os.symlink(str(new_target), str(link_path))

    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "symlnk0001", run)
    entries = worktree.copy_uncommitted(repo, Path(found.path), run)

    paths = {e["path"] for e in entries}
    assert "tracked_link" in paths

    wt_link = Path(found.path) / "tracked_link"
    # The worktree must have a symlink, not a regular file.
    assert wt_link.is_symlink(), "tracked_link should be a symlink in the worktree"
    assert _os.readlink(str(wt_link)) == str(new_target)

    # The old target file must be untouched.
    assert old_target.read_text() == "precious data\n", \
        "the old symlink target must not be overwritten"


def test_untracked_symlink_outside_repo_recreated_as_link(repo, tmp_path):
    """An untracked symlink to a file outside the repository is recreated as a link.

    Before the fix, ``copy_uncommitted`` followed the symlink and committed the
    target file's contents.  After the fix, the symlink is recreated faithfully.
    """
    import os as _os

    # Create an external file (simulating credentials or other sensitive data).
    creds = tmp_path / "secrets" / "api_key.txt"
    creds.parent.mkdir(parents=True)
    creds.write_text("sk-secret-12345\n")

    # Create an untracked symlink to it in the repo.
    link_path = repo / "ext_link"
    _os.symlink(str(creds), str(link_path))

    run = _run(tmp_path)
    found = worktree.create(repo, "HEAD", "symlnk0002", run)
    entries = worktree.copy_uncommitted(repo, Path(found.path), run)

    paths = {e["path"] for e in entries}
    assert "ext_link" in paths

    wt_link = Path(found.path) / "ext_link"
    # Must be a symlink, not a regular file containing the credentials.
    assert wt_link.is_symlink(), "ext_link should be a symlink, not a regular file"
    assert _os.readlink(str(wt_link)) == str(creds)

    # The entry's hash should be the hash of the link target string, not the
    # file content.
    import hashlib
    ext_entry = next(e for e in entries if e["path"] == "ext_link")
    expected_sha = hashlib.sha256(str(creds).encode()).hexdigest()
    assert ext_entry["sha256"] == expected_sha


def test_include_uncommitted_knob_through_prepare(repo, tmp_path):
    """The include_uncommitted knob drives copy_uncommitted through _open_worktree.

    With the knob on, the worktree starts from the person's uncommitted changes.
    With it off (default), the worktree has only committed content.
    On a redo (branch has attempt commits), copy_uncommitted is not called again
    and the base stays at the first copy's commit.
    """
    import os
    from unittest.mock import patch
    from refactory_dsh import repo_change
    from refactory_dsh import worktree as wt_mod

    # Modify a tracked file in the person's checkout.
    (repo / "app.py").write_text("uncommitted via knob\n")

    # ---- Part 1: include_uncommitted=True ----
    run_on = tmp_path / "run-knobon"
    run_on.mkdir(parents=True)
    inv_on = run_on / ".refactory"
    inv_on.mkdir(parents=True, exist_ok=True)
    (inv_on / "invocation.json").write_text('{"id": "aabbccdd0001"}')

    class Ctx:
        def __init__(self, workdir, knobs):
            self.workdir = workdir
            self._knobs = knobs
        def knob(self, name, default=None):
            return self._knobs.get(name, default)

    ctx_on = Ctx(run_on, {"include_uncommitted": True})

    with patch("refactory_dsh.repo_change.project_for_nodes", return_value=repo):
        result_on = repo_change._open_worktree(ctx_on, "knobtest1", {"target": str(repo)})

    wt_on = Path(result_on["worktree"])
    assert (wt_on / "app.py").read_text() == "uncommitted via knob\n"
    found_on = wt_mod.recorded(run_on)
    assert found_on is not None
    record_on = wt_mod.uncommitted_record(found_on)
    assert any(e["path"] == "app.py" for e in record_on), "uncommitted record must list app.py"
    # The diff from base to HEAD is empty: the uncommitted change IS the base.
    diff = wt_mod.git(wt_on, "diff", "--name-only", found_on.base, "HEAD").strip()
    assert diff == "", "uncommitted changes should be the base, not a diff from it"
    first_base = found_on.base

    # ---- Part 2: include_uncommitted=False (default) ----
    run_off = tmp_path / "run-knoboff"
    run_off.mkdir(parents=True)
    inv_off = run_off / ".refactory"
    inv_off.mkdir(parents=True, exist_ok=True)
    (inv_off / "invocation.json").write_text('{"id": "aabbccdd0002"}')

    ctx_off = Ctx(run_off, {})

    with patch("refactory_dsh.repo_change.project_for_nodes", return_value=repo):
        result_off = repo_change._open_worktree(ctx_off, "knobtest2", {"target": str(repo)})

    wt_off = Path(result_off["worktree"])
    assert (wt_off / "app.py").read_text() == "print('app')\n", \
        "without include_uncommitted, the worktree should have committed content only"
    found_off = wt_mod.recorded(run_off)
    assert wt_mod.uncommitted_record(found_off) == []

    # ---- Part 3: redo guard — after attempt commits, copy_uncommitted is skipped ----
    # Simulate an attempt commit on the first worktree's branch.
    (wt_on / "attempt.py").write_text("attempt 1\n")
    wt_mod.git(wt_on, "add", "attempt.py")
    wt_mod.git(wt_on, *wt_mod.NO_HOOKS, "commit", "-m", "attempt 1",
               env={**os.environ, **wt_mod.COMMITTER})

    # Person now edits a file in the checkout — this must NOT be re-copied.
    (repo / "app.py").write_text("redo edit should not appear\n")

    with patch("refactory_dsh.repo_change.project_for_nodes", return_value=repo), \
         patch.object(wt_mod, "copy_uncommitted") as spy:
        repo_change._open_worktree(ctx_on, "knobtest1", {"target": str(repo)})

    spy.assert_not_called()
    # The worktree still has the original uncommitted content, not the redo edit.
    assert (wt_on / "app.py").read_text() == "uncommitted via knob\n"
    # The base stays at the first copy's commit.
    found_redo = wt_mod.recorded(run_on)
    assert found_redo.base == first_base, "the base must not move on redo"
