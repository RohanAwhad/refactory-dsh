"""Which tests design mode's self-check runs for an emission, in the order it runs them (b41a).

The engine reads the repository's own imports; it names no test or file of any
repository. Classified as a *product* module in ``data/boundary.json``, and no core
module imports it.

The selection, in order:

- **Engine tests**, only when the emission changed a hub. A *hub* is a source module at
  least a quarter of the repository's Python test files import (`HUB_SHARE`): the
  engine or graph modules most of the suite is built on. The engine tests are the
  structural tests (test files that import no module of the repository: they read it
  as files, its layout, records and docs, so no import says a change cannot reach them)
  and the tests that import every hub, the ones built on the whole engine.
- **The test files the emission touched.**
- **The last check's failures** that are not inherited (dm-128's set), as test ids.
- **The tests that import a module the emission changed**, depth 1, the ones importing
  the most changed modules first.
- **The structural tests**, last, when a source file changed but no hub did.

For a TypeScript runner only the spec files the emission touched are selected: the
check's own vitest invocation (``vitest run``) has no related-file selection, and
``--related`` there is an unknown option. A docs-only emission selects nothing.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A source module at least this share of the Python test files import is a hub.
HUB_SHARE = 0.25

#: Files that are only read, never run or imported: an emission of these alone selects nothing.
DOC_SUFFIXES = (".md", ".rst", ".txt")

_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


@dataclass
class Selection:
    """The ordered selection, and how it was made, for the self-check's record."""

    tests: list[str] = field(default_factory=list)
    engine: list[str] = field(default_factory=list)
    hubs_changed: list[str] = field(default_factory=list)
    touched_tests: list[str] = field(default_factory=list)
    last_failures: list[str] = field(default_factory=list)
    importers: list[str] = field(default_factory=list)
    structural: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {"engine": self.engine, "hubs_changed": self.hubs_changed,
                "touched_tests": self.touched_tests, "last_failures": self.last_failures,
                "importers": self.importers, "structural": self.structural}


def is_test_file(path: str) -> bool:
    """Whether a runner collects ``path`` as a test module (pytest's or vitest's naming)."""
    from refactory_dsh.base_check import _TEST_FILE_PATTERNS

    return bool(_TEST_FILE_PATTERNS.search(path))


def repository_files(root: Path) -> list[str]:
    """The repository's files, from git; a directory that is not a checkout is walked."""
    try:
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                                timeout=60, check=True).stdout.decode(errors="replace")
        return [name for name in listed.split("\0") if name]
    except (OSError, subprocess.SubprocessError):
        skip = {".git", ".venv", "node_modules", "__pycache__"}
        return [str(path.relative_to(root)) for path in root.rglob("*")
                if path.is_file() and not skip & set(path.relative_to(root).parts)]


def _import_roots(files: list[str]) -> list[Path]:
    """Where an absolute import resolves from: the root, each Python project, its ``src``."""
    projects = {Path(name).parent for name in files if Path(name).name in _PROJECT_MARKERS}
    roots = [Path("")]
    for project in sorted(projects, key=lambda p: (len(p.parts), str(p))):
        roots += [project, project / "src"]
    return roots


def _resolve(module: str, roots: list[Path], known: set[str]) -> str | None:
    parts = module.replace(".", "/")
    for base in roots:
        for candidate in (f"{parts}.py", f"{parts}/__init__.py"):
            name = (base / candidate).as_posix()
            if name in known:
                return name
    return None


def imports_of(test: str, text: str, roots: list[Path], known: set[str]) -> set[str]:
    """The repository files a Python test file imports, depth 1.

    ``from package import name`` resolves to the submodule when ``name`` is one, and to
    the package otherwise. A test's own directory comes first, as pytest puts it on the
    path; a relative import resolves from the test's package.
    """
    try:
        tree = ast.parse(text, filename=test)
    except (SyntaxError, ValueError):
        return set()
    own = [Path(test).parent, *roots]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve(alias.name, own, known)
                if resolved:
                    found.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = Path(test).parent
                for _ in range(node.level - 1):
                    package = package.parent
                bases, module = [package], node.module or ""
            else:
                bases, module = own, node.module or ""
            for alias in node.names:
                dotted = f"{module}.{alias.name}" if module else alias.name
                resolved = _resolve(dotted, bases, known) or (
                    _resolve(module, bases, known) if module else None)
                if resolved:
                    found.add(resolved)
    return found


def import_map(root: Path, files: list[str], extra: tuple[str, ...] = (),
               scope: list[str] | None = None) -> dict[str, set[str]]:
    """Each Python test file the check runs (in ``scope``) and the repository files it imports.

    ``extra`` names files that resolve though they are gone, the ones an emission
    deleted, so a test still importing one is found.
    """
    known = set(files) | set(extra)
    roots = _import_roots(files)
    found: dict[str, set[str]] = {}
    for name in files:
        if not name.endswith(".py") or not is_test_file(name) or not _in_scope(name, scope):
            continue
        try:
            text = (root / name).read_text(errors="replace")
        except OSError:
            continue
        found[name] = imports_of(name, text, roots, known) - {name}
    return found


