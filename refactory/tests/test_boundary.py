"""Enforce the core/product import boundary.

Driven by ``refactory/refactory_dsh/data/boundary.json``, the single source of
truth for the module classification.  Fails when:

- a module is unclassified;
- a core module imports a product module outside the allow-list;
- an allow-listed leak no longer exists (stale entry);
- the TypeScript core packages import a product package.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]  # refactory/
_DSH = _ROOT / "refactory_dsh"
_PACKAGES = _ROOT / "refactory_packages"
_REPO = _ROOT.parent  # the checkout root
_BOUNDARY = _DSH / "data" / "boundary.json"


def _load_boundary() -> dict[str, Any]:
    return json.loads(_BOUNDARY.read_text())


def _module_name(path: Path, root: Path) -> str:
    """Convert a .py file path to a dotted module name."""
    relative = path.relative_to(root)
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _discover_py_modules(root: Path, pkg_name: str) -> set[str]:
    """Discover every .py module under *root*, as dotted names."""
    modules = set()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        modules.add(_module_name(path, root.parent))
    return modules


def _discover_non_py_files(root: Path) -> set[str]:
    """Discover every non-.py file under *root*, excluding __pycache__."""
    files = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix == ".py":
            continue
        rel = str(path.relative_to(root.parent))
        files.add(rel)
    return files


# ---------------------------------------------------------------------------
# Test 1: every module and non-Python file is classified
# ---------------------------------------------------------------------------
def test_all_modules_classified():
    boundary = _load_boundary()
    classified = set(boundary["core"]) | set(boundary["product"])

    # Python modules under refactory_dsh/
    dsh_modules = _discover_py_modules(_DSH, "refactory_dsh")
    unclassified = dsh_modules - classified
    assert not unclassified, f"unclassified Python modules in refactory_dsh/: {sorted(unclassified)}"

    # Python modules under refactory_packages/
    pkg_modules = _discover_py_modules(_PACKAGES, "refactory_packages")
    pack_classified = set()
    for pack_name, side in boundary["refactory_packages"].items():
        pack_classified.add(f"refactory_packages.{pack_name}")
    # Individual submodules of packs are covered by the pack classification
    for mod in pkg_modules:
        parts = mod.split(".")
        if len(parts) >= 2:
            pack = parts[1]
            if pack not in boundary["refactory_packages"]:
                # __init__.py of refactory_packages itself
                if mod == "refactory_packages":
                    continue
                assert False, f"unclassified pack module: {mod}"

    # Non-Python files under refactory_dsh/
    non_py = _discover_non_py_files(_DSH)
    declared_non_py = {entry["path"] for entry in boundary["non_python_files"]}
    undeclared = non_py - declared_non_py
    assert not undeclared, f"non-Python files under refactory_dsh/ not in boundary.json: {sorted(undeclared)}"


# ---------------------------------------------------------------------------
# Test 2: static import scan — no core module imports product
# ---------------------------------------------------------------------------
def _resolve_module(name: str | None, level: int, package: str) -> str | None:
    """Resolve an import's module name, handling relative imports.

    *package* is the importing module's own package: for ``__init__.py``
    modules, that is the module name itself; for plain modules, it is the
    parent (``mod.rsplit('.', 1)[0]``).  A ``level == 1`` import (``from .``)
    stays in *package*; ``level >= 2`` strips ``level - 1`` components.
    """
    if level > 0:
        parts = package.split(".")
        strip = level - 1
        if strip > 0:
            if strip >= len(parts):
                return None
            base = ".".join(parts[:-strip])
        else:
            base = ".".join(parts)
        if name:
            return f"{base}.{name}" if base else name
        return base or None
    return name


def _extract_imports(source: str, package: str) -> list[tuple[str, list[str]]]:
    """Return (module, [imported_names]) for every import in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    results: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_module(node.module, node.level, package)
            if resolved:
                names = [alias.name for alias in node.names]
                results.append((resolved, names))
    return results


def _is_product(name: str, product_set: set[str]) -> bool:
    """Check if a module name is or is under a product module."""
    if name in product_set:
        return True
    # Check parent packages
    for prod in product_set:
        if name.startswith(prod + "."):
            return True
    return False


