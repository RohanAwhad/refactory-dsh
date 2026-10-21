"""AST-based patch-target detection for evidence scope enforcement.

Checks whether a test function patches the project's own modules on the
tested path, which would disqualify it from ``connected`` scope (it is
``local``).  Allowlisted targets (clocks, randomness, environment) are
never flagged.

The attribute-chain walk uses the owning module's imports to resolve
targets like ``refactory_dsh.budgets.time.time`` to ``time.time``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

ALLOWED_TARGETS = frozenset({
    # Clocks
    "time.time", "time.sleep", "time.monotonic", "time.perf_counter",
    "datetime.datetime.now", "datetime.datetime.utcnow",
    "Date", "Date.now", "performance.now",
    # Randomness
    "random.random", "random.randint", "random.choice", "random.shuffle",
    "random.sample", "random.uniform", "Math.random",
    # Environment
    "os.environ", "os.getenv", "process.env",
})


def _resolve_through_imports(
    target: str, imports: dict[str, str],
) -> str:
    """Walk an attribute chain through import aliases.

    ``imports`` maps local names to their fully-qualified origins.
    ``refactory_dsh.budgets.time.time`` resolves to ``time.time`` when
    ``budgets.py`` has ``import time``.
    """
    parts = target.split(".")
    # Walk from the longest prefix that matches an import
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in imports:
            resolved = imports[prefix]
            suffix = ".".join(parts[i:])
            return f"{resolved}.{suffix}" if suffix else resolved
    return target


def _collect_imports(tree: ast.Module) -> dict[str, str]:
    """Map local names to fully-qualified module paths from import statements."""
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                imports[name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                imports[name] = f"{module}.{alias.name}" if module else alias.name
    return imports


def _find_function_body(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """Find a function definition by name in the module."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


def _find_fixture_bodies(
    tree: ast.Module, func: ast.FunctionDef, conftest_trees: list[ast.Module],
) -> list[ast.FunctionDef]:
    """Find fixture functions requested by *func*'s arguments."""
    fixtures: list[ast.FunctionDef] = []
    arg_names = {a.arg for a in func.args.args if a.arg not in ("self", "cls")}
    all_trees = [tree] + conftest_trees
    for t in all_trees:
        for node in ast.walk(t):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check for @pytest.fixture decorator
                for dec in node.decorator_list:
                    dec_name = ""
                    if isinstance(dec, ast.Attribute):
                        dec_name = dec.attr
                    elif isinstance(dec, ast.Name):
                        dec_name = dec.id
                    elif isinstance(dec, ast.Call):
                        if isinstance(dec.func, ast.Attribute):
                            dec_name = dec.func.attr
                        elif isinstance(dec.func, ast.Name):
                            dec_name = dec.func.id
                    if dec_name == "fixture" and node.name in arg_names:
                        fixtures.append(node)
    return fixtures


def _extract_patch_targets_from_nodes(
    nodes: list[ast.AST], imports: dict[str, str],
) -> list[str]:
    """Extract patched target strings from AST nodes."""
    targets: list[str] = []
    for node in ast.walk(ast.Module(body=[n for n in nodes if isinstance(n, ast.stmt)], type_ignores=[])):
        # monkeypatch.setattr('module.func', ...)  — string form
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "setattr":
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    targets.append(node.args[0].value)
                elif len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                    # monkeypatch.setattr(obj, 'attr', ...) — object form
                    if isinstance(node.args[0], ast.Name):
                        obj_name = node.args[0].id
                        resolved = imports.get(obj_name, obj_name)
                        targets.append(f"{resolved}.{node.args[1].value}")
                    elif isinstance(node.args[0], ast.Attribute):
                        # e.g. design_ops.func
                        parts_list = []
                        n = node.args[0]
                        while isinstance(n, ast.Attribute):
                            parts_list.append(n.attr)
                            n = n.value
                        if isinstance(n, ast.Name):
                            parts_list.append(n.id)
                        parts_list.reverse()
                        obj_str = ".".join(parts_list)
                        resolved = _resolve_through_imports(obj_str, imports)
                        targets.append(f"{resolved}.{node.args[1].value}")
            # monkeypatch.setenv / monkeypatch.delenv — allowlisted
            elif isinstance(func, ast.Attribute) and func.attr in ("setenv", "delenv"):
                pass  # always allowed
            # patch('module.func') / patch.object(obj, 'attr')
            elif isinstance(func, ast.Attribute) and func.attr == "patch":
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    targets.append(node.args[0].value)
            elif isinstance(func, ast.Attribute) and func.attr == "object":
                # patch.object(obj, 'attr')
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                    if isinstance(node.args[0], ast.Name):
                        obj_name = node.args[0].id
                        resolved = imports.get(obj_name, obj_name)
                        targets.append(f"{resolved}.{node.args[1].value}")
        # @patch('module.func') decorator
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    if isinstance(dec.func, ast.Attribute) and dec.func.attr == "patch":
                        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                            targets.append(dec.args[0].value)
                    elif isinstance(dec.func, ast.Name) and dec.func.id == "patch":
                        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                            targets.append(dec.args[0].value)
    return targets


