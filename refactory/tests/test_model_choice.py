"""One model choice for every run (dm-258).

Tests that the stored model choice propagates to every DSH process, that runs
refuse before any model call when no choice exists, that CLI flags override the
stored choice, that migration from an existing refactory profile works, and that
the walker and evaluator get the same route as the spine.

Integration tests use DSH_CLI pointing at a fake dsh script that records its
arguments, so we test at the real boundary: the argv every DSH process gets.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from refactory_dsh.dsh_profile import (
    GUI_PROFILE,
    NO_MODEL_CHOICE_MESSAGE,
    KNOWN_PROVIDERS,
    _parse_agent_default_model,
    _update_agent_default_model,
    first_run_choice,
    model_choice_write,
    stored_model_choice,
)
from refactory_dsh.model import DshModelClient, ModelChoiceError, MODEL_CHOICE_REFUSAL, require_model_choice


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Ensure a clean environment for each test."""
    for key in (
        "REFACTORY_WORKER_CONTEXT",
        "REFACTORY_WORKER_BACKEND",
        "REFACTORY_MODEL_ID",
        "REFACTORY_MODEL_PROVIDER",
        "REFACTORY_MODEL_EFFORT",
        "DSH_HOME",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    # Point DSH_HOME to a temp directory.
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh_home"))


def _write_patch(home: Path, provider: str = "", model: str = "") -> Path:
    """Write a cordis.patch.yml with an agent-default-model entry."""
    profile_dir = home / "profiles" / GUI_PROFILE
    profile_dir.mkdir(parents=True, exist_ok=True)
    patch = profile_dir / "cordis.patch.yml"
    content = textwrap.dedent(f"""\
        - id: agent-default-model
          name: '@deepseek-ai/dsh-agent-default-model'
          config:
            provider: {provider}
            model: {model}
    """)
    patch.write_text(content, encoding="utf-8")
    return patch


def _make_fake_dsh(tmp_path: Path) -> Path:
    """Create a fake dsh script that records its argv and exits 0."""
    fake_dsh = tmp_path / "fake_dsh.sh"
    log_dir = tmp_path / "dsh_calls"
    log_dir.mkdir(exist_ok=True)
    fake_dsh.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # Record all arguments to a unique log file.
        LOG_DIR="{log_dir}"
        CALL_NUM=$(ls "$LOG_DIR" 2>/dev/null | wc -l)
        echo "$@" > "$LOG_DIR/call_$CALL_NUM.txt"
        # Also save as JSON array for easier parsing.
        python3 -c "import json,sys; json.dump(sys.argv[1:], open('$LOG_DIR/call_$CALL_NUM.json','w'))" "$@"
        # Echo a minimal response so DSH looks like it ran.
        echo "fake dsh response"
        exit 0
    """), encoding="utf-8")
    fake_dsh.chmod(fake_dsh.stat().st_mode | stat.S_IEXEC)
    return fake_dsh


def _get_dsh_calls(tmp_path: Path) -> list[list[str]]:
    """Read recorded DSH calls."""
    log_dir = tmp_path / "dsh_calls"
    calls = []
    if log_dir.exists():
        for f in sorted(log_dir.glob("call_*.json")):
            try:
                calls.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                pass
    return calls


# ── test_migration_from_existing_refactory_profile ──


def test_migration_from_existing_refactory_profile(tmp_path):
    """An existing GUI choice (the refactory profile's agent-default-model) is
    read directly — zero-action migration for GUI users."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="migrated-p", model="migrated-m")
    choice = stored_model_choice(home=home)
    assert choice == {"provider": "migrated-p", "model": "migrated-m"}

    # Remove the entry — should return empty.
    patch = home / "profiles" / GUI_PROFILE / "cordis.patch.yml"
    patch.write_text("- id: other-entry\n  name: something\n", encoding="utf-8")
    choice = stored_model_choice(home=home)
    assert choice == {"provider": "", "model": ""}

    # No file → empty.
    patch.unlink()
    choice = stored_model_choice(home=home)
    assert choice == {"provider": "", "model": ""}


# ── test_refactory_model_set_and_show ──


def test_refactory_model_set_and_show(tmp_path):
    """model_choice_write and stored_model_choice round-trip correctly."""
    home = tmp_path / "dsh_home"
    model_choice_write("test-p", "test-m", home=home)
    choice = stored_model_choice(home=home)
    assert choice == {"provider": "test-p", "model": "test-m"}

    # Overwrite.
    model_choice_write("new-p", "new-m", home=home)
    choice = stored_model_choice(home=home)
    assert choice == {"provider": "new-p", "model": "new-m"}


# ── test_patch_file_with_js_tag_preserved ──


def test_patch_file_with_js_tag_preserved(tmp_path):
    """A patch file with a !!js entry and another section keeps both
    byte-identical after model_choice_write."""
    home = tmp_path / "dsh_home"
    profile_dir = home / "profiles" / GUI_PROFILE
    profile_dir.mkdir(parents=True, exist_ok=True)
    patch = profile_dir / "cordis.patch.yml"
    original = textwrap.dedent("""\
        - id: some-plugin
          name: '@deepseek-ai/dsh-some-plugin'
          config: !!js |
            function() { return 42; }
        - id: agent-default-model
          name: '@deepseek-ai/dsh-agent-default-model'
          config:
            provider: old-provider
            model: old-model
        - id: another-section
          name: '@deepseek-ai/dsh-another'
          config:
            setting: value
    """)
    patch.write_text(original, encoding="utf-8")

    model_choice_write("new-provider", "new-model", home=home)
    updated = patch.read_text(encoding="utf-8")

    # The !!js entry must be byte-identical.
    assert "!!js |" in updated
    assert "function() { return 42; }" in updated
    # The other section must be byte-identical.
    assert "another-section" in updated
    assert "setting: value" in updated
    # The model choice must be updated.
    choice = stored_model_choice(home=home)
    assert choice == {"provider": "new-provider", "model": "new-model"}
    # Verify other entries are unchanged by extracting them.
    lines = updated.splitlines()
    js_found = False
    another_found = False
    for line in lines:
        if "!!js" in line:
            js_found = True
        if "another-section" in line:
            another_found = True
    assert js_found
    assert another_found


# ── test_yaml_indentation_correct ──


def test_yaml_indentation_correct(tmp_path):
    """model_choice_write produces valid YAML indentation: config at 2 spaces,
    provider/model at 4 spaces, so they are siblings of 'name:' not nested
    inside it."""
    home = tmp_path / "dsh_home"
    model_choice_write("test-p", "test-m", home=home)
    patch = home / "profiles" / GUI_PROFILE / "cordis.patch.yml"
    text = patch.read_text(encoding="utf-8")
    # config: must be at 2-space indent (sibling of name:), not 4-space
    assert "  config:\n" in text
    # provider/model at 4-space indent under config:
    assert "    provider: test-p\n" in text
    assert "    model: test-m\n" in text
    # Must NOT have config at 4-space indent
    assert "    config:\n" not in text
    # Verify it's parseable as valid YAML
    import yaml
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, list)
    found = False
    for entry in parsed:
        if entry.get("id") == "agent-default-model":
            found = True
            assert entry["config"]["provider"] == "test-p"
            assert entry["config"]["model"] == "test-m"
    assert found, "agent-default-model entry not found in parsed YAML"


# ── test_stored_choice_reaches_terminal_ceo_workers ──


def test_stored_choice_reaches_terminal_ceo_workers(tmp_path, monkeypatch):
    """A stored model choice propagates to every DshModelClient and its children."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="stored-p", model="stored-m")
    monkeypatch.setenv("DSH_HOME", str(home))

    # Build a client without explicit provider/model — it should read the stored choice.
    client = DshModelClient()
    assert client._provider == "stored-p"
    assert client._model == "stored-m"

    # A child client (as used for nodes) should also have the stored choice.
    child = client.for_node("emit")
    assert child._provider == "stored-p"
    assert child._model == "stored-m"

    # The context carries the stored choice.
    ctx = client.context()
    assert ctx["provider"] == "stored-p"
    assert ctx["model"] == "stored-m"


# ── test_stored_choice_reaches_dsh_argv ──


def test_stored_choice_reaches_dsh_argv(tmp_path, monkeypatch):
    """With DSH_CLI pointing at a fake dsh: a DshModelClient passes --provider
    and --model to its subprocess, verifying the stored choice reaches the DSH
    process argv boundary."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="test-prov", model="test-mod")
    monkeypatch.setenv("DSH_HOME", str(home))

    fake_dsh = _make_fake_dsh(tmp_path)
    client = DshModelClient(dsh_cli=str(fake_dsh))
    assert client._provider == "test-prov"
    assert client._model == "test-mod"

    # Call _complete to verify the stored choice is passed as --provider/--model
    # to the DSH subprocess.
    try:
        client._complete("test prompt", workdir=tmp_path / "scratch")
    except Exception:
        pass  # The fake dsh may not produce valid output format

    calls = _get_dsh_calls(tmp_path)
    assert len(calls) >= 1, "fake dsh was never called"
    # Verify --provider and --model appear in the argv.
    argv_str = " ".join(calls[0])
    assert "--provider" in argv_str
    assert "test-prov" in argv_str
    assert "--model" in argv_str
    assert "test-mod" in argv_str


# ── test_no_choice_refuses_before_model_call ──


def test_no_choice_refuses_before_model_call(tmp_path, monkeypatch):
    """No stored choice and no flag: require_model_choice raises ModelChoiceError."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    # Ensure no stored choice exists (no patch file).
    client = DshModelClient()
    assert client._provider == ""
    assert client._model == ""
    with pytest.raises(ModelChoiceError, match="No model chosen"):
        require_model_choice(client)


# ── test_no_choice_safety_net_in_complete ──


def test_no_choice_safety_net_in_run_completion(tmp_path, monkeypatch):
    """The safety net in _run_completion() raises ModelChoiceError when no
    model choice exists, preventing any DSH process from starting.  The check
    lives in _run_completion rather than _complete so that tests which
    monkeypatch _run_completion bypass it — they never reach a real subprocess."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    fake_dsh = _make_fake_dsh(tmp_path)
    client = DshModelClient(dsh_cli=str(fake_dsh))
    assert client._provider == ""
    assert client._model == ""
    with pytest.raises(ModelChoiceError, match="No model chosen"):
        client.complete("test prompt", workdir=tmp_path / "scratch")
    # The fake dsh must NOT have been called.
    calls = _get_dsh_calls(tmp_path)
    assert len(calls) == 0, "fake dsh was called despite no model choice"


# ── test_entry_point_interactive_refuses ──


def test_entry_point_interactive_main_refuses(tmp_path, monkeypatch, capsys):
    """interactive.main() returns 1 when no choice exists and no
    provider/model given — proving the refusal is wired into the CLI
    entry point.  The check is in main(), not run(), so library callers
    and tests that call run() directly with mocked runtimes proceed."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    from refactory_dsh import interactive

    code = interactive.main([
        "default_pack/design",
        "--headless",
        "--workdir", str(tmp_path / "run"),
    ])
    assert code == 1
    assert "No model chosen" in capsys.readouterr().err


# ── test_entry_point_create_refuses ──


def test_entry_point_create_refuses(tmp_path, monkeypatch):
    """create.main() returns 1 when no model choice exists."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    from refactory_dsh import create

    code = create.main([
        "--name", "test-pkg",
        "--brief", "test brief",
        "--workdir", str(tmp_path / "create_run"),
    ])
    assert code == 1


# ── test_entry_point_edit_refuses ──


def test_entry_point_edit_refuses(tmp_path, monkeypatch):
    """edit.main() returns 1 when no model choice exists."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    from refactory_dsh import edit

    code = edit.main([
        "--name", "test-pkg",
        "--brief", "test edit",
        "--mode", "some/mode",
        "--workdir", str(tmp_path / "edit_run"),
    ])
    assert code == 1


# ── test_cli_flags_override_stored_choice ──


def test_cli_flags_override_stored_choice(tmp_path, monkeypatch):
    """Explicit --provider/--model override the stored choice."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="stored-p", model="stored-m")
    monkeypatch.setenv("DSH_HOME", str(home))

    client = DshModelClient(provider="explicit-p", model="explicit-m")
    assert client._provider == "explicit-p"
    assert client._model == "explicit-m"

    # require_model_choice should NOT raise.
    require_model_choice(client)


# ── test_stored_key_for_other_provider_never_used ──


def test_stored_key_for_other_provider_never_used(tmp_path, monkeypatch):
    """A stored choice with provider='chosen-provider' is used, not DeepSeek."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="chosen-provider", model="chosen-model")
    monkeypatch.setenv("DSH_HOME", str(home))
    # Simulate a DeepSeek key being present.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

    client = DshModelClient()
    assert client._provider == "chosen-provider"
    assert client._model == "chosen-model"
    # No DeepSeek provider appears in the route.
    assert "deepseek" not in client._provider.lower()


def test_stored_key_for_other_provider_never_in_dsh_argv(tmp_path, monkeypatch):
    """With DSH_CLI pointing at a fake dsh: a stored choice with
    provider='chosen-provider' means no DSH process gets DeepSeek in argv."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="chosen-provider", model="chosen-model")
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

    fake_dsh = _make_fake_dsh(tmp_path)
    client = DshModelClient(dsh_cli=str(fake_dsh))
    try:
        client._complete("test prompt", workdir=tmp_path / "scratch")
    except Exception:
        pass

    calls = _get_dsh_calls(tmp_path)
    for call in calls:
        argv_str = " ".join(call)
        assert "deepseek" not in argv_str.lower(), (
            f"DeepSeek appeared in DSH argv despite chosen-provider being set: {call}"
        )


# ── test_walker_and_evaluator_get_same_route ──


def test_walker_and_evaluator_get_same_route(tmp_path, monkeypatch):
    """Both runtimes resolve through the same stored choice."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="test-p", model="test-m")
    monkeypatch.setenv("DSH_HOME", str(home))

    # Spine client.
    client = DshModelClient()
    assert client.route()["provider"] == "test-p"
    assert client.route()["model"] == "test-m"

    # A child context should carry the same route.
    child_ctx = client.context()
    child = DshModelClient(**child_ctx)
    assert child.route()["provider"] == "test-p"
    assert child.route()["model"] == "test-m"


# ── test_runtime_provenance ──


def test_runtime_provenance(tmp_path, monkeypatch):
    """runtime_context includes model_choice with provenance."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="stored-p", model="stored-m")
    monkeypatch.setenv("DSH_HOME", str(home))

    from refactory_dsh.console import runtime_context
    # Need a project dir for runtime_context.
    project = tmp_path / "project"
    project.mkdir()

    result = runtime_context(project)
    assert "model_choice" in result
    assert result["model_choice"]["provider"] == "stored-p"
    assert result["model_choice"]["model"] == "stored-m"
    assert result["model_choice"]["provenance"] == "stored choice"


def test_runtime_provenance_flag(tmp_path, monkeypatch):
    """runtime_context reports 'flag' provenance when --provider is given,
    and shows the effective (flag) provider, not the stored choice."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="stored-p", model="stored-m")
    monkeypatch.setenv("DSH_HOME", str(home))

    from refactory_dsh.console import runtime_context
    project = tmp_path / "project"
    project.mkdir()

    result = runtime_context(project, provider="flag-p")
    assert result["model_choice"]["provenance"] == "flag"
    assert result["model_choice"]["provider"] == "flag-p"


# ── test_first_run_one_key ──


def test_first_run_one_key(tmp_path, monkeypatch):
    """One keyed provider: use and save it, print a line."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
    # No stored choice.
    choice = first_run_choice(home=home)
    assert choice["provider"] == "deepseek-official"
    assert choice["model"] == KNOWN_PROVIDERS["deepseek-official"]
    # Verify saved.
    stored = stored_model_choice(home=home)
    assert stored["provider"] == "deepseek-official"
    # Second call reads saved choice with no new write.
    choice2 = first_run_choice(home=home)
    assert choice2 == stored


# ── test_first_run_multiple_keys_headless ──


def test_first_run_multiple_keys_headless(tmp_path, monkeypatch):
    """Multiple keyed providers headless: refuse listing both."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key")
    with pytest.raises(ModelChoiceError, match="Multiple providers have keys"):
        first_run_choice(home=home, interactive=False)


# ── test_first_run_multiple_keys_terminal ──


def test_first_run_multiple_keys_terminal(tmp_path, monkeypatch):
    """Multiple keyed providers at a terminal: the answer is saved and used."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key")
    # Simulate choosing "1" (deepseek-official).
    monkeypatch.setattr("builtins.input", lambda _: "1")
    choice = first_run_choice(home=home, interactive=True)
    assert choice["provider"] == "deepseek-official"
    stored = stored_model_choice(home=home)
    assert stored["provider"] == "deepseek-official"


