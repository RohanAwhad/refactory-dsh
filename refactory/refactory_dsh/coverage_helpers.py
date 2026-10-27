"""Shared markdown-section helpers for the finding coverage check.

Extracted from pitfalls.py, which no longer judges pitfall coverage: the approval card
quotes where a design cites each pitfall instead (dm-262).
"""
from __future__ import annotations

import re


def extract_section(text: str, heading: str) -> str:
    """Extract the body text between ``## heading`` and the next ``##``.

    Case-insensitive heading match.  Returns the empty string when the heading
    is not found.
    """
    lines = text.splitlines()
    inside = False
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == f"## {heading.lower()}":
            inside = True
            continue
        if inside and stripped.startswith("## "):
            break
        if inside:
            result.append(line)
    return "\n".join(result)


#: What can open an inline code span: a run of backticks. A backslash escape is matched
#: too, so an escaped backtick outside a span opens nothing.
_SPAN_TOKEN = re.compile(r"\\.|`+", re.DOTALL)
_BACKTICK_RUN = re.compile(r"`+")
#: A blank line, which ends a paragraph and so any code span still open in it.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def code_spans(text: str) -> list[str]:
    """The text's inline code spans, read the CommonMark way (dm-146), in order.

    A run of N backticks opens a span that only a run of exactly N backticks closes, so
    a double-backtick span holding a single backtick is one span and shifts nothing after
    it. A run that nothing closes is literal text, a blank line ends any span, and outside
    a span a backslash-escaped backtick opens nothing. Each span's line endings read as
    spaces, and one space comes off each side when it has one on both and is not all spaces.
    """
    spans: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        position = 0
        while (opener := _SPAN_TOKEN.search(paragraph, position)) is not None:
            position = opener.end()
            run = opener.group()
            if not run.startswith("`"):
                continue  # an escape: what it escapes is literal
            closer = next((found for found in _BACKTICK_RUN.finditer(paragraph, opener.end())
                           if len(found.group()) == len(run)), None)
            if closer is None:
                continue  # no run of the same length closes it: the backticks are literal
            content = paragraph[opener.end():closer.start()].replace("\n", " ")
            if len(content) > 1 and content[0] == content[-1] == " " and content.strip():
                content = content[1:-1]
            spans.append(content)
            position = closer.end()
    return spans


#: A markdown heading: its marks and its text.
_HEADING = re.compile(r"(#{1,6})\s+(.*)")


def entry_lines(lines: list[str], index: int, ids: re.Pattern[str]) -> list[str]:
    """The entry that ``lines[index]``, a line naming an id, starts (dm-219).

    A heading's entry is the heading and the block under it, up to the next heading of the
    same or a higher level or the next line naming another id that ``ids`` matches. Any
    other line is its own entry. A ``#`` line inside a code fence is code, not a heading.
    """
    found = _HEADING.match(lines[index].strip())
    if found is None:
        return [lines[index]]
    level, own = len(found[1]), _ids_in(lines[index], ids)
    entry = [lines[index]]
    fenced = False
    for line in lines[index + 1:]:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and (heading := _HEADING.match(stripped)) and len(heading[1]) <= level:
            break
        if _ids_in(line, ids) - own:
            break
        entry.append(line)
    return entry


def _ids_in(line: str, ids: re.Pattern[str]) -> set[str]:
    """The whole ids ``ids`` finds in ``line``."""
    return {found.group(0) for found in ids.finditer(line)}


#: A cited site: a file and a line, ``path/to/file.py:12`` or ``file.py:12-20``.
_SITE_CITATION = re.compile(r'[\w./-]*\w\.\w+:\d+(?:-\d+)?')
#: A file name anywhere in a section: ``tool.py``, ``pkg/tool.py``.
_FILE_NAME = re.compile(r'[\w./-]*\w\.\w+')


def _names_file(path: str, names: set[str]) -> bool:
    """Whether *path* is one of *names*, as written or by a whole trailing segment."""
    return any(path == name or path.endswith("/" + name) or name.endswith("/" + path)
               for name in names)


def site_paths(text: str, source: str = "") -> list[str]:
    """The design's sites: the backticked paths in its ``## Rule and sites`` section.

    That section is optional. A design without it names its sites where it uses them:
    for such a design a site is any backticked ``path:line`` citation in it, the entry
    being checked included, whose file is in the checkout at *source* (the shared
    resolver) or is named in ``## Changes``. So a finding answered with the site that
    handles it (``tool.py:12``) counts, and a made-up ``nowhere/made_up.py:999`` names
    nothing. A file cited without a line is not a site.
    """
    if not any(line.strip().lower() == "## rule and sites" for line in text.splitlines()):
        changes = set(_FILE_NAME.findall(extract_section(text, "Changes")))
        tracked = None
        if source:
            from refactory_dsh.design_ops import tracked_files

            tracked = tracked_files(source)
        sites: list[str] = []
        for span in code_spans(text):
            token = span.strip()
            if not _SITE_CITATION.fullmatch(token):
                continue
            path = re.sub(r':\d+(-\d+)?$', '', token)
            if _names_file(path, changes) or (source and _in_checkout(source, path, tracked)):
                sites.append(token)
        return sites
    section = extract_section(text, "Rule and sites")
    return [span.strip() for span in code_spans(section) if span.strip()]


def _in_checkout(source: str, path: str, tracked: set[str] | None) -> bool:
    """Whether a cited path names exactly one file in the checkout (the shared resolver)."""
    from refactory_dsh.design_ops import resolve_cited

    return resolve_cited(source, path, tracked) is not None


def headings(text: str) -> list[str]:
    """All section headings in the design, ``##`` and the subsections under them.

    A ``#`` line inside a fenced code block is code, not a heading.
    """
    found: list[str] = []
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and (heading := re.match(r'#{2,6}\s+(.+)', stripped)):
            found.append(heading[1].strip())
    return found


def has_valid_reference(entry: str, sites: list[str], hdgs: list[str]) -> bool:
    """True when the entry references a valid site path or heading."""
    # Backticked paths matching a site from Rule and sites.
    for span in code_spans(entry):
        token = re.sub(r':\d+(-\d+)?$', '', span.strip())
        for site in sites:
            site_clean = re.sub(r':\d+(-\d+)?$', '', site)
            if token == site_clean:
                return True
    # Heading references with the § sigil. The text after it names a heading, which may
    # hold dots or commas, and may go on to say where in it ("§ Changes rule 4",
    # "§ Changes → Regeneration"); designs write both, and reading only an exact name
    # sent sound designs back.
    for m in re.finditer('§', entry):
        ref = entry[m.end():].lstrip().lower()
        for h in hdgs:
            name = h.lower()
            if ref.startswith(name) and not ref[len(name):][:1].isalnum():
                return True
    return False


# Recognise the set-aside marker bare, bold or in backticks (dm-98 style).
SET_ASIDE_RE = re.compile(
    r'\*{0,2}`?\[set-aside:\s*([^\]]+)\]`?\*{0,2}',
    re.IGNORECASE,
)