def test_no_core_imports_product_static():
    boundary = _load_boundary()
    core_modules = set(boundary["core"])
    product_modules = set(boundary["product"])
    # Include product packs in the product set
    for pack_name, side in boundary["refactory_packages"].items():
        if side == "product":
            product_modules.add(f"refactory_packages.{pack_name}")
    allowed = boundary["allowed_leaks"]

    violations: list[str] = []
    visited: set[str] = set()

    # Scan refactory_dsh modules
    for py_path in sorted(_DSH.rglob("*.py")):
        if "__pycache__" in py_path.parts or "tests" in py_path.parts:
            continue
        mod_name = _module_name(py_path, _DSH.parent)
        if mod_name not in core_modules:
            continue

        visited.add(mod_name)
        source = py_path.read_text()
        package = mod_name if py_path.name == "__init__.py" else (mod_name.rsplit(".", 1)[0] if "." in mod_name else mod_name)
        imports = _extract_imports(source, package)

        allowed_for_mod = {
            entry["imports"]
            for entry in allowed.get(mod_name, [])
        }

        for imp_name, imp_names in imports:
            if _is_product(imp_name, product_modules):
                if imp_name not in allowed_for_mod:
                    violations.append(f"{mod_name} -> {imp_name}")
            # Check the base module when importing subnames
            # e.g. from refactory_dsh.tools import recipes -> recipes is product
            for sub in imp_names:
                full = f"{imp_name}.{sub}"
                if _is_product(full, product_modules) and full not in allowed_for_mod:
                    # If the base module import is already allowed, its
                    # imported names (attributes or submodules) are
                    # implicitly covered by that allow-list entry.
                    if imp_name not in allowed_for_mod:
                        violations.append(f"{mod_name} -> {full}")

    # Scan core packs in refactory_packages (skip product packs)
    pack_class = boundary["refactory_packages"]
    for pack_name, side in pack_class.items():
        if side != "core":
            continue
        pack_dir = _PACKAGES / pack_name
        if not pack_dir.is_dir():
            continue
        visited.add(f"refactory_packages.{pack_name}")
        for py_path in sorted(pack_dir.rglob("*.py")):
            if "__pycache__" in py_path.parts:
                continue
            source = py_path.read_text()
            mod_name = _module_name(py_path, _PACKAGES.parent)
            package = mod_name if py_path.name == "__init__.py" else (mod_name.rsplit(".", 1)[0] if "." in mod_name else mod_name)
            imports = _extract_imports(source, package)
            for imp_name, imp_names in imports:
                if _is_product(imp_name, product_modules):
                    violations.append(f"{mod_name} (core pack) -> {imp_name}")
                for sub in imp_names:
                    full = f"{imp_name}.{sub}"
                    if _is_product(full, product_modules):
                        violations.append(f"{mod_name} (core pack) -> {full}")

    # Assert that we visited every classified core module (prevents vacuous pass)
    expected_core = set(boundary["core"])
    for pn, ps in pack_class.items():
        if ps == "core":
            expected_core.add(f"refactory_packages.{pn}")
    missed = expected_core - visited
    assert not missed, (
        "static scan did not visit these classified core modules "
        "(files missing or skipped):\n" + "\n".join(f"  {m}" for m in sorted(missed))
    )

    assert not violations, (
        "core modules import product modules outside the allow-list:\n"
        + "\n".join(f"  {v}" for v in sorted(set(violations)))
    )


# ---------------------------------------------------------------------------
# Test 2b: _resolve_module unit test
# ---------------------------------------------------------------------------
def test_resolve_module():
    """_resolve_module handles plain modules and __init__ relative imports correctly."""
    # level=1 from . import X — stays in current package
    assert _resolve_module("graph", 1, "refactory_dsh.nn") == "refactory_dsh.nn.graph"
    assert _resolve_module("validate", 1, "refactory_dsh.nn") == "refactory_dsh.nn.validate"
    # level=1 from . (no name) — returns the package itself
    assert _resolve_module(None, 1, "refactory_dsh.nn") == "refactory_dsh.nn"
    # level=2 from .. import Y — goes up one level
    assert _resolve_module("invocations", 2, "refactory_dsh.nn") == "refactory_dsh.invocations"
    # level=2 from .. (no name) — returns parent
    assert _resolve_module(None, 2, "refactory_dsh.nn") == "refactory_dsh"
    # level=3 from ... import Z
    assert _resolve_module("something", 3, "a.b.c") == "a.something"
    # Absolute import (level=0)
    assert _resolve_module("os.path", 0, "anything") == "os.path"
    # level too high
    assert _resolve_module("x", 4, "a.b") is None


