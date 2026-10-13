"""Credential references use metadata and exact file policy; no secret copying."""
from pathlib import Path

import pytest

from benchmarks.create_fidelity.native_credentials import reference, apply_environment
from benchmarks.create_fidelity.isolation import profile


def test_existing_reference_is_exact_metadata_without_reading_or_copying(tmp_path, monkeypatch):
    file = tmp_path / "credential.json"
    file.write_text("controlled fixture content")
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("credential content inspected"))
    monkeypatch.setattr(Path, "read_bytes", lambda *args, **kwargs: pytest.fail("credential content inspected"))
    mapping = reference(file)
    assert mapping["path"] == str(file)
    assert mapping["access"] == "exact-file-read-only"
    assert apply_environment({}, mapping)["GOOGLE_APPLICATION_CREDENTIALS"] == str(file)
    assert set(tmp_path.iterdir()) == {file}


def test_reference_rejects_symlink_missing_and_overbroad_access(tmp_path):
    file = tmp_path / "credential.json"
    file.write_text("controlled fixture")
    link = tmp_path / "link.json"
    link.symlink_to(file)
    for bad in (link, tmp_path / "missing.json", tmp_path):
        with pytest.raises(ValueError, match="nonsymlink file"):
            reference(bad)
    with pytest.raises(ValueError, match="overbroad"):
        apply_environment({}, {**reference(file), "access": "whole-home"})


def test_policy_uses_literal_single_file_and_forbids_credential_write(tmp_path):
    file = tmp_path / "gcloud/credential.json"
    policy = profile(tmp_path / "project", [], [], [], allowed_reads=[], writable_roots=[], readonly_files=[file])
    assert f'(allow file-read* (literal "{file}"))' in policy
    assert f'(deny file-write* (literal "{file}"))' in policy
    assert f'(require-not (literal "{file}"))' in policy
    assert f'(subpath "{file.parent}")' not in policy
