"""Map diff hunk line ranges onto function spans via ast (Python) and hunk ranges (TypeScript).

The ``changed_functions`` helper parses a unified diff, maps ``+`` lines onto function
spans in the head version and ``-`` lines onto spans in the base version (via
``git show base:path``).  This finds removed functions and stops matching neighbours
from context lines.

TypeScript changes are reported as one ``<file>:<first>-<last>`` entry per hunk, not
attributed to functions.  Function-level TypeScript attribution is a follow-up.

The module is classified as *product* in ``data/boundary.json`` and no core module
imports it.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from typing import Any


# ── diff parsing ──────────────────────────────────────────────────────────────

_DIFF_HEADER = re.compile(r'^diff --git a/(.+) b/(.+)$')
_HUNK_HEADER = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

_TS_EXTENSIONS = frozenset(('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs'))


def _parse_diff(diff_text: str) -> dict[str, dict[str, list[int]]]:
    """Parse unified diff into per-file added/removed line numbers.

    Returns ``{path: {"added": [line, ...], "removed": [line, ...]}}``.
    """
    result: dict[str, dict[str, list[int]]] = {}
    current_file: str | None = None
    old_line = 0
    new_line = 0

    for line in diff_text.splitlines():
        header = _DIFF_HEADER.match(line)
        if header:
            current_file = header.group(2)
            if current_file not in result:
                result[current_file] = {"added": [], "removed": []}
            continue

        hunk = _HUNK_HEADER.match(line)
        if hunk:
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(3))
            continue

        if current_file is None:
            continue

        if line.startswith('+') and not line.startswith('+++'):
            result[current_file]["added"].append(new_line)
            new_line += 1
        elif line.startswith('-') and not line.startswith('---'):
            result[current_file]["removed"].append(old_line)
            old_line += 1
        else:
            old_line += 1
            new_line += 1

    return result


def _parse_diff_ts_hunks(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse unified diff into per-file hunk ranges for TypeScript files.

    Returns ``{path: [(first_line, last_line), ...]}``.
    For added hunks, uses the new-side range; for deletion-only hunks, uses old-side.
    """
    result: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    hunk_lines: list[int] = []
    hunk_old_lines: list[int] = []
    old_line = 0
    new_line = 0

    def _flush_hunk() -> None:
        if current_file is None:
            return
        if hunk_lines:
            result.setdefault(current_file, []).append((min(hunk_lines), max(hunk_lines)))
        elif hunk_old_lines:
            # Deletion-only hunk: use old-side range
            result.setdefault(current_file, []).append((min(hunk_old_lines), max(hunk_old_lines)))

    for line in diff_text.splitlines():
        header = _DIFF_HEADER.match(line)
        if header:
            _flush_hunk()
            hunk_lines = []
            hunk_old_lines = []
            current_file = header.group(2)
            continue

        hunk = _HUNK_HEADER.match(line)
        if hunk:
            _flush_hunk()
            hunk_lines = []
            hunk_old_lines = []
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(3))
            continue

        if current_file is None:
            continue

        if line.startswith('+') and not line.startswith('+++'):
            hunk_lines.append(new_line)
            new_line += 1
        elif line.startswith('-') and not line.startswith('---'):
            hunk_old_lines.append(old_line)
            old_line += 1
        else:
            old_line += 1
            new_line += 1

    _flush_hunk()
    return result


# ── Python span mapping ──────────────────────────────────────────────────────

def _python_spans(source: str) -> list[tuple[int, int, str]]:
    """Return (start, end, qualname) for each function/class in *source*.

    A decorator counts as part of the function: the span starts at
    ``decorator_list[0].lineno`` when decorators exist.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans: list[tuple[int, int, str]] = []

    def _walk(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                end = node.end_lineno or node.lineno
                name = f"{prefix}{node.name}" if not prefix else f"{prefix}.{node.name}"
                spans.append((start, end, name))
            elif isinstance(node, ast.ClassDef):
                start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                end = node.end_lineno or node.lineno
                class_name = f"{prefix}{node.name}" if not prefix else f"{prefix}.{node.name}"
                spans.append((start, end, class_name))
                _walk(node.body, class_name)

    _walk(tree.body)
    return spans


def _python_top_level_names(source: str) -> list[tuple[int, int, str]]:
    """Return (start, end, NAME) for top-level assignments outside any function."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: list[tuple[int, int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    start = node.lineno
                    end = node.end_lineno or node.lineno
                    names.append((start, end, target.id))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                start = node.lineno
                end = node.end_lineno or node.lineno
                names.append((start, end, node.target.id))
    return names


