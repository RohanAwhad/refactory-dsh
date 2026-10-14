"""Pin explicitly selected Claude Code Vertex routing without importing user state."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

VERSION = "configured-native-vertex-v1"
ENVIRONMENT_KEYS = (
    "CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)
_EXCLUDED_KEYS = {
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SUBAGENT_MODEL",
}


def _digest(environment: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_environment(environment: Any) -> dict[str, str]:
    if not isinstance(environment, dict) or set(environment) != set(ENVIRONMENT_KEYS):
        raise ValueError("Native Vertex mapping requires exactly the allowlisted routing fields")
    if any(not isinstance(value, str) or not value.strip() or value != value.strip()
           or any(ord(char) < 32 for char in value) for value in environment.values()):
        raise ValueError("Native Vertex routing fields must be nonempty strings without control characters")
    if environment["CLAUDE_CODE_USE_VERTEX"] != "1":
        raise ValueError("Native Vertex settings must explicitly enable Vertex")
    return dict(environment)


def from_settings(path: Path) -> dict[str, Any]:
    """Read routing fields once; neither raw settings nor authentication enters the pin."""
    path = Path(path).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Native Vertex settings must be an existing absolute nonsymlink file")
    settings = json.loads(path.read_bytes())
    configured = settings.get("env") if isinstance(settings, dict) else None
    if not isinstance(configured, dict):
        raise ValueError("Native Vertex settings require an env object")
    environment = _validate_environment({key: configured[key] for key in ENVIRONMENT_KEYS if key in configured})
    return {"version": VERSION, "source_settings": str(path.resolve()),
            "environment": environment, "environment_sha256": _digest(environment),
            "model_selection": "Pinned source role aliases; explicitly configured native alias targets",
            "import_boundary": "Only allowlisted nonsecret routing fields; no prompts, hooks, permissions, sessions, settings file or authentication values copied"}


def apply_environment(env: dict[str, str], mapping: dict[str, Any]) -> dict[str, str]:
    """Use the frozen routing pin, independent of later changes to user settings."""
    if mapping.get("version") != VERSION:
        raise ValueError("Unknown native Vertex mapping")
    environment = _validate_environment(mapping.get("environment"))
    if mapping.get("environment_sha256") != _digest(environment):
        raise ValueError("Native Vertex routing pin changed")
    # Avoid a provider/API-key override inherited from the controller. This
    # changes the child environment only, never the user's configuration.
    configured = {key: value for key, value in env.items()
                  if not key.startswith("ANTHROPIC_") and key not in _EXCLUDED_KEYS}
    return {**configured, **environment}


def validate_process(env: dict[str, str], mapping: dict[str, Any]) -> None:
    if env != apply_environment(env, mapping):
        raise ValueError("Pinned native Vertex environment must be configured before source imports")
