"""Private temp identity and cleanup controls, without native model calls."""
import json
from pathlib import Path

import pytest

from benchmarks.create_fidelity import native_temp
from benchmarks.create_fidelity.isolation import profile


def test_native_temp_is_short_owned_and_cleans_only_exact_recorded_root():
    mapping = native_temp.create("controlled-trial", "controlled-lane")
    root = native_temp.validate(mapping)
    try:
        assert mapping["identity"]["mode"] == 0o700
        assert len(str(root).encode()) <= 48
        assert native_temp.apply_environment({}, mapping)["CLAUDE_CODE_TMPDIR"] == str(root)
        with pytest.raises(ValueError, match="identity"):
            native_temp.cleanup({**mapping, "identity": {**mapping["identity"], "inode": -1}})
        assert root.is_dir()
    finally:
        result = native_temp.cleanup(mapping)
    assert result["status"] == "removed" and not root.exists()


def test_native_temp_refuses_permission_change():
    mapping = native_temp.create("controlled-trial", "controlled-lane")
    root = Path(mapping["path"])
    try:
        root.chmod(0o755)
        with pytest.raises(ValueError, match="permissions"):
            native_temp.validate(mapping)
    finally:
        root.chmod(0o700)
        native_temp.cleanup(mapping)


def test_linux_allocation_and_cleanup_need_no_macos_directory(monkeypatch):
    monkeypatch.setattr(native_temp.sys, "platform", "linux")
    mapping = native_temp.create("linux-controlled-trial", "controlled-lane")
    root = Path(mapping["path"])
    try:
        assert root.parent == Path("/tmp")
        assert native_temp.validate(mapping) == root
        assert mapping["identity"]["mode"] == 0o700
        assert (root / "isolation-canary.txt").read_text() == "private native temp\n"
        assert native_temp.apply_environment({}, mapping)["CLAUDE_CODE_TMPDIR"] == str(root)
        with pytest.raises(ValueError, match="identity"):
            native_temp.cleanup({**mapping, "identity": {**mapping["identity"], "inode": -1}})
        assert root.is_dir()
        # Allocation is usable for preparation; it must not certify isolation.
        monkeypatch.setattr(native_temp.subprocess, "run", lambda *a, **kw: pytest.fail("No native probe may launch without Seatbelt"))
        result = native_temp.probe({"native_temp_mapping": mapping}, {})
        assert result["status"] == "unavailable" and "Seatbelt" in result["reason"]
    finally:
        native_temp.cleanup(mapping)
    assert not root.exists()


def test_temp_policy_has_own_write_and_peer_operator_read_write_denials(tmp_path):
    project, own, peer, operator = (tmp_path / key for key in ("project", "own", "peer", "operator"))
    policy = profile(project, [peer], [], [operator], allowed_reads=[own], writable_roots=[own])
    assert f'(allow file-write* (subpath {json.dumps(str(own))}))' in policy
    for path in (peer, operator):
        assert f'(deny file-read* (subpath {json.dumps(str(path))}))' in policy
        assert f'(deny file-write* (subpath {json.dumps(str(path))}))' in policy
    assert '(deny file-write* (require-all (subpath "/private/tmp")' in policy