def _is_on_path(target: str, site_files: list[str]) -> bool:
    """Whether the patched target lives in one of the site files."""
    # Strip the function/attribute name; get the module path
    parts = target.split(".")
    for site_file in site_files:
        # Normalize: refactory_dsh/foo.py -> refactory_dsh.foo
        normalized = site_file.replace("/", ".").replace(".py", "")
        for i in range(len(parts)):
            candidate = ".".join(parts[:i + 1])
            if candidate == normalized or normalized.endswith(candidate):
                return True
    return False


def check_python_patches(
    test_source: str, test_name: str,
    project_modules: list[str], site_files: list[str],
) -> list[str]:
    """Return project-module targets patched on the tested path.

    Checks the specific test function ``test_name`` plus the fixtures it
    requests (including conftest fixtures), not the whole file.
    The attribute-chain walk uses the owning module's imports to resolve
    targets.
    """
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return []

    imports = _collect_imports(tree)

    # Find the specific test function
    func = _find_function_body(tree, test_name) if test_name else None
    if func is None and test_name:
        # Try without 'test_' prefix or with class
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == test_name:
                    func = node
                    break

    if func is None:
        # Fall back to whole-file check
        targets = _extract_patch_targets_from_nodes([tree], imports)
    else:
        # Check function body + its fixtures
        fixture_bodies = _find_fixture_bodies(tree, func, [])
        all_nodes: list[ast.AST] = [func] + fixture_bodies
        targets = _extract_patch_targets_from_nodes(all_nodes, imports)

    violations: list[str] = []
    for target in targets:
        resolved = _resolve_through_imports(target, imports)
        if resolved in ALLOWED_TARGETS or target in ALLOWED_TARGETS:
            continue
        # Check if it's a project module on the path
        is_project = any(resolved.startswith(pm) or target.startswith(pm)
                         for pm in project_modules)
        if is_project and _is_on_path(resolved, site_files):
            violations.append(target)
    return violations


def check_typescript_patches(
    test_source: str, test_name: str,
    project_modules: list[str], site_files: list[str],
) -> list[str]:
    """Return project-module targets patched on the tested path.

    Uses regex since no TypeScript AST parser is available in the Python
    runtime.  Project modules are relative imports (starting with ``./`` or
    ``../``) plus workspace package names.
    """
    violations: list[str] = []

    # vi.mock('module')
    for m in re.finditer(r"vi\.mock\(['\"]([^'\"]+)['\"]\)", test_source):
        module = m.group(1)
        if module.startswith("./") or module.startswith("../"):
            # Relative import — project module
            # Normalize relative path
            normalized = module.lstrip("./").replace("/", ".")
            if any(normalized in sf.replace("/", ".").replace(".ts", "").replace(".py", "")
                   for sf in site_files):
                violations.append(module)
        elif any(module.startswith(pm) for pm in project_modules):
            violations.append(module)
        # External packages (e.g. @deepseek-ai/) are not flagged

    # vi.spyOn(object, 'method')
    for m in re.finditer(r"vi\.spyOn\((\w+),\s*['\"](\w+)['\"]\)", test_source):
        obj = m.group(1)
        method = m.group(2)
        # Check if the object is imported from a project module
        import_match = re.search(
            rf"import\s+.*?\b{re.escape(obj)}\b.*?from\s+['\"]([^'\"]+)['\"]",
            test_source,
        )
        if import_match:
            from_module = import_match.group(1)
            if from_module.startswith("./") or from_module.startswith("../"):
                normalized = from_module.lstrip("./").replace("/", ".")
                if any(normalized in sf.replace("/", ".").replace(".ts", "").replace(".py", "")
                       for sf in site_files):
                    violations.append(f"{obj}.{method}")

    return violations
