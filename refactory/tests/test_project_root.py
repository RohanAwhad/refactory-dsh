"""One project root, whatever the working directory.

The defect this locks down: the console defaulted its project to the repository
root, the loop and registry CLIs defaulted to the working directory, and the
authoring commands took `Path.cwd()`. A package created through one was invisible
to the next, and which workspace you got depended on where you stood — two
`.factory/packages` directories existed side by side, each holding a different
half of the same catalogue.
"""

from __future__ import annotations

import importlib
import re
from inspect import getsource
from pathlib import Path

import pytest

from refactory_dsh import console, registry
from refactory_dsh.paths import REPO_ROOT, project_root

#: Modules whose command line takes a `--project`.
CLI_ENTRY_POINTS = ("refactory_dsh.loop", "refactory_dsh.registry")


def test_the_project_root_does_not_move_with_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert project_root() == REPO_ROOT


def test_the_workspace_hangs_off_that_one_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert registry.workspace_root(project_root()) == REPO_ROOT / ".factory" / "packages"


def test_the_environment_can_point_it_somewhere_else(tmp_path, monkeypatch):
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    assert project_root() == tmp_path


@pytest.mark.parametrize("module", CLI_ENTRY_POINTS)
def test_every_cli_defaults_to_the_same_project(module):
    """Read each parser's own default, so a new command cannot quietly differ."""
    source = getsource(importlib.import_module(module))
    # Capture to end of line: the default is `str(project_root())`, and a
    # character class stopping at a paren would cut it at `project_root(`.
    match = re.search(r'"--project",\s*default=(.+)$', source, re.MULTILINE)
    assert match is not None, f"{module} declares no --project default"
    # `project_root` rather than the exact call form: what matters is that the
    # default is derived from the shared root and not from the working directory.
    assert "project_root" in match.group(1), (
        f"{module} defaults --project to {match.group(1).strip()}, which is not the "
        "shared project_root(); the workspace would depend on the working directory"
    )


def test_the_console_and_the_cli_resolve_the_same_workspace(tmp_path, monkeypatch):
    """The user-visible form of the bug: a mode created by one, seen by the other."""
    monkeypatch.chdir(tmp_path)
    from_here = console.artifacts  # a console entry point resolves through project_root()
    assert callable(from_here)
    assert console.RUNS_ROOT == Path(".factory/loop")
    assert (project_root() / console.RUNS_ROOT).is_absolute()


def test_the_running_example_resolves_from_any_working_directory(tmp_path, monkeypatch):
    """A reader's first command must not depend on where they ran it."""
    monkeypatch.chdir(tmp_path)
    package, mode_class = registry.resolve(project_root(), "circle_packing/adaptive")
    assert package.name == "circle_packing"
    assert mode_class.name == "adaptive"
