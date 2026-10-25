"""Shared markdown-section helpers for pitfall and finding coverage checks.

Extracted from pitfalls.py so both pitfalls.py and findings_coverage.py
can import them without copying.
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
    resolver) or is named in ``## Changes``. So a pitfall answered with the site that
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
        for m in re.finditer(r'`([^`]+)`', text):
            token = m.group(1).strip()
            if not _SITE_CITATION.fullmatch(token):
                continue
            path = re.sub(r':\d+(-\d+)?$', '', token)
            if _names_file(path, changes) or (source and _in_checkout(source, path, tracked)):
                sites.append(token)
        return sites
    section = extract_section(text, "Rule and sites")
    return [m.group(1).strip() for m in re.finditer(r'`([^`]+)`', section)]


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
    for m in re.finditer(r'`([^`]+)`', entry):
        token = re.sub(r':\d+(-\d+)?$', '', m.group(1).strip())
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
