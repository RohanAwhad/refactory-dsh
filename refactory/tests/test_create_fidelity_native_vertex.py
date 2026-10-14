"""Explicit native Vertex pins are shared by readiness and real lane launch."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.create_fidelity import harness, lane, remote_readiness
from benchmarks.create_fidelity.native_vertex import from_settings, apply_environment, validate_process


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "unrelated-user-default", "hooks": {"private": "not-imported"},
        "env": {"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_VERTEX_PROJECT_ID": "configured-project",
            "CLOUD_ML_REGION": "global", "ANTHROPIC_DEFAULT_OPUS_MODEL": "configured-opus",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "configured-sonnet", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "configured-haiku",
            "ANTHROPIC_API_KEY": "fixture-secret-not-imported", "UNRELATED_SECRET": "also-not-imported"}}))
    return path


def test_settings_pin_only_selected_routing_independent_of_subsequent_user_edits(settings):
    mapping = from_settings(settings)
    serialized = json.dumps(mapping)
    for excluded in ("fixture-secret", "UNRELATED_SECRET", "not-imported", "unrelated-user-default"):
        assert excluded not in serialized
    settings.unlink()
    env = apply_environment({"PATH": "/bin", "GOOGLE_APPLICATION_CREDENTIALS": "/exact/reference",
        "ANTHROPIC_VERTEX_PROJECT_ID": "wrong-inherited-project", "ANTHROPIC_API_KEY": "not-used",
        "ANTHROPIC_AUTH_TOKEN": "not-used", "ANTHROPIC_BASE_URL": "https://not-used.invalid",
        "ANTHROPIC_MODEL": "not-used", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_FOUNDRY": "1",
        "CLAUDE_CODE_OAUTH_TOKEN": "not-used", "CLAUDE_CODE_SUBAGENT_MODEL": "not-used"}, mapping)
    assert env == {"PATH": "/bin", "GOOGLE_APPLICATION_CREDENTIALS": "/exact/reference", **mapping["environment"]}
    validate_process(env, mapping)
    with pytest.raises(ValueError, match="before source imports"):
        validate_process({**env, "ANTHROPIC_API_KEY": "not-used"}, mapping)
    # A pinned alias target does not impose a single model over source role aliases.
    assert "ANTHROPIC_MODEL" not in env


def test_settings_and_frozen_mapping_fail_closed(settings):
    mapping = from_settings(settings)
    tampered = {**mapping, "environment": {**mapping["environment"], "CLOUD_ML_REGION": "changed"}}
    with pytest.raises(ValueError, match="pin changed"):
        apply_environment({}, tampered)
    with pytest.raises(ValueError, match="allowlisted"):
        apply_environment({}, {**mapping, "environment": {**mapping["environment"], "ANTHROPIC_API_KEY": "bad"}})
    with pytest.raises(ValueError, match="Unknown"):
        apply_environment({}, {**mapping, "version": "unknown"})
    for edit in ({"CLAUDE_CODE_USE_VERTEX": "0"}, {"CLOUD_ML_REGION": ""}, {"CLOUD_ML_REGION": 1}):
        settings.write_text(json.dumps({"env": {**mapping["environment"], **edit}}))
        with pytest.raises(ValueError):
            from_settings(settings)
    settings.write_text(json.dumps({"env": {"CLAUDE_CODE_USE_VERTEX": "1"}}))
    with pytest.raises(ValueError, match="allowlisted"):
        from_settings(settings)
    link = settings.with_name("linked.json")
    link.symlink_to(settings)
    with pytest.raises(ValueError, match="nonsymlink"):
        from_settings(link)


def test_probe_and_full_launch_use_identical_routing_before_native_imports(tmp_path, settings, monkeypatch):
    project = tmp_path / "remote-reference/project"
    project.mkdir(parents=True)
    source = tmp_path / "source"
    source.mkdir()
    mapping = from_settings(settings)
    settings.unlink()  # Isolated execution neither needs nor reads the user settings.
    identity = {"declared_revision": "fixture"}
    native_home = remote_readiness.environment_mapping(project)
    config = {"source": str(source), "project": str(project), "identity": identity,
        "runtime_identity": None, "adapter_root": str(tmp_path / "adapter"), "python": "/native/python",
        "policy": str(tmp_path / "policy.sb"), "total_timeout_s": 60, "shutdown_grace_s": 1,
        "native_environment_mapping": native_home, "native_vertex_mapping": mapping,
        "runtime_state_isolation": {"dsh_home": str(project / "runtime/dsh"),
            "claude_config_dir": native_home["claude_config_dir"], "private_tmp": str(project / "tmp")}}
    (project.parent / "config.json").write_text(json.dumps(config))
    monkeypatch.setattr(harness, "source_identity", lambda *args: identity)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-inherited-secret")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "wrong-inherited-project")
    launched = []
    monkeypatch.setattr(harness, "execute_command", lambda *args, **kw: launched.append(kw["env"]) or {"status": "completed"})
    harness.run_lane(tmp_path, "remote-reference", "author")
    harness.run_lane(tmp_path, "remote-reference", "native-probe")
    observed = {key: {"status": "passed"} for key in ("ceo_prompt", "runner", "native_auth_status")}
    def fake_run(*args, **kw):
        launched.append(kw["env"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(observed))
    monkeypatch.setattr(remote_readiness.subprocess, "run", fake_run)
    receipt = remote_readiness.probe(config, dict(launched[0]))
    assert receipt["status"] == "passed"
    for env in launched:
        validate_process(env, mapping)
        assert {key: env[key] for key in mapping["environment"]} == mapping["environment"]
        assert env["HOME"] == native_home["native_home"]
    with pytest.raises(ValueError, match="before source imports"):
        lane.remote_author({"native_vertex_mapping": mapping}, project)