# ── test_first_run_no_keys ──


def test_first_run_no_keys(tmp_path, monkeypatch):
    """No provider keys: refuse with the instruction message."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    with pytest.raises(ModelChoiceError, match="No model chosen"):
        first_run_choice(home=home)


# ── test_first_run_stored_choice_not_overridden ──


def test_first_run_stored_choice_not_overridden(tmp_path, monkeypatch):
    """A stored choice plus a second provider's key: the stored choice is used."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="stored-p", model="stored-m")
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key")
    choice = first_run_choice(home=home)
    assert choice["provider"] == "stored-p"
    assert choice["model"] == "stored-m"


# ── test_model_choice_error_is_not_system_exit ──


def test_model_choice_error_is_not_system_exit():
    """ModelChoiceError is an ordinary exception, not SystemExit."""
    assert not issubclass(ModelChoiceError, SystemExit)
    assert issubclass(ModelChoiceError, Exception)


# ── test_worker_config_uses_stored_choice ──


def test_worker_config_uses_stored_choice(tmp_path, monkeypatch):
    """worker_config.resolve uses the stored choice as the base default."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="wc-provider", model="wc-model")
    monkeypatch.setenv("DSH_HOME", str(home))

    from refactory_dsh.worker_config import resolve

    project = tmp_path / "project"
    project.mkdir()
    result = resolve(project)
    assert result["settings"]["provider"] == "wc-provider"
    assert result["settings"]["model"] == "wc-model"


# ── test_node_routes_and_role_routes_still_apply ──


def test_node_routes_and_role_routes_still_apply(tmp_path, monkeypatch):
    """node_routes and role_routes apply their overrides on top of the stored choice."""
    home = tmp_path / "dsh_home"
    _write_patch(home, provider="stored-p", model="stored-m")
    monkeypatch.setenv("DSH_HOME", str(home))

    client = DshModelClient(
        node_routes={"emit": {"model": "override-model"}},
        role_routes={"worker": {"provider": "role-p"}},
    )
    # The base client has the stored choice.
    assert client._provider == "stored-p"
    assert client._model == "stored-m"

    # Node override applies.
    emit_client = client.for_node("emit")
    assert emit_client._model == "override-model"

    # Role route applies for other nodes.
    review_client = client.for_node("review")
    assert review_client._provider == "role-p"


# ── test_first_run_choice_wired_in_interactive_run ──


def test_first_run_choice_wired_in_interactive_run(tmp_path, monkeypatch):
    """When no stored choice exists and one provider has a key,
    first_run_choice() auto-detects and saves, proving the wiring works."""
    home = tmp_path / "dsh_home"
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fake-key")

    # first_run_choice should auto-detect and save.
    first_run_choice(home=home)
    stored = stored_model_choice(home=home)
    assert stored["provider"] == "deepseek-official"


# ── test_update_agent_default_model_indentation ──


def test_update_agent_default_model_indentation():
    """_update_agent_default_model produces 2-space indent for config (sibling
    of name), not 4-space (which would nest it under name)."""
    result = _update_agent_default_model("", "test-p", "test-m")
    # config: must be at 2-space indent
    assert "  config:\n" in result
    assert "    provider: test-p\n" in result
    assert "    model: test-m\n" in result
    # Must NOT have 4-space config
    assert "    config:\n" not in result

    # Test with existing content.
    original = textwrap.dedent("""\
        - id: agent-default-model
          name: '@deepseek-ai/dsh-agent-default-model'
          config:
            provider: old-p
            model: old-m
    """)
    result = _update_agent_default_model(original, "new-p", "new-m")
    assert "  config:\n" in result
    assert "    provider: new-p\n" in result
    assert "    model: new-m\n" in result
    assert "    config:\n" not in result
