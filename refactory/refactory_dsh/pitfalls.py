"""Pitfall validation and coverage logic.

Separated from design_contract.py so this change merges alongside b26e_markers,
which changes the marker parser and prompt_contract in design_contract.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

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


# ── coverage check ─────────────────────────────────────────────────────────

from refactory_dsh import coverage_helpers

#: How many words of its own a line citing a pitfall needs to be a remedy (dm-262): a
#: sentence saying what the design does about it ("Line counting uses `count('\n')`,
#: never `splitlines()`"), not a bare pointer ("handled by `retry()`", "see Changes").
REMEDY_WORDS = 5

#: A pitfall id, with the title a design gives it right after (``PIT-002 (splitlines
#: trap)``, ``**PIT-002** (weakened guard)``): neither is the design's remedy.
_ID_AND_TITLE = re.compile(r"PIT-\d+\**\s*(?:\([^)]*\))?")


def _is_remedy(line: str) -> bool:
    """Whether a line citing a pitfall says, in words of its own, what the design does
    about it: at least `REMEDY_WORDS` words once its ids, their titles, its code spans
    and its markup are gone. A word is a whitespace-separated token with a letter in it."""
    text = _ID_AND_TITLE.sub(" ", re.sub(r"`[^`]*`", " ", line))
    return sum(1 for token in text.split() if re.search(r"[^\W\d_]", token)) >= REMEDY_WORDS


def _answer_lines(design_text: str) -> list[str]:
    """The lines a design can answer a pitfall on: every line but its ``## Summary``,
    which states the problem rather than what the design does about it."""
    lines: list[str] = []
    in_summary = False
    for line in design_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_summary = stripped.lower() == "## summary"
        if not in_summary:
            lines.append(line)
    return lines


def pitfall_coverage(
    design_text: str, pitfalls: list[dict[str, Any]], source: str = "",
) -> tuple[list[str], list[str], list[str]]:
    """Which pitfalls design.md answers, wherever it answers them.

    A design answers a pitfall on a line that cites its id: an entry in ``## Anti-patterns
    to avoid``, and also a ``## Changes`` bullet ("avoids PIT-002"), a test that pins it,
    a paragraph ("per PIT-003") or a heading of the design. Its ``## Summary`` does not
    count. Returns ``(addressed, set_aside, missing)``:

    - *addressed*: the lines citing the id name a valid site or § heading, or one of them
      is a remedy, a sentence of its own on what the design does (`_is_remedy`, dm-262).
    - *set_aside*: a line citing the id has a ``[set-aside: <reason>]`` marker with a
      non-empty reason.
    - *missing*: no line cites the id, or none names a site, a heading or a remedy.

    *source* is the checkout a design without ``## Rule and sites`` has its cited sites
    checked against (`coverage_helpers.site_paths`).
    """
    lines = _answer_lines(design_text)
    sites = coverage_helpers.site_paths(design_text, source=source)
    hdgs = coverage_helpers.headings(design_text)

    addressed: list[str] = []
    set_aside: list[str] = []
    missing: list[str] = []

    for p in pitfalls:
        pid = p.get("id", "")
        if not pid:
            continue

        entry_lines = [ln for ln in lines if pid in ln]
        if not entry_lines:
            missing.append(pid)
            continue
        entry_text = "\n".join(entry_lines)

        # Check for a set-aside marker with a non-empty reason.
        sa_match = coverage_helpers.SET_ASIDE_RE.search(entry_text)
        if sa_match and sa_match.group(1).strip():
            set_aside.append(pid)
            continue

        # Not set-aside: a site or heading that exists, or a remedy in its own words.
        if (coverage_helpers.has_valid_reference(entry_text, sites, hdgs)
                or any(_is_remedy(line) for line in entry_lines)):
            addressed.append(pid)
        else:
            missing.append(pid)

    return (addressed, set_aside, missing)
