"""Tests for package-owned modes: identity, grouping, and lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import packages


def _graph(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"name": name, "nodes": {}, "edges": [],
                                "start_node": "x", "terminal": True}))
    return path


def test_a_bare_name_is_refused(tmp_path: Path) -> None:
    """Defaulting a bare name into a package is how a flat namespace grows back."""
    with pytest.raises(packages.ModeIdError):
        packages.split("circle_packing_loop")


def test_identifiers_are_validated(tmp_path: Path) -> None:
    assert packages.mode_id("refactory", "circle_packing_loop") == "refactory/circle_packing_loop"
    for bad in ("Refactory", "with space", "", "with/slash"):
        with pytest.raises(packages.ModeIdError):
            packages.mode_id(bad, "loop")


def test_two_packages_can_own_the_same_short_name(tmp_path: Path) -> None:
    """The flat registry resolved a clash by silent precedence; packages do not."""
    packages.register(tmp_path, "refactory/loop", _graph(tmp_path, "a"))
    packages.register(tmp_path, "science/loop", _graph(tmp_path, "b"))
    assert packages.resolve(tmp_path, "refactory/loop") != packages.resolve(tmp_path, "science/loop")
    grouped = packages.catalogue(tmp_path)
    assert set(grouped) == {"refactory", "science"}
    assert [m["name"] for m in grouped["refactory"]] == ["loop"]


def test_registration_writes_a_discoverable_wrapper(tmp_path: Path) -> None:
    packages.register(tmp_path, "refactory/loop", _graph(tmp_path, "a"), description="d")
    wrapper = tmp_path / ".factory" / "workflows" / "refactory__loop.py"
    assert wrapper.exists()
    # Discovery globs *.py without recursing, so the package lives in the file
    # name rather than in a directory it would never look inside.
    assert "meta" in wrapper.read_text() and "def workflow()" in wrapper.read_text()


def test_disable_keeps_the_mode_but_takes_it_out_of_the_runnable_set(tmp_path: Path) -> None:
    packages.register(tmp_path, "refactory/loop", _graph(tmp_path, "a"))
    assert packages.set_enabled(tmp_path, "refactory/loop", False)
    assert packages.resolve(tmp_path, "refactory/loop") is None
    assert packages.enabled_ids(tmp_path) == []
    assert packages.set_enabled(tmp_path, "refactory/loop", True)
    assert packages.resolve(tmp_path, "refactory/loop") is not None


def test_remove_deletes_the_wrapper_and_the_entry(tmp_path: Path) -> None:
    packages.register(tmp_path, "refactory/loop", _graph(tmp_path, "a"))
    assert packages.remove(tmp_path, "refactory/loop")
    assert packages.resolve(tmp_path, "refactory/loop") is None
    assert not (tmp_path / ".factory" / "workflows" / "refactory__loop.py").exists()
    assert packages.remove(tmp_path, "refactory/loop") is False


def test_import_copies_rather_than_links(tmp_path: Path) -> None:
    source = _graph(tmp_path, "imported")
    identifier = packages.import_mode(tmp_path, source, package="science")
    assert identifier == "science/imported"
    source.unlink()  # the original going away must not break the imported mode
    assert packages.resolve(tmp_path, identifier) is not None


def test_the_listing_is_grouped_and_shows_disabled_modes(tmp_path: Path) -> None:
    packages.register(tmp_path, "refactory/loop", _graph(tmp_path, "a"), description="first")
    packages.register(tmp_path, "science/loop", _graph(tmp_path, "b"), description="second")
    packages.set_enabled(tmp_path, "science/loop", False)
    rendered = packages.render_catalogue(tmp_path)
    assert "refactory/" in rendered and "science/" in rendered
    assert "(disabled)" in rendered


def test_an_unregistered_project_lists_nothing(tmp_path: Path) -> None:
    assert packages.catalogue(tmp_path) == {}
    assert "no modes registered" in packages.render_catalogue(tmp_path)
