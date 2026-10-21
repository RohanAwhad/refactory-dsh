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

#: A generated source file says so in its first lines ("Generated … Do not edit"), as
#: ``gen_discovery`` writes ``generated-help.ts``. Nobody reviews its callers by hand.
_GENERATED_MARK = re.compile(r'\bgenerated\b.*\bdo not edit\b', re.IGNORECASE)
_GENERATED_HEAD_LINES = 5


def is_test_path(path: str) -> bool:
    """Whether *path* is a test file, by its directory or its name."""
    name = Path(path).name
    return ("/tests/" in path or "/test/" in path
            or path.startswith("tests/") or path.startswith("test/")
            or ".spec." in name or ".test." in name
            or (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py"))


def is_generated(source_root: Path, path: str) -> bool:
    """Whether the file at *path* carries a generated-file mark in its first lines."""
    try:
        with open(source_root / path, encoding="utf-8", errors="replace") as handle:
            head = [next(handle, "") for _ in range(_GENERATED_HEAD_LINES)]
    except OSError:
        return False
    return any(_GENERATED_MARK.search(line) for line in head)


def review_functions(entries: list[dict[str, Any]]) -> list[str]:
    """The changed functions a reviewer answers repository questions for: product code only.

    Tests and generated files are left out. Their callers are the test runner and the
    generator, so a reviewer has no path:line to cite, and requiring one only ends runs
    whose reviews passed.
    """
    return [entry["function"] for entry in entries
            if isinstance(entry, dict) and "function" in entry
            and not entry.get("test") and not entry.get("generated")]


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

    Returns ``[{"function": "path::qualname", "file": "path", "line": N, "test": bool,
    "generated": bool}, ...]`` for Python, and the same with ``"function": "path:first-last"``
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
        generated = is_generated(source_root, path)

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
                                   or is_test_path(path))
                        result.append({"function": key, "file": path, "line": line, "test": is_test,
                                       "generated": generated})

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
                                       or is_test_path(path))
                            result.append({"function": key, "file": path, "line": line, "test": is_test,
                                           "generated": generated})

        elif ext in _TS_EXTENSIONS:
            # TypeScript: one entry per hunk range
            hunks = ts_hunks.get(path, [])
            for first, last in hunks:
                key = f"{path}:{first}-{last}"
                if key not in seen:
                    seen.add(key)
                    result.append({"function": key, "file": path, "line": first,
                                   "test": is_test_path(path), "generated": generated})

    return result


def format_changed_functions(entries: list[dict[str, Any]]) -> str:
    """Format, for the reviewers' prompts, the changed functions that need repository questions."""
    if not entries:
        return "(none)"
    needed = set(review_functions(entries))
    lines = [f"- {entry['function']} ({entry['file']}:{entry['line']})"
             for entry in entries if entry["function"] in needed]
    others = len(entries) - len(lines)
    if others:
        lines.append(f"({others} more changed in tests or generated files; "
                     "they need no repository questions)")
    return "\n".join(lines)


def _git(source_root: Path, *args: str) -> str | None:
    """One git command's stdout in *source_root*, or None when it fails."""
    try:
        result = subprocess.run(["git", "-C", str(source_root), *args],
                                capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout if result.returncode == 0 else None


def merged_upstream(source_root: Path, base_ref: str) -> str | None:
    """The newest commit the run's branch merged in from outside its own history, or None.

    A run merges its base branch in when the branch has moved (a builder resolving a
    conflict, or delivery). Each such merge's other parent is history the run did not
    write, so the newest one marks where the run's own changes begin.
    """
    merges = _git(source_root, "rev-list", "--first-parent", "--merges", f"{base_ref}..HEAD")
    for merge in (merges or "").split():
        parents = (_git(source_root, "rev-list", "--parents", "-n", "1", merge) or "").split()[2:]
        for parent in parents:
            if _git(source_root, "merge-base", "--is-ancestor", parent, base_ref) is None:
                return parent
    return None


def _own_entries(entries: list[dict[str, Any]], upstream_entries: list[dict[str, Any]],
                 ) -> list[dict[str, Any]]:
    """The entries the run itself changed: also changed relative to the upstream it merged in.

    A Python function is matched by name. A TypeScript hunk is kept when it overlaps a hunk
    of the upstream diff in the same file; both diffs end at HEAD, so their added lines
    share HEAD's numbering.
    """
    names = {entry["function"] for entry in upstream_entries}
    ranges: dict[str, list[tuple[int, int]]] = {}
    for entry in upstream_entries:
        match = re.search(r':(\d+)-(\d+)$', entry["function"])
        if match:
            ranges.setdefault(entry["file"], []).append((int(match.group(1)), int(match.group(2))))
    kept = []
    for entry in entries:
        match = re.search(r':(\d+)-(\d+)$', entry["function"])
        if match is None:
            if entry["function"] in names:
                kept.append(entry)
            continue
        first, last = int(match.group(1)), int(match.group(2))
        if any(first <= other_last and other_first <= last
               for other_first, other_last in ranges.get(entry["file"], [])):
            kept.append(entry)
    return kept


def compute_and_write(
    diff_text: str, source_root: Path, workdir: Path, base_ref: str,
) -> str:
    """Compute changed functions and write ``changed-functions.json``.

    Only the run's own changes count: when the branch merged its base in after the run
    began, an entry must also be changed relative to that merged-in commit, so the base
    branch's own work never becomes the run's to review.

    Returns the formatted string for the ``changed_functions`` fact.
    """
    entries = changed_functions(diff_text, source_root, base_ref) if diff_text.strip() else []
    upstream = merged_upstream(source_root, base_ref) if entries else None
    if upstream is not None:
        upstream_diff = _git(source_root, "diff", upstream, "HEAD") or ""
        entries = _own_entries(entries, changed_functions(upstream_diff, source_root, upstream))
    (workdir / "changed-functions.json").write_text(json.dumps(entries, indent=2) + "\n")
    return format_changed_functions(entries)