def _git_show(source_root: Path, base_ref: str, path: str) -> str | None:
    """Read a file from the base revision; return None if it does not exist."""
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "show", f"{base_ref}:{path}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _match_lines_to_spans(
    lines: list[int],
    func_spans: list[tuple[int, int, str]],
    top_level_names: list[tuple[int, int, str]],
) -> set[str]:
    """Map changed line numbers to qualified function names or top-level names."""
    matched: set[str] = set()
    for line in lines:
        found = False
        # Match against function spans (most specific first: methods before classes)
        best: tuple[int, int, str] | None = None
        for start, end, name in func_spans:
            if start <= line <= end:
                if best is None or (end - start) < (best[1] - best[0]):
                    best = (start, end, name)
        if best is not None:
            matched.add(best[2])
            found = True
        if not found:
            # Check top-level assignments
            for start, end, name in top_level_names:
                if start <= line <= end:
                    matched.add(name)
                    found = True
                    break
    return matched


def changed_functions(diff_text: str, source_root: Path, base_ref: str) -> list[dict[str, Any]]:
    """Map diff hunk line ranges onto function spans.

    Returns ``[{"function": "path::qualname", "file": "path", "line": N, "test": bool}, ...]``
    for Python, and ``[{"function": "path:first-last", "file": "path", "line": N, "test": bool}, ...]``
    for TypeScript.
    """
    if not diff_text.strip():
        return []

    per_file = _parse_diff(diff_text)
    ts_hunks = _parse_diff_ts_hunks(diff_text)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for path, changes in per_file.items():
        ext = Path(path).suffix

        if ext == '.py':
            # ── head-side: added lines ──
            head_source = None
            full_path = source_root / path
            if full_path.is_file():
                try:
                    head_source = full_path.read_text()
                except OSError:
                    pass

            if head_source and changes["added"]:
                func_spans = _python_spans(head_source)
                top_names = _python_top_level_names(head_source)
                matched = _match_lines_to_spans(changes["added"], func_spans, top_names)
                for name in sorted(matched):
                    key = f"{path}::{name}"
                    if key not in seen:
                        seen.add(key)
                        # Find the line number of this function
                        line = changes["added"][0]
                        for start, end, n in func_spans + top_names:
                            if n == name:
                                line = start
                                break
                        is_test = (name.startswith("test_") or name.startswith("Test")
                                   or "/tests/" in path or "/test/" in path
                                   or path.startswith("tests/") or path.startswith("test/"))
                        result.append({"function": key, "file": path, "line": line, "test": is_test})

            # ── base-side: removed lines ──
            if changes["removed"]:
                base_source = _git_show(source_root, base_ref, path)
                if base_source:
                    base_func_spans = _python_spans(base_source)
                    base_top_names = _python_top_level_names(base_source)
                    base_matched = _match_lines_to_spans(changes["removed"], base_func_spans, base_top_names)
                    # Only add functions that exist in the base but not in the head
                    head_names = set()
                    if head_source:
                        head_names = {n for _, _, n in _python_spans(head_source)}
                        head_names |= {n for _, _, n in _python_top_level_names(head_source)}
                    for name in sorted(base_matched):
                        key = f"{path}::{name}"
                        if key not in seen:
                            seen.add(key)
                            line = changes["removed"][0]
                            for start, end, n in base_func_spans + base_top_names:
                                if n == name:
                                    line = start
                                    break
                            is_test = (name.startswith("test_") or name.startswith("Test")
                                       or "/tests/" in path or "/test/" in path
                                       or path.startswith("tests/") or path.startswith("test/"))
                            result.append({"function": key, "file": path, "line": line, "test": is_test})

        elif ext in _TS_EXTENSIONS:
            # TypeScript: one entry per hunk range
            hunks = ts_hunks.get(path, [])
            for first, last in hunks:
                key = f"{path}:{first}-{last}"
                if key not in seen:
                    seen.add(key)
                    is_test = ("/tests/" in path or "/test/" in path
                               or path.startswith("tests/") or path.startswith("test/")
                               or ".spec." in path or ".test." in path)
                    result.append({"function": key, "file": path, "line": first, "test": is_test})

    return result


def format_changed_functions(entries: list[dict[str, Any]]) -> str:
    """Format the changed-functions list as a human-readable string for prompt rendering."""
    if not entries:
        return "(none)"
    lines = []
    for entry in entries:
        lines.append(f"- {entry['function']} ({entry['file']}:{entry['line']})")
    return "\n".join(lines)


def compute_and_write(
    diff_text: str, source_root: Path, workdir: Path, base_ref: str,
) -> str:
    """Compute changed functions and write ``changed-functions.json``.

    Returns the formatted string for the ``changed_functions`` fact.
    """
    entries = changed_functions(diff_text, source_root, base_ref) if diff_text.strip() else []
    (workdir / "changed-functions.json").write_text(json.dumps(entries, indent=2) + "\n")
    return format_changed_functions(entries)
