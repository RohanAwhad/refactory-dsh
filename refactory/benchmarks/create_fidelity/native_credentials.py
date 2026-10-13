"""Explicit existing-file native authentication reference; no secret copying."""
from __future__ import annotations

from pathlib import Path
from typing import Any

VERSION = "existing-native-adc-reference-v1"


def reference(path: Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("Native ADC reference must be an existing absolute nonsymlink file")
    return {"version": VERSION, "path": str(path.resolve()), "type": "existing-google-adc-file",
            "environment": "GOOGLE_APPLICATION_CREDENTIALS", "access": "exact-file-read-only",
            "contents": "Not inspected, dumped, hashed or copied by benchmark preparation"}


def apply_environment(env: dict[str, str], mapping: dict[str, Any]) -> dict[str, str]:
    if mapping.get("version") != VERSION or mapping.get("access") != "exact-file-read-only":
        raise ValueError("Unknown or overbroad native credential mapping")
    current = reference(Path(mapping["path"]))
    if current != mapping:
        raise ValueError("Native credential reference changed")
    return {**env, "GOOGLE_APPLICATION_CREDENTIALS": mapping["path"]}
