"""The design-document contract: what design.md must contain.

Defined once; read by design_doc, gate_design_review, delivery and the audit.
No split_clauses: the overwatch audit enumerates each constraint's clauses
itself, and audit_problems checks them.
"""
from __future__ import annotations

import dataclasses
import re


TITLE = "Title"
SUMMARY = "Summary"
RULE_AND_SITES = "Rule and sites"
DOCS = "Docs"
CHANGES = "Changes"
CONTRACT_SECTIONS = (SUMMARY, RULE_AND_SITES, DOCS, CHANGES)


@dataclasses.dataclass
class DesignContract:
    title: str
    summary: str
    rule_sites: list[dict[str, str]]
    docs: list[dict[str, str]]
    changes: list[str]
    has_contract: bool


def _strip_prefix(heading: str) -> str:
    """Strip a leading ``Design:`` prefix (case-insensitive)."""
    m = re.match(r'^[Dd]esign:\s*', heading)
    return heading[m.end():].strip() if m else heading.strip()


def _strip_run_name(text: str, run_name: str) -> str:
    """Strip a leading run name and its separator from *text*.

    ``"b16b_graph_ends — Make sinks() agree ..."`` with run_name ``b16b_graph_ends``
    gives ``"Make sinks() agree ..."``.  The separator may be `` — ``, `` - ``,
    `` – ``, or ``: ``.
    """
    if not run_name:
        return text
    collapsed = re.sub(r'\s+', ' ', text).strip()
    name_collapsed = re.sub(r'\s+', ' ', run_name).strip()
    if not collapsed.lower().startswith(name_collapsed.lower()):
        return text
    rest = collapsed[len(name_collapsed):]
    rest = re.sub(r'^[\s]*[—–:\-][\s]*', '', rest, count=1)
    return rest.strip()


def _names_match(a: str, b: str) -> bool:
    """Case-insensitive, whitespace-collapsed equality."""
    return re.sub(r'\s+', ' ', a).strip().lower() == re.sub(r'\s+', ' ', b).strip().lower()


def _section_lines(lines: list[str], heading: str) -> list[str]:
    """Return the body lines between ``## heading`` and the next ``##``."""
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
            result.append(stripped)
    return result


def _bullets(lines: list[str]) -> list[str]:
    """Return lines starting with ``- `` or ``* ``."""
    result: list[str] = []
    for line in lines:
        if line.startswith("- "):
            result.append(line[2:].strip())
        elif line.startswith("* "):
            result.append(line[2:].strip())
    return result


def _h1(lines: list[str]) -> str:
    """The first H1 heading, stripped."""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _find_marker(body: str, pattern: str) -> re.Match | None:
    """Find the first match of *pattern* not inside a backtick span.

    A match whose start has an odd number of backtick characters before it
    is inside an inline code span and is skipped.  This prevents
    a backticked mention of the marker in prose from shadowing the real
    ``**[changed]**`` marker later on the same line.
    """
    for m in re.finditer(pattern, body, re.IGNORECASE):
        if body[:m.start()].count('`') % 2 == 0:
            return m
    return None


