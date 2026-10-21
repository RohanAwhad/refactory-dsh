"""Finding coverage logic for research and perspective findings.

Placed beside pitfalls.py.  Imports from coverage_helpers.
Does not import from pitfalls.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from refactory_dsh import coverage_helpers

# Greedy id pattern: FND-step-2-3 parses as slug='step-2', n=3.
FND_ID_RE = re.compile(r'(?<!\w)FND-([a-z0-9]+(?:-[a-z0-9]+)*)-(\d+)(?!\d)')


def parse_fnd_id(text: str) -> list[tuple[str, str, int]]:
    """Extract all (full_id, slug, n) tuples from a line.

    Uses FND_ID_RE, which is greedy: FND-step-2-3 parses as
    (full_id='FND-step-2-3', slug='step-2', n=3).
    """
    results: list[tuple[str, str, int]] = []
    for m in FND_ID_RE.finditer(text):
        results.append((m.group(0), m.group(1), int(m.group(2))))
    return results


def _strip_md_decoration(line: str) -> str:
    """Strip leading markdown decoration from a line for definition detection.

    Accepts leading ``#``, ``-``, ``*``, ``+``, ``1.``, ``>``, ``**``, backticks.
    """
    s = line
    changed = True
    while changed:
        s = s.lstrip()
        if not s:
            break
        changed = False
        # heading hashes
        if s[0] == '#':
            s = s.lstrip('#')
            changed = True
        # blockquote
        elif s[0] == '>':
            s = s[1:]
            changed = True
        # bold **
        elif s.startswith('**'):
            s = s[2:]
            changed = True
        # bullet: -, *, + followed by whitespace
        elif s[0] in '-*+' and len(s) > 1 and s[1] in ' \t':
            s = s[1:]
            changed = True
        # single italic *
        elif s[0] == '*':
            s = s[1:]
            changed = True
        # backtick
        elif s[0] == '`':
            s = s[1:]
            changed = True
        # numbered list: N. ...
        else:
            m = re.match(r'\d+\.\s', s)
            if m:
                s = s[m.end():]
                changed = True
    return s


def definitions(text: str) -> list[tuple[str, str, int, str]]:
    """Extract (full_id, slug, n, line_text) for ids at the START of a line
    or heading, tolerating leading markdown decoration.

    A bulleted ``- **FND-api-1** — …`` is a definition.
    Used by both collect_findings and gate_research.
    """
    results: list[tuple[str, str, int, str]] = []
    for line in text.splitlines():
        stripped = _strip_md_decoration(line)
        if not stripped:
            continue
        m = FND_ID_RE.match(stripped)
        if m:
            results.append((m.group(0), m.group(1), int(m.group(2)), line.strip()))
    return results


def _extract_finding_text(line_text: str, full_id: str, full_text: str) -> str:
    """Extract the finding text after the id, falling back to next non-empty line."""
    idx = line_text.find(full_id)
    if idx >= 0:
        after = line_text[idx + len(full_id):]
        # Strip closing bold/backtick markers and separators
        after = re.sub(r'^[\s*`]*[—–:]\s*', '', after)
        after = re.sub(r'^[\s*`]+', '', after)
        after = after.rstrip('*`').rstrip()
        if after:
            return after
    # Fall back to the next non-empty line in the full text
    lines = full_text.splitlines()
    found_line = False
    for line in lines:
        if found_line and line.strip():
            return line.strip()
        if full_id in line:
            found_line = True
    return ''


def collect_findings(
    workdir: Path, detail_dirs: list[str],
) -> tuple[list[dict], list[str]]:
    """Scan the given directories for FND-<slug>-<n> ids.

    Returns (findings, blocking_problems).

    Each finding is {'id': str, 'source_file': str, 'text': str, 'slug': str}.
    A file with no definitions counts as one finding whose id is its path.
    Slug mismatches and duplicate ids are blocking_problems.
    """
    findings: list[dict] = []
    blocking: list[str] = []
    seen_ids: dict[str, str] = {}  # id → source_file

    for detail_dir in detail_dirs:
        dir_path = workdir / detail_dir.rstrip('/')
        if not dir_path.is_dir():
            continue
        for md_file in sorted(dir_path.glob("*.md")):
            rel_path = f"{detail_dir.rstrip('/')}/{md_file.name}"
            file_stem = md_file.stem
            text = md_file.read_text()
            defs = definitions(text)

            if not defs:
                # File with no definitions → one finding whose id is its path
                findings.append({
                    'id': rel_path,
                    'source_file': rel_path,
                    'text': '',
                    'slug': file_stem,
                })
                continue

            for full_id, slug, n, line_text in defs:
                # Check slug matches file stem
                if slug != file_stem:
                    blocking.append(
                        f"{rel_path} has id {full_id} with slug '{slug}' "
                        f"but the file's slug is '{file_stem}'"
                    )

                finding_text = _extract_finding_text(line_text, full_id, text)

                findings.append({
                    'id': full_id,
                    'source_file': rel_path,
                    'text': finding_text,
                    'slug': slug,
                })

                # Check for duplicates
                if full_id in seen_ids:
                    blocking.append(
                        f"duplicate id {full_id}: found in "
                        f"{seen_ids[full_id]} and {rel_path}"
                    )
                seen_ids[full_id] = rel_path

    return (findings, blocking)


def finding_coverage(
    document_text: str, findings: list[dict],
    section_heading: str = 'Research and perspective findings',
) -> tuple[list[str], list[str], list[str]]:
    """Check finding id coverage in the named section.

    Returns (addressed, set_aside, missing).

    Coverage matching uses full-id equality: parse each section line with
    parse_fnd_id and compare whole ids.
    """
    section = coverage_helpers.extract_section(document_text, section_heading)
    sites = coverage_helpers.site_paths(document_text)
    hdgs = coverage_helpers.headings(document_text)

    addressed: list[str] = []
    set_aside: list[str] = []
    missing: list[str] = []

    for f in findings:
        fid = f['id']
        # Find lines in section containing this full id (by full-id equality)
        found_lines: list[str] = []
        for line in section.splitlines():
            for parsed_id, _, _ in parse_fnd_id(line):
                if parsed_id == fid:
                    found_lines.append(line)
                    break

        if not found_lines:
            missing.append(fid)
            continue

        entry_text = "\n".join(found_lines)

        sa_match = coverage_helpers.SET_ASIDE_RE.search(entry_text)
        if sa_match and sa_match.group(1).strip():
            set_aside.append(fid)
            continue

        if coverage_helpers.has_valid_reference(entry_text, sites, hdgs):
            addressed.append(fid)
        else:
            missing.append(fid)

    return (addressed, set_aside, missing)


def finding_coverage_from_json(
    strategy_text: str, findings: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    """For create mode: parse the 'findings' field of strategy.json.

    Returns (addressed, set_aside, missing).

    Parses the {…} slice the way _review does (create_ops.py). Never raises —
    returns all-missing on parse error.
    """
    try:
        payload = json.loads(
            strategy_text[strategy_text.index("{"):strategy_text.rindex("}") + 1]
        )
    except (ValueError, json.JSONDecodeError):
        return ([], [], [f['id'] for f in findings])

    findings_map = payload.get('findings') or {}
    if not isinstance(findings_map, dict):
        return ([], [], [f['id'] for f in findings])

    # Build set of declared node ids and knob names
    declared_names: set[str] = set()
    for n in payload.get('nodes', []):
        if isinstance(n, dict) and 'id' in n:
            declared_names.add(str(n['id']))
    for k in payload.get('knobs', []):
        if isinstance(k, dict) and 'name' in k:
            declared_names.add(str(k['name']))

    addressed: list[str] = []
    set_aside: list[str] = []
    missing: list[str] = []

    for f in findings:
        fid = f['id']
        desc = findings_map.get(fid)
        if desc is None:
            missing.append(fid)
            continue
        desc = str(desc).strip()
        if not desc:
            missing.append(fid)
            continue

        sa_match = coverage_helpers.SET_ASIDE_RE.search(desc)
        if sa_match and sa_match.group(1).strip():
            set_aside.append(fid)
            continue

        # Check if description names a declared node or knob as whole token
        found_name = False
        for name in declared_names:
            if re.search(r'\b' + re.escape(name) + r'\b', desc):
                found_name = True
                break

        if found_name:
            addressed.append(fid)
        else:
            missing.append(fid)

    return (addressed, set_aside, missing)


def gate_problems(ctx: Any, document: str = 'design.md') -> list[str]:
    """Single entry point for gates.

    Called as:
        problems.extend(findings_coverage.gate_problems(ctx))
        problems.extend(findings_coverage.gate_problems(ctx, document='strategy.json'))

    Discovery: reads getattr(ctx, 'declared_reads', ()) and picks entries ending
    in '/'. If none, sets a fact and returns [].
    """
    detail_dirs = [r for r in getattr(ctx, 'declared_reads', ()) if r.endswith('/')]
    if not detail_dirs:
        ctx.set_fact('finding_coverage',
                     "findings check skipped: no detail-file directories "
                     "in this gate's reads")
        return []

    findings, blocking = collect_findings(ctx.workdir, detail_dirs)
    if blocking:
        return blocking

    if not findings:
        ctx.set_fact('finding_coverage',
                     "finding coverage: no findings found in detail files")
        return []

    if document == 'strategy.json':
        doc_text = ctx.read_text('strategy.json') if ctx.exists('strategy.json') else ''
        addressed, set_aside, missing_ids = finding_coverage_from_json(
            doc_text, findings)
    else:
        doc_text = ctx.read_text('design.md') if ctx.exists('design.md') else ''
        addressed, set_aside, missing_ids = finding_coverage(
            doc_text, findings)

    # Build coverage fact
    coverage_lines: list[str] = []
    for f in findings:
        fid = f['id']
        if fid in addressed:
            coverage_lines.append(f"  {fid}: addressed")
        elif fid in set_aside:
            coverage_lines.append(f"  {fid}: set aside")
        else:
            coverage_lines.append(f"  {fid}: MISSING")
    if coverage_lines:
        ctx.set_fact('finding_coverage',
                     "Finding coverage:\n" + "\n".join(coverage_lines))

    # Build problem strings for missing findings
    problems: list[str] = []
    finding_by_id = {f['id']: f for f in findings}
    for mid in missing_ids:
        f = finding_by_id.get(mid, {})
        text = f.get('text', '')
        source = f.get('source_file', '')
        problems.append(
            f'research/perspective finding not addressed: {mid} '
            f'"{text}" (from {source})'
        )

    return problems
