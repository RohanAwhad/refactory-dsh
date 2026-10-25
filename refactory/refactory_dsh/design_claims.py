"""Deterministic claims checker for design documents (dm-126).

A design's claims about existing code — paths, quoted lines and named
identifiers — are verified mechanically between design_doc and the design-review gate.
A line that drifted, a quote with an ellipsis and a bare file name are resolved here
rather than sent back; what still cannot be confirmed is listed for the person at the
approval gate. The check never sends the design back (one design pass).
"""

from __future__ import annotations

import ast
import bisect
import json
import re
from pathlib import Path
from typing import Any


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


def _resolve_path(source: str, file_path: str,
                  tracked: set[str] | None) -> tuple[Path | None, str | None]:
    """Resolve a cited path: as written, else the one tracked file that ends with it.

    The second step is #498's resolver (`design_ops.cited_candidates`), shared with
    classify_scale, so a bare or package-relative path names a file here exactly when it
    does there. A path that is absolute, holds ``..``, or whose real path (a symlink
    followed) is outside the checkout names nothing: the check never reads, or puts on
    the card, a line from outside the checkout. Returns (resolved_path, why it is
    unverified).
    """
    from refactory_dsh.design_ops import cited_candidates, inside_checkout

    if not source:
        return None, f"`{file_path}`: no checkout to check it against"
    if not file_path.startswith(("/", "~")) and ".." not in Path(file_path).parts:
        full = Path(source) / file_path
        if full.is_file():
            if not inside_checkout(source, file_path):
                return None, f"`{file_path}` resolves outside the checkout"
            return full, None
    candidates = cited_candidates(source, file_path, tracked)
    if len(candidates) == 1:
        return Path(source) / candidates[0], None
    if len(candidates) > 1:
        shown = ", ".join(candidates[:4]) + (", …" if len(candidates) > 4 else "")
        return None, f"`{file_path}` is ambiguous ({shown}); cite the full path"
    return None, f"`{file_path}` is not in the checkout"


def _collapse(text: str) -> str:
    """Collapse whitespace for quote comparison."""
    return re.sub(r'\s+', ' ', text).strip()


#: How far a quoted line may have drifted from the line its claim cites and still count.
QUOTE_WINDOW = 20

#: An ellipsis in a quote stands for the text it leaves out: "…" or "...".
_ELLIPSIS = re.compile(r'\s*(?:…|\.\.\.)\s*')

#: Each part of a quote with an ellipsis must name something: a word, not only
#: punctuation. ``(…)`` would otherwise match any line with a parenthesis in it.
_WORD = re.compile(r'[^\W\d]\w*')

#: A quote with an ellipsis leaves out text inside a statement, so its parts must all lie
#: within the lines it cites plus ELLIPSIS_SLACK more: room for a signature or a call
#: wrapped over a line or two (``def load(self,`` / ``path) -> None:``), not for the start
#: of one statement paired with the end of another (``load(`` with the ``) -> None:`` of
#: the method below it).
ELLIPSIS_SLACK = 2
#: ...and never more lines than this, however many lines the claim cites.
ELLIPSIS_MAX_LINES = 5


def _ellipsis_parts(quote: str) -> list[str] | None:
    """The parts between a quote's ellipses, or None when the quote has none."""
    if not _ELLIPSIS.search(quote):
        return None
    return [part for part in _ELLIPSIS.split(quote) if part]


def _wordless_part(parts: list[str]) -> str | None:
    """The first part that names nothing ('' for a quote that is only an ellipsis)."""
    if not parts:
        return ""
    return next((part for part in parts if not _WORD.search(part)), None)


def _ellipsis_span(start: int, end: int) -> int:
    """How many lines an ellipsis quote's parts may lie across (see ELLIPSIS_SLACK)."""
    return min(end - start + 1 + ELLIPSIS_SLACK, ELLIPSIS_MAX_LINES)


