"""Strict generated identity is separate from generic source/raw inventories."""
import os

import pytest

from benchmarks.create_fidelity.artifact_inventory import strict_artifact_inventory
from benchmarks.create_fidelity.evidence import hash_tree


def test_strict_generated_identity_includes_authored_cache_data_and_all_generated_records(tmp_path):
    names = ["code.py", "__pycache__/authored-fixture.json", "__pycache__/code.pyc",
             ".pytest_cache/v/cache/authored-test.json", ".git/authored-data.json",
             "SKILL.md", ".origin.json", ".native-admission.json"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original " + name)
    original = strict_artifact_inventory(tmp_path)
    assert set(original) == set(names)
    # Preserve the established generic tree semantics for source/runtime/raw.
    generic = hash_tree(tmp_path)
    marker = tmp_path / "__pycache__/authored-fixture.json"
    marker.write_text("changed authored data")
    assert strict_artifact_inventory(tmp_path) != original
    assert hash_tree(tmp_path) == generic


@pytest.mark.parametrize("link_kind", ["file", "directory", "root"])
def test_generated_inventory_rejects_links_instead_of_omitting_implementation(tmp_path, link_kind):
    root = tmp_path / "artifact"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "code.py").write_text("unrelated implementation")
    if link_kind == "root":
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
    else:
        (root / "linked").symlink_to(outside if link_kind == "directory" else outside / "code.py",
                                    target_is_directory=link_kind == "directory")
    with pytest.raises(ValueError, match="symlink"):
        strict_artifact_inventory(root)


def test_generated_inventory_rejects_special_files_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "named-pipe")
    with pytest.raises(ValueError, match="nonregular"):
        strict_artifact_inventory(tmp_path)


@pytest.mark.skipif(os.getuid() == 0, reason="Root bypasses directory mode permissions")
def test_complete_artifact_freeze_does_not_silently_skip_unreadable_cache_directory(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "authored-contract.json").write_text("private authored contract")
    cache.chmod(0)
    try:
        with pytest.raises(PermissionError):
            strict_artifact_inventory(tmp_path)
    finally:
        cache.chmod(0o700)
