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
            m_changed = re.search(r'\[changed\]', body, re.IGNORECASE)
            m_left_reason = re.search(r'\[left:\s*(.*?)\]', body, re.IGNORECASE)
            m_left = re.search(r'\[left\]', body, re.IGNORECASE)
            if m_changed:
                status = "changed"
                path_text = body[:m_changed.start()].strip().rstrip("—–-:").strip()
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
            rule_sites.append({"path": path_text, "status": status, "reason": reason})

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
