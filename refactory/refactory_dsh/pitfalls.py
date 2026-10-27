"""Pitfall validation and coverage logic.

Separated from design_contract.py so this change merges alongside b26e_markers,
which changes the marker parser and prompt_contract in design_contract.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from refactory_dsh import coverage_helpers

VALID_RISKS = ("high", "medium", "low")
VALID_CATEGORIES = (
    "pinning_tests", "repo_conventions", "shared_state",
    "callers", "imitated_steps", "scope_boundaries",
)
PIT_ID_RE = re.compile(r'^PIT-\d{3}$')


def validate_pitfalls(raw: str, source: str = "", run_dir: str = "") -> list[str]:
    """Validate pitfalls.json content.  Returns a list of problems (empty = valid)."""
    problems: list[str] = []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"pitfalls.json is not valid JSON: {exc}"]

    if not isinstance(data, list) or not data:
        return ["pitfalls.json must be a non-empty JSON array"]

    seen_ids: set[str] = set()
    for i, entry in enumerate(data):
        label = f"entry {i}"
        if not isinstance(entry, dict):
            problems.append(f"{label}: not a JSON object")
            continue

        pid = entry.get("id", "")
        if not pid:
            problems.append(f"{label}: missing id")
        elif not PIT_ID_RE.match(str(pid)):
            problems.append(f"{label}: id {pid!r} is not in PIT-NNN form")
        elif pid in seen_ids:
            problems.append(f"{label}: duplicate id {pid!r}")
        else:
            seen_ids.add(pid)
            label = pid

        risk = entry.get("risk", "")
        if risk not in VALID_RISKS:
            problems.append(f"{label}: risk {risk!r} is not one of {VALID_RISKS}")

        category = entry.get("category", "")
        if category not in VALID_CATEGORIES:
            problems.append(f"{label}: category {category!r} is not one of {VALID_CATEGORIES}")

        evidence = str(entry.get("evidence", ""))
        if not evidence:
            problems.append(f"{label}: evidence is empty")
        elif evidence.lower().startswith('absent:'):
            # Form 1: absence claim — absent: <path or glob>
            claimed_path = evidence.split(':', 1)[1].strip()
            if not claimed_path:
                problems.append(f"{label}: absence claim has no path")
            elif Path(claimed_path).is_absolute():
                problems.append(f"{label}: absence claim {claimed_path!r} is an absolute path")
            elif '..' in Path(claimed_path).parts:
                problems.append(f"{label}: absence claim {claimed_path!r} escapes the worktree")
            elif source:
                if any(c in claimed_path for c in ('*', '?', '[')):
                    try:
                        matches = sorted(
                            str(p.relative_to(source))
                            for p in Path(source).glob(claimed_path)
                        )
                    except ValueError as exc:
                        problems.append(f"{label}: absence claim {claimed_path!r} is not a valid glob: {exc}")
                        matches = []
                    if matches:
                        first = ', '.join(matches[:3])
                        problems.append(
                            f"{label}: false absence claim 'absent: {claimed_path}'"
                            f" \u2014 matches exist: {first}"
                        )
                else:
                    if (Path(source) / claimed_path).exists():
                        problems.append(
                            f"{label}: false absence claim 'absent: {claimed_path}'"
                            f" \u2014 path exists"
                        )
        elif re.match(r'^(brief\.json|research/[\w-]+\.md)$', evidence):
            # Form 2: run-file citation
            if run_dir:
                if not (Path(run_dir) / evidence).is_file():
                    problems.append(
                        f"{label}: run-file citation '{evidence}'"
                        f" \u2014 file not found in run directory"
                    )
        elif ':' in evidence:
            # Form 3: file:line citation (existing behaviour preserved)
            if source:
                # Best-effort file existence check, consistent with
                # gate_design_review's citation check: unresolvable paths are
                # skipped, not blocked.
                for cite in re.finditer(r'([^\s,;]+):(\d+)', evidence):
                    file_path = cite.group(1)
                    full = Path(source) / file_path
                    if not full.is_file():
                        # Skip: the file may use a shortened path the gate
                        # cannot resolve from here.
                        pass
        else:
            problems.append(f"{label}: evidence {evidence!r} does not cite file:line")

        prescription = str(entry.get("prescription", ""))
        if not prescription.strip():
            problems.append(f"{label}: prescription is empty")

    return problems


# ── where the design cites each pitfall ────────────────────────────────────

#: How many lines citing one pitfall the approval card quotes, and how long each may be.
QUOTES_PER_PITFALL = 2
QUOTE_LIMIT = 160


def _quote(line: str, pid: str) -> str:
    """A design line as the card quotes it: its markdown bullet, heading marks and bold
    dropped, its spacing collapsed, and at most `QUOTE_LIMIT` characters, cut at word
    boundaries, starting a little before the id when the id comes late in the line."""
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+", "", line).replace("**", "")
    text = " ".join(text.split())
    if len(text) <= QUOTE_LIMIT:
        return text
    at = text.find(pid)
    if at > QUOTE_LIMIT // 2:
        start = text.find(" ", at - QUOTE_LIMIT // 4) + 1
        text = "\u2026" + text[start if 0 < start <= at else at:]
    if len(text) > QUOTE_LIMIT:
        cut = text.rfind(" ", 0, QUOTE_LIMIT)
        text = text[:cut if cut > 0 else QUOTE_LIMIT - 1].rstrip() + "\u2026"
    return text


#: A list item's start: a bullet or a number.
_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")

#: Any pitfall id in a design, and a heading that names one.
_PIT_MENTION = re.compile(r"(?<![\w-])PIT-\d{3}(?!\d)")
_HEADING_ID = re.compile(r"#{1,6}\s+.*(?<![\w-])PIT-\d{3}(?!\d)")

#: The section whose lines the card quotes first: the one a design answers pitfalls in.
ANSWERS_SECTION = "anti-patterns to avoid"


def pitfall_citations(
    design_text: str, pitfalls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Where design.md cites each pitfall, for the approval card to quote (dm-262).

    No verdict: whether a line answers its pitfall is the person's judgment, so the card
    shows the lines, at most `QUOTES_PER_PITFALL`, each with the heading it sits under,
    and says "not mentioned" only for an id the design never cites. Lines in
    ``## Anti-patterns to avoid`` come first, then the rest in design order; a list item
    is quoted with the lines that continue it, where its answer often is, and a heading
    that names a pitfall with the block under it (dm-219). A ``#`` line
    inside a code fence is code, not a heading. One entry per pitfall with an id, in
    order: ``{"id", "risk", "quotes": [(heading, text), ...]}``.
    """
    rows = design_text.splitlines()
    # (heading, in the answers section, the line, the line with what continues it)
    lines: list[tuple[str, bool, str, str]] = []
    section = ""
    # The headings the line sits under, outermost first: (level, text).
    enclosing: list[tuple[int, str]] = []
    fenced = False
    for index, line in enumerate(rows):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
        found = None if fenced else re.match(r"(#{1,6})\s+(.+)", stripped)
        if found:
            # A heading sits under the headings above its own level, not under its sibling.
            enclosing = [(level, name) for level, name in enclosing if level < len(found[1])]
        heading = enclosing[-1][1] if enclosing else ""
        text = line
        if not fenced and _HEADING_ID.match(stripped):
            # A pitfall answered under its own heading: the block under it is its answer.
            text = " ".join(part.strip() for part in coverage_helpers.entry_lines(
                rows, index, _PIT_MENTION) if part.strip())
        elif _ITEM.match(line):
            for following in rows[index + 1:]:
                if not following.strip() or not following[:1].isspace() or _ITEM.match(following):
                    break
                text += " " + following.strip()
        lines.append((heading, section.lower() == ANSWERS_SECTION, line, text))
        if found:
            enclosing.append((len(found[1]), found[2].strip()))
            if len(found[1]) <= 2:
                section = found[2].strip()
    cited: list[dict[str, Any]] = []
    for pitfall in pitfalls:
        pid = str(pitfall.get("id") or "")
        if not pid:
            continue
        mention = re.compile(rf"(?<![\w-]){re.escape(pid)}(?!\d)")
        found_lines = [(answers, under, text) for under, answers, line, text in lines
                       if mention.search(line)]
        found_lines.sort(key=lambda row: not row[0])   # stable: design order within each
        cited.append({"id": pid, "risk": str(pitfall.get("risk") or "?"),
                      "quotes": [(under, _quote(text, pid))
                                 for _, under, text in found_lines[:QUOTES_PER_PITFALL]]})
    return cited


def citation_line(entry: dict[str, Any]) -> str:
    """One pitfall as the card lists it: its quoted lines, or "not mentioned"."""
    head = f"{entry['id']} ({entry['risk']})"
    if not entry["quotes"]:
        return f"{head}: not mentioned"
    return head + ": " + "; ".join(
        (f"\u00a7 {under}: " if under else "") + f"\"{text}\"" for under, text in entry["quotes"])