# ---------------------------------------------------------------------------
# Test 3: runtime import scan — no core module loads product at import time
# ---------------------------------------------------------------------------
def test_no_core_imports_product_runtime():
    boundary = _load_boundary()
    core_modules = boundary["core"]
    product_modules = set(boundary["product"])
    # Include product packs in the product set
    for pack_name, side in boundary["refactory_packages"].items():
        if side == "product":
            product_modules.add(f"refactory_packages.{pack_name}")
    allowed = boundary["allowed_leaks"]

    # Compute allowed transitive product modules per core module
    def allowed_product(mod: str) -> set[str]:
        """Product modules allowed to appear via allow-listed imports."""
        result = set()
        for entry in allowed.get(mod, []):
            result.add(entry["imports"])
        return result

    violations: list[str] = []
    probe_failures: list[str] = []

    for mod in core_modules:
        script = (
            f"import sys\n"
            f"import {mod}\n"
            f"product = {product_modules!r}\n"
            f"leaked = [m for m in sys.modules if m in product]\n"
            f"print(','.join(leaked) if leaked else '')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
            cwd=str(_ROOT),
            env={**__import__("os").environ, "PYTHONPATH": str(_ROOT)},
        )
        if result.returncode != 0:
            probe_failures.append(f"{mod} probe failed (rc={result.returncode}): {result.stderr[:200]}")
            continue
        leaked = [m for m in result.stdout.strip().split(",") if m]
        expected = allowed_product(mod)
        # Allow transitives reached only through allowed imports
        unexpected = [m for m in leaked if m not in expected]
        # Transitives through an allowed module: if mod allows importing A,
        # and A imports B (product), B is implicitly allowed
        if unexpected and expected:
            # Re-run checking which product modules each allowed module brings
            for allowed_mod in list(expected):
                script2 = (
                    f"import sys\n"
                    f"import {allowed_mod}\n"
                    f"product = {product_modules!r}\n"
                    f"leaked = [m for m in sys.modules if m in product]\n"
                    f"print(','.join(leaked) if leaked else '')\n"
                )
                r2 = subprocess.run(
                    [sys.executable, "-c", script2],
                    capture_output=True, text=True, timeout=60,
                    cwd=str(_ROOT),
                    env={**__import__("os").environ, "PYTHONPATH": str(_ROOT)},
                )
                if r2.returncode != 0:
                    probe_failures.append(
                        f"{allowed_mod} (transitive of {mod}) probe failed "
                        f"(rc={r2.returncode}): {r2.stderr[:200]}"
                    )
                elif r2.returncode == 0:
                    for m in r2.stdout.strip().split(","):
                        if m:
                            expected.add(m)
            unexpected = [m for m in leaked if m not in expected]

        for m in unexpected:
            violations.append(f"{mod} loaded {m} at import time")

    # Also scan core packs
    pack_class = boundary["refactory_packages"]
    for pack_name, side in pack_class.items():
        if side != "core":
            continue
        mod = f"refactory_packages.{pack_name}"
        script = (
            f"import sys\n"
            f"import {mod}\n"
            f"product = {product_modules!r}\n"
            f"leaked = [m for m in sys.modules if m in product]\n"
            f"print(','.join(leaked) if leaked else '')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=60,
            cwd=str(_ROOT),
            env={**__import__("os").environ, "PYTHONPATH": str(_ROOT)},
        )
        if result.returncode != 0:
            probe_failures.append(f"{mod} (core pack) probe failed (rc={result.returncode}): {result.stderr[:200]}")
            continue
        leaked = [m for m in result.stdout.strip().split(",") if m]
        for m in leaked:
            violations.append(f"{mod} (core pack) loaded {m} at import time")

    assert not probe_failures, (
        "import probes failed (subprocess errors):\n"
        + "\n".join(f"  {f}" for f in probe_failures)
    )

    assert not violations, (
        "core modules load product modules at import time:\n"
        + "\n".join(f"  {v}" for v in sorted(set(violations)))
    )