def parse(text: str, run_name: str = "") -> DesignContract:
    """Parse a design.md into its contract parts."""
    lines = text.splitlines()
    lower_lines = [line.strip().lower() for line in lines]

    # Detect contract: all four section headings present.
    has_contract = all(
        f"## {sec.lower()}" in lower_lines
        for sec in CONTRACT_SECTIONS
    )

    raw_heading = _h1(lines)
    heading = _strip_prefix(raw_heading)
    heading = _strip_run_name(heading, run_name)

    if has_contract:
        # Contract design.
        title = heading if heading and not _names_match(heading, run_name) else ""

        summary_lines = _section_lines(lines, SUMMARY)
        summary = " ".join(line for line in summary_lines if line)

        rule_lines = _section_lines(lines, RULE_AND_SITES)
        rule_sites: list[dict[str, str]] = []
        for line in rule_lines:
            if not (line.startswith("- ") or line.startswith("* ")):
                continue
            body = (line[2:] if line.startswith("- ") or line.startswith("* ") else line).strip()
            status = "changed"
            reason = ""
            desc = ""
            m_changed = _find_marker(body, r'\*{0,2}\[changed\]\*{0,2}')
            m_left_reason = _find_marker(body, r'\*{0,2}\[left:\s*(.*?)\]\*{0,2}')
            m_left = _find_marker(body, r'\*{0,2}\[left\]\*{0,2}')
            if m_changed:
                status = "changed"
                path_text = body[:m_changed.start()].strip().rstrip("—–-:").strip()
                desc = body[m_changed.end():].strip()
                desc = re.sub(r'^[—–\-:\s]+', '', desc).strip()
                desc = desc.rstrip('.')
            elif m_left_reason:
                status = "left"
                reason = m_left_reason.group(1).strip()
                path_text = body[:m_left_reason.start()].strip().rstrip("—–-:").strip()
            elif m_left:
                status = "left"
                reason = ""
                path_text = body[:m_left.start()].strip().rstrip("—–-:").strip()
            else:
                # No marker: the design has not said whether this change covers it.
                status = "unmarked"
                path_text = body
            rule_sites.append({"path": path_text, "status": status, "reason": reason, "description": desc})

        docs_lines = _section_lines(lines, DOCS)
        docs = [{"path": b, "update": ""} for b in _bullets(docs_lines)]

        changes_lines = _section_lines(lines, CHANGES)
        changes = _bullets(changes_lines)

        return DesignContract(
            title=title, summary=summary, rule_sites=rule_sites,
            docs=docs, changes=changes, has_contract=True,
        )
    else:
        # Pre-contract design.
        title = heading
        if _names_match(title, run_name) or not title:
            # Fall back to the brief's first sentence — the caller handles that.
            title = ""

        # Summary: first paragraph after H1, skipping sub-headings.
        summary = ""
        past_heading = False
        para: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not past_heading:
                if stripped.startswith("# "):
                    past_heading = True
                continue
            if stripped.startswith("#"):
                if not para:
                    continue  # skip sub-heading before prose
                break  # hit next heading after collecting prose
            if not stripped and para:
                break
            if stripped.startswith("---") and para:
                break
            if stripped:
                para.append(stripped)
        summary = " ".join(para)

        return DesignContract(
            title=title, summary=summary, rule_sites=[], docs=[],
            changes=[], has_contract=False,
        )


def missing_parts(parsed: DesignContract) -> list[str]:
    """Section names from CONTRACT_SECTIONS whose content is empty."""
    missing: list[str] = []
    if not parsed.summary or not parsed.summary.strip():
        missing.append(SUMMARY)
    if not parsed.rule_sites:
        missing.append(RULE_AND_SITES)
    if not parsed.docs:
        missing.append(DOCS)
    if not parsed.changes:
        missing.append(CHANGES)
    return missing


def left_without_reason(parsed: DesignContract) -> list[str]:
    """Sites marked ``[left]`` with no reason."""
    return [site["path"] for site in parsed.rule_sites
            if site["status"] == "left" and not site["reason"]]


def unmarked_sites(parsed: DesignContract) -> list[str]:
    """Sites listed without a ``[changed]`` or ``[left: <reason>]`` marker."""
    return [site["path"] for site in parsed.rule_sites if site["status"] == "unmarked"]


def _extract_file_path(site_path: str) -> str:
    """Extract the file path from a site's ``path`` text.

    Takes the first backticked token, or the text before the first " — ",
    and strips its ``:line-range`` suffix.  For example::

        `delivery.py:989-1022` — `_body()` renders the PR body
        →  delivery.py

        `nn/knob.py:49-81` (`Knob` dataclass)
        →  nn/knob.py
    """
    text = site_path.strip()
    m = re.match(r'`([^`]+)`', text)
    if m:
        path = m.group(1).strip()
    else:
        idx = text.find(' \u2014 ')
        path = text[:idx].strip() if idx >= 0 else text
    return re.sub(r':\d+(-\d+)?$', '', path)


