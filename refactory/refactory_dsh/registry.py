"""Discover, list and resolve packages of modes.

Discovery is a scan of the ``refactory_packages`` distribution for subpackages
that declare ``PACKAGE``. Nothing here names a package: the library can enumerate
and import every package without knowing that any particular one exists.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import structlog

from refactory_dsh.nn.package import Package, PackageError, mode_id, split

log = structlog.get_logger()

#: The distribution that holds packages, kept separate from the library so the
#: library cannot reach one by accident.
ROOT_PACKAGE = "refactory_packages"

#: Which packages a run may use, and which are switched off.
STATE_FILE = Path(".factory") / "packages.json"


def packages_root() -> Path:
    """The directory holding every package."""
    module = importlib.import_module(ROOT_PACKAGE)
    if not module.__file__:  # pragma: no cover - a namespace package has no dir
        raise PackageError(f"{ROOT_PACKAGE} is a namespace package; it needs a directory")
    return Path(module.__file__).parent


def discover_names() -> list[str]:
    """Every package directory, whether or not it imports successfully."""
    root = packages_root()
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / "__init__.py").exists() and not entry.name.startswith("_")
    )


def load(name: str) -> Package:
    """Import one package and return its manifest."""
    try:
        module = importlib.import_module(f"{ROOT_PACKAGE}.{name}")
    except ImportError as exc:
        raise PackageError(f"cannot import package {name!r}: {exc}") from exc
    package = getattr(module, "PACKAGE", None)
    if not isinstance(package, Package):
        raise PackageError(
            f"package {name!r} does not declare PACKAGE = Package(...) in its __init__.py"
        )
    if package.name != name:
        raise PackageError(
            f"package {name!r} declares the name {package.name!r}; they must agree"
        )
    return package


def discover() -> dict[str, Package]:
    """Every package that loads. A package that fails to import is reported."""
    found: dict[str, Package] = {}
    for name in discover_names():
        try:
            found[name] = load(name)
        except PackageError as exc:
            log.warning("package_unavailable", package=name, reason=str(exc))
    return found


def _state(project: Path) -> dict[str, Any]:
    path = Path(project) / STATE_FILE
    if not path.exists():
        return {"packages": {}}
    try:
        loaded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"packages": {}}
    return loaded if isinstance(loaded, dict) else {"packages": {}}


def _save_state(project: Path, data: dict[str, Any]) -> None:
    path = Path(project) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def is_enabled(project: Path, name: str) -> bool:
    entry = _state(Path(project)).get("packages", {}).get(name)
    return True if entry is None else bool(entry.get("enabled", True))


def set_enabled(project: Path, name: str, enabled: bool) -> None:
    state = _state(Path(project))
    entry = state.setdefault("packages", {}).setdefault(name, {})
    entry["enabled"] = enabled
    _save_state(Path(project), state)


def installed_names() -> list[str]:
    """Package directories on disk, enabled or not."""
    return discover_names()


def enabled_packages(project: Path) -> dict[str, Package]:
    return {
        name: package
        for name, package in discover().items()
        if is_enabled(Path(project), name)
    }


def resolve(project: Path, identifier: str) -> tuple[Package, type]:
    """The package and mode class for ``<package>/<name>``."""
    package_name, mode_name = split(identifier)
    if not is_enabled(Path(project), package_name):
        raise PackageError(f"package {package_name!r} is disabled for this run")
    package = load(package_name)
    return package, package.mode(mode_name)


def catalogue(project: Path) -> dict[str, list[dict[str, Any]]]:
    """Modes grouped by package: the only way they are ever listed."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for name, package in discover().items():
        entry = package.describe()
        entry["enabled"] = is_enabled(Path(project), name)
        grouped[name] = entry["modes"]  # type: ignore[assignment]
        for mode in grouped[name]:
            mode["package_enabled"] = entry["enabled"]
    return grouped


def render_catalogue(project: Path) -> str:
    """The grouped listing a user sees."""
    lines: list[str] = []
    for name in installed_names():
        try:
            package = load(name)
        except PackageError as exc:
            lines.append(f"{name}/  (unavailable: {exc})")
            continue
        state = "" if is_enabled(Path(project), name) else "  (disabled)"
        lines.append(f"{name}/  {package.description}{state}")
        for mode in sorted(package.modes, key=lambda m: m.name):
            identifier = mode_id(name, mode.name)
            description = getattr(mode, "description", "")
            lines.append(f"  {identifier:<38} {description}")
    return "\n".join(lines) + "\n" if lines else "no packages installed\n"


def describe_mode(project: Path, identifier: str) -> dict[str, Any]:
    """One mode's surface, for a person about to optimize it or read a report."""
    package, mode_class = resolve(Path(project), identifier)
    mode = mode_class()
    described = mode.describe()
    described["package"] = package.name
    described["id"] = identifier
    described["readme"] = package.description
    return described


def main(argv: list[str] | None = None) -> int:
    """``python -m refactory_dsh.registry list | show <mode>``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.registry",
        description="List packages of modes, or describe one mode.",
    )
    parser.add_argument("verb", choices=["list", "show"], nargs="?", default="list")
    parser.add_argument("mode", nargs="?", help="a <package>/<name> mode, for 'show'")
    parser.add_argument("--project", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project = Path(args.project)
    if args.verb == "list":
        if args.json:
            print(json.dumps(catalogue(project), indent=2, default=str))
        else:
            print(render_catalogue(project), end="")
        return 0
    if not args.mode:
        print("'show' needs a <package>/<name> mode", file=sys.stderr)
        return 2
    try:
        print(json.dumps(describe_mode(project, args.mode), indent=2, default=str))
    except PackageError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
