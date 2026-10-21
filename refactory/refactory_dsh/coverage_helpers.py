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


def site_paths(text: str) -> list[str]:
    """Extract backticked paths from the Rule and sites section."""
    section = extract_section(text, "Rule and sites")
    return [m.group(1).strip() for m in re.finditer(r'`([^`]+)`', section)]


def headings(text: str) -> list[str]:
    """All ``## Heading`` headings in the design."""
    return [line.strip()[3:].strip() for line in text.splitlines()
            if line.strip().startswith("## ")]


def has_valid_reference(entry: str, sites: list[str], hdgs: list[str]) -> bool:
    """True when the entry references a valid site path or heading."""
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
        for h in hdgs:
            if ref.lower() == h.lower():
                return True
    return False


# Recognise the set-aside marker bare, bold or in backticks (dm-98 style).
SET_ASIDE_RE = re.compile(
    r'\*{0,2}`?\[set-aside:\s*([^\]]+)\]`?\*{0,2}',
    re.IGNORECASE,
)