def _joined(file_lines: list[str], first: int, last: int) -> tuple[str, list[int], list[int]]:
    """Lines *first*..*last* (1-based) as one collapsed text, with where each line starts
    in it and its number.

    Blank lines are left out, so a quote that spans lines matches across them as it
    matches across the whitespace it collapses.
    """
    parts: list[str] = []
    offsets: list[int] = []
    numbers: list[int] = []
    offset = 0
    for number in range(first, last + 1):
        text = _collapse(file_lines[number - 1])
        if not text:
            continue
        offsets.append(offset)
        numbers.append(number)
        parts.append(text)
        offset += len(text) + 1
    return " ".join(parts), offsets, numbers


def _exact_begins(file_lines: list[str], first: int, last: int, quote: str,
                  limit: int | None = None) -> list[int]:
    """The lines in *first*..*last* where *quote* begins, as written (whitespace collapsed).

    Only those lines are read, and after them only as many as a quote beginning on the
    last of them could run into. *limit* stops after that many lines are found.
    """
    stop, room = last, 0
    while stop < len(file_lines) and room < len(quote):
        stop += 1
        room += len(_collapse(file_lines[stop - 1])) + 1
    text, offsets, numbers = _joined(file_lines, first, stop)
    begins: list[int] = []
    index = text.find(quote)
    while index >= 0:
        number = numbers[bisect.bisect_right(offsets, index) - 1]
        if number > last:
            break
        if number not in begins:
            begins.append(number)
            if limit is not None and len(begins) >= limit:
                break
        index = text.find(quote, index + 1)
    return begins


def _ellipsis_begins(file_lines: list[str], first: int, last: int, parts: list[str],
                     span: int) -> list[int]:
    """The lines in *first*..*last* where an ellipsis quote begins: its first part begins
    on that line, and every part follows in order within *span* lines of it."""
    begins: list[int] = []
    for number in range(first, last + 1):
        head = _collapse(file_lines[number - 1])
        if not head:
            continue
        text, _, _ = _joined(file_lines, number, min(len(file_lines), number + span - 1))
        index = text.find(parts[0])
        if not 0 <= index < len(head):
            continue
        position = index + len(parts[0])
        for part in parts[1:]:
            index = text.find(part, position)
            if index < 0:
                break
            position = index + len(part)
        else:
            begins.append(number)
    return begins


def _locate_quote(file_lines: list[str], start: int, end: int, quote: str,
                  window: int = QUOTE_WINDOW) -> int | None:
    """The nearest line within *window* lines of the cited range where *quote* begins.

    Only the window is searched. A match counts when it begins inside it, wherever a
    quote spanning lines ends. A quote with an ellipsis that does not match as written
    matches where its parts appear in order within a few lines (`_ellipsis_span`),
    provided each part names something (`_WORD`).
    """
    first, last = max(1, start - window), min(len(file_lines), end + window)
    if first > last:
        return None
    near = _exact_begins(file_lines, first, last, quote)
    parts = _ellipsis_parts(quote)
    if not near and parts is not None and _wordless_part(parts) is None:
        near = _ellipsis_begins(file_lines, first, last, parts, _ellipsis_span(start, end))

    def distance(number: int) -> int:
        return 0 if start <= number <= end else min(abs(number - start), abs(number - end))

    return min(near, key=distance) if near else None


def _check_quote(resolved: Path, start: int, end: int, quote: str,
                 cited: str) -> tuple[str | None, str | None]:
    """Check a quoted line against the file: (a note when it drifted, why it is unverified).

    A quote found within ±QUOTE_WINDOW lines of the cited line counts; when it is not
    on the cited line the note says where it is ("cited N, found at M").
    """
    try:
        file_lines = resolved.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None, None
    collapsed_quote = _collapse(quote)
    if not collapsed_quote:
        return None, None
    span = f"{start}" if start == end else f"{start}-{end}"
    found = _locate_quote(file_lines, start, end, collapsed_quote)
    if found is not None:
        if start <= found <= end:
            return None, None
        return f"`{cited}`: cited {span}, found at {found}", None
    actual = (f"line {start} is `{file_lines[start - 1].strip()}`" if 1 <= start <= len(file_lines)
              else f"the file has {len(file_lines)} lines")
    parts = _ellipsis_parts(collapsed_quote)
    wordless = _wordless_part(parts) if parts is not None else None
    if wordless is not None:
        where = ("a part beside its ellipsis names nothing"
                 + (f" (`{wordless}`)" if wordless else "")
                 + ", so it would match almost any line; quote a word of it")
    elif parts is not None:
        where = (f"its parts do not all appear, in order, within "
                 f"{_ellipsis_span(start, end)} lines")
    else:
        # Where it is instead, for the person: the first three lines, in one pass over the
        # file (none of them is in the window, or the quote would have been found there).
        elsewhere = _exact_begins(file_lines, 1, len(file_lines), collapsed_quote, limit=3)
        where = (f"found at line {', '.join(str(n) for n in elsewhere)}, more than "
                 f"{QUOTE_WINDOW} lines away" if elsewhere else "not found anywhere in the file")
    return None, (f"quote `{quote}` not within {QUOTE_WINDOW} lines of `{cited}`: "
                  f"{actual}; {where}")


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


