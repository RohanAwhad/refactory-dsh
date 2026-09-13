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

#: What the writer records about where a package came from.
ORIGIN = ".origin.json"

#: Where generated packages land. The installed tree is read-only, so a run never
#: writes into the checkout; this is the writable side, and it is also the boundary
#: a sandbox needs.
WORKSPACE = Path(".factory") / "packages"


class PackageClash(PackageError):
    """A package name exists in both the install and the workspace."""


def workspace_root(project: Path) -> Path:
    """The writable package root for a project."""
    return Path(project) / WORKSPACE


def install_workspace(project: Path) -> Path | None:
    """Make workspace packages importable under `refactory_packages`.

    Extending the distribution's `__path__` is what lets a package in a writable
    directory be imported by the same ``refactory_packages.<name>`` path the
    installed ones use, so an op written in a generated package needs no special
    case and the op namespace does not depend on where a package happens to live.
    """
    import refactory_packages

    root = workspace_root(project)
    if not root.is_dir():
        return None
    resolved = str(root.resolve())
    if resolved not in refactory_packages.__path__:
        refactory_packages.__path__.append(resolved)
        importlib.invalidate_caches()
    return root


def packages_root() -> Path:
    """The directory holding every package."""
    module = importlib.import_module(ROOT_PACKAGE)
    if not module.__file__:  # pragma: no cover - a namespace package has no dir
        raise PackageError(f"{ROOT_PACKAGE} is a namespace package; it needs a directory")
    return Path(module.__file__).parent


