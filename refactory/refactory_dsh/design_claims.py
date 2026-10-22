"""Deterministic claims checker for design documents (dm-126).

A design's claims about existing code — paths, quoted lines and named
identifiers — are verified mechanically before the design review agent runs.
Mismatches reloop ``design_doc`` without spending a design-review attempt.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from refactory_dsh import worktree
from refactory_dsh.nn import Verdict

# Match the subject-at-citation-quote claim format:
#   `subject` at `path:N`: `quote`
# OR the subject-less variant:
#   `path:N`: `quote`
_CLAIM_RE = re.compile(
    r'(?:`([^`]+)`\s+at\s+)?'          # group 1: optional subject
    r'`([A-Za-z_][\w/._-]*\.\w+)'      # group 2: file path
    r':(\d+)(?:-(\d+))?`'              # group 3: start line, group 4: optional end line
    r'\s*:\s*`([^`]+)`'                 # group 5: quoted text
)

# Code-shaped identifier patterns in a subject span.
_CALL_RE = re.compile(r'(\w+)\s*\(')       # name()
_DOT_RE = re.compile(r'(\w+)\.(\w+)')      # a.b
_BARE_NAME_RE = re.compile(r'^(\w+)$')      # bare name

_TS_EXTENSIONS = {'.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs'}


def _strip_fenced_blocks(text: str) -> tuple[str, set[str]]:
    """Strip fenced code blocks, returning (stripped_text, names_defined_in_blocks)."""
    lines = text.splitlines(keepends=True)
    outside: list[str] = []
    inside: list[str] = []
    in_fence = False
    defined_names: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            if not in_fence:
                block = ''.join(inside)
                for m in re.finditer(r'(?:def|class|function|const|let|var|export)\s+(\w+)', block):
                    defined_names.add(m.group(1))
                inside = []
            continue
        if in_fence:
            inside.append(line)
        else:
            outside.append(line)
    return ''.join(outside), defined_names


def _extract_claims(text: str) -> list[dict[str, Any]]:
    """Extract claims in subject-at-citation-quote form from non-fenced text."""
    stripped, defined_names = _strip_fenced_blocks(text)
    claims = []
    for m in _CLAIM_RE.finditer(stripped):
        subject = m.group(1)  # may be None
        path = m.group(2)
        start = int(m.group(3))
        end = int(m.group(4)) if m.group(4) else start
        quote = m.group(5)
        claims.append({
            'subject': subject,
            'path': path,
            'start': start,
            'end': end,
            'quote': quote,
            'defined_names': defined_names,
        })
    return claims


def _resolve_path(source: str, file_path: str, tracked_files: list[str]) -> tuple[Path | None, str | None]:
    """Resolve a citation path: try direct, then match as a suffix of tracked files.

    Returns (resolved_path, error_message).
    """
    if not source:
        return None, f"{file_path} — no source directory"
    full = Path(source) / file_path
    if full.is_file():
        return full, None
    matches = []
    for tracked in tracked_files:
        if tracked == file_path or tracked.endswith("/" + file_path):
            candidate = Path(source) / tracked
            if candidate.is_file():
                matches.append((tracked, candidate))
    if len(matches) == 1:
        return matches[0][1], None
    if len(matches) > 1:
        names = ', '.join(m[0] for m in matches)
        return None, f"{file_path} is ambiguous ({names}); use the full path"
    return None, f"{file_path} does not exist in the worktree"


def _collapse(text: str) -> str:
    """Collapse whitespace for quote comparison."""
    return re.sub(r'\s+', ' ', text).strip()


def _check_quote(resolved: Path, start: int, end: int, quote: str) -> str | None:
    """Check that the quoted text appears within ±3 lines of the cited range.

    Returns an error message on mismatch, or None.
    """
    try:
        file_lines = resolved.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    window_start = max(1, start - 3)
    window_end = min(len(file_lines), end + 3)
    collapsed_quote = _collapse(quote)
    if not collapsed_quote:
        return None
    for i in range(window_start - 1, window_end):
        if i < len(file_lines) and collapsed_quote in _collapse(file_lines[i]):
            return None
    window_text = _collapse(' '.join(file_lines[window_start - 1:window_end]))
    if collapsed_quote in window_text:
        return None
    actual_at_line = file_lines[start - 1].rstrip() if start <= len(file_lines) else "(beyond end of file)"
    found_at: list[int] = []
    for i, line in enumerate(file_lines, 1):
        if collapsed_quote in _collapse(line):
            found_at.append(i)
    location = f" (found at line {', '.join(str(n) for n in found_at[:3])})" if found_at else " (not found anywhere in the file)"
    return (
        f"quote `{quote}` not found near line {start} of {resolved.name}; "
        f"line {start} is: `{actual_at_line}`{location}"
    )


def _python_names(resolved: Path) -> set[str]:
    """Collect every name a Python file binds: defs, classes, assignments, args, imports."""
    try:
        source_text = resolved.read_text()
        tree = ast.parse(source_text, filename=str(resolved))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
                names.add(arg.arg)
            if node.args.vararg:
                names.add(node.args.vararg.arg)
            if node.args.kwarg:
                names.add(node.args.kwarg.arg)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Attribute):
                    names.add(target.attr)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node.target, ast.Attribute):
                names.add(node.target.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split('.')[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.ctx, ast.Store):
                names.add(node.attr)
    return names


def _python_imports(resolved: Path) -> dict[str, str]:
    """Return a map of import alias -> module path for imports in a Python file."""
    try:
        source_text = resolved.read_text()
        tree = ast.parse(source_text, filename=str(resolved))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            for alias in node.names:
                name = alias.asname or alias.name
                imports[name] = f"{module}.{alias.name}" if module else alias.name
    return imports


def _check_identifier_python(
    name: str,
    resolved: Path,
    source: str,
    tracked_files: list[str],
    defined_names: set[str],
    subject: str,
) -> str | None:
    """Check a Python identifier. Returns error message or None."""
    if name in defined_names:
        return None
    file_names = _python_names(resolved)
    if name in file_names:
        return None
    # For a.b where a is an import, check b in that module.
    dot_match = _DOT_RE.search(subject)
    if dot_match and dot_match.group(2) == name:
        receiver = dot_match.group(1)
        imports = _python_imports(resolved)
        if receiver in imports:
            mod_path = imports[receiver]
            candidates = [
                mod_path.replace('.', '/') + '.py',
                mod_path.replace('.', '/') + '/__init__.py',
            ]
            for candidate in candidates:
                mod_file = Path(source) / candidate
                if mod_file.is_file():
                    mod_names = _python_names(mod_file)
                    if name in mod_names:
                        return None
                    return f"`{name}` not found in `{candidate}` (imported as `{receiver}`)"
            # Module outside repo — skip.
            return None
        # a is not an import — it might be a class or self/cls.
        if receiver in ('self', 'cls') or receiver in file_names:
            return None
        # Unknown receiver: look up `name` across tracked files of the same language.
        for tracked in tracked_files:
            if tracked.endswith('.py'):
                tf = Path(source) / tracked
                if tf.is_file() and tf != resolved:
                    try:
                        tf_names = _python_names(tf)
                        if name in tf_names:
                            return None
                    except Exception:
                        continue
        return f"`{name}` not found in `{resolved.name}` or any tracked Python file"
    return f"`{name}` not found in `{resolved.name}`"


def _check_identifier_ts(name: str, resolved: Path) -> str | None:
    """Check a TypeScript/JavaScript identifier via word-boundary search."""
    try:
        content = resolved.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    if re.search(r'\b' + re.escape(name) + r'\b', content):
        return None
    return f"`{name}` not found in `{resolved.name}`"


def _extract_identifiers(subject: str | None) -> list[str]:
    """Extract code-shaped identifier names from a claim's subject span."""
    if not subject:
        return []
    names: list[str] = []
    # Skip tokens that look like file paths (contain a file extension).
    token = subject.split('(')[0].strip() if '(' in subject else subject.strip()
    if re.match(r'[\w/._-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|md|txt|json|yaml|yml|toml|html|css|rs|go|rb|java|c|cpp|h|hpp)$', token):
        return []
    for m in _CALL_RE.finditer(subject):
        names.append(m.group(1))
    for m in _DOT_RE.finditer(subject):
        names.append(m.group(2))
    if not names:
        m = _BARE_NAME_RE.match(subject.strip())
        if m:
            names.append(m.group(1))
    return names


