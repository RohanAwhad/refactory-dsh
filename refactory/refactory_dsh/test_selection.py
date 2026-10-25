"""Build the import-based test selection for the self-check.

Classified as a *product* module in ``data/boundary.json`` — no core module
imports it (b39b's lesson: ``test_boundary::test_no_core_imports_product_static``).

The module owns the "which tests to run" question: given the files an emission
touched, it returns an ordered list of test file paths that the self-check
should run, plus the touched source files (for vitest ``--related``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


def _find_python_roots(source_root: Path) -> list[Path]:
    """Find Python package roots (directories with pyproject.toml or setup.py).

    Returns paths relative to source_root that could be on PYTHONPATH, plus
    source_root itself.
    """
    roots = [source_root]
    for marker in ("pyproject.toml", "setup.py", "setup.cfg"):
        for found in source_root.rglob(marker):
            parent = found.parent
            if parent != source_root and "node_modules" not in str(parent):
                if parent not in roots:
                    roots.append(parent)
    return roots


def build_import_map(source_root: Path) -> dict[str, list[str]]:
    """Build a depth-1 import reverse map: source file → [test files that import it].

    For Python test files (``is_test_path``): parse ``import`` and ``from … import``
    statements and resolve dotted names to relative file paths.

    For TypeScript/JS test files (``.spec.ts``, ``.test.ts``, etc.): regex-extract
    ``import … from '…'`` and ``require('…')`` and resolve relative paths.

    Depth-1 only.  The b39 evidence shows depth-1 caught 4/4 (b39a) and 8/9 (b39b)
    round-1 failures.  The full check is the backstop.
    """
    from refactory_dsh.changed_functions import is_test_path

    reverse: dict[str, list[str]] = {}  # source_rel → [test_rel, …]
    python_roots = _find_python_roots(source_root)

    # ── Python test files ──
    for py_file in sorted(source_root.rglob("*.py")):
        if "node_modules" in str(py_file) or ".venv" in str(py_file):
            continue
        rel = str(py_file.relative_to(source_root))
        if not is_test_path(rel):
            continue
        try:
            tree = ast.parse(py_file.read_text(errors="replace"), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.append(node.module)
            for mod in modules:
                # Resolve dotted import name → relative file path
                # Try each Python root as a base for resolution
                parts = mod.replace(".", "/")
                candidates = [parts + ".py", parts + "/__init__.py"]
                resolved = False
                for py_root in python_roots:
                    for cand in candidates:
                        abs_path = py_root / cand
                        if abs_path.is_file():
                            # Store relative to source_root
                            try:
                                rel_source = str(abs_path.relative_to(source_root))
                            except ValueError:
                                continue
                            reverse.setdefault(rel_source, [])
                            if rel not in reverse[rel_source]:
                                reverse[rel_source].append(rel)
                            resolved = True
                            break
                    if resolved:
                        break

    # ── TypeScript / JS test files ──
    _TS_TEST = re.compile(r"\.(spec|test)\.(ts|tsx|js|jsx|mjs|cjs)$")
    _TS_IMPORT = re.compile(
        r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
    )
    _TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    for ts_file in sorted(source_root.rglob("*")):
        rel = str(ts_file.relative_to(source_root))
        if not _TS_TEST.search(rel):
            continue
        try:
            text = ts_file.read_text(errors="replace")
        except OSError:
            continue
        for m in _TS_IMPORT.finditer(text):
            target = m.group(1) or m.group(2)
            if not target or not target.startswith("."):
                continue
            resolved = (ts_file.parent / target).resolve()
            # Try with and without extensions
            for ext in ("", *_TS_EXT):
                candidate = Path(str(resolved) + ext)
                if candidate.is_file():
                    try:
                        cand_rel = str(candidate.relative_to(source_root))
                    except ValueError:
                        continue
                    reverse.setdefault(cand_rel, [])
                    if rel not in reverse[cand_rel]:
                        reverse[cand_rel].append(rel)
                    break

    return reverse


def _fan_in(import_map: dict[str, list[str]], touched_sources: list[str]) -> dict[str, int]:
    """For each touched source, count how many test files import it (fan-in)."""
    return {src: len(import_map.get(src, [])) for src in touched_sources}


def select_test_ids(
    touched_files: list[str],
    source_root: Path,
    prev_record: dict[str, Any],
    check_command: str,
) -> tuple[list[str], list[str]]:
    """Return ``(ordered_test_ids, touched_source_files)`` for the self-check.

    Partitions ``touched_files`` using ``is_test_path`` and ``is_generated``:

    - **Test files** → tier (a), directly touched.
    - **Generated files** → skipped.
    - **Source files** → lookup targets for ``build_import_map``.

    The ordering:

    - **Engine-fan-in tests** — when a high-fan-in module changed, the tests
      importing it sort first overall.  High-fan-in means the touched module
      is imported by more test files than the median of all modules' fan-ins.
      No file or path names in the engine.
    - **Tier 1:** Test files the emission touched directly.
    - **Tier 2:** The last check's failures (from ``prev_record``), filtered
      to non-inherited.
    - **Tier 3:** Tests that import the touched source modules, sorted by the
      number of touched modules they import (most-touched first).

    A docs-only emission (all ``.md`` or generated files) returns empty lists.
    """
    from refactory_dsh.changed_functions import is_generated, is_test_path

    touched_tests: list[str] = []
    touched_sources: list[str] = []

    for f in touched_files:
        if not f.strip():
            continue
        # Skip .md files
        if f.endswith(".md"):
            continue
        if is_generated(source_root, f):
            continue
        if is_test_path(f):
            touched_tests.append(f)
        else:
            touched_sources.append(f)

    if not touched_tests and not touched_sources:
        return [], []

    # Build import map
    import_map = build_import_map(source_root)
    fan_in_counts = _fan_in(import_map, touched_sources)

    # Detect high-fan-in modules: those with fan-in above the median of all
    # modules' fan-ins in the import map.
    all_fan_ins = sorted(len(tests) for tests in import_map.values()) if import_map else []
    median_fan_in = all_fan_ins[len(all_fan_ins) // 2] if all_fan_ins else 0
    high_fan_in_sources = [
        src for src in touched_sources
        if fan_in_counts.get(src, 0) > median_fan_in and fan_in_counts.get(src, 0) > 0
    ]

    # Tier 0: engine-fan-in tests — tests importing high-fan-in touched modules
    engine_tests: list[str] = []
    if high_fan_in_sources:
        engine_test_set: set[str] = set()
        for src in high_fan_in_sources:
            for test_file in import_map.get(src, []):
                engine_test_set.add(test_file)
        # Sort by number of high-fan-in modules imported (descending)
        def _engine_sort_key(tf: str) -> int:
            return -sum(1 for src in high_fan_in_sources if tf in import_map.get(src, []))
        engine_tests = sorted(engine_test_set, key=_engine_sort_key)

    # Tier 2: last check's failures, non-inherited
    prev_failing_files: list[str] = []
    inherited_set = set(prev_record.get("inherited", []))
    if prev_record.get("status") == "fail":
        for runner in prev_record.get("runners", []):
            for f in runner.get("failures", []):
                tid = f.get("test_id", "")
                if tid and tid not in inherited_set:
                    # Extract the file part
                    if "::" in tid:
                        tf = tid.split("::")[0]
                    elif " > " in tid:
                        tf = tid.split(" > ")[0].strip()
                    else:
                        tf = tid
                    if tf and tf not in prev_failing_files:
                        prev_failing_files.append(tf)

    # Tier 3: tests importing touched source modules
    import_tests: list[str] = []
    for src in touched_sources:
        for test_file in import_map.get(src, []):
            if test_file not in import_tests:
                import_tests.append(test_file)
    # Sort by number of touched modules imported (descending)
    def _import_sort_key(tf: str) -> tuple[int, str]:
        count = sum(1 for src in touched_sources if tf in import_map.get(src, []))
        return (-count, tf)
    import_tests.sort(key=_import_sort_key)

    # Merge with dedup preserving priority order
    seen: set[str] = set()
    ordered: list[str] = []
    for tier in (engine_tests, touched_tests, prev_failing_files, import_tests):
        for tf in tier:
            if tf not in seen:
                seen.add(tf)
                ordered.append(tf)

    return ordered, touched_sources