def _site_matches_diff(site_path: str, diff_files: list[str]) -> str | None:
    """Return the diff file that matches *site_path*, or ``None``.

    Extracts the file path from the first backticked token or text before
    " \u2014 ", strips ``:line-range`` suffixes, and matches against *diff_files*.
    Prefers an exact path match.  Accepts a basename/suffix match only when
    exactly one diff file matches.
    """
    cleaned = _extract_file_path(site_path)
    if not cleaned:
        return None
    for df in diff_files:
        if df == cleaned:
            return df
    suffix_matches = [df for df in diff_files if df.endswith('/' + cleaned)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    return None


def build_what_changed(
    parsed: DesignContract, diff_files: list[str],
) -> tuple[list[str], list[str]]:
    """Build the "What changed" list from ``[changed]`` sites cross-checked
    against the diff.

    Returns ``(items, mismatches)``:

    - ``items``: one line per changed file for the PR body.  Multiple
      ``[changed]`` sites in the same file are merged into one item with
      their descriptions joined by ``; ``.
    - ``mismatches``: file paths of sites marked ``[changed]`` whose file
      is not in the diff.

    For a pre-contract design (``not parsed.has_contract``), returns the
    diff file list as items with no mismatches.
    """
    if not parsed.has_contract:
        return (list(diff_files), [])

    changed_sites = [s for s in parsed.rule_sites if s['status'] == 'changed']

    file_descriptions: dict[str, list[str]] = {}
    mismatches: list[str] = []

    for site in changed_sites:
        diff_file = _site_matches_diff(site['path'], diff_files)
        if diff_file is not None:
            descs = file_descriptions.setdefault(diff_file, [])
            desc = site.get('description', '')
            if desc:
                descs.append(desc)
        else:
            extracted = _extract_file_path(site['path'])
            mismatches.append(extracted if extracted else site['path'])

    items: list[str] = []
    for diff_file in diff_files:
        if diff_file in file_descriptions:
            descs = file_descriptions[diff_file]
            if descs:
                items.append(f"{diff_file} \u2014 {'; '.join(descs)}")
            else:
                items.append(diff_file)
        else:
            items.append(diff_file)

    return (items, mismatches)


def title_is_run_name(text: str, run_name: str) -> bool:
    """True when the H1 heading, after stripping the Design: prefix, equals the run name or starts with it and leaves nothing after the separator."""
    if not run_name:
        return False
    raw = _strip_prefix(_h1(text.splitlines()))
    return bool(raw) and not _strip_run_name(raw, run_name)


def prompt_contract() -> str:
    """The text the design_doc prompt appends."""
    return (
        "Your design.md must contain these sections, in this order:\n\n"
        "1. An H1 heading (`#`) that says what the change does. Never use "
        "the run name or artifact name as the title.\n\n"
        "2. `## Summary` — a prose summary of the problem and the change, "
        "in two to four sentences.\n\n"
        "3. `## Rule and sites` — the rule this change makes or corrects, "
        "then every place in the code that applies or restates that rule, "
        "found by searching the source. List each as a bullet with its "
        "file path and line range cited, and mark it `[changed]` (this "
        "change modifies it) or `[left: <reason>]` (this change leaves it "
        "unchanged, with the reason). A site left unchanged without a "
        "reason is a defect.\n\n"
        "4. `## Docs` — the docs that describe the changed behaviour, found "
        "by searching `docs/` and any other documentation directories. "
        "List each as a bullet with its path, and say what update the "
        "design plans for it.\n\n"
        "5. `## Changes` — the list of files this change adds or modifies, "
        "as bullets.\n"
    )