# ---------------------------------------------------------------------------
# Test 4: allowed leaks still exist (staleness check) and reason guard
# ---------------------------------------------------------------------------
def test_allowed_leaks_still_exist():
    boundary = _load_boundary()
    product_modules = set(boundary["product"])
    stale: list[str] = []

    for core_mod, entries in boundary["allowed_leaks"].items():
        # Find the source file
        parts = core_mod.split(".")
        candidates = [
            _DSH.parent / Path(*parts).with_suffix(".py"),
            _DSH.parent / Path(*parts) / "__init__.py",
        ]
        source_path = None
        for c in candidates:
            if c.exists():
                source_path = c
                break
        if source_path is None:
            stale.append(f"{core_mod}: module file not found")
            continue

        source = source_path.read_text()
        package = core_mod if source_path.name == "__init__.py" else (core_mod.rsplit(".", 1)[0] if "." in core_mod else core_mod)
        imports = _extract_imports(source, package)
        imported_names = set()
        for imp_name, imp_names in imports:
            imported_names.add(imp_name)
            for sub in imp_names:
                imported_names.add(f"{imp_name}.{sub}")

        for entry in entries:
            target = entry["imports"]
            if not any(
                imp == target or imp.startswith(target + ".")
                for imp in imported_names
            ):
                stale.append(f"{core_mod} -> {target}: leak no longer exists")
            # Requirement 4: reason must mention "split"
            if "split" not in entry.get("reason", "").lower():
                stale.append(f'{core_mod} -> {target}: reason does not mention the split')

    assert not stale, (
        "stale or invalid allow-list entries:\n"
        + "\n".join(f"  {s}" for s in stale)
    )


# ---------------------------------------------------------------------------
# Test 5: non-import references exist and are complete
# ---------------------------------------------------------------------------
def _ts_core_package_dirs(boundary: dict[str, Any]) -> list[Path]:
    """Resolve each declared TypeScript core package name to its directory.

    Asserts every declared core package name resolves to exactly one directory.
    """
    core_names = set(boundary["typescript_core_packages"])
    found: dict[str, list[Path]] = {name: [] for name in core_names}
    for pkg_json in sorted((_REPO / "packages").rglob("package.json")):
        if "node_modules" in pkg_json.parts:
            continue
        try:
            data = json.loads(pkg_json.read_text())
            name = data.get("name", "")
        except (json.JSONDecodeError, OSError):
            continue
        if name in core_names:
            found[name].append(pkg_json.parent)
    errors: list[str] = []
    for name, dirs in found.items():
        if len(dirs) != 1:
            errors.append(f"{name}: found {len(dirs)} directories")
    assert not errors, f"TypeScript core package resolution failures: {errors}"
    return [dirs[0] for dirs in found.values()]