def _scan(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return [
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and (entry / "__init__.py").exists() and not entry.name.startswith("_")
    ]


def discover_names(project: Path | None = None) -> list[str]:
    """Every package directory, installed or in the workspace.

    Both roots are scanned, and a name in both is refused rather than resolved:
    silent precedence is how a contributor's package gets shadowed and nobody can
    find out.
    """
    installed = set(_scan(packages_root()))
    if project is None:
        return sorted(installed)
    here = set(_scan(workspace_root(Path(project))))
    clash = sorted(installed & here)
    if clash:
        raise PackageClash(
            f"package(s) {clash} exist in both the install and {workspace_root(Path(project))}; "
            "one must be renamed"
        )
    return sorted(installed | here)


def where(name: str, project: Path | None = None) -> Path | None:
    """Which root a package lives in, or ``None`` if it is not there."""
    for root in ([workspace_root(Path(project))] if project else []) + [packages_root()]:
        if (root / name / "__init__.py").exists():
            return root / name
    return None


def load(name: str, project: Path | None = None) -> Package:
    """Import one package and return its manifest, with its provenance applied.

    A workspace package is evicted from the import cache first, because the workspace
    is mutable: create-mode rewrites it, and a regenerated package must never be
    judged by the module object the previous attempt left behind.
    """
    if project is not None:
        _evict_workspace(name, Path(project))
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
    return _with_provenance(package, project)


def _evict_workspace(name: str, project: Path) -> None:
    """Drop a workspace package and its submodules from the import cache."""
    import sys

    root = workspace_root(project)
    if not (root / name / "__init__.py").exists():
        return
    module_name = f"{ROOT_PACKAGE}.{name}"
    for cached in [
        cached for cached in sys.modules
        if cached == module_name or cached.startswith(module_name + ".")
    ]:
        del sys.modules[cached]
    importlib.invalidate_caches()


def _origin_file(name: str, project: Path | None = None) -> Path | None:
    """The writer's record for a package, following the project it belongs to."""
    roots = [workspace_root(Path(project)) if project is not None else Path.cwd() / WORKSPACE]
    roots.append(packages_root())
    for root in roots:
        path = root / name / ORIGIN
        if path.exists():
            return path
    return None


def _with_provenance(package: Package, project: Path | None = None) -> Package:
    """Apply what the writer recorded, which outranks what the manifest claims.

    A manifest is code, and code can say anything. The tier a package is *run* under
    is the system's record of where it came from, so the record wins.
    """
    path = _origin_file(package.name, project)
    if path is None:
        return package
    try:
        recorded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return package
    if not isinstance(recorded, dict):
        return package
    from dataclasses import replace as _replace

    changes = {}
    if recorded.get("trust") in ("trusted", "generated"):
        changes["trust"] = recorded["trust"]
    if recorded.get("origin"):
        changes["origin"] = str(recorded["origin"])
    return _replace(package, **changes) if changes else package


def provenance(name: str, project: Path | None = None) -> dict[str, Any]:
    """Where a package came from, as the writer recorded it."""
    path = _origin_file(name, project)
    if path is None:
        return {"package": name, "trust": "trusted", "origin": "installed", "at": ""}
    try:
        recorded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        recorded = {}
    return {"package": name, **recorded}


def discover(project: Path | None = None) -> dict[str, Package]:
    """Every package that loads. A package that fails to import is reported."""
    found: dict[str, Package] = {}
    if project is not None:
        install_workspace(Path(project))
    for name in discover_names(project):
        try:
            found[name] = load(name, project)
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


def installed_names(project: Path | None = None) -> list[str]:
    """Package directories on disk, enabled or not."""
    return discover_names(project)


def enabled_packages(project: Path) -> dict[str, Package]:
    return {
        name: package
        for name, package in discover(Path(project)).items()
        if is_enabled(Path(project), name)
    }


def resolve(project: Path, identifier: str) -> tuple[Package, type]:
    """The package and mode class for ``<package>/<name>``."""
    package_name, mode_name = split(identifier)
    install_workspace(Path(project))
    if not is_enabled(Path(project), package_name):
        raise PackageError(f"package {package_name!r} is disabled for this run")
    package = load(package_name, project)
    return package, package.mode(mode_name)


def catalogue(project: Path) -> dict[str, list[dict[str, Any]]]:
    """Modes grouped by package: the only way they are ever listed."""
    project = Path(project)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for name, package in discover(project).items():
        entry = package.describe()
        entry["enabled"] = is_enabled(project, name)
        root = where(name, project)
        entry["where"] = str(root) if root else ""
        grouped[name] = entry["modes"]  # type: ignore[assignment]
        for mode in grouped[name]:
            mode["package_enabled"] = entry["enabled"]
            mode["trust"] = entry["trust"]
            mode["where"] = entry["where"]
    return grouped


def render_catalogue(project: Path) -> str:
    """The grouped listing a user sees."""
    lines: list[str] = []
    for name in installed_names(Path(project)):
        try:
            package = load(name, Path(project))
        except PackageError as exc:
            lines.append(f"{name}/  (unavailable: {exc})")
            continue
        state = "" if is_enabled(Path(project), name) else "  (disabled)"
        marks = [] if package.trust == "trusted" else [package.trust]
        if not is_enabled(Path(project), name):
            marks.append("disabled")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        lines.append(f"{name}/  {package.description}{suffix}")
        for mode in sorted(package.modes, key=lambda m: m.name):
            identifier = mode_id(name, mode.name)
            description = getattr(mode, "description", "")
            lines.append(f"  {identifier:<38} {description}")
    return "\n".join(lines) + "\n" if lines else "no packages installed\n"


def smoke(project: Path, identifier: str) -> dict[str, Any]:
    """Run one mode once, so a package can be checked rather than merely imported."""
    import tempfile

    package, mode_class = resolve(Path(project), identifier)
    mode = mode_class()
    with tempfile.TemporaryDirectory(prefix="refactory-smoke-") as tmp:
        result = mode.evaluate(Path(tmp))
    return {
        "mode": identifier,
        "package": package.name,
        "exit_ok": result.exit_ok,
        "error": result.error,
        "score": result.score,
        "iterations": result.iterations,
    }


# ── the lifecycle ──────────────────────────────────────────────────────────
def _write_origin(root: Path, *, origin: str, trust: str, ref: str = "") -> Path:
    import datetime

    path = root / ORIGIN
    path.write_text(json.dumps({
        "origin": origin,
        "ref": ref,
        "trust": trust,
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))
    return path


def add(project: Path, source: Path, *, as_name: str = "", trust: str = "generated") -> str:
    """Copy a package into the workspace, refusing a clash.

    A copy rather than a link, so the added package keeps working if the source is
    moved. The name is refused if it is already taken: the old registry resolved a
    clash by source precedence, silently, which is how a contributor's package gets
    shadowed and nobody can find out.
    """
    import shutil

    project = Path(project)
    source = Path(source).resolve()
    if not (source / "__init__.py").exists():
        raise PackageError(f"{source} is not a package: it has no __init__.py")
    destination_root = workspace_root(project)
    if destination_root.is_dir() and source.is_relative_to(destination_root.resolve()):
        raise PackageError(f"{source} is already in the workspace")
    name = as_name or source.name
    _check_name(name)
    existing = where(name, project)
    if existing is not None:
        raise PackageClash(
            f"package {name!r} already exists at {existing}; pass --as to rename it"
        )
    target = destination_root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    _write_origin(target, origin=str(source), trust=trust)
    install_workspace(project)
    if load(name, project).name != name:
        shutil.rmtree(target, ignore_errors=True)
        raise PackageError(f"the copied package declares a different name than {name!r}")
    importlib.invalidate_caches()
    log.info("package_added", package=name, source=str(source), trust=trust)
    return name


def import_package(
    project: Path, source: str, *, ref: str = "", as_name: str = "", trust: str = "generated"
) -> str:
    """Bring a package in from a path or a git URL."""
    candidate = Path(source).expanduser()
    if candidate.exists():
        if ref:
            raise PackageError("--ref only applies to a git URL")
        return add(project, candidate, as_name=as_name, trust=trust)
    if not source.startswith(("http://", "https://", "git@")):
        raise PackageError(
            f"{source!r} is neither a path that exists nor a git URL; a package is a "
            "directory, so an import needs somewhere to copy it from"
        )
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="refactory-import-") as tmp:
        command = ["git", "clone", "--depth", "1"]
        if ref:
            command += ["--branch", ref]
        command += [source, tmp]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise PackageError(f"git clone failed: {result.stderr.strip()[-400:]}")
        return add(project, Path(tmp), as_name=as_name, trust=trust)


def remove(project: Path, name: str, *, force: bool = False) -> Path:
    """Delete a workspace package, refusing to break a dependent.

    Only the workspace is writable. The installed tree is read-only, so removing a
    shipped package is refused rather than attempted.
    """
    import shutil

    project = Path(project)
    target = where(name, project)
    if target is None:
        raise PackageError(f"no package {name!r}")
    if not target.is_relative_to(workspace_root(project)):
        raise PackageError(
            f"{target} is in the install tree, which is read-only; only workspace "
            "packages can be removed"
        )
    if not force:
        dependents = sorted(
            other
            for other in discover_names(project)
            if other != name and name in load(other, project).requires
        )
        if dependents:
            raise PackageError(
                f"{dependents} require {name!r}; remove them first, or pass --force"
            )
    shutil.rmtree(target)
    state = _state(project)
    state.get("packages", {}).pop(name, None)
    _save_state(project, state)
    importlib.invalidate_caches()
    log.info("package_removed", package=name)
    return target


def verify(project: Path, name: str | None = None) -> dict[str, list[str]]:
    """Import every mode of a package and say what is wrong with it."""
    from refactory_dsh.nn import validate_state

    project = Path(project)
    findings: dict[str, list[str]] = {}
    for package_name in ([name] if name else discover_names(project)):
        problems: list[str] = []
        try:
            package = load(package_name, project)
        except PackageError as exc:
            findings[package_name] = [str(exc)]
            continue
        for required in package.requires:
            if where(required, project) is None:
                problems.append(f"requires {required!r}, which is not installed")
        for mode_class in package.modes:
            identifier = f"{package_name}/{mode_class.name}"
            try:
                mode = mode_class()
            except Exception as exc:
                problems.append(f"{identifier}: cannot be constructed: {exc}")
                continue
            for issue in validate_state(
                mode.state_dict(), invariants=mode.invariants(), resolve_calls=True
            ):
                problems.append(f"{identifier}: {issue}")
        findings[package_name] = problems
    return findings


def _check_name(name: str) -> None:
    from refactory_dsh.nn.package import _check as _validate

    _validate(name, "package")


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
    parser.add_argument(
        "verb",
        choices=["list", "show", "verify", "smoke", "add", "import", "remove",
                 "enable", "disable"],
        nargs="?", default="list",
    )
    parser.add_argument("target", nargs="?", help="a <package>/<name> mode, or a package")
    parser.add_argument("--project", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--as", dest="as_name", default="", help="rename on add or import")
    parser.add_argument("--ref", default="", help="a git ref, for import")
    parser.add_argument("--force", action="store_true", help="override a refusal")
    args = parser.parse_args(argv)
    project = Path(args.project)

    def fail(message: str) -> int:
        print(json.dumps({"error": message}), file=sys.stderr)
        return 2

    try:
        if args.verb == "list":
            print(
                json.dumps(catalogue(project), indent=2, default=str)
                if args.json
                else render_catalogue(project),
                end="\n" if args.json else "",
            )
            return 0
        if args.verb == "show":
            if not args.target:
                return fail("'show' needs a <package>/<name> mode")
            print(json.dumps(describe_mode(project, args.target), indent=2, default=str))
            return 0
        if args.verb == "verify":
            findings = verify(project, args.target or None)
            print(json.dumps(findings, indent=2, default=str))
            return 1 if any(findings.values()) else 0
        if args.verb == "smoke":
            if not args.target:
                return fail("'smoke' needs a <package>/<name> mode")
            result = smoke(project, args.target)
            print(json.dumps(result, indent=2, default=str))
            return 0 if result["exit_ok"] else 1
        if args.verb == "add":
            if not args.target:
                return fail("'add' needs a path to a package directory")
            print(json.dumps({"added": add(project, Path(args.target), as_name=args.as_name)}))
            return 0
        if args.verb == "import":
            if not args.target:
                return fail("'import' needs a path or a git URL")
            imported = import_package(
                project, args.target, ref=args.ref, as_name=args.as_name
            )
            print(json.dumps({"imported": imported}))
            return 0
        if args.verb == "remove":
            if not args.target:
                return fail("'remove' needs a package name")
            removed = remove(project, args.target, force=args.force)
            print(json.dumps({"removed": str(removed)}))
            return 0
        if args.verb in ("enable", "disable"):
            if not args.target:
                return fail(f"'{args.verb}' needs a package name")
            set_enabled(project, args.target, args.verb == "enable")
            print(json.dumps({args.verb + "d": args.target}))
            return 0
    except PackageError as exc:
        return fail(str(exc))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