def check_design_claims(ctx: Any) -> dict[str, Any]:
    """Deterministic op: check the design's claims about existing code, and never bounce.

    Each claim in subject-at-citation-quote form names a file, a line and the line's
    text. A quote within ±QUOTE_WINDOW lines of its citation counts (a note says where
    it was found); a quote with an ellipsis counts when its parts, each naming
    something, appear in order there within a few lines of one another; and a bare or
    package-relative path counts when exactly one tracked file ends with it. What still
    cannot be confirmed -- a quote not there, a path not in the checkout (or leading out
    of it), a named identifier the file does not define, a probe id probes.jsonl does
    not hold -- is an unverified claim: listed first on the approval gate's card as a
    high-severity advisory. Nothing here sends the design back.

    Returns the ``claims_check`` fact, ``{"unverified": [...], "notes": [...]}``, and
    ``claims_check_feedback``, the same as lines for the prompts that read it.
    """
    from refactory_dsh.design_ops import tracked_files

    design_text = ctx.read_text("design.md") if ctx.exists("design.md") else ""
    source = str(ctx.facts.get("source") or ctx.facts.get("artifact") or "")
    tracked = tracked_files(source) if source else None
    tracked_list = sorted(tracked or ())

    unverified: list[str] = []
    notes: list[str] = []

    for claim in _extract_claims(design_text):
        file_path = claim['path']
        start = claim['start']
        end = claim['end']
        cited = f"{file_path}:{start}" + (f"-{end}" if end != start else "")
        subject = claim['subject']
        defined_names = claim['defined_names']

        # 1. Path check.
        resolved, path_error = _resolve_path(source, file_path, tracked)
        if resolved is None:
            unverified.append(path_error or f"`{file_path}` is not in the checkout")
            continue

        # 2. Quote check.
        note, quote_error = _check_quote(resolved, start, end, claim['quote'], cited)
        if note:
            notes.append(note)
        if quote_error:
            unverified.append(quote_error)

        # 3. Identifier check (only when there is a subject).
        if subject:
            identifiers = _extract_identifiers(subject)
            suffix = Path(file_path).suffix
            for ident_name in identifiers:
                if ident_name in defined_names:
                    continue
                if suffix == '.py':
                    id_error = _check_identifier_python(
                        ident_name, resolved, source, tracked_list, defined_names, subject,
                    )
                elif suffix in _TS_EXTENSIONS:
                    id_error = _check_identifier_ts(ident_name, resolved)
                else:
                    id_error = None
                if id_error:
                    unverified.append(f"`{cited}`: {id_error}")

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
                unverified.append(
                    f"probe id `{cited_id}` cited on claim line is not in probes.jsonl "
                    f"(no probe with that id was run)"
                )
            elif probe_records[cited_id] != cited_status:
                unverified.append(
                    f"probe id `{cited_id}` cited as `{cited_status}` but probes.jsonl "
                    f"records status `{probe_records[cited_id]}`"
                )

    return {"claims_check": {"unverified": unverified, "notes": notes},
            "claims_check_feedback": claims_feedback(unverified, notes)}


def claims_feedback(unverified: list[str], notes: list[str]) -> str:
    """The claims check as lines, unverified claims first; '' when there is nothing."""
    return "\n".join([*(f"- unverified claim: {item}" for item in unverified),
                      *(f"- {note}" for note in notes)])