def pytest_scope(check_command: str, root: Path) -> list[str] | None:
    """The paths the check's pytest steps collect from, or None when one collects everything.

    A path is a step's argument that names a file or directory of the repository; a step
    that names none collects from the root, and so does a check with no pytest step.
    """
    import shlex

    from refactory_dsh.base_check import _runner, _steps

    scope: list[str] = []
    steps = [step for step in _steps(check_command) if _runner(step) == "pytest"]
    if not steps:
        return None
    for step in steps:
        try:
            words = shlex.split(step)
        except ValueError:
            return None
        named = [word.rstrip("/") for word in words[1:]
                 if not word.startswith("-") and (root / word).exists()
                 and Path(word).name not in ("python", "python3", "pytest")]
        if not named:
            return None
        scope += named
    return scope


def _in_scope(path: str, scope: list[str] | None) -> bool:
    return scope is None or any(path == entry or path.startswith(entry + "/") for entry in scope)


def _source(path: str) -> bool:
    from refactory_dsh.changed_functions import is_test_path

    return not is_test_path(path)


def hubs(imports: dict[str, set[str]]) -> set[str]:
    """The source modules at least `HUB_SHARE` of the test files import."""
    if not imports:
        return set()
    counts: dict[str, int] = {}
    for modules in imports.values():
        for module in modules:
            counts[module] = counts.get(module, 0) + 1
    floor = HUB_SHARE * len(imports)
    return {module for module, count in counts.items() if count >= floor and _source(module)}


def _failures(prev_record: dict[str, Any], root: Path) -> list[str]:
    """The last check's failures that are not inherited, as ids, in the order it found them."""
    if prev_record.get("status") != "fail":
        return []
    inherited = set(prev_record.get("inherited", []))
    ids: list[str] = []
    for runner in prev_record.get("runners", []):
        for failure in runner.get("failures", []):
            test_id = str(failure.get("test_id") or "")
            if not test_id or test_id in inherited or test_id in ids:
                continue
            file = test_id.split("::")[0] if "::" in test_id else test_id.split(" > ")[0].strip()
            if (root / file).is_file():
                ids.append(test_id)
    return ids


def select(touched: list[str], root: Path, prev_record: dict[str, Any] | None = None, *,
           check_command: str = "", files: list[str] | None = None,
           imports: dict[str, set[str]] | None = None) -> Selection:
    """The self-check's selection for an emission that changed ``touched`` in ``root``.

    ``touched`` are paths from the repository root (`base_check._all_changed_files`);
    ``files`` the repository's files, listed from git when not given. Only Python tests
    the check's pytest steps collect are selected (`pytest_scope`), and the hubs are
    counted among them. ``imports`` stands for `import_map` when given: the imports of
    a tree as it was, for a selection replayed on it.
    """
    from refactory_dsh.changed_functions import is_generated

    root = Path(root)
    files = ([] if imports is not None else repository_files(root)) if files is None else files
    scope = pytest_scope(check_command, root) if check_command else None
    last_failures = _failures(prev_record or {}, root)
    touched_tests: list[str] = []
    sources: list[str] = []
    for name in dict.fromkeys(path.strip() for path in touched if path.strip()):
        if name.endswith(DOC_SUFFIXES) or is_generated(root, name):
            continue
        if is_test_file(name):
            if (root / name).is_file() and (not name.endswith(".py") or _in_scope(name, scope)):
                touched_tests.append(name)
        else:
            sources.append(name)
    selection = Selection(touched_tests=sorted(touched_tests), last_failures=last_failures)
    if not sources and not touched_tests and not last_failures:
        return selection
    if imports is None:
        imports = import_map(root, files, tuple(sources), scope)
    hub_set = hubs(imports)
    structural = sorted(test for test, modules in imports.items() if not modules)
    changed = set(sources)
    selection.hubs_changed = sorted(changed & hub_set)
    if selection.hubs_changed:
        whole_engine = sorted((test for test, modules in imports.items()
                               if modules and hub_set <= modules),
                              key=lambda test: (len(imports[test]), test))
        selection.engine = [*structural, *whole_engine]
    importers = [test for test, modules in imports.items() if modules & changed]
    selection.importers = sorted(importers, key=lambda test: (
        -len(imports[test] & changed), len(imports[test]), test))
    if sources and not selection.hubs_changed:
        selection.structural = structural
    ordered: list[str] = []
    whole: set[str] = set()
    for name in [*selection.engine, *selection.touched_tests]:
        if name not in whole:
            whole.add(name)
            ordered.append(name)
    for test_id in last_failures:
        file = test_id.split("::")[0] if "::" in test_id else test_id.split(" > ")[0].strip()
        if file not in whole:
            ordered.append(test_id)
    for name in [*selection.importers, *selection.structural]:
        if name not in whole:
            whole.add(name)
            ordered.append(name)
    selection.tests = ordered
    return selection
