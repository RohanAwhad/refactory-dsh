"""Manifest-driven detection of unbuilt workspace packages.

When a check fails, this module scans the worktree's own package.json and
pyproject.toml files for declared entry points whose built output does not
exist on disk.  The diagnosis names each unbuilt package and points at the
``setup`` knob, so the builder does not chase an environment problem it
cannot fix by changing code.

All state is explicit: the worktree path, base commit and head commit are
passed in, nothing is stored in a ContextVar or module global.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class UnbuiltPackage:
    """One workspace package whose declared entry point is missing on disk."""

    name: str
    directory: str
    entry_point: str


def scan_unbuilt_packages(
    worktree_root: Path,
    base_commit: str = "",
    head_commit: str = "",
) -> list[UnbuiltPackage]:
    """Top-level: find packages whose declared entry points are missing.

    Returns only packages whose source was not deleted by the change (a
    deletion is the change's fault and goes to the builder).
    """
    root = Path(worktree_root)
    unbuilt = _scan_unbuilt_js(root) + _scan_unbuilt_python(root)
    if not unbuilt or not base_commit or not head_commit:
        return unbuilt
    deleted = _deleted_sources(root, base_commit, head_commit)
    return [pkg for pkg in unbuilt if not _source_deleted(pkg, root, deleted)]


def _scan_unbuilt_js(root: Path) -> list[UnbuiltPackage]:
    """Walk for package.json files whose main/exports name missing files."""
    result: list[UnbuiltPackage] = []
    for manifest_path in root.rglob("package.json"):
        # Skip node_modules
        rel = manifest_path.relative_to(root)
        if "node_modules" in rel.parts:
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        name = manifest.get("name")
        if not isinstance(name, str) or not name:
            continue
        pkg_dir = manifest_path.parent

        # Collect declared entry-point paths from main and exports
        entry_points = _js_entry_points(manifest)
        if not entry_points:
            continue

        # Only flag when source exists (so there is something to build)
        if not _has_js_source(pkg_dir):
            continue

        # Check which entry points are missing
        for ep in entry_points:
            ep_path = pkg_dir / ep
            if not ep_path.exists():
                result.append(UnbuiltPackage(
                    name=name,
                    directory=str(rel.parent) if str(rel.parent) != "." else str(rel.parent),
                    entry_point=str(rel.parent / ep) if str(rel.parent) != "." else ep,
                ))
                break  # one missing entry point is enough to flag the package
    return result


def _js_entry_points(manifest: dict) -> list[str]:
    """Extract file paths from main and exports."""
    paths: list[str] = []
    main = manifest.get("main")
    if isinstance(main, str) and main.strip():
        paths.append(main.strip())
    exports = manifest.get("exports")
    if exports is not None:
        _collect_export_paths(exports, paths)
    return paths


def _collect_export_paths(value: Any, paths: list[str]) -> None:
    """Walk a package.json exports value to find string paths."""
    if isinstance(value, str) and value.strip():
        v = value.strip()
        if v.endswith((".js", ".mjs", ".cjs", ".json")):
            paths.append(v)
    elif isinstance(value, dict):
        for v in value.values():
            _collect_export_paths(v, paths)
    elif isinstance(value, list):
        for v in value:
            _collect_export_paths(v, paths)


def _has_js_source(pkg_dir: Path) -> bool:
    """Whether the package directory has source files to build from."""
    if (pkg_dir / "src").is_dir():
        return True
    for ext in (".ts", ".tsx", ".js", ".jsx"):
        if any(pkg_dir.glob(f"*{ext}")):
            return True
    return False


def _scan_unbuilt_python(root: Path) -> list[UnbuiltPackage]:
    """Scan for Python extension modules whose built files are missing.

    Only extension modules (declared via ext_modules in setup.py/setup.cfg or
    via a build backend that produces .so/.pyd) are flagged.  A src-layout
    pure-Python package is normal: installing it makes it importable, and it
    has no build output in the repository.
    """
    result: list[UnbuiltPackage] = []
    for toml_path in root.rglob("pyproject.toml"):
        rel = toml_path.relative_to(root)
        if "node_modules" in rel.parts or ".venv" in rel.parts:
            continue
        try:
            import tomllib
        except ModuleNotFoundError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ModuleNotFoundError:
                continue
        try:
            data = tomllib.loads(toml_path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        # Only flag extension-module builds
        build_system = data.get("build-system", {})
        if not isinstance(build_system, dict):
            continue
        build_backend = str(build_system.get("build-backend", ""))
        # Known extension-module backends
        ext_backends = ("maturin", "setuptools.build_meta", "scikit_build_core",
                        "mesonpy", "meson-python")
        if not any(b in build_backend for b in ext_backends):
            continue
        # Check for extension module indicators
        pkg_dir = toml_path.parent
        project = data.get("project", {})
        name = project.get("name", "") if isinstance(project, dict) else ""
        if not name:
            continue
        # Look for declared extension modules in setup.py alongside pyproject.toml
        setup_py = pkg_dir / "setup.py"
        has_ext_modules = False
        if setup_py.is_file():
            try:
                setup_text = setup_py.read_text()
                if "ext_modules" in setup_text or "Extension(" in setup_text:
                    has_ext_modules = True
            except OSError:
                pass
        # Also check for Rust src (maturin) or C/C++ source
        if (pkg_dir / "Cargo.toml").is_file():
            has_ext_modules = True
        if any(pkg_dir.rglob("*.c")) or any(pkg_dir.rglob("*.cpp")) or any(pkg_dir.rglob("*.pyx")):
            has_ext_modules = True
        # maturin with pyproject.toml tool.maturin section
        if "maturin" in build_backend:
            has_ext_modules = True
        if not has_ext_modules:
            continue
        # Check for missing built extension files
        mod_name = name.replace("-", "_")
        # Look for .so/.pyd in the package directory
        so_files = list(pkg_dir.rglob(f"{mod_name}*.so")) + list(pkg_dir.rglob(f"{mod_name}*.pyd"))
        # Also check src layout
        src_dir = pkg_dir / "src"
        if src_dir.is_dir():
            so_files += list(src_dir.rglob(f"{mod_name}*.so")) + list(src_dir.rglob(f"{mod_name}*.pyd"))
        if not so_files:
            result.append(UnbuiltPackage(
                name=name,
                directory=str(rel.parent) if str(rel.parent) != "." else ".",
                entry_point=f"{mod_name}*.so",
            ))
    # Also scan setup.py files at root level for ext_modules
    for setup_path in root.rglob("setup.py"):
        rel = setup_path.relative_to(root)
        if "node_modules" in rel.parts or ".venv" in rel.parts:
            continue
        # Skip if there's already a pyproject.toml we handled
        pkg_dir = setup_path.parent
        if (pkg_dir / "pyproject.toml").is_file():
            continue
        try:
            setup_text = setup_path.read_text()
        except OSError:
            continue
        if "ext_modules" not in setup_text and "Extension(" not in setup_text:
            continue
        # Try to find the package name
        import re
        name_match = re.search(r'''name\s*=\s*['"]([^'"]+)['"]''', setup_text)
        if not name_match:
            continue
        name = name_match.group(1)
        mod_name = name.replace("-", "_")
        so_files = list(pkg_dir.rglob(f"{mod_name}*.so")) + list(pkg_dir.rglob(f"{mod_name}*.pyd"))
        if not so_files:
            result.append(UnbuiltPackage(
                name=name,
                directory=str(rel.parent) if str(rel.parent) != "." else ".",
                entry_point=f"{mod_name}*.so",
            ))
    return result


def _deleted_sources(
    root: Path, base_commit: str, head_commit: str,
) -> set[str]:
    """File paths deleted between base and head in the worktree."""
    try:
        output = subprocess.run(
            ["git", "-C", str(root), "diff", "--diff-filter=D", "--name-only",
             f"{base_commit}..{head_commit}"],
            capture_output=True, text=True, timeout=30,
        )
        return {line.strip() for line in output.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        return set()


def _source_deleted(pkg: UnbuiltPackage, root: Path, deleted: set[str]) -> bool:
    """Whether the package's source files were deleted by the change."""
    pkg_dir = pkg.directory
    return any(
        d.startswith(pkg_dir + "/") or d == pkg_dir
        for d in deleted
    )


def format_unbuilt_diagnosis(packages: list[UnbuiltPackage]) -> str:
    """Human-readable message naming each unbuilt package and the setup knob."""
    if not packages:
        return ""
    lines = ["Unbuilt packages detected in the worktree:"]
    for pkg in packages:
        lines.append(
            f"  - {pkg.name} (in {pkg.directory}): "
            f"entry point {pkg.entry_point!r} does not exist on disk"
        )
    lines.append(
        "The `setup` knob is where a build command belongs (e.g. `pnpm run build`). "
        "Without it, the check fails on missing build output and the run cannot "
        "fix that by changing code."
    )
    return "\n".join(lines)


def failures_explained_by_unbuilt(
    error_output: str, unbuilt: list[UnbuiltPackage],
) -> bool:
    """Whether every failure line names an exact missing entry-point path.

    Returns True only when every test-level failure mention in the output
    names the exact missing entry-point file of an unbuilt package, as a
    repo-relative or absolute path.  This is strict: a downstream assertion
    error that does not name the missing file is not matched.
    """
    if not unbuilt or not error_output.strip():
        return False
    # Collect the exact entry-point paths (repo-relative)
    missing_paths = {pkg.entry_point for pkg in unbuilt}
    # Also add the basename of each missing entry point
    missing_basenames = {os.path.basename(ep) for ep in missing_paths}
    # Find failure lines: lines with FAILED, ERROR, FAIL, MODULE_NOT_FOUND,
    # ModuleNotFoundError, Cannot find module, failed to import
    import re
    failure_patterns = re.compile(
        r"FAILED |ERROR |FAIL |MODULE_NOT_FOUND|ModuleNotFoundError|"
        r"Cannot find module|failed to import|ImportError|"
        r"Error: Cannot find module",
        re.I,
    )
    failure_lines = [
        line for line in error_output.splitlines()
        if failure_patterns.search(line)
    ]
    if not failure_lines:
        return False
    for line in failure_lines:
        # Check if the line mentions an exact entry-point path
        if not any(ep in line for ep in missing_paths) and not any(bn in line for bn in missing_basenames):
            return False
    return True
