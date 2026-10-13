"""No-model checks for an isolated native reference environment."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

NATIVE_ENVIRONMENT_VERSION = "private-native-home-v1"


def environment_mapping(project: Path) -> dict[str, Any]:
    return {"version": NATIVE_ENVIRONMENT_VERSION,
            "native_home": str(project / "runtime/native-home"),
            "claude_config_dir": str(project / "runtime/claude"),
            "factory_globals": "Private empty native home; shipped source prompts and defaults. Original user .factory config, prompts, playbooks and workflow state are excluded.",
            "native_setting_env": {"FACTORY_RUNNER": "claude"},
            "excluded_native_setting_env": ["FACTORY_PLAYBOOKS_DIR", "FACTORY_PROFILE"],
            "credential_references": [],
            "auth_boundary": "Inherited authentication environment is retained; no credential files, secrets or original native settings are copied. Additional file references require explicit scoped configuration."}


def apply_environment(env: dict[str, str], mapping: dict[str, Any], project: Path) -> dict[str, str]:
    """Configure the native process before its import-time Path.home constants."""
    if mapping.get("version") != NATIVE_ENVIRONMENT_VERSION:
        raise ValueError("Unknown remote native environment mapping")
    expected = environment_mapping(project)
    for key in ("native_home", "claude_config_dir"):
        if mapping.get(key) != expected[key] or not Path(mapping[key]).resolve().is_relative_to(project.resolve()):
            raise ValueError(f"Remote private native environment escapes its project: {key}")
    configured = {key: value for key, value in env.items()
                  if key not in expected["excluded_native_setting_env"]}
    return {**configured, **expected["native_setting_env"], "HOME": mapping["native_home"],
            "CLAUDE_CONFIG_DIR": mapping["claude_config_dir"]}


def probe(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Inspect real source resolution and native auth status under lane policy.

    Auth output is allowlisted: neither prompt bodies, settings, environment
    values nor credential contents enter the receipt. No model is dispatched.
    """
    project = Path(config["project"])
    mapping = config["native_environment_mapping"]
    isolated = apply_environment(env, mapping, project)
    if config.get("native_temp_mapping"):
        from .native_temp import apply_environment as native_temp_environment
        isolated = native_temp_environment(isolated, config["native_temp_mapping"])
    if config.get("native_credential_mapping"):
        from .native_credentials import apply_environment as native_credential_environment
        isolated = native_credential_environment(isolated, config["native_credential_mapping"])
    code = r'''
import hashlib, json, os, subprocess, sys
from pathlib import Path
project=Path(sys.argv[1]); result={}
def attempt(name, action):
    try: result[name]={'status':'passed',**action()}
    except Exception as error: result[name]={'status':'failed','exception_type':type(error).__name__}
def prompt():
    from factory.agents.runner import resolve_prompt, _PROMPTS_DIR, _USER_PROMPTS_DIR
    from factory.user_config import CONFIG_PATH
    from factory.ace.paths import DEFAULTS_DIR
    from factory.ace.injector import inject_playbook
    body=resolve_prompt('ceo',project)
    shipped=(_PROMPTS_DIR/'ceo.md').read_text()
    playbook=DEFAULTS_DIR/'ceo.md'
    expected=inject_playbook(shipped,playbook.read_text().strip()) if playbook.is_file() else shipped
    if body != expected: raise ValueError('Prompt contains an unreviewed override or playbook')
    if not CONFIG_PATH.resolve().is_relative_to(Path.home().resolve()): raise ValueError('Global config escaped native home')
    return {'prompt_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'shipped_prompt_sha256':hashlib.sha256(shipped.encode()).hexdigest(),
            'shipped_playbook':str(playbook),'shipped_playbook_sha256':hashlib.sha256(playbook.read_bytes()).hexdigest() if playbook.is_file() else None,
            'shipped_prompt':str(_PROMPTS_DIR/'ceo.md'), 'user_prompt_root':str(_USER_PROMPTS_DIR),
            'native_config':str(CONFIG_PATH),'native_config_present':CONFIG_PATH.exists()}
def runner():
    from factory.runners import get_runner
    selected=get_runner()
    return {'name':selected.name,'type':type(selected).__name__}
def auth():
    cp=subprocess.run(['claude','auth','status','--json'],capture_output=True,text=True,timeout=20)
    parsed=json.loads(cp.stdout)
    safe={key:parsed.get(key) for key in ('loggedIn','authMethod','apiProvider')}
    safe['returncode']=cp.returncode
    if cp.returncode != 0 or safe['loggedIn'] is not True: raise ValueError('Native authentication not reported ready')
    return safe
attempt('ceo_prompt',prompt); attempt('runner',runner); attempt('native_auth_status',auth)
result['native_home']=str(Path.home()); result['claude_config_dir']=os.environ.get('CLAUDE_CONFIG_DIR')
result['claude_native_tmp']=os.environ.get('CLAUDE_CODE_TMPDIR')
result['model_dispatched']=False
print(json.dumps(result))
'''
    argv = ["/usr/bin/sandbox-exec", "-f", config["policy"], config["python"], "-B", "-c", code, str(project)]
    result = subprocess.run(argv, cwd=project, env=isolated, capture_output=True, text=True, timeout=30)
    try:
        observed = json.loads(result.stdout)
    except ValueError:
        observed = {"status": "failed", "reason": "Native readiness returned no structured receipt"}
    passed = result.returncode == 0 and all(observed.get(key, {}).get("status") == "passed"
        for key in ("ceo_prompt", "runner", "native_auth_status"))
    return {"schema_version": 1, "kind": "isolated native readiness without model execution",
            "status": "passed" if passed else "failed", "returncode": result.returncode,
            "mapping": mapping, "credential_mapping": config.get("native_credential_mapping"),
            "temp_mapping": config.get("native_temp_mapping"), "policy": config["policy"], "observed": observed,
            "limits": "Auth status reports configuration/login readiness; no API credential refresh, served model or authoring success is established."}


