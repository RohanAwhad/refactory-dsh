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
            "mapping": mapping, "policy": config["policy"], "observed": observed,
            "limits": "Auth status reports configuration/login readiness; no API credential refresh, served model or authoring success is established."}
