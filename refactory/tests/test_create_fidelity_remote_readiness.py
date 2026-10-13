"""Native configuration isolation tests; these do not dispatch models."""
import json
from pathlib import Path

import pytest

from benchmarks.create_fidelity import harness
from benchmarks.create_fidelity import lane
from benchmarks.create_fidelity.remote_readiness import apply_environment, environment_mapping


def test_private_native_home_keeps_auth_environment_but_excludes_global_factory_settings(tmp_path):
    original = {"HOME": "/original/home", "CLAUDE_CONFIG_DIR": "/original/claude",
                "FACTORY_PLAYBOOKS_DIR": "/original/evolved", "FACTORY_PROFILE": "global-profile",
                "FACTORY_RUNNER": "global-runner", "CLAUDE_CODE_USE_VERTEX": "1",
                "GOOGLE_APPLICATION_CREDENTIALS": "/explicit/auth/reference", "PATH": "/native/bin"}
    mapping = environment_mapping(tmp_path)
    configured = apply_environment(original, mapping, tmp_path)
    assert configured["HOME"] == str(tmp_path / "runtime/native-home")
    assert configured["CLAUDE_CONFIG_DIR"] == str(tmp_path / "runtime/claude")
    assert configured["FACTORY_RUNNER"] == "claude"
    assert "FACTORY_PLAYBOOKS_DIR" not in configured and "FACTORY_PROFILE" not in configured
    assert configured["GOOGLE_APPLICATION_CREDENTIALS"] == original["GOOGLE_APPLICATION_CREDENTIALS"]
    assert configured["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert original["HOME"] == "/original/home"
    assert mapping["credential_references"] == []
    assert "/explicit/auth/reference" not in json.dumps(mapping)


@pytest.mark.parametrize("key", ["native_home", "claude_config_dir"])
def test_native_environment_refuses_external_or_symlinked_state_roots(tmp_path, key):
    project = tmp_path / "project"
    project.mkdir()
    mapping = environment_mapping(project)
    bad = {**mapping, key: str(tmp_path / "shared")}
    with pytest.raises(ValueError, match="escapes"):
        apply_environment({}, bad, project)
    root = Path(mapping[key])
    root.parent.mkdir(parents=True, exist_ok=True)
    root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        apply_environment({}, mapping, project)


def test_native_home_is_applied_before_remote_lane_process_imports_source(tmp_path, monkeypatch):
    project = tmp_path / "remote-reference/project"
    project.mkdir(parents=True)
    source = tmp_path / "frozen-source"
    source.mkdir()
    identity = {"declared_revision": "controlled"}
    mapping = environment_mapping(project)
    config = {"source": str(source), "project": str(project), "identity": identity,
              "runtime_identity": None, "adapter_root": str(tmp_path / "frozen-adapter"),
              "python": "/native/python", "policy": str(tmp_path / "policy.sb"),
              "total_timeout_s": 60, "shutdown_grace_s": 1,
              "native_environment_mapping": mapping,
              "runtime_state_isolation": {"dsh_home": str(project / "runtime/dsh"),
                  "native_home": mapping["native_home"], "claude_config_dir": mapping["claude_config_dir"],
                  "private_tmp": str(project / "tmp")}}
    (project.parent / "config.json").write_text(json.dumps(config))
    monkeypatch.setattr(harness, "source_identity", lambda *args: identity)
    captured = []

    def native_command(*args, **kwargs):
        captured.append(kwargs["env"])
        return {"status": "completed", "returncode": 0}

    monkeypatch.setattr(harness, "execute_command", native_command)
    assert harness.run_lane(tmp_path, "remote-reference", "author")["status"] == "completed"
    assert captured[0]["HOME"] == mapping["native_home"]
    assert captured[0]["CLAUDE_CONFIG_DIR"] == mapping["claude_config_dir"]


def test_unknown_native_environment_mapping_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown remote"):
        apply_environment({}, {**environment_mapping(tmp_path), "version": "unreviewed"}, tmp_path)


def test_remote_adapter_rejects_unconfigured_home_before_any_native_import(tmp_path):
    with pytest.raises(ValueError, match="before source imports"):
        lane.remote_author({"native_environment_mapping": environment_mapping(tmp_path)}, tmp_path)
