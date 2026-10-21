"""The GUI's projects endpoint: the console lists the same projects as ``refactory projects`` (#351).

This test fails without the ``list_projects`` function because it would not exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refactory_dsh import projects
from refactory_dsh.console import list_projects
from tests_support import git_repo


def test_gui_projects_list(tmp_path):
    """A registered project appears in the list_projects() result."""
    repo = git_repo(tmp_path / "myrepo")
    projects.register(repo, kind="existing")
    result = list_projects()
    names = [p["name"] for p in result]
    paths = [p["path"] for p in result]
    assert "myrepo" in names or str(repo) in paths
    assert any(p["path"] == str(repo) for p in result)