def check_design_claims(ctx: Any) -> Any:
    """Deterministic fn-gate: check design claims before the review runs.

    Returns ``"proceed"`` when all claims are valid, ``Verdict("reloop", report)``
    when mismatches are found, or ``"proceed"`` on exhaustion (mismatches appended
    to the review's feedback).
    """
    design_text = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")

    tracked_files: list[str] = []
    if source:
        try:
            output = worktree.git(Path(source), "ls-files")
            tracked_files = [f for f in output.splitlines() if f.strip()]
        except ValueError:
            pass

    claims = _extract_claims(design_text)
    mismatches: list[str] = []

    for claim in claims:
        file_path = claim['path']
        start = claim['start']
        end = claim['end']
        quote = claim['quote']
        subject = claim['subject']
        defined_names = claim['defined_names']

        # 1. Path check.
        resolved, path_error = _resolve_path(source, file_path, tracked_files)
        if path_error:
            mismatches.append(path_error)
            continue

        # 2. Quote check.
        quote_error = _check_quote(resolved, start, end, quote)
        if quote_error:
            mismatches.append(quote_error)

        # 3. Identifier check (only when there is a subject).
        if subject:
            identifiers = _extract_identifiers(subject)
            suffix = Path(file_path).suffix
            for ident_name in identifiers:
                if ident_name in defined_names:
                    continue
                if suffix == '.py':
                    id_error = _check_identifier_python(
                        ident_name, resolved, source, tracked_files, defined_names, subject,
                    )
                elif suffix in _TS_EXTENSIONS:
                    id_error = _check_identifier_ts(ident_name, resolved)
                else:
                    id_error = None
                if id_error:
                    mismatches.append(id_error)

    # ── Probe evidence: verify cited probe ids against probes.jsonl ──
    _PROBE_ID_RE = re.compile(r'\(probe\s+([0-9a-f]{12}):\s*(\S+)\)')
    probes_file = Path(ctx.workdir) / ".refactory" / "probes.jsonl"
    probe_records: dict[str, str] = {}  # id -> status
    if probes_file.is_file():
        for line in probes_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if isinstance(record, dict) and "id" in record and "status" in record:
                    probe_records[record["id"]] = record["status"]
            except json.JSONDecodeError:
                continue

    # Scan only non-fenced text, from the claim's end to the end of its line.
    unfenced_text = _strip_fenced_blocks(design_text)[0]
    for m in _CLAIM_RE.finditer(unfenced_text):
        # From the claim's end to the end of its line
        line_end = unfenced_text.find('\n', m.end())
        if line_end == -1:
            line_end = len(unfenced_text)
        tail = unfenced_text[m.end():line_end]

        for pm in _PROBE_ID_RE.finditer(tail):
            cited_id = pm.group(1)
            cited_status = pm.group(2)
            if cited_id not in probe_records:
                mismatches.append(
                    f"probe id `{cited_id}` cited on claim line is not in probes.jsonl "
                    f"(no probe with that id was run)"
                )
            elif probe_records[cited_id] != cited_status:
                mismatches.append(
                    f"probe id `{cited_id}` cited as `{cited_status}` but probes.jsonl "
                    f"records status `{probe_records[cited_id]}`"
                )

    # Attempt tracking.
    used = int(ctx.facts.get("claims_check_attempts_used", 0))
    allowed = int(ctx.knob("claims_check_attempts", 2))

    if not mismatches:
        ctx.set_fact("claims_check_feedback", "")
        return "proceed"

    report = "\n".join(f"- {m}" for m in mismatches)

    if used < allowed:
        ctx.set_fact("claims_check_attempts_used", used + 1)
        ctx.set_fact("claims_check_feedback", report)
        return Verdict("reloop", report)

    # Exhaustion: pass mismatches to the review.
    ctx.set_fact("claims_check_feedback", "")
    existing_feedback = str(ctx.facts.get("design_review_feedback", ""))
    ctx.set_fact(
        "design_review_feedback",
        existing_feedback + "\n\n## Unresolved design claims\n" + report,
    )
    ctx.append_text(
        "reviews/design-review-feedback.txt",
        "\n\n## Unresolved design claims\n" + report + "\n",
    )
    return "proceed"