def invocation_probe(config: dict[str, Any], project: Path) -> dict[str, Any]:
    """One real native CEO request, with tools disabled and a small budget.

    The supported FACTORY_CLAUDE_BIN override adds probe-only CLI restrictions
    while retaining the same executable, runner, provider and source alias.
    Authoring never uses this wrapper. Native writes stay in the private probe.
    """
    import asyncio
    import hashlib
    import os
    import re
    import shutil
    from .native_temp import validate
    validate(config["native_temp_mapping"])
    from factory.agents.runner import invoke_agent
    from factory.workflow.primitives import DEFAULT_AGENT_POOL

    directory = project / "native-route-probe"
    directory.mkdir(exist_ok=False)
    native = Path(shutil.which("claude") or "")
    if not native.is_file():
        raise ValueError("Native Claude executable is unavailable")
    wrapper = directory / "native-probe-cli"
    code = (f"#!{config['python']}\nimport os,sys\n"
            f"os.execv({str(native)!r},[{str(native)!r},'--tools','','--max-turns','1',"
            "'--max-budget-usd','0.10',*sys.argv[1:]])\n")
    wrapper.write_text(code)
    wrapper.chmod(0o700)
    previous = os.environ.get("FACTORY_CLAUDE_BIN")
    os.environ["FACTORY_CLAUDE_BIN"] = str(wrapper)
    alias = DEFAULT_AGENT_POOL["ceo"].model
    try:
        response, returncode = asyncio.run(invoke_agent("ceo",
            "Respond with exactly NATIVE_ROUTE_OK. Do not call any tools, read credentials or modify files. This is only a native invocation readiness probe.",
            directory, timeout=20, model=alias, dangerously_skip_permissions=False,
            _track_failures=False))
    finally:
        if previous is None:
            os.environ.pop("FACTORY_CLAUDE_BIN", None)
        else:
            os.environ["FACTORY_CLAUDE_BIN"] = previous
    marker = response.strip() == "NATIVE_ROUTE_OK"
    # Keep diagnostic text out of the receipt: provider errors can contain
    # account identifiers. Exact native stderr remains in the private probe log.
    lowered = response.lower()
    categories = [label for label, pattern in (
        ("budget-limit", r"budget|cost limit|spending limit"),
        ("missing-adc", r"default credentials|application.default.credentials"),
        ("authentication", r"authenticate|authentication|unauthorized|credential"),
        ("permission", r"eperm|permission|access denied|403"),
        ("model-alias", r"model.*not found|unsupported model|invalid model"),
    ) if re.search(pattern, lowered)]
    events = directory / ".factory/events.jsonl"
    completed = [json.loads(line) for line in events.read_text().splitlines()
                 if json.loads(line).get("type") == "agent.completed"] if events.is_file() else []
    return {"schema_version": 1, "kind": "real native invocation preflight; not workflow authoring",
            "status": "passed" if returncode == 0 and marker else "failed",
            "returncode": returncode, "marker_received": marker,
            "requested_alias": alias, "runner": "claude", "provider_policy": "unchanged inherited native configuration",
            "tools": [], "max_turns": 1, "budget_usd": 0.10, "inactivity_timeout_s": 20,
            "diagnostic_categories": categories,
            "native_cli": {"path": str(native.resolve()), "sha256": hashlib.sha256(native.read_bytes()).hexdigest()},
            "probe_wrapper_sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            "native_completed_usage": [{key: row.get("data", {}).get(key) for key in
                ("model", "input_tokens", "output_tokens", "total_cost_usd")} for row in completed],
            "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
            "limits": "A passing probe establishes this one native alias invocation only; it does not establish other roles or Create acceptance."}
