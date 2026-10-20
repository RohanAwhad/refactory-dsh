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

def _extract_section(text: str, heading: str) -> str:
    """Extract the body text between ``## heading`` and the next ``##``."""
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


def _site_paths(text: str) -> list[str]:
    """Extract backticked paths from the Rule and sites section."""
    section = _extract_section(text, "Rule and sites")
    return [m.group(1).strip() for m in re.finditer(r'`([^`]+)`', section)]


def _headings(text: str) -> list[str]:
    """All ``## Heading`` headings in the design."""
    return [line.strip()[3:].strip() for line in text.splitlines()
            if line.strip().startswith("## ")]


def _has_valid_reference(entry: str, sites: list[str], headings: list[str]) -> bool:
    """True when the Anti-patterns entry references a valid site path or heading."""
    # Backticked paths matching a site from Rule and sites.
    for m in re.finditer(r'`([^`]+)`', entry):
        token = re.sub(r':\d+(-\d+)?$', '', m.group(1).strip())
        for site in sites:
            site_clean = re.sub(r':\d+(-\d+)?$', '', site)
            if token == site_clean:
                return True
    # Heading references with the § sigil.
    for m in re.finditer(r'§\s*([^,.\n]+)', entry):
        ref = m.group(1).strip().rstrip('.')
        for h in headings:
            if ref.lower() == h.lower():
                return True
    return False


def pitfall_coverage(
    design_text: str, pitfalls: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    """Check pitfall id coverage in the Anti-patterns section of design.md.

    Returns ``(addressed, set_aside, missing)``:

    - *addressed*: pitfall ids that appear in the Anti-patterns section and
      reference a valid site or heading.
    - *set_aside*: pitfall ids with a ``[set-aside: <reason>]`` marker where
      *reason* is non-empty.
    - *missing*: pitfall ids not found in the Anti-patterns section, or found
      without a valid reference and without a set-aside marker.
    """
    anti = _extract_section(design_text, "Anti-patterns to avoid")
    sites = _site_paths(design_text)
    headings = _headings(design_text)

    addressed: list[str] = []
    set_aside: list[str] = []
    missing: list[str] = []

    # Recognise the set-aside marker bare, bold or in backticks (dm-98 style).
    _set_aside_re = re.compile(
        r'\*{0,2}`?\[set-aside:\s*([^\]]+)\]`?\*{0,2}',
        re.IGNORECASE,
    )

    for p in pitfalls:
        pid = p.get("id", "")
        if not pid:
            continue

        # The id must appear in the Anti-patterns section, not elsewhere.
        if not anti.strip() or pid not in anti:
            missing.append(pid)
            continue

        # Find the line(s) containing this pid in the section.
        entry_lines = [ln for ln in anti.splitlines() if pid in ln]
        entry_text = "\n".join(entry_lines)

        # Check for a set-aside marker with a non-empty reason.
        sa_match = _set_aside_re.search(entry_text)
        if sa_match and sa_match.group(1).strip():
            set_aside.append(pid)
            continue

        # Not set-aside: verify the entry references something that exists.
        if _has_valid_reference(entry_text, sites, headings):
            addressed.append(pid)
        else:
            missing.append(pid)

    return (addressed, set_aside, missing)
