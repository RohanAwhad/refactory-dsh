"""Short, owned native temp roots with explicit identity and cleanup."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile
import subprocess
import json
from typing import Any

VERSION = "private-native-temp-v1"


def create(trial_id: str, lane_id: str) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="cf-", dir="/private/tmp"))
    root.chmod(0o700)
    observed = root.stat()
    (root / "isolation-canary.txt").write_text("private native temp\n")
    return {"version": VERSION, "path": str(root), "trial_id": trial_id, "lane_id": lane_id,
            "identity": {"uid": observed.st_uid, "device": observed.st_dev,
                         "inode": observed.st_ino, "mode": stat.S_IMODE(observed.st_mode)},
            "environment": "CLAUDE_CODE_TMPDIR", "shared_global_tmp": False,
            "cleanup": "Explicit controller cleanup after lane owners settle; identity checked before removal"}


def validate(mapping: dict[str, Any]) -> Path:
    if mapping.get("version") != VERSION:
        raise ValueError("Unknown native temp mapping")
    root = Path(mapping["path"])
    if (root.parent != Path("/private/tmp") or not root.name.startswith("cf-")
            or root.is_symlink() or len(str(root).encode()) > 48):
        raise ValueError("Native temp root is not a short private owned directory")
    observed = root.stat()
    actual = {"uid": observed.st_uid, "device": observed.st_dev,
              "inode": observed.st_ino, "mode": stat.S_IMODE(observed.st_mode)}
    if not root.is_dir() or actual != mapping.get("identity") or actual["uid"] != os.getuid() or actual["mode"] != 0o700:
        raise ValueError("Native temp identity or permissions changed")
    return root


def apply_environment(env: dict[str, str], mapping: dict[str, Any]) -> dict[str, str]:
    return {**env, "CLAUDE_CODE_TMPDIR": str(validate(mapping))}


def cleanup(mapping: dict[str, Any]) -> dict[str, Any]:
    root = validate(mapping)
    shutil.rmtree(root)
    return {"status": "removed", "mapping": mapping, "exists_after": root.exists()}


def probe(config: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Actual positive own-temp access and negative forbidden read/write controls."""
    root = validate(config["native_temp_mapping"])
    forbidden = config.get("forbidden_controls", [])
    global_control = Path("/private/tmp") / ("native-global-denial-" + root.name)
    code = r'''
import json,sys
from pathlib import Path
own,forbidden,global_path=json.loads(sys.argv[1]); own=Path(own)
positive=own/'native-positive-control'; positive.write_text('owned'); assert positive.read_text()=='owned'; positive.unlink()
checks=[]
for name in forbidden:
    for operation in (('read','write') if Path(name).name=='isolation-canary.txt' else ('read',)):
        try:
            Path(name).read_bytes() if operation=='read' else Path(name).write_text('forbidden control')
            denied=False
        except PermissionError: denied=True
        checks.append({'path':name,'operation':operation,'denied':denied})
try: Path(global_path).write_text('forbidden global control'); denied=False
except PermissionError: denied=True
checks.append({'target':'shared-global-tmp','operation':'write','denied':denied})
print(json.dumps({'checks':checks,'passed':bool(forbidden) and all(row['denied'] for row in checks)}))
'''
    result = subprocess.run(["/usr/bin/sandbox-exec", "-f", config["policy"], config["python"], "-B", "-c", code,
        json.dumps([str(root), forbidden, str(global_control)])], cwd=config["project"], env=env,
        capture_output=True, text=True, timeout=15)
    if global_control.is_file():
        global_control.unlink()
    try:
        observed = json.loads(result.stdout)
    except ValueError:
        observed = {"passed": False, "reason": "No structured native temp control output"}
    return {"status": "passed" if result.returncode == 0 and observed.get("passed") else "unavailable",
            "observed": observed, "mapping": config["native_temp_mapping"], "returncode": result.returncode}