def test_references_exist_and_complete():
    boundary = _load_boundary()
    core_modules = set(boundary["core"])
    product_modules = set(boundary["product"])

    # Part 1: declared references still exist (staleness)
    stale: list[str] = []
    for ref in boundary["references"]:
        source_file = _REPO / ref["source"]
        if not source_file.exists():
            stale.append(f"{ref['source']}: file not found")
            continue
        content = source_file.read_text()
        target = ref["target"]
        if target not in content:
            stale.append(f"{ref['source']} -> {target}: reference no longer exists")
    assert not stale, (
        "stale reference entries:\n" + "\n".join(f"  {s}" for s in stale)
    )

    # Part 2: completeness — scan core sources for undeclared product references
    declared_refs: set[tuple[str, str]] = set()
    for ref in boundary["references"]:
        declared_refs.add((ref["source"], ref["target"]))

    # Build product module prefixes for matching
    product_prefixes = sorted(product_modules, key=len, reverse=True)

    missing: list[str] = []

    # Scan core Python files for string constants naming product modules
    for py_path in sorted(_DSH.rglob("*.py")):
        if "__pycache__" in py_path.parts or "tests" in py_path.parts:
            continue
        mod_name = _module_name(py_path, _DSH.parent)
        if mod_name not in core_modules:
            continue

        source = py_path.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        rel_path = str(py_path.relative_to(_REPO))

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                if "refactory_dsh." not in val:
                    continue
                for pm in product_prefixes:
                    # Word-boundary match: pm must not be followed by an
                    # identifier character, to avoid "refactory_dsh.create"
                    # matching inside "refactory_dsh.create_ops".
                    if re.search(re.escape(pm) + r'(?![a-zA-Z0-9_])', val):
                        key = (rel_path, pm)
                        if key not in declared_refs:
                            missing.append(f"{rel_path} references {pm} in string constant")
                        break

    # Scan cordis.patch.yml for product module references
    patch_yml = _REPO / "packages" / "bundle" / "graph" / "cordis.patch.yml"
    if patch_yml.exists():
        content = patch_yml.read_text()
        rel_path = str(patch_yml.relative_to(_REPO))
        for pm in product_modules:
            if re.search(re.escape(pm) + r'(?![a-zA-Z0-9_])', content):
                key = (rel_path, pm)
                if key not in declared_refs:
                    missing.append(f"{rel_path} references {pm}")

    # Scan core TS source files for product module/package references
    ts_core_dirs = [d / "src" for d in _ts_core_package_dirs(boundary)]
    for ts_dir in ts_core_dirs:
        if not ts_dir.is_dir():
            continue
        for ts_path in sorted(ts_dir.rglob("*.ts")):
            content = ts_path.read_text()
            rel_path = str(ts_path.relative_to(_REPO))
            for pm in product_modules:
                if re.search(re.escape(pm) + r'(?![a-zA-Z0-9_])', content):
                    key = (rel_path, pm)
                    if key not in declared_refs:
                        missing.append(f"{rel_path} references {pm}")

    assert not missing, (
        "undeclared product references in core sources:\n"
        + "\n".join(f"  {m}" for m in sorted(set(missing)))
    )


# ---------------------------------------------------------------------------
# Test 6: TypeScript boundary
# ---------------------------------------------------------------------------
def test_typescript_boundary():
    boundary = _load_boundary()
    ts_product = set(boundary["typescript_product_packages"])
    ts_core_pkgs = boundary["typescript_core_packages"]

    violations: list[str] = []

    # Check core TS package.json files
    core_pkg_dirs = _ts_core_package_dirs(boundary)
    for pkg_dir in core_pkg_dirs:
        pkg_json = pkg_dir / "package.json"
        if not pkg_json.exists():
            continue
        data = json.loads(pkg_json.read_text())
        for dep_field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            deps = data.get(dep_field, {})
            for dep_name in deps:
                if dep_name in ts_product:
                    violations.append(f"{pkg_dir.name}/package.json {dep_field} -> {dep_name}")

    # Check core TS source files for product package imports
    ts_core_dirs = [d / "src" for d in core_pkg_dirs]
    for ts_dir in ts_core_dirs:
        if not ts_dir.is_dir():
            continue
        for ts_path in sorted(ts_dir.rglob("*.ts")):
            content = ts_path.read_text()
            for pkg in ts_product:
                if pkg in content:
                    violations.append(f"{ts_path.relative_to(_REPO)} imports {pkg}")

    # Check cordis.patch.yml for product TS package names
    patch_yml = _REPO / "packages" / "bundle" / "graph" / "cordis.patch.yml"
    if patch_yml.exists():
        content = patch_yml.read_text()
        for pkg in ts_product:
            if pkg in content:
                violations.append(f"cordis.patch.yml references product package {pkg}")

    # Check all TS package directories are classified
    all_pkg_dirs = list((_REPO / "packages").rglob("package.json"))
    classified_names = set(ts_product) | set(ts_core_pkgs)
    for pkg_json in all_pkg_dirs:
        if pkg_json.relative_to(_REPO).parts[1:3] == ("node_modules",):
            continue
        try:
            data = json.loads(pkg_json.read_text())
            name = data.get("name", "")
            if name and name.startswith("@deepseek-ai/dsh-") and name not in classified_names:
                violations.append(f"unclassified TS package: {name}")
        except (json.JSONDecodeError, KeyError):
            continue

    assert not violations, (
        "TypeScript boundary violations:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
