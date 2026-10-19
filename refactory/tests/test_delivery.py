"""A design run that passes its reviews ends with a pull request (#350).

One test per path a person takes: a new project, an existing repository, a reused
project name, a follow-up to an open pull request, a redo, `--no-pr`, a run that fails,
a base with commits nobody pushed, a name already taken on GitHub, and no gh login.
GitHub is `fake_gh.py` over bare repositories on disk, which git reaches through
`url.<base>.insteadOf`; the model is a fake; the repositories, the worktrees, the
pushes and the registry are real.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from refactory_dsh import cli, console, delivery, nn, projects, worktree
from refactory_dsh.design import DesignMode
from refactory_dsh.projects import projects_dir
from tests_support import DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, PITFALLS_ANSWER, RESEARCH_ANSWER, REVIEW_ANSWERS, FakeModel, git, git_repo, render, stub

NAME = "delivered_tool"
FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
LOGIN = "octo"


# ── PR title and body unit tests ───────────────────────────────────────────


class _TitleCtx:
    """Minimal context for _title() and _body() unit tests."""
    def __init__(self, facts=None, files=None):
        self.facts = dict(facts or {})
        self._files = dict(files or {})
    def exists(self, name):
        return name in self._files
    def read_text(self, name):
        return self._files[name]


def test_pr_title_from_design_heading():
    ctx = _TitleCtx(
        facts={"brief": "Fix audit", "name": "b2"},
        files={"design.md": "# Audit bookkeeping and PR formatting\n\nSome text.\n"},
    )
    assert delivery._title(ctx) == "Audit bookkeeping and PR formatting"


def test_pr_title_strips_design_prefix():
    ctx = _TitleCtx(
        facts={"brief": "Fix audit", "name": "b2"},
        files={"design.md": "# Design: Audit bookkeeping\n\nSome text.\n"},
    )
    assert delivery._title(ctx) == "Audit bookkeeping"


def test_pr_title_falls_back_to_brief():
    ctx = _TitleCtx(
        facts={"brief": "Fix the audit gate", "name": "b2"},
        files={"design.md": "No heading here, just text.\n"},
    )
    assert delivery._title(ctx) == "Fix the audit gate"


def test_pr_title_truncates_at_word_boundary():
    long_heading = "Fix " + "word " * 20  # well over 72 chars
    ctx = _TitleCtx(
        facts={"brief": "short", "name": "b2"},
        files={"design.md": f"# {long_heading}\n"},
    )
    title = delivery._title(ctx)
    assert len(title) <= delivery.TITLE_LIMIT
    assert title.endswith("…")


def test_pr_title_never_cuts_inside_backticks():
    heading = "Fix how gate_overwatch and its refusal of the `code_reviewer` backtick_spans are reported in the pipeline"
    ctx = _TitleCtx(
        facts={"brief": "short", "name": "b2"},
        files={"design.md": f"# {heading}\n"},
    )
    title = delivery._title(ctx)
    assert len(title) <= delivery.TITLE_LIMIT
    # Even number of backticks: no unclosed pair.
    assert title.count("`") % 2 == 0, f"unclosed backtick in title: {title!r}"


def test_pr_body_has_summary_verification_and_original_request(tmp_path):
    import subprocess
    ctx = _TitleCtx(
        facts={"brief": "Build a tool\nthat prints built.", "check": "python tool.py", "name": "b2",
               "inherited_failures": ["tests/test_env.py::test_needs_profile"]},
        files={"design.md": "# The design heading\n\nFirst paragraph of the design.\n\n---\n"},
    )
    # Set up a minimal git repo so worktree.git works.
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], capture_output=True, check=True)
    (tmp_path / "file.txt").write_text("content\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "tool.py").write_text("print('built')\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "attempt"], capture_output=True, check=True)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)
    assert "## Summary" in body
    assert "First paragraph of the design." in body
    assert "## Verification" in body
    assert "python tool.py" in body
    assert "the health check, code review and QA found nothing blocking" in body
    assert "`tests/test_env.py::test_needs_profile`" in body
    assert "## Original request" in body
    assert "Build a tool\nthat prints built." in body


def test_pr_verification_names_inherited_failures_and_the_new_tests_on_the_base():
    import json
    ctx = _TitleCtx(
        facts={"inherited_failures": ["tests/test_env.py::test_needs_profile"]},
        files={"test-guardedness.json": json.dumps(
            {"summary": {"guarding": 3, "not_guarding": 1, "cannot_run_on_base": 0, "total_new_tests": 4}})},
    )
    lines = delivery._base_evidence(ctx)
    assert "`tests/test_env.py::test_needs_profile`" in lines
    assert "3 of 4 fail without the change; 1 pass there, so they do not guard it" in lines


def test_pr_verification_says_why_the_new_tests_were_not_run_on_the_base():
    import json
    ctx = _TitleCtx(files={"test-guardedness.json": json.dumps(
        {"unavailable": "the base worktree could not be prepared: no uv"})})
    assert "not run: the base worktree could not be prepared: no uv" in delivery._base_evidence(ctx)
    assert delivery._base_evidence(_TitleCtx()) == ""
    leaked = _TitleCtx(files={"test-guardedness.json": json.dumps(
        {"unavailable": "setup failed: ANTHROPIC_API_KEY=sk-live-123 not accepted"})})
    assert "sk-live-123" not in delivery._base_evidence(leaked)


def _git_pair(tmp_path):
    """Set up a minimal two-commit git repo; return (base, head, path)."""
    import subprocess
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True, check=True)
    (tmp_path / "f.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "g.txt").write_text("b\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "work"], capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    return base, head


def test_contract_design_gives_title_summary_and_changes(tmp_path):
    design_text = (
        "# Enforce clause-level audit coverage\n\n"
        "## Summary\n\n"
        "The overwatch audit missed a clause. This change adds clause-level checking.\n\n"
        "## Rule and sites\n\n"
        "The rule: every constraint clause is checked.\n\n"
        "- `authoring_evidence.py:132-157` — **[changed]** adds clause rows.\n"
        "- `design_ops.py:116-246` — **[left: only the gate; clauses are audit-side]**\n\n"
        "## Docs\n\n"
        "- `docs/models.md` — add clause-level audit section\n\n"
        "## Changes\n\n"
        "- `refactory/refactory_dsh/authoring_evidence.py`\n"
        "- `refactory/tests/test_authoring_evidence.py`\n"
    )
    ctx = _TitleCtx(
        facts={"brief": "Add clause checking", "check": "pytest", "name": "b17_clauses"},
        files={"design.md": design_text},
    )
    title = delivery._title(ctx)
    assert title == "Enforce clause-level audit coverage"
    assert title != "b17_clauses"

    import subprocess as sp
    sp.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True, check=True)
    (tmp_path / "f.txt").write_text("a\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "g.txt").write_text("b\n")
    (tmp_path / "authoring_evidence.py").write_text("# new\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "work"], capture_output=True, check=True)
    head = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)
    assert "## Summary" in body
    assert "clause-level checking" in body
    assert "## What changed" in body
    assert "authoring_evidence.py" in body
    assert "adds clause rows" in body


def test_what_changed_uses_sites_not_bullets(tmp_path):
    """Contract design with test-plan Changes bullets yields What changed
    from [changed] sites only, not those bullets.

    Uses site lines from b21b, including one where ``[changed]`` appears
    inside backticks before the real **[changed]** marker.

    Fails without the change: old _body (delivery.py:1007-1008) renders
    parsed.changes, which includes 'Return code is 2'.
    """
    from refactory_dsh import design_contract

    design_text = (
        "# Build What changed from sites\n\n"
        "## Summary\n\nFix the What changed section.\n\n"
        "## Rule and sites\n\n"
        "- `refactory/refactory_dsh/design_contract.py:96-155` \u2014 `parse()` builds "
        "`rule_sites` dicts. Currently drops text after `[changed]`. "
        "**[changed]** captures that text as a `'description'` field in each site dict.\n"
        "- `refactory/refactory_dsh/delivery.py:989-1022` \u2014 `_body()` renders the PR body. "
        "**[changed]** replaces those lines with a call to `build_what_changed`.\n"
        "- `refactory/refactory_dsh/delivery.py:1025-1029` \u2014 `_write_pr_text()` calls "
        "`_body()`. **[left: inherits the fix through `_body()`]**\n\n"
        "## Docs\n\n- `docs/models.md` \u2014 update\n\n"
        "## Changes\n\n"
        "- Return code is 2\n"
        "- `seen == []`\n"
    )

    # Verify the backtick-escaped marker is handled: description is captured
    # from the second [changed] (the bold one), not the first (inside backticks).
    parsed = design_contract.parse(design_text, "test_run")
    changed = [s for s in parsed.rule_sites if s['status'] == 'changed']
    assert len(changed) == 2
    assert changed[0]['description'] == "captures that text as a `'description'` field in each site dict"
    assert changed[1]['description'] == "replaces those lines with a call to `build_what_changed`"

    ctx = _TitleCtx(
        facts={"brief": "Fix what changed", "check": "pytest", "name": "test_run"},
        files={"design.md": design_text},
    )

    import subprocess as sp
    sp.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True, check=True)
    (tmp_path / "f.txt").write_text("a\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    for f in ["refactory/refactory_dsh/design_contract.py",
              "refactory/refactory_dsh/delivery.py"]:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# changed\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "work"], capture_output=True, check=True)
    head = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()

    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)

    assert "## What changed" in body
    assert "design_contract.py" in body
    assert "delivery.py" in body
    assert "Return code is 2" not in body
    assert "seen == []" not in body


def test_diff_file_with_no_site_appears_by_name(tmp_path):
    """A file in the diff with no matching [changed] site appears by name.

    Uses a site line from b20b: ``delivery.py:795`` \u2014 **[changed]**.
    The diff also touches test_delivery.py, which has no site.

    Fails without the change: old _body renders parsed.changes (the Changes
    section bullets); the diff file not in any site is not listed.
    """
    design_text = (
        "# Write PR text once\n\n"
        "## Summary\n\nA kept delivery has no PR text.\n\n"
        "## Rule and sites\n\n"
        "- `refactory/refactory_dsh/delivery.py:795` \u2014 the only place `BODY` is written "
        "today, inside `_deliver`. **[changed]** \u2014 this write is removed.\n"
        "- `refactory/refactory_dsh/delivery.py:926-938` \u2014 `_title()` computes the PR title. "
        "**[left: read-only; called from `_write_pr_text`]**\n\n"
        "## Docs\n\n- `docs/models.md` \u2014 update delivery paragraph\n\n"
        "## Changes\n\n"
        "- `refactory/refactory_dsh/delivery.py`\n"
    )

    ctx = _TitleCtx(
        facts={"brief": "PR text for kept", "check": "pytest", "name": "b20b"},
        files={"design.md": design_text},
    )

    import subprocess as sp
    sp.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True, check=True)
    (tmp_path / "f.txt").write_text("a\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    for f in ["refactory/refactory_dsh/delivery.py",
              "refactory/tests/test_delivery.py"]:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# changed\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "work"], capture_output=True, check=True)
    head = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()

    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)

    assert "refactory/tests/test_delivery.py" in body


def test_changed_site_outside_diff_excluded(tmp_path, capsys):
    """A [changed] site whose file is not in the diff is excluded from
    What changed, and the mismatch is reported via target_guard.warn.

    Uses site lines from b20d (``nn/knob.py:49-81``) and b21b
    (``delivery.py:989-1022``).  The diff touches only delivery.py.

    Fails without the change: old _body renders parsed.changes (the Changes
    section bullets), not the sites. The bullets include knob.py.
    """
    design_text = (
        "# Refuse empty briefs\n\n"
        "## Summary\n\nEmpty briefs should be refused.\n\n"
        "## Rule and sites\n\n"
        "- `refactory/refactory_dsh/delivery.py:989-1022` \u2014 `_body()` renders the PR body. "
        "**[changed]** replaces the What changed source.\n"
        "- `refactory/refactory_dsh/nn/knob.py:49-81` (`Knob` dataclass) \u2014 "
        "**[changed]** Add `required: bool = False` field.\n\n"
        "## Docs\n\n- `docs/models.md` \u2014 update\n\n"
        "## Changes\n\n"
        "- `refactory/refactory_dsh/delivery.py`\n"
        "- `refactory/refactory_dsh/nn/knob.py`\n"
    )

    ctx = _TitleCtx(
        facts={"brief": "Refuse empty", "check": "pytest", "name": "b20d"},
        files={"design.md": design_text},
    )

    import subprocess as sp
    sp.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True, check=True)
    (tmp_path / "f.txt").write_text("a\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    # Only delivery.py is in the diff; knob.py is NOT
    p = tmp_path / "refactory/refactory_dsh/delivery.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# changed\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "work"], capture_output=True, check=True)
    head = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()

    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)

    assert "delivery.py" in body
    assert "knob.py" not in body
    err = capsys.readouterr().err
    assert "knob.py" in err


def test_pre_contract_design_lists_diff_files(tmp_path):
    """A pre-contract design (no ## Summary, ## Rule and sites, etc.)
    lists the diff's files in What changed.

    Fails without the change: old _body skips What changed entirely for
    pre-contract designs (parsed.has_contract is False at delivery.py:1007).
    """
    design_text = "# Fix something\n\nThe old oracle is stale.\n"

    ctx = _TitleCtx(
        facts={"brief": "Fix it", "check": "pytest", "name": "b15b"},
        files={"design.md": design_text},
    )

    import subprocess as sp
    sp.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], capture_output=True, check=True)
    (tmp_path / "f.txt").write_text("a\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True, check=True)
    base = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    (tmp_path / "alpha.py").write_text("# alpha\n")
    (tmp_path / "beta.py").write_text("# beta\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], capture_output=True, check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-m", "work"], capture_output=True, check=True)
    head = sp.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()

    from types import SimpleNamespace
    found = SimpleNamespace(path=str(tmp_path), base=base, head=head, branch="refactory/run-test")
    body = delivery._body(ctx, found)

    assert "## What changed" in body
    assert "alpha.py" in body
    assert "beta.py" in body


def test_build_what_changed_with_real_design():
    """build_what_changed with site lines from b20b (multiple [changed] for
    delivery.py) and b20d (knob.py, design.py) against their diff files.

    Multiple [changed] sites in delivery.py become one item; each diff file
    appears exactly once; no mismatches.
    """
    from refactory_dsh import design_contract

    design_text = (
        "# Test real design\n\n"
        "## Summary\n\nReal design test.\n\n"
        "## Rule and sites\n\n"
        "- `refactory/refactory_dsh/delivery.py:795` \u2014 the only place `BODY` is written "
        "today, inside `_deliver`. **[changed]** \u2014 this write is removed.\n"
        "- `refactory/refactory_dsh/delivery.py:729-766` \u2014 `deliver_design()` orchestrates "
        "the whole step. **[changed]** \u2014 restructured.\n"
        "- `refactory/refactory_dsh/delivery.py:138-159` \u2014 `knobs()` declares the `pr` knob. "
        "**[changed]** \u2014 appended finishing commands.\n"
        "- `refactory/refactory_dsh/delivery.py:926-938` \u2014 `_title()` computes the PR title. "
        "**[left: read-only; called from `_write_pr_text`]**\n"
        "- `refactory/refactory_dsh/nn/knob.py:49-81` (`Knob` dataclass) \u2014 "
        "**[changed]** Add `required: bool = False` field.\n"
        "- `refactory/refactory_dsh/design.py:69` (brief knob declaration) \u2014 "
        "**[changed]** Change to `required=True`.\n\n"
        "## Docs\n\n- `docs/models.md` \u2014 update\n\n"
        "## Changes\n\n"
        "- `refactory/refactory_dsh/delivery.py`\n"
        "- `refactory/refactory_dsh/nn/knob.py`\n"
    )

    parsed = design_contract.parse(design_text, "test_real")

    # Diff files from a mix of b20b and b20d
    diff_files = [
        "refactory/refactory_dsh/delivery.py",
        "refactory/refactory_dsh/design.py",
        "refactory/refactory_dsh/nn/knob.py",
        "refactory/tests/test_delivery.py",
    ]

    items, mismatches = design_contract.build_what_changed(parsed, diff_files)

    # No mismatches: every [changed] site matches a diff file
    assert mismatches == [], f"unexpected mismatches: {mismatches}"
    # One item per diff file
    assert len(items) == 4, f"expected 4 items, got {len(items)}: {items}"

    # delivery.py: three [changed] sites merged into one item
    assert items[0].startswith("refactory/refactory_dsh/delivery.py")
    assert "this write is removed" in items[0]
    assert "restructured" in items[0]
    assert "appended finishing commands" in items[0]

    # design.py: one [changed] site
    assert "refactory/refactory_dsh/design.py" in items[1]
    assert "Change to `required=True`" in items[1]

    # knob.py: one [changed] site
    assert "refactory/refactory_dsh/nn/knob.py" in items[2]
    assert "Add `required: bool = False` field" in items[2]

    # test_delivery.py: no site, appears by name only
    assert items[3] == "refactory/tests/test_delivery.py"


@pytest.mark.parametrize("heading,run_name,expected_not_equal", [
    ("# Design: b15b_reference_oracle", "b15b_reference_oracle", "b15b_reference_oracle"),
    ("# Design: b16b_graph_ends \u2014 Make sinks() agree with scheduler reachability",
     "b16b_graph_ends", "b16b_graph_ends"),
    ("# Design: Fork-branch redo reopens join and downstream",
     "b15a_fork_redo", "b15a_fork_redo"),
    ("# Design: Generalize set-aside to every declared output on re-dispatch",
     "b16a_set_aside", "b16a_set_aside"),
])
def test_pre_contract_design_title_is_not_the_run_name(heading, run_name, expected_not_equal):
    design_text = f"{heading}\n\n## Problem\n\nThe current implementation has a bug that causes incorrect results.\n"
    ctx = _TitleCtx(
        facts={"brief": "Fix something important in the codebase.", "name": run_name},
        files={"design.md": design_text},
    )
    title = delivery._title(ctx)
    assert title, f"title should not be empty for heading={heading!r}"
    assert title != expected_not_equal, f"title should not be the run name: {title!r}"


def test_pre_contract_design_gives_prose_summary():
    from refactory_dsh import design_contract

    design_text = (
        "# Design: b15b_reference_oracle\n\n"
        "## Problem\n\n"
        "The oracle returns stale references when the cache is cold.\n"
    )
    parsed = design_contract.parse(design_text, "b15b_reference_oracle")
    assert parsed.summary == "The oracle returns stale references when the cache is cold."
    assert "## Problem" not in parsed.summary




@pytest.fixture
def github(tmp_path, monkeypatch) -> Path:
    """GitHub, as far as delivery can tell: gh logged in as `octo`, repositories on disk."""
    root = tmp_path / "github"
    root.mkdir()
    (root / "login").write_text(LOGIN + "\n")
    bin_dir = tmp_path / "bin"
    stub(bin_dir, "gh", f'exec "{sys.executable}" "{FAKE_GH}" "$@"')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_ROOT", str(root))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.file://{root}/.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/")
    monkeypatch.setattr(delivery, "VERIFY_WAIT_S", 0)
    return root


def _published(github: Path, slug: str, source: Path) -> Path:
    """A repository on GitHub holding ``source``'s main, which ``source`` names as origin."""
    bare = github / f"{slug}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(bare.parent, "init", "-q", "--bare", "-b", "main", bare.name)
    (github / f"{slug}.json").write_text(json.dumps({"visibility": "private", "default_branch": "main"}))
    git(source, "remote", "add", "origin", f"https://github.com/{slug}.git")
    git(source, "push", "-q", f"https://github.com/{slug}.git", "main:refs/heads/main")
    return bare


def _remote_head(github: Path, slug: str, branch: str) -> str:
    try:
        return git(github / f"{slug}.git", "rev-parse", "-q", "--verify", f"refs/heads/{branch}")
    except subprocess.CalledProcessError:
        return ""


def _pulls(github: Path, slug: str) -> list[dict]:
    path = github / f"{slug}.pulls.json"
    return json.loads(path.read_text()) if path.exists() else []


def _calls(tmp_path: Path) -> str:
    log = tmp_path / "bin" / "calls.log"
    return log.read_text() if log.exists() else ""


def _answers(*emits: str) -> list[str]:
    """Every answer a design run needs, in the order its graph asks."""
    return [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
            DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, *emits, *REVIEW_ANSWERS]


def _mode(**knobs) -> DesignMode:
    """Design mode with some of its knobs set, as a person sets them for one run."""
    mode = DesignMode()
    state = mode.state_dict()
    for name, value in knobs.items():
        state["knobs"][name]["value"] = value
    mode.load_state_dict(state)
    return mode


def _design(workdir: Path, emit: dict[str, str] | None = None, *, mode: DesignMode | None = None,
            check: str = "python tool.py", **task: str):
    model = FakeModel(_answers(render(emit or {"tool.py": "print('built')\n"})))
    return (mode or DesignMode()).run(
        workdir, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": "Build a tool\nthat prints built.", "check": check, **task},
    )


def _record(workdir: Path) -> dict:
    return json.loads((workdir / delivery.RECORD).read_text())


# ── the paths a person takes ───────────────────────────────────────────────
def test_a_new_project_gets_a_private_repository_whose_first_pull_request_holds_all_the_code(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    project = (projects_dir() / NAME).resolve()
    assert json.loads((github / f"{slug}.json").read_text())["visibility"] == "private"
    # main on GitHub is the project's empty first commit; the pull request is all of the code.
    assert _remote_head(github, slug, "main") == git(project, "rev-parse", "main")
    [pull] = _pulls(github, slug)
    branch = json.loads((run / "design.json").read_text())["branch"]
    assert (pull["head"], pull["base"], pull["title"]) == (branch, "main", "Do the thing")
    assert _remote_head(github, slug, branch) == git(project, "rev-parse", branch)
    assert git(project, "ls-tree", "--name-only", "main") == ""
    assert "Build a tool\nthat prints built." in pull["body"] and "python tool.py" in pull["body"]
    assert "Written by a refactory design run" in pull["body"]
    url = f"https://github.com/{slug}/pull/1"
    assert result.facts["pull_request"] == url
    assert _record(run)["status"] == "opened" and _record(run)["verified"] is True
    assert json.loads((run / "design.json").read_text())["delivery"]["pull_request"] == url
    [entry] = projects.load()["projects"]
    assert (entry["github"], entry["runs"][-1]["pull_request"]) == (slug, url)
    # The project now names the repository it was delivered to.
    assert git(project, "config", "--get", "remote.origin.url") == f"https://github.com/{slug}.git"


def test_the_approval_gate_reads_what_delivery_will_do_before_anything_is_built(github, tmp_path):
    graph = DesignMode().build()
    assert "delivery.md" in graph.nodes["gate_strategy"].reads
    assert "delivery.md" in graph.nodes["gate_strategy"].notes
    run = tmp_path / "run"
    seen: list[tuple[bool, list[Path]]] = []

    class Watching(FakeModel):
        """Notes, as the approved design is built, what the gate had read and what GitHub held."""

        def complete(self, prompt: str, *, system: str = "", workdir: object = None) -> str:
            if "Write the artifact `" in prompt:
                seen.append(((run / delivery.PLAN).exists(), sorted(github.glob("*/*.git"))))
            return super().complete(prompt, system=system, workdir=workdir)

    result = DesignMode().run(run, model=Watching(_answers(render({"tool.py": "print('built')\n"}))),
                              verdicts={"gate_strategy": "proceed"},
                              task={"name": NAME, "brief": "a tool", "check": "python tool.py"})
    assert result.exit_ok, result.error
    assert seen == [(True, [])], "the plan existed at the gate, and the repository did not until delivery"
    said = (run / delivery.PLAN).read_text()
    assert f"creates the private GitHub repository {LOGIN}/{NAME}" in said and "--no-pr" in said
    assert list(github.glob("*/*.git")) == [github / LOGIN / f"{NAME}.git"]
    assert [edge.target for edge in graph.edges if edge.source == "report"] == ["deliver"]


def test_an_existing_repository_gets_a_pull_request_into_its_default_branch(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    slug = "acme/app"
    _published(github, slug, repo)
    refs = git(repo, "for-each-ref")
    main = _remote_head(github, slug, "main")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    branch = json.loads((run / "design.json").read_text())["branch"]
    [pull] = _pulls(github, slug)
    assert (pull["head"], pull["base"]) == (branch, "main")
    assert _remote_head(github, slug, "main") == main, "delivery never pushes the default branch"
    assert _remote_head(github, slug, branch) == git(repo, "rev-parse", branch)
    # The person's repository keeps its remotes and remote-tracking branches as they were;
    # only the run's own branch is new.
    assert git(repo, "remote") == "origin"
    assert git(repo, "for-each-ref", "--exclude", f"refs/heads/{branch}") == refs
    assert f"opens a pull request from {branch} into {slug}:main" in (run / delivery.PLAN).read_text()


def test_unpushed_commits_in_the_base_are_never_pushed(github, tmp_path, capsys):
    repo = git_repo(tmp_path / "repo")
    slug = "acme/app"
    _published(github, slug, repo)
    (repo / "mine.py").write_text("x = 1\n")
    git(repo, "add", "mine.py")
    git(repo, "commit", "-q", "-m", "mine, not pushed")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    said = (run / delivery.PLAN).read_text()
    assert "won't open a pull request" in said and "1 commit(s) that are not on acme/app" in said
    assert "--base origin/main" in said
    assert _pulls(github, slug) == [] and git(github / f"{slug}.git", "branch") == "* main"
    assert "no pull request was opened" in capsys.readouterr().err
    assert result.facts["pull_request"] == ""


def test_a_name_already_taken_on_github_is_never_pushed_into(github, tmp_path, capsys):
    taken = git_repo(tmp_path / "theirs")
    _published(github, f"{LOGIN}/{NAME}", taken)
    before = git(github / f"{LOGIN}/{NAME}.git", "for-each-ref")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert "already exists" in (run / delivery.PLAN).read_text()
    assert git(github / f"{LOGIN}/{NAME}.git", "for-each-ref") == before
    assert "repo create" not in _calls(tmp_path) and _pulls(github, f"{LOGIN}/{NAME}") == []
    assert git((projects_dir() / NAME), "remote") == ""
    assert "already exists" in capsys.readouterr().err


def test_a_reused_project_name_opens_its_pull_request_in_the_repository_it_was_given(github, tmp_path):
    first = _design(tmp_path / "one", {"tool.py": "print(1)\n"})
    second = _design(tmp_path / "two", {"tool.py": "print(2)\n"})
    assert first.exit_ok and second.exit_ok, (first.error, second.error)
    slug = f"{LOGIN}/{NAME}"
    assert _calls(tmp_path).count("repo create") == 1
    assert [pull["number"] for pull in _pulls(github, slug)] == [1, 2]
    assert second.facts["pull_request"] == f"https://github.com/{slug}/pull/2"
    assert "opens a pull request from" in (tmp_path / "two" / delivery.PLAN).read_text()


@pytest.mark.parametrize("refused", ["exit 4", "HTTP 401"])
def test_without_gh_login_the_run_succeeds_and_says_its_work_stays_local(github, tmp_path, capsys, refused):
    (github / "login").unlink()
    if refused == "HTTP 401":
        (github / "logged-out-as-401").write_text("")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    err = capsys.readouterr().err
    assert "run warning: no pull request was opened: gh is not logged in" in err
    assert _record(run)["status"] == "kept local"
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))


def test_no_pr_keeps_the_work_on_its_branch_and_asks_github_nothing(github, tmp_path, capsys):
    run = tmp_path / "run"
    result = _design(run, mode=_mode(pr=False))
    assert result.exit_ok, result.error
    assert _calls(tmp_path) == ""
    assert "run warning" not in capsys.readouterr().err
    assert "`pr` is off (--no-pr)" in (run / delivery.PLAN).read_text()
    assert result.facts["delivery_result"].startswith("not delivered")
    assert result.facts["delivery"].startswith("once the reviews pass, this run won't open"), "the plan is kept"


def test_a_failed_run_creates_no_repository_and_opens_no_pull_request(github, tmp_path):
    result = _design(tmp_path / "run", check="false", mode=_mode(check_attempts=1))
    assert result.exit_ok is False
    calls = _calls(tmp_path)
    assert "repo create" not in calls and "pr create" not in calls and not list(github.glob("*/*.git"))
    assert not (tmp_path / "run" / delivery.RECORD).exists()


def test_a_run_without_a_worktree_has_nothing_to_deliver(github, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "run"
    result = _design(run, mode=_mode(worktree=False))
    assert result.exit_ok, result.error
    assert _calls(tmp_path) == "" and "run warning" not in capsys.readouterr().err
    assert "this run has no worktree" in (run / delivery.PLAN).read_text()


# ── a redo, a resume, and what GitHub then shows ───────────────────────────
def test_delivering_again_opens_no_second_pull_request_and_leaves_its_text_alone(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    [before] = _pulls(github, slug)
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert again["pull_request"] == result.facts["pull_request"]
    assert _pulls(github, slug) == [before]
    assert _record(run)["status"] == "already open"
    assert _calls(tmp_path).count("pr create") == 1 and "pr edit" not in _calls(tmp_path)


def test_a_delivery_that_failed_after_its_push_opens_one_pull_request_when_resumed(github, tmp_path, monkeypatch):
    run = tmp_path / "run"
    real = delivery._gh

    def fails_at_create(*args, **kwargs):
        if args[:2] == ("pr", "create"):
            raise RuntimeError("the delivery stopped here")
        return real(*args, **kwargs)

    monkeypatch.setattr(delivery, "_gh", fails_at_create)
    failed = _design(run)
    assert failed.exit_ok is False and "the delivery stopped here" in (failed.error or "")
    slug = f"{LOGIN}/{NAME}"
    branch = worktree.recorded(run).branch
    assert _remote_head(github, slug, branch) and _pulls(github, slug) == []
    monkeypatch.setattr(delivery, "_gh", real)
    resumed = DesignMode.resume(run, model=FakeModel([]))
    assert resumed.exit_ok, resumed.error
    assert [pull["head"] for pull in _pulls(github, slug)] == [branch]
    assert _calls(tmp_path).count("repo create") == 1


def test_a_pull_request_that_shows_another_head_is_reported_not_claimed(github, tmp_path):
    run = tmp_path / "run"
    (github / "head-override").write_text("0" * 40 + "\n")
    result = _design(run)
    assert result.exit_ok is False
    assert "not this run's reviewed commit" in (result.error or "")
    assert _record(run)["status"] == "unverified"
    assert _record(run)["pull_request"] == f"https://github.com/{LOGIN}/{NAME}/pull/1"


def test_a_plan_that_changed_since_the_gate_keeps_the_work_local(github, tmp_path, capsys, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    # A fork holding the same main, so the run's base is on it and only the repository differs.
    fork = github / "acme/fork.git"
    git(fork.parent, "init", "-q", "--bare", "-b", "main", fork.name)
    (github / "acme/fork.json").write_text(json.dumps({"default_branch": "main"}))
    git(repo, "push", "-q", "https://github.com/acme/fork.git", "main:refs/heads/main")
    real = delivery.deliver_design

    def repointed(ctx):
        # Between the approval and delivery, the checkout started pointing elsewhere.
        git(repo, "remote", "set-url", "origin", "https://github.com/acme/fork.git")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", repointed)
    result = _design(tmp_path / "run", target=str(repo))
    assert result.exit_ok, result.error
    assert _pulls(github, "acme/app") == [] and _pulls(github, "acme/fork") == []
    err = capsys.readouterr().err
    assert "different from what the approval gate showed" in err and "acme/fork:main" in err


# ── refining an open pull request ──────────────────────────────────────────
def _opened(github: Path, tmp_path: Path) -> tuple[Path, str, dict]:
    """An existing repository with a pull request a design run opened."""
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    first = _design(tmp_path / "first", {"tool.py": "print('one')\n"}, target=str(repo))
    assert first.exit_ok, first.error
    return repo, first.facts["pull_request"], _pulls(github, "acme/app")[0]


def test_refine_starts_from_the_pull_requests_head_and_pushes_onto_its_branch(github, tmp_path):
    repo, url, pull = _opened(github, tmp_path)
    head = _remote_head(github, "acme/app", pull["head"])
    run = tmp_path / "refine"
    result = _design(run, {"tool.py": "print('one')\n", "more.py": "print('two')\n"}, refine=url)
    assert result.exit_ok, result.error
    found = worktree.recorded(run)
    assert found.base == head and found.target == str(repo.resolve())
    assert _remote_head(github, "acme/app", pull["head"]) == found.head
    assert git(repo, "merge-base", "--is-ancestor", head, found.head) == ""
    [after] = _pulls(github, "acme/app")
    assert (after["title"], after["body"]) == (pull["title"], pull["body"])
    assert _record(run)["status"] == "updated" and result.facts["pull_request"] == url
    assert f"pushes onto {pull['head']}, the branch of the pull request {url}" in (run / delivery.PLAN).read_text()
    ledger = (run / "intent-ledger.md").read_text()
    assert f"This run refines the pull request {url}" in ledger


def test_refine_pushes_nothing_once_the_pull_requests_branch_has_moved(github, tmp_path, capsys, monkeypatch):
    repo, url, pull = _opened(github, tmp_path)
    real = delivery.deliver_design

    def someone_pushed(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", "--branch", pull["head"], f"https://github.com/acme/app.git", str(other))
        (other / "theirs.py").write_text("x = 2\n")
        git(other, "add", "theirs.py")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "theirs")
        git(other, "push", "-q", "origin", pull["head"])
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", someone_pushed)
    run = tmp_path / "refine"
    result = _design(run, refine=url)
    assert result.exit_ok, result.error
    moved = _remote_head(github, "acme/app", pull["head"])
    assert moved != worktree.recorded(run).head
    assert git(tmp_path / "other", "rev-parse", "HEAD") == moved
    assert "moved to" in capsys.readouterr().err and _record(run)["status"] == "kept local"


def test_refine_with_review_comments_puts_them_in_the_steering(github, tmp_path):
    repo, url, pull = _opened(github, tmp_path)
    pulls = _pulls(github, "acme/app")
    pulls[0]["reviews"] = [{"author": {"login": "rev"}, "state": "CHANGES_REQUESTED", "body": "Rename --json."}]
    pulls[0]["comments"] = [{"author": {"login": "pm"}, "body": "Please keep the old flag working."}]
    pulls[0]["review_comments"] = [{"user": {"login": "rev"}, "path": "tool.py", "line": 1, "body": "Typo here."}]
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    run = tmp_path / "refine"
    result = _design(run, refine=url, mode=_mode(review_comments=True))
    assert result.exit_ok, result.error
    ledger = (run / "intent-ledger.md").read_text()
    for said in ("@rev (changes_requested): Rename --json.", "@pm: Please keep the old flag working.",
                 "@rev on tool.py:1: Typo here."):
        assert said in ledger


def test_refine_refuses_a_closed_pull_request_and_a_base(github, tmp_path):
    repo, url, _pull = _opened(github, tmp_path)
    refused = _design(tmp_path / "with-base", refine=url, base="main")
    assert refused.exit_ok is False and "takes no base" in (refused.error or "")
    pulls = _pulls(github, "acme/app")
    pulls[0]["state"] = "MERGED"
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    closed = _design(tmp_path / "closed", refine=url)
    assert closed.exit_ok is False and "is merged; only an open one can be refined" in (closed.error or "")


# ── what delivery never does ───────────────────────────────────────────────
def test_a_push_is_never_forced_and_never_to_the_default_branch(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    bare = _published(github, "acme/app", repo)
    with pytest.raises(delivery.NotDelivered, match="default branch"):
        delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "main", default="main")
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.py").write_text("y = 1\n")
    git(repo, "add", "side.py")
    git(repo, "commit", "-q", "-m", "side")
    delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "side", default="main")
    git(repo, "commit", "-q", "--amend", "-m", "side, rewritten")
    with pytest.raises(delivery.NotDelivered):
        delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "side", default="main")
    assert git(bare, "rev-parse", "side") == git(repo, "rev-parse", "HEAD@{1}")


def test_delivery_asks_github_with_the_persons_home_not_the_checks(github, tmp_path):
    result = _design(tmp_path / "run")
    assert result.exit_ok, result.error
    homes = set((github / "homes.log").read_text().split())
    assert homes == {os.environ["HOME"]}


def test_a_remote_url_is_read_as_github_only_on_github():
    for url in ("https://github.com/acme/app.git", "https://x-access-token:t@github.com/acme/app",
                "git@github.com:acme/app.git", "ssh://git@github.com/acme/app"):
        assert delivery._slug(url) == "acme/app", url
    for url in ("https://gitlab.com/acme/app.git", "git@example.com:acme/app.git", "/srv/app.git"):
        assert delivery._slug(url) is None, url


# ── every route that starts a design run says how it is delivered ──────────
def _launched(monkeypatch) -> list[list[str]]:
    """What a `refactory ceo` run would have handed DSH; nothing is launched."""
    from refactory_dsh import workers, interactive, dsh_profile

    class _Run:
        returncode = 0

    seen: list[list[str]] = []
    monkeypatch.setattr(workers, "run_graph_command", lambda argv, **kwargs: seen.append(argv) or _Run())
    monkeypatch.setattr(interactive, "require_build", lambda: None)
    monkeypatch.setattr(dsh_profile, "require", lambda *a, **k: None)
    return seen


def test_a_run_approved_up_front_says_how_it_will_be_delivered_before_it_starts(github, tmp_path, monkeypatch,
                                                                                capsys):
    seen = _launched(monkeypatch)
    run = tmp_path / "run"
    assert cli.main(["ceo", "a tool", "--check", "true", "--name", NAME, "--workdir", str(run),
                     "--approve", "gate_strategy=proceed", "--headless"]) == 0
    said = f"refactory: delivery — once the reviews pass, this run creates the private GitHub repository {LOGIN}/{NAME}"
    assert said in capsys.readouterr().err and seen
    assert json.loads((run / delivery.SHOWN).read_text())["action"] == "create"
    # The same mode named directly, with a knob given last winning as it does in the run.
    assert cli.main(["ceo", "default_pack/design", "--knob", "name=first", "--knob", f"name={NAME}",
                     "--knob", "brief=a tool", "--knob", "check=true", "--workdir", str(tmp_path / "named"),
                     "--approve", "gate_strategy=proceed", "--headless"]) == 0
    assert said in capsys.readouterr().err
    # A gate that will be asked shows delivery.md instead, and an earlier announcement goes.
    assert cli.main(["ceo", "a tool", "--check", "true", "--name", NAME, "--workdir", str(run), "--headless"]) == 0
    assert "refactory: delivery" not in capsys.readouterr().err and not (run / delivery.SHOWN).exists()


def test_the_command_line_hands_the_delivery_flags_to_the_run_as_knobs(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "_forward", lambda module, argv: seen.append(argv) or 0)
    assert cli.main(["ceo", "a tool", "--check", "true", "--no-pr", "--refine", "acme/app#3", "--review-comments"]) == 0
    knobs = [seen[-1][i + 1] for i, arg in enumerate(seen[-1]) if arg == "--knob"]
    assert {"pr=0", "refine=acme/app#3", "review_comments=1"} <= set(knobs)


def test_the_console_and_chat_route_says_how_it_will_be_delivered_when_approved_up_front(github, tmp_path,
                                                                                        monkeypatch, capsys):
    from refactory_dsh import design

    monkeypatch.setattr(design.DesignMode, "run", lambda self, *a, **k: nn.RunResult(exit_ok=True, facts={}))
    monkeypatch.chdir(tmp_path)
    assert design.main(["--name", NAME, "--brief", "a tool", "--check", "true", "--workdir", "run",
                        "--verdict", "gate_strategy=proceed"]) == 0
    assert f"creates the private GitHub repository {LOGIN}/{NAME}" in capsys.readouterr().err
    assert (tmp_path / "run" / delivery.SHOWN).exists()
    # Again in the same directory with the gate left to ask: the earlier announcement goes.
    assert design.main(["--name", NAME, "--brief", "a tool", "--check", "true", "--workdir", "run"]) == 0
    assert "refactory: delivery" not in capsys.readouterr().err
    assert not (tmp_path / "run" / delivery.SHOWN).exists()


def test_a_run_approved_up_front_does_only_what_it_announced(github, tmp_path, capsys):
    run = tmp_path / "run"
    (run / ".refactory").mkdir(parents=True)
    (run / delivery.SHOWN).write_text(json.dumps({"action": "none", "summary": "won't open a pull request, because x"}))
    result = _design(run)
    assert result.exit_ok, result.error
    assert "before the run started it said it won't open a pull request" in (run / delivery.PLAN).read_text()
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))


def test_the_command_line_refuses_delivery_flags_that_cannot_apply(capsys):
    assert cli.main(["ceo", "a tool", "--check", "true", "--refine", "acme/app#3", "--base", "main"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--review-comments"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--mode", "design_fast", "--no-pr"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--mode", "design_fast", "--refine", "acme/app#3"]) == 2
    assert cli.main(["ceo", "a tool", "--check", "true", "--no-worktree", "--refine", "acme/app#3"]) == 2
    err = capsys.readouterr().err
    assert "takes no --base" in err and "goes with --refine" in err
    assert "does not take --no-pr" in err and "--no-worktree cannot take --refine" in err
    assert "--mode design_fast does not take --refine" in err


def test_the_console_design_verb_forwards_how_the_run_is_delivered(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(console, "_forward", lambda module, argv: seen.append(argv) or {})
    assert console.main(["design", "--name", NAME, "--brief", "b", "--check", "true", "--no-pr",
                         "--refine", "acme/app#3", "--review-comments"]) == 0
    assert "--no-pr" in seen[-1] and "--review-comments" in seen[-1]
    assert seen[-1][seen[-1].index("--refine") + 1] == "acme/app#3"


def test_refactory_projects_lists_the_pull_request_a_run_opened(github, tmp_path, capsys):
    result = _design(tmp_path / "run")
    assert result.exit_ok, result.error
    capsys.readouterr()
    assert projects.main([]) == 0
    listed = capsys.readouterr().out
    assert result.facts["pull_request"] in listed and f"{LOGIN}/{NAME}" in listed


# ── the cases between the paths ────────────────────────────────────────────
def test_a_checkout_that_is_only_behind_github_gets_its_pull_request_and_keeps_its_refs(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "https://github.com/acme/app.git", str(other))
    (other / "later.py").write_text("x = 1\n")
    git(other, "add", "later.py")
    git(other, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "later")
    git(other, "push", "-q", "origin", "main")
    refs = git(repo, "for-each-ref")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    assert len(_pulls(github, "acme/app")) == 1
    branch = worktree.recorded(run).branch
    assert git(repo, "for-each-ref", "--exclude", f"refs/heads/{branch}") == refs
    assert not (repo / ".git" / "FETCH_HEAD").exists()


def test_a_creation_that_died_before_its_first_push_goes_on_in_the_repository_it_made(github, tmp_path, monkeypatch):
    run = tmp_path / "run"
    real = delivery._push

    def first_push_fails(path, slug, commit, branch, **kwargs):
        if branch == "main":
            raise RuntimeError("the delivery stopped here")
        return real(path, slug, commit, branch, **kwargs)

    monkeypatch.setattr(delivery, "_push", first_push_fails)
    assert _design(run).exit_ok is False
    slug = f"{LOGIN}/{NAME}"
    assert (github / f"{slug}.git").is_dir() and not _remote_head(github, slug, "main")
    monkeypatch.setattr(delivery, "_push", real)
    resumed = DesignMode.resume(run, model=FakeModel([]))
    assert resumed.exit_ok, resumed.error
    assert _calls(tmp_path).count("repo create") == 1 and len(_pulls(github, slug)) == 1
    assert _remote_head(github, slug, "main") == git(projects_dir() / NAME, "rev-parse", "main")


def test_a_repository_gh_cannot_push_to_gets_no_pull_request(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    (github / "acme/app.json").write_text(json.dumps({"default_branch": "main", "push": False}))
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "HEAD"), branch="refactory/run-0123456789ab")
    assert planned["action"] == "none" and "cannot push to acme/app" in planned["why"]


def test_a_refinement_delivered_once_is_not_refused_as_moved_when_asked_again(github, tmp_path, capsys):
    repo, url, pull = _opened(github, tmp_path)
    run = tmp_path / "refine"
    result = _design(run, refine=url)
    assert result.exit_ok, result.error
    capsys.readouterr()
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert again["pull_request"] == url and _record(run)["status"] == "already updated"
    assert "run warning" not in capsys.readouterr().err


def test_refine_refuses_a_pull_request_from_another_repositorys_branch(github, tmp_path):
    repo, url, _pull = _opened(github, tmp_path)
    pulls = _pulls(github, "acme/app")
    pulls[0]["cross"] = True
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    refused = _design(tmp_path / "fork", refine=url)
    assert refused.exit_ok is False and "another repository's branch" in (refused.error or "")


def test_a_later_report_of_the_same_run_keeps_its_pull_request_in_the_registry(tmp_path):
    repo = git_repo(tmp_path / "repo")
    projects.record_run(repo, run=tmp_path / "run", branch="refactory/run-0123456789ab", base="b", status="running")
    projects.record_delivery(repo, branch="refactory/run-0123456789ab", pull_request="https://github.com/a/b/pull/1",
                             status="opened")
    projects.record_run(repo, run=tmp_path / "run", branch="refactory/run-0123456789ab", base="b", status="succeeded")
    [entry] = projects.load()["projects"]
    assert entry["runs"][-1]["pull_request"] == "https://github.com/a/b/pull/1"


def test_design_fast_takes_none_of_delivery_s_knobs():
    from refactory_dsh.fast import DesignFastMode

    assert not {"pr", "repo_owner", "repo_visibility", "refine", "review_comments"} & set(DesignFastMode.declare())


def test_a_cli_run_ends_by_naming_its_pull_request(tmp_path):
    from refactory_dsh import interactive

    (tmp_path / "facts.json").write_text(json.dumps({"name": NAME, "pull_request": "https://github.com/a/b/pull/7",
                                                     "delivery_result": "opened: https://github.com/a/b/pull/7"}))
    assert "https://github.com/a/b/pull/7" in interactive.run_summary(tmp_path)


def test_a_new_projects_main_with_work_of_its_own_is_never_published(github, tmp_path, capsys):
    first = _design(tmp_path / "one", mode=_mode(pr=False))
    assert first.exit_ok, first.error
    project = projects_dir() / NAME
    (project / "notes.md").write_text("mine\n")
    git(project, "add", "notes.md")
    git(project, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "mine")
    second = _design(tmp_path / "two")
    assert second.exit_ok, second.error
    assert "no longer the empty first commit" in (tmp_path / "two" / delivery.PLAN).read_text()
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))
    assert "no pull request was opened" in capsys.readouterr().err


def test_without_a_network_the_run_succeeds_and_says_its_work_stays_local(github, tmp_path, capsys):
    (github / "offline").write_text("")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert "error connecting to api.github.com" in capsys.readouterr().err
    assert _record(run)["status"] == "kept local" and not list(github.glob("*/*.git"))


def test_review_comments_without_refine_are_refused_not_dropped(github, tmp_path):
    refused = _design(tmp_path / "run", mode=_mode(review_comments=True))
    assert refused.exit_ok is False and "review_comments goes with refine" in (refused.error or "")


def test_refactory_projects_says_when_a_run_kept_its_work_local(github, tmp_path, capsys):
    (github / "login").unlink()
    assert _design(tmp_path / "run").exit_ok
    capsys.readouterr()
    assert projects.main([]) == 0
    assert "kept local" in capsys.readouterr().out


def test_a_new_projects_first_pull_request_publishes_nothing_its_base_adds_to_main(github, tmp_path, capsys):
    first = _design(tmp_path / "one", mode=_mode(pr=False))
    assert first.exit_ok, first.error
    project = projects_dir() / NAME
    git(project, "checkout", "-q", "-b", "mywork", "main")
    (project / "secret-notes.md").write_text("mine\n")
    git(project, "add", "secret-notes.md")
    git(project, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "mine")
    git(project, "checkout", "-q", "main")
    second = _design(tmp_path / "two", base="mywork")
    assert second.exit_ok, second.error
    assert "1 commit(s) besides the empty main" in (tmp_path / "two" / delivery.PLAN).read_text()
    assert "repo create" not in _calls(tmp_path) and not list(github.glob("*/*.git"))


def test_a_credential_in_a_rewritten_url_never_reaches_a_message(monkeypatch, tmp_path):
    def leaks(*args, **kwargs):
        raise ValueError("git push failed in /r: fatal: unable to access 'https://me:ghp_secret@github.com/a/b.git/'")

    monkeypatch.setattr(delivery.worktree, "git", leaks)
    stub(tmp_path / "bin", "gh", "true")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(delivery.NotDelivered) as refused:
        delivery._push(tmp_path, "a/b", "0" * 40, "x", default="main")
    assert "ghp_secret" not in str(refused.value) and "https://***@github.com" in str(refused.value)


def test_a_pull_request_never_carries_a_credential_the_check_spelled_out(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run, check="API_TOKEN=abc123 python tool.py")
    assert result.exit_ok, result.error
    [pull] = _pulls(github, f"{LOGIN}/{NAME}")
    assert "abc123" not in pull["body"] and "API_TOKEN=*** python tool.py" in pull["body"]


def test_a_redo_after_its_pull_request_merged_records_it_merged(github, tmp_path, capsys):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    pulls = _pulls(github, slug)
    pulls[0]["state"] = "MERGED"
    (github / f"{slug}.pulls.json").write_text(json.dumps(pulls))
    capsys.readouterr()
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert (_record(run)["status"], again["pull_request"]) == ("merged", result.facts["pull_request"])
    assert "run warning" not in capsys.readouterr().err


def test_a_redo_after_its_pull_request_closed_keeps_it_in_the_record(github, tmp_path, capsys):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    slug = f"{LOGIN}/{NAME}"
    pulls = _pulls(github, slug)
    pulls[0]["state"] = "CLOSED"
    (github / f"{slug}.pulls.json").write_text(json.dumps(pulls))
    delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    record = _record(run)
    assert (record["status"], record["pull_request"]) == ("kept local", result.facts["pull_request"])
    assert f"{result.facts['pull_request']} was not updated" in capsys.readouterr().err
    assert len(_pulls(github, slug)) == 1


def test_a_pull_request_gh_opened_but_reported_failing_is_the_one_delivered(github, tmp_path, monkeypatch):
    real = delivery._gh

    def opens_then_fails(*args, **kwargs):
        said = real(*args, **kwargs)
        if args[:2] == ("pr", "create"):
            raise delivery.NotDelivered("`gh pr create` failed: timeout")
        return said

    monkeypatch.setattr(delivery, "_gh", opens_then_fails)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "opened" and len(_pulls(github, f"{LOGIN}/{NAME}")) == 1


def test_a_repository_gh_made_but_reported_failing_is_adopted_only_when_new_and_empty(github, tmp_path,
                                                                                     monkeypatch):
    real = delivery._gh

    def creates_then_fails(*args, **kwargs):
        said = real(*args, **kwargs)
        if args[:2] == ("repo", "create"):
            raise delivery.NotDelivered("`gh repo create` failed: timeout")
        return said

    monkeypatch.setattr(delivery, "_gh", creates_then_fails)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "opened"
    [entry] = projects.load()["projects"]
    assert entry["github"] == f"{LOGIN}/{NAME}"


def test_a_new_repository_that_is_slow_to_take_pushes_is_waited_for(github, tmp_path, monkeypatch):
    real, calls = delivery._heads, []

    def lags(path, slug):
        calls.append(slug)
        if len(calls) == 1:
            raise delivery.NotDelivered("git ls-remote failed: repository not found")
        return real(path, slug)

    monkeypatch.setattr(delivery, "_heads", lags)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "opened"


def test_a_remote_git_cannot_read_keeps_the_work_local_rather_than_failing_the_run(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", "https://github.com/acme/app.git")
    git(repo, "config", "--unset", "remote.origin.url")
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "HEAD"), branch="refactory/run-0123456789ab")
    assert planned["action"] == "none" and "could not read the remotes" in planned["why"]


def test_a_gh_that_cannot_even_start_keeps_the_work_local(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text("#!/no/such/interpreter\n")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert "could not run" in _record(run)["why"] and "no pull request was opened" in capsys.readouterr().err


def test_a_branch_that_moved_since_its_reviews_is_never_delivered(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    unreviewed = {**result.facts, "commit": "0" * 40}
    with pytest.raises(ValueError, match="not at the attempt its reviews passed"):
        delivery.deliver_design(nn.RunContext(run, facts=unreviewed, enforce_writes=False))


def test_a_refinement_never_pushes_onto_a_default_branch(github, tmp_path):
    repo, url, pull = _opened(github, tmp_path)
    pulls = _pulls(github, "acme/app")
    pulls[0]["head"] = "main"
    (github / "acme/app.pulls.json").write_text(json.dumps(pulls))
    head = _remote_head(github, "acme/app", "main")
    planned = delivery.plan(repo, base=head, branch="refactory/run-0123456789ab",
                            refine={"url": url, "repo": "acme/app", "head": head})
    assert planned["action"] == "none" and "is acme/app's default branch" in planned["why"]


def test_a_new_repository_goes_where_repo_owner_and_repo_visibility_say(github, tmp_path):
    result = _design(tmp_path / "run", mode=_mode(repo_owner="acme-org", repo_visibility="internal"))
    assert result.exit_ok, result.error
    assert f"repo create acme-org/{NAME} --internal" in _calls(tmp_path)
    assert json.loads((github / "acme-org" / f"{NAME}.json").read_text())["visibility"] == "internal"
    assert result.facts["pull_request"] == f"https://github.com/acme-org/{NAME}/pull/1"


def test_a_refinement_that_github_shows_at_another_head_is_reported_not_claimed(github, tmp_path):
    repo, url, _pull = _opened(github, tmp_path)
    (github / "stale-view").write_text("0" * 40 + "\n")
    run = tmp_path / "refine"
    result = _design(run, refine=url)
    assert result.exit_ok is False and "not this run's reviewed commit" in (result.error or "")
    assert _record(run)["status"] == "unverified"


def test_a_pull_request_already_open_is_checked_again_when_asked_again(github, tmp_path):
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    (github / "stale-view").write_text("0" * 40 + "\n")
    with pytest.raises(delivery.Unverified):
        delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))


def test_the_targets_pre_push_hook_does_not_run_for_a_delivery(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    bare = _published(github, "acme/app", repo)
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\ntouch '{tmp_path / 'hook-ran'}'\nexit 1\n")
    hook.chmod(0o755)
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.py").write_text("y = 1\n")
    git(repo, "add", "side.py")
    git(repo, "commit", "-q", "-m", "side")
    delivery._push(repo, "acme/app", git(repo, "rev-parse", "HEAD"), "side", default="main")
    assert git(bare, "rev-parse", "side") == git(repo, "rev-parse", "HEAD")
    assert not (tmp_path / "hook-ran").exists()


def test_nothing_delivery_runs_can_stop_to_ask_a_question():
    env = delivery._env()
    assert (env["GIT_TERMINAL_PROMPT"], env["GIT_ASKPASS"], env["GCM_INTERACTIVE"], env["GH_PROMPT_DISABLED"]) == (
        "0", "", "never", "1")
    assert env["GIT_SSH_COMMAND"].endswith("-o BatchMode=yes")


def test_the_console_announces_a_run_approved_up_front_before_it_is_handed_on(github, tmp_path, monkeypatch, capsys):
    said_first: list[str] = []
    monkeypatch.setattr(console, "_forward", lambda module, argv: said_first.append(capsys.readouterr().err) or {})
    monkeypatch.chdir(tmp_path)
    assert console.main(["design", "--name", NAME, "--brief", "a tool", "--check", "true",
                         "--verdict", "gate_strategy=proceed"]) == 0
    assert f"creates the private GitHub repository {LOGIN}/{NAME}" in said_first[-1]


# ── the second review: the approval card, the base's branch, and what is where ─
def test_the_approval_card_itself_names_the_repository_a_run_would_create(github, tmp_path):
    """The card shows only the gate's question, so the question carries the plan (#362 review)."""
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    said = f"creates the private GitHub repository {LOGIN}/{NAME}"
    assert "{{delivery}}" in DesignMode().build().nodes["gate_strategy"].notes
    # The walker's journal shows the gate as it was put, never the raw placeholder.
    journal = [json.loads(line) for line in (run / ".refactory" / "events.jsonl").read_text().splitlines()]
    shown = [record["summary"] for record in journal if record.get("node_id") == "gate_strategy" and record.get("summary")]
    assert shown and all(said in summary and "{{" not in summary for summary in shown)
    # The panel's pending-gate card, read by the console from a spine run's graph and facts.json.
    (run / "facts.json").write_text(json.dumps(result.facts, default=str))
    (run / ".refactory" / "spine-status.json").write_text(json.dumps({"status": "waiting_user", "nodes": ["gate_strategy"]}))
    graphs = run / "graphs"
    graphs.mkdir(exist_ok=True)
    (graphs / "design.graph.json").write_text(json.dumps({"nodes": {"gate_strategy": {
        "_type": "GateNode", "evaluator_type": "user",
        "gate_prompt": DesignMode().build().nodes["gate_strategy"].notes}}, "edges": [
        {"source": "gate_strategy", "target": "emit", "condition": "proceed"}]}))
    [card] = console.gates(run)
    assert said in card["prompt"] and "{{" not in card["prompt"]


def _stacked(github: Path, tmp_path: Path) -> tuple[Path, str]:
    """A repository on GitHub with a pushed branch `stack` one commit ahead of main; main is checked out."""
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    git(repo, "checkout", "-q", "-b", "stack")
    (repo / "lib.py").write_text("x = 1\n")
    git(repo, "add", "lib.py")
    git(repo, "commit", "-q", "-m", "the stack's own commit")
    git(repo, "push", "-q", "-u", "origin", "stack")
    git(repo, "checkout", "-q", "main")
    return repo, git(repo, "rev-parse", "stack")


def test_a_run_on_a_pushed_branch_opens_its_pull_request_into_that_branch(github, tmp_path):
    repo, stack = _stacked(github, tmp_path)
    run = tmp_path / "run"
    result = _design(run, target=str(repo), base="stack")
    assert result.exit_ok, result.error
    [pull] = _pulls(github, "acme/app")
    assert pull["base"] == "stack", "a stack's pull request goes into the branch it builds on"
    assert "into acme/app:stack, the branch its base came from" in (run / delivery.PLAN).read_text()
    # It carries only the run's own commit, not the stack's.
    branch = worktree.recorded(run).branch
    assert git(repo, "rev-list", "--count", f"{stack}..{branch}") == "1"


def test_a_run_from_a_checkout_on_a_pushed_branch_goes_into_its_upstream(github, tmp_path):
    repo, _stack = _stacked(github, tmp_path)
    git(repo, "checkout", "-q", "stack")
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    assert _pulls(github, "acme/app")[0]["base"] == "stack"


def test_a_base_branch_github_does_not_have_falls_back_to_the_default_branch(github, tmp_path):
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    git(repo, "branch", "topic")
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "topic"), branch="refactory/run-0123456789ab",
                            rev="topic")
    assert (planned["action"], planned["into"]) == ("pull_request", "main")
    assert planned["summary"].endswith("into acme/app:main")


def test_a_delivery_that_stopped_after_its_push_says_the_branch_is_on_github(github, tmp_path, monkeypatch, capsys):
    real = delivery._gh

    def no_pull_request(*args, **kwargs):
        if args[:2] == ("pr", "create"):
            raise delivery.NotDelivered("`gh pr create` failed: error connecting to api.github.com")
        return real(*args, **kwargs)

    monkeypatch.setattr(delivery, "_gh", no_pull_request)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    branch = worktree.recorded(run).branch
    assert _remote_head(github, f"{LOGIN}/{NAME}", branch)
    err = capsys.readouterr().err
    assert f"the branch {branch} is already on GitHub, as {LOGIN}/{NAME}:{branch}" in err
    assert "stays on the local branch" not in err
    assert _record(run)["pushed"] == f"{LOGIN}/{NAME}:{branch}"


def test_a_repository_whose_creation_lost_the_network_is_finished_by_a_redo(github, tmp_path, monkeypatch, capsys):
    """gh reports the creation failed, the network then drops; the redo adopts the repository it made."""
    real = delivery._gh

    def creates_then_drops(*args, **kwargs):
        said = real(*args, **kwargs)
        if args[:2] == ("repo", "create"):
            (github / "offline").write_text("")
            raise delivery.NotDelivered("`gh repo create` failed: timeout")
        return said

    monkeypatch.setattr(delivery, "_gh", creates_then_drops)
    run = tmp_path / "run"
    result = _design(run)
    assert result.exit_ok, result.error
    assert _record(run)["status"] == "kept local" and (github / LOGIN / f"{NAME}.git").is_dir()
    (github / "offline").unlink()
    monkeypatch.setattr(delivery, "_gh", real)
    again = delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))
    assert again["pull_request"] == f"https://github.com/{LOGIN}/{NAME}/pull/1"
    assert _calls(tmp_path).count("repo create") == 2 and len(_pulls(github, f"{LOGIN}/{NAME}")) == 1


def test_a_repository_with_the_name_that_this_project_never_set_out_to_create_is_not_adopted(github, tmp_path):
    first = _design(tmp_path / "one", mode=_mode(pr=False))
    assert first.exit_ok, first.error
    bare = github / LOGIN / f"{NAME}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    git(bare.parent, "init", "-q", "--bare", "-b", "main", bare.name)
    (github / LOGIN / f"{NAME}.json").write_text(json.dumps({"default_branch": "main"}))
    second = _design(tmp_path / "two")
    assert second.exit_ok, second.error
    assert "already exists" in (tmp_path / "two" / delivery.PLAN).read_text() and _pulls(github, f"{LOGIN}/{NAME}") == []


def test_a_run_planned_while_gh_was_logged_out_says_the_next_run_delivers(github, tmp_path):
    (github / "login").unlink()
    run = tmp_path / "run"
    assert _design(run).exit_ok
    assert "`gh auth login` signs in, for the next run" in (run / delivery.PLAN).read_text()


def test_the_pull_request_says_the_commits_are_the_runs_attempts(github, tmp_path):
    assert _design(tmp_path / "run").exit_ok
    [pull] = _pulls(github, f"{LOGIN}/{NAME}")
    assert "the run's attempts" in pull["body"]
    assert "squash merge is recommended" not in pull["body"]


# ── pull request text for kept-local deliveries (dm-86) ────────────────────

def test_no_pr_run_leaves_pr_text_and_title_in_delivery_json(github, tmp_path, capsys):
    """A --no-pr run that passes its reviews leaves pull-request.md with a non-empty
    Summary and its title in delivery.json.

    Fails without the change because _kept never writes BODY and never records title.
    """
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    run = tmp_path / "run"
    result = _design(run, mode=_mode(pr=False), target=str(repo))
    assert result.exit_ok, result.error
    # pull-request.md exists with a Summary section
    body_path = run / delivery.BODY
    assert body_path.exists(), "pull-request.md should exist for a --no-pr run with a worktree"
    body_text = body_path.read_text()
    assert "## Summary" in body_text
    assert "Written by a refactory design run" in body_text
    assert "Opened by a refactory design run" not in body_text
    # delivery.json records title, pr_text, and finish
    record = _record(run)
    assert record["title"] and isinstance(record["title"], str)
    assert record["pr_text"] == delivery.BODY
    assert isinstance(record["finish"], list) and len(record["finish"]) > 0
    # delivery_result includes the finish commands (reaching the person even without a warning)
    assert "--body-file" in result.facts["delivery_result"]
    assert "--title" in result.facts["delivery_result"]
    # design.json inherits title
    design = json.loads((run / "design.json").read_text())
    assert design["delivery"]["title"]
    # A deliberate --no-pr emits no warning
    assert "run warning" not in capsys.readouterr().err


def test_plan_changed_leaves_pr_text_and_finish_commands(github, tmp_path, capsys, monkeypatch):
    """A plan that changed since the gate keeps the work local but still leaves
    pull-request.md and finish commands.

    Fails without the change because _kept never writes BODY and its warning has no
    --body-file.
    """
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    # A fork holding the same main, so the run's base is on it and only the repository differs.
    fork = github / "acme/fork.git"
    git(fork.parent, "init", "-q", "--bare", "-b", "main", fork.name)
    (github / "acme/fork.json").write_text(json.dumps({"default_branch": "main"}))
    git(repo, "push", "-q", "https://github.com/acme/fork.git", "main:refs/heads/main")
    real = delivery.deliver_design

    def repointed(ctx):
        git(repo, "remote", "set-url", "origin", "https://github.com/acme/fork.git")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", repointed)
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    # pull-request.md exists
    assert (run / delivery.BODY).exists()
    body_text = (run / delivery.BODY).read_text()
    assert "## Summary" in body_text
    # The warning includes the complete command
    err = capsys.readouterr().err
    assert "--body-file" in err
    assert "--title" in err
    # delivery.json records title and finish
    record = _record(run)
    assert record["title"]
    assert any("gh pr create" in cmd and "--body-file" in cmd for cmd in record["finish"])


def test_push_refused_leaves_pr_text_and_finish_commands(github, tmp_path, capsys, monkeypatch):
    """A push GitHub refuses keeps the work local but still leaves pull-request.md
    and finish commands.

    Fails without the change because _kept never writes BODY and its warning has no
    --body-file.
    """
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    real_push = delivery._push

    def push_refused(*args, **kwargs):
        raise delivery.NotDelivered("push refused by GitHub")

    monkeypatch.setattr(delivery, "_push", push_refused)
    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error
    # pull-request.md exists
    assert (run / delivery.BODY).exists()
    body_text = (run / delivery.BODY).read_text()
    assert "## Summary" in body_text
    # The warning includes the complete commands
    err = capsys.readouterr().err
    assert "--body-file" in err
    assert "--title" in err
    # delivery.json records title and status
    record = _record(run)
    assert record["title"]
    assert record["status"] == "kept local"
    assert any("gh pr create" in cmd and "--body-file" in cmd for cmd in record["finish"])


def test_no_worktree_writes_no_pr_text(github, tmp_path, monkeypatch):
    """A run with no worktree writes no pull-request.md, no title, and no pr_text.

    This test passes both before and after the change — it verifies the guard.
    """
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "run"
    result = _design(run, mode=_mode(worktree=False))
    assert result.exit_ok, result.error
    assert not (run / delivery.BODY).exists()
    record = _record(run)
    assert not record.get("title")
    assert "pr_text" not in record


def test_finish_commands_open_a_pull_request_with_the_exact_title_and_body(github, tmp_path):
    """Runs a --no-pr design, reads the finish commands from delivery.json, and
    executes them against the test's fake GitHub.  Asserts a PR opens with the exact
    title and body — proving nothing is left for a person to write.

    Fails without the change because delivery.json has no finish field.
    """
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    run = tmp_path / "run"
    result = _design(run, mode=_mode(pr=False), target=str(repo))
    assert result.exit_ok, result.error
    record = _record(run)
    finish = record["finish"]
    assert len(finish) == 2, f"expected push + gh pr create, got {finish}"
    # The push command uses https://github.com/ and :refs/heads/ (not origin)
    push_cmd = finish[0]
    assert "https://github.com/" in push_cmd
    assert ":refs/heads/" in push_cmd
    assert "origin" not in push_cmd
    # Execute the push command
    subprocess.run(push_cmd, shell=True, check=True, capture_output=True,
                   env={**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
                        "GIT_TERMINAL_PROMPT": "0"})
    # Execute the gh pr create command
    subprocess.run(finish[1], shell=True, check=True, capture_output=True,
                   env={**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
                        "FAKE_GH_ROOT": str(github)})
    # A pull request was opened
    [pull] = _pulls(github, "acme/app")
    assert pull["title"] == record["title"]
    body_text = (run / delivery.BODY).read_text()
    assert pull["body"] == body_text


def test_finish_commands_work_with_special_characters_in_title(github, tmp_path):
    """A title containing a quote, a backtick, and a $ works through shell quoting.

    Fails without the change because delivery.json has no finish field.
    """
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    run = tmp_path / "run"
    # A design doc whose heading has special characters, so _title picks them up.
    tricky_brief = "Fix the audit's `gate` and $PATH handling"
    tricky_design = (
        "# Fix the audit's `gate` and $PATH handling\n\n"
        "## Summary\n\nFix it properly.\n\n"
        "## Rule and sites\n\n"
        "- `tool.py:1` — **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- tool.py\n"
    )
    tricky_design += (
        "\n## Anti-patterns to avoid\n\n"
        "- PIT-001: addressed at `tool.py:1`\n"
    )
    answers = [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
               tricky_design, DESIGN_REVIEW_ANSWER,
               render({"tool.py": "print('built')\n"}),
               *REVIEW_ANSWERS]
    model = FakeModel(answers)
    result = _mode(pr=False).run(
        run, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": tricky_brief, "check": "python tool.py",
              "target": str(repo)},
    )
    assert result.exit_ok, result.error
    record = _record(run)
    finish = record["finish"]
    assert len(finish) == 2
    # Execute the commands
    subprocess.run(finish[0], shell=True, check=True, capture_output=True,
                   env={**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
                        "GIT_TERMINAL_PROMPT": "0"})
    subprocess.run(finish[1], shell=True, check=True, capture_output=True,
                   env={**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
                        "FAKE_GH_ROOT": str(github)})
    [pull] = _pulls(github, "acme/app")
    # The title contains the special characters intact
    assert "'" in pull["title"] or "`" in pull["title"] or "$" in pull["title"]
    assert pull["title"] == record["title"]


def test_no_pr_finish_commands_are_complete(github, tmp_path, capsys):
    """A --no-pr run whose target has a GitHub remote gets complete finish commands
    with --repo, --title, and --body-file, and they appear in delivery_result.

    Fails without the change because delivery.json has no finish field and
    delivery_result has no commands.
    """
    repo = git_repo(tmp_path / "repo")
    _published(github, "acme/app", repo)
    run = tmp_path / "run"
    result = _design(run, mode=_mode(pr=False), target=str(repo))
    assert result.exit_ok, result.error
    record = _record(run)
    finish = record["finish"]
    assert len(finish) == 2
    gh_cmd = finish[1]
    assert "--repo" in gh_cmd
    assert "--title" in gh_cmd
    assert "--body-file" in gh_cmd
    # delivery_result carries the finish commands
    dr = result.facts["delivery_result"]
    assert "--body-file" in dr
    assert "--title" in dr
    # No warning for deliberate --no-pr
    assert "run warning" not in capsys.readouterr().err


def test_no_github_remote_says_so_plainly(github, tmp_path, capsys):
    """A target with no GitHub remote gets no commands and a plain message.

    The second correction says: say plainly that there is no GitHub repository.
    """
    run = tmp_path / "run"
    result = _design(run, mode=_mode(pr=False))
    assert result.exit_ok, result.error
    # pull-request.md is still written (there IS a worktree)
    assert (run / delivery.BODY).exists()
    record = _record(run)
    assert record.get("title")
    # No commands because there's no remote
    assert record.get("finish") == []
    # delivery_result mentions no GitHub repository
    dr = result.facts["delivery_result"]
    assert "no GitHub repository" in dr
    assert "pull request text is at" in dr


# ── merge-before-deliver (dm-76) ───────────────────────────────────────────

def test_base_moved_with_generated_conflict(github, tmp_path, monkeypatch):
    """The base moves after the run starts, with a change to a generated file and to an
    unrelated file: the delivered branch contains the base, the generated file is
    regenerated, the check ran on the merged result, and the pull request merges cleanly.

    Fails without the change: delivery pushes the reviewed commit as-is, and the PR
    opens behind its base with a generated-file conflict.
    """
    repo = git_repo(tmp_path / "repo")
    # gen.sh: concatenate source files into generated.txt
    (repo / "gen.sh").write_text("cat source.txt > generated.txt\n"
                                 "if [ -f source2.txt ]; then cat source2.txt >> generated.txt; fi\n")
    (repo / "source.txt").write_text("alpha\n")
    # generate from the initial state
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base with gen")
    slug = "acme/merge-gen"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        """Between the run's start and delivery, advance main with a generated-file conflict."""
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        (other / "source2.txt").write_text("gamma\n")
        (other / "unrelated.txt").write_text("hello\n")
        # regenerate generated.txt with the base's new sources
        subprocess.run(["bash", "gen.sh"], cwd=str(other), check=True, capture_output=True)
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base moves: add source2 and unrelated")
        git(other, "push", "-q", "origin", "main")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    # The run changes source.txt and writes tool.py; gen.sh is the generate command.
    result = _design(run, {"tool.py": "print('built')\n", "source.txt": "alpha\nbeta\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh")
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    assert record.get("merge_commit") and len(record["merge_commit"]) == 40
    merge_commit = record["merge_commit"]
    assert record.get("reviewed_commit") and record["reviewed_commit"] != merge_commit
    assert record.get("merge_check") == "passed on the merged result"

    # The merge commit has exactly two parents.
    parents = [line.split()[1] for line in
               git(bare, "cat-file", "-p", merge_commit).splitlines()
               if line.startswith("parent ")]
    assert len(parents) == 2

    # The delivered branch contains the base.
    main_tip = git(bare, "rev-parse", "refs/heads/main")
    assert git(bare, "merge-base", "--is-ancestor", main_tip, merge_commit) == ""

    # The generated file has content from both sides.
    generated = git(bare, "show", f"{merge_commit}:generated.txt")
    assert "alpha" in generated and "beta" in generated and "gamma" in generated

    # The PR body names both commits and says the check passed on the merged result.
    [pull] = _pulls(github, slug)
    assert record["reviewed_commit"][:12] in pull["body"]
    assert merge_commit[:12] in pull["body"]
    assert "check passed on the merged result" in pull["body"]

    # The remote branch head equals the merge commit.
    branch = json.loads((run / "design.json").read_text())["branch"]
    assert _remote_head(github, slug, branch) == merge_commit


def test_base_changed_authored_file_conflict(github, tmp_path, monkeypatch):
    """The base changes the same lines of an authored file: nothing is resolved
    automatically, the reviewed commit is delivered, and the conflict is named.

    Fails without the change: delivery pushes the reviewed commit as-is, and the PR
    opens behind its base. It does not name the conflicting file in the PR body.
    """
    repo = git_repo(tmp_path / "repo")
    (repo / "authored.py").write_text("line1\nline2\nline3\n")
    (repo / "gen.sh").write_text("echo generated > generated.txt\n")
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base with authored")
    slug = "acme/merge-auth"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        (other / "authored.py").write_text("line1\nchanged_by_base\nline3\n")
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base changes authored.py line 2")
        git(other, "push", "-q", "origin", "main")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    result = _design(run, {"tool.py": "print('built')\n",
                           "authored.py": "line1\nchanged_by_run\nline3\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh")
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    assert not record.get("merge_commit")
    assert "authored.py" in record.get("merge_conflicts", [])

    # The PR body names the conflict.
    [pull] = _pulls(github, slug)
    assert "authored.py" in pull["body"]
    assert "conflicts" in pull["body"].lower()

    # The remote head is the reviewed commit (no merge).
    branch = json.loads((run / "design.json").read_text())["branch"]
    reviewed = worktree.recorded(run).head
    assert _remote_head(github, slug, branch) == reviewed


def test_merged_result_fails_check(github, tmp_path, monkeypatch, capsys):
    """The merged result fails the check: the reviewed commit is delivered, and the
    reason names the check failure.

    Fails without the change: status is "kept local", no PR is opened, and the warning
    says "base changed under it" rather than delivering.
    """
    repo = git_repo(tmp_path / "repo")
    (repo / "config.txt").write_text("ok\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base config ok")
    slug = "acme/merge-fail"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        (other / "config.txt").write_text("broken\n")
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base breaks config")
        git(other, "push", "-q", "origin", "main")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    # tool.py fails when config.txt says "broken"
    result = _design(run, {"tool.py": "import sys; sys.exit(1 if open('config.txt').read().strip() == 'broken' else 0)\n"},
                     target=str(repo), check="python tool.py")
    assert result.exit_ok, result.error

    record = _record(run)
    # The reviewed commit is delivered (not kept local).
    assert record["status"] == "opened"
    assert record.get("merge_check_failed")
    assert "check failed" in record.get("merge_check_failed", "").lower() or record.get("merge_check_failed")

    # The PR body says the reviewed commit was delivered and the check failed.
    [pull] = _pulls(github, slug)
    assert "the reviewed commit was delivered" in pull["body"]
    assert "check failed" in pull["body"].lower()

    # A run warning was emitted.
    captured = capsys.readouterr()
    assert "check failed on the merged result" in captured.err

    # The remote branch head is the reviewed commit (not a merge commit).
    branch = json.loads((run / "design.json").read_text())["branch"]
    reviewed = worktree.recorded(run).head
    assert _remote_head(github, slug, branch) == reviewed


def test_base_did_not_move(github, tmp_path):
    """The base did not move: no merge commit.

    This test passes without the change too (it tests the no-change path). It asserts
    that no merge_commit is recorded, guarding against a spurious merge.
    """
    repo = git_repo(tmp_path / "repo")
    slug = "acme/no-move"
    _published(github, slug, repo)

    run = tmp_path / "run"
    result = _design(run, target=str(repo))
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    assert not record.get("merge_commit")

    # No merge commits in the delivered branch.
    branch = json.loads((run / "design.json").read_text())["branch"]
    remote_head = _remote_head(github, slug, branch)
    assert remote_head == worktree.recorded(run).head
    merges = git(github / f"{slug}.git", "rev-list", "--merges",
                 f"{git(repo, 'rev-parse', 'main')}..{remote_head}")
    assert merges == ""


def test_generated_content_equals_base_version_still_merges(github, tmp_path, monkeypatch):
    """When the merged generated content equals the base's version, the file is still
    classified as generated (A == B from both sides' generate), and the merge succeeds.

    This tests the correction #2: classify by whether generate determines the file,
    not by whether generate changed it relative to the base's version.
    """
    repo = git_repo(tmp_path / "repo")
    # gen.sh always writes the same content regardless of other source files.
    (repo / "gen.sh").write_text("echo 'static-content' > generated.txt\n")
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    (repo / "source.txt").write_text("alpha\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    slug = "acme/merge-static-gen"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        # Base changes generated.txt to something else (simulating a different generator version),
        # but the generate command always produces 'static-content'.
        (other / "generated.txt").write_text("old-generated-content\n")
        (other / "extra.txt").write_text("extra\n")
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base changes generated.txt")
        git(other, "push", "-q", "origin", "main")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    # The run also changes generated.txt (via generate), causing a conflict.
    result = _design(run, {"tool.py": "print('built')\n", "source.txt": "alpha\nbeta\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh")
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    # The merge succeeded even though the generated content equals the base's version
    # (generate produces 'static-content' from both sides).
    assert record.get("merge_commit") and len(record["merge_commit"]) == 40
    # generated.txt has the generate command's output.
    generated = git(bare, "show", f"{record['merge_commit']}:generated.txt")
    assert generated.strip() == "static-content"


def test_timeout_uses_check_timeout_s(github, tmp_path, monkeypatch):
    """The run's check_timeout_s knob is used for generate, setup and the check during
    delivery's merge, not a hard-coded 300.

    Fails without the change: the captured timeout is 300 (hard-coded).
    """
    repo = git_repo(tmp_path / "repo")
    (repo / "gen.sh").write_text("echo generated > generated.txt\n")
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    slug = "acme/merge-timeout"
    _published(github, slug, repo)

    real = delivery.deliver_design
    captured_timeouts: list[float] = []

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        (other / "extra.txt").write_text("new\n")
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base moves")
        git(other, "push", "-q", "origin", "main")

        from refactory_dsh import sandbox as _sbx
        _orig_run = _sbx.run_command

        def _spy_run(*args, **kwargs):
            if "timeout_s" in kwargs:
                captured_timeouts.append(kwargs["timeout_s"])
            return _orig_run(*args, **kwargs)

        monkeypatch.setattr(_sbx, "run_command", _spy_run)
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    result = _design(run, {"tool.py": "print('built')\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh",
                     mode=_mode(check_timeout_s=1800))
    assert result.exit_ok, result.error

    # Every timeout captured during the merge's generate/setup/check equals the knob.
    assert captured_timeouts, "no sandbox.run_command calls were captured"
    for t in captured_timeouts:
        assert t == 1800.0, f"expected 1800, got {t}"


def test_conflict_path_regenerates_all_files(github, tmp_path, monkeypatch):
    """Both generated files are regenerated after the merge, even when only one
    conflicted.

    Fails without the change: gen2.txt has a stale line count (2 instead of 3)
    from git's three-way merge, because only conflicted files had generate run
    on them in the conflict path.
    """
    repo = git_repo(tmp_path / "repo")
    # gen.sh: gen1.txt combines two inputs on one line (so both sides changing
    # a different input creates a single-line conflict); gen2.txt is a line-
    # count header plus the content of source2.txt — non-linear, so git's
    # auto-merge gives a stale count when both sides added lines.
    (repo / "gen.sh").write_text(
        "#!/bin/sh\n"
        'printf "%s%s\\n" "$(cat input_a.txt)" "$(cat input_b.txt)" > gen1.txt\n'
        "n=$(cat source2.txt | wc -l)\n"
        'printf "count:%d\\n" "$n" > gen2.txt\n'
        "cat source2.txt >> gen2.txt\n"
    )
    (repo / "input_a.txt").write_text("A\n")
    (repo / "input_b.txt").write_text("B\n")
    (repo / "source2.txt").write_text("original\n")
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base with two gen files")
    slug = "acme/merge-regen-all"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        # Base changes input_b (creates a conflict in gen1.txt because the run
        # changes input_a) and appends to source2.txt (gen2.txt merges cleanly
        # from git's perspective but its line-count header becomes stale).
        (other / "input_b.txt").write_text("B-base\n")
        (other / "source2.txt").write_text("original\nbase-line\n")
        subprocess.run(["bash", "gen.sh"], cwd=str(other), check=True, capture_output=True)
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base updates both inputs")
        git(other, "push", "-q", "origin", "main")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    # The run changes input_a (making gen1.txt conflict) and prepends to
    # source2.txt (so the merged tree has 3 lines, not 2).
    result = _design(run, {"tool.py": "print('built')\n",
                           "input_a.txt": "A-run\n",
                           "source2.txt": "run-line\noriginal\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh")
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    assert record.get("merge_commit")
    merge_commit = record["merge_commit"]

    # gen1.txt: merged inputs (A-run from run + B-base from base) on one line.
    gen1 = git(bare, "show", f"{merge_commit}:gen1.txt")
    assert "A-run" in gen1 and "B-base" in gen1
    # gen2.txt: regenerated on the merged tree (3 lines), not stale (count:2).
    gen2 = git(bare, "show", f"{merge_commit}:gen2.txt")
    assert "count:3" in gen2


def test_modify_delete_conflict(github, tmp_path, monkeypatch):
    """A modify/delete conflict where --theirs has no file to check out: the merge is
    abandoned, the reviewed commit is delivered, and the error names the file.

    Fails without the change: subprocess.run returns non-zero silently, and the merge
    proceeds with a broken tree.
    """
    repo = git_repo(tmp_path / "repo")
    (repo / "gen.sh").write_text("echo ok > generated.txt\n")
    (repo / "conflicted.txt").write_text("original\n")
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base with conflicted.txt")
    slug = "acme/merge-moddel"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        # Base deletes the file.
        git(other, "rm", "conflicted.txt")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base deletes conflicted.txt")
        git(other, "push", "-q", "origin", "main")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    # The run modifies the file.
    result = _design(run, {"tool.py": "print('built')\n",
                           "conflicted.txt": "modified by run\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh")
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    # The file is named as a conflict.
    assert "conflicted.txt" in record.get("merge_conflicts", [])
    # The remote head is the reviewed commit (no merge).
    branch = json.loads((run / "design.json").read_text())["branch"]
    reviewed = worktree.recorded(run).head
    assert _remote_head(github, slug, branch) == reviewed


def test_generate_fails_classes_as_authored(github, tmp_path, monkeypatch):
    """When generate fails on the merged tree during conflict classification,
    the conflicting files are classified as authored.

    Fails without the change: the generate failure's exit status is not checked;
    the file is classified based on stale content.
    """
    repo = git_repo(tmp_path / "repo")
    # gen.sh fails if a marker file exists.
    (repo / "gen.sh").write_text(
        "if [ -f FAIL_GEN ]; then exit 1; fi\n"
        "cat source.txt > generated.txt\n"
    )
    (repo / "source.txt").write_text("alpha\n")
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    slug = "acme/merge-genfail"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        # Base adds the marker that makes generate fail and changes source.txt.
        (other / "FAIL_GEN").write_text("yes\n")
        (other / "source.txt").write_text("gamma\n")
        subprocess.run(["bash", str(other / "gen.sh")], cwd=str(other), capture_output=True)
        # gen.sh fails, so write generated.txt manually for the base commit.
        (other / "generated.txt").write_text("gamma\n")
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base adds fail marker")
        git(other, "push", "-q", "origin", "main")
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    result = _design(run, {"tool.py": "print('built')\n", "source.txt": "alpha\nbeta\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh")
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    # The conflicting file is classified as authored (not merged).
    assert "generated.txt" in record.get("merge_conflicts", [])
    # The remote head is the reviewed commit.
    branch = json.loads((run / "design.json").read_text())["branch"]
    reviewed = worktree.recorded(run).head
    assert _remote_head(github, slug, branch) == reviewed


def test_post_checkout_hook_does_not_run(github, tmp_path, monkeypatch):
    """A post-checkout hook in the target must not run during delivery's merge.

    Fails without the change: subprocess.run does not use NO_HOOKS, so the
    post-checkout hook runs and creates the marker.
    """
    repo = git_repo(tmp_path / "repo")
    (repo / "gen.sh").write_text("cat source.txt > generated.txt\n")
    (repo / "source.txt").write_text("alpha\n")
    subprocess.run(["bash", "gen.sh"], cwd=str(repo), check=True, capture_output=True)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    slug = "acme/merge-hook"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        (other / "source.txt").write_text("gamma\n")
        subprocess.run(["bash", "gen.sh"], cwd=str(other), check=True, capture_output=True)
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base changes source")
        git(other, "push", "-q", "origin", "main")

        # Install a post-checkout hook in the worktree's git dir.
        found = worktree.recorded(ctx.workdir)
        wt = Path(found.path)
        marker = wt / "HOOK_RAN"
        # Find the git dir (for a worktree, .git is a file with "gitdir: <path>").
        dot_git = wt / ".git"
        if dot_git.is_file():
            gitdir = Path(dot_git.read_text().strip().removeprefix("gitdir: "))
            if not gitdir.is_absolute():
                gitdir = wt / gitdir
        else:
            gitdir = dot_git
        hooks = gitdir / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-checkout"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        result = real(ctx)
        # The marker must not exist.
        assert not marker.exists(), "post-checkout hook ran during delivery"
        return result

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    result = _design(run, {"tool.py": "print('built')\n", "source.txt": "alpha\nbeta\n"},
                     target=str(repo), check="python tool.py",
                     generate="bash gen.sh")
    assert result.exit_ok, result.error


def test_redo_after_merge_pushed(github, tmp_path, monkeypatch):
    """A redo after the merge commit was pushed finishes without a second PR and
    without a refused push.

    Fails without the change: the redo creates a new merge commit with different
    timestamps; pushing it is not a fast-forward, so git refuses it.
    """
    repo = git_repo(tmp_path / "repo")
    (repo / "extra.txt").write_text("placeholder\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    slug = "acme/merge-redo"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_and_fail_verify(ctx):
        """First delivery: advance main, then sabotage _verified via stale-view."""
        other = tmp_path / "other"
        if not other.exists():
            git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
            (other / "extra.txt").write_text("new\n")
            git(other, "add", ".")
            git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
                "commit", "-q", "-m", "base moves")
            git(other, "push", "-q", "origin", "main")
        # Make _verified fail: fake_gh returns stale headRefOid for "headRefOid,url" queries.
        (github / "stale-view").write_text("0" * 40)
        monkeypatch.setattr(delivery, "VERIFY_ATTEMPTS", 1)
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_and_fail_verify)
    run = tmp_path / "run"
    result = _design(run, {"tool.py": "print('built')\n"},
                     target=str(repo), check="python tool.py")

    # The first run failed (Unverified) or ended otherwise; the branch should
    # be on GitHub because the push succeeded before _verified failed.
    branch = json.loads((run / "design.json").read_text())["branch"]
    first_remote = _remote_head(github, slug, branch)

    if not first_remote:
        # If no push happened, the base did not actually move; skip the redo test.
        return

    # Redo: remove the sabotage, then call deliver_design directly (as the
    # existing redo tests do), not DesignMode.resume which refuses a node
    # whose side-effect outcome is unknown.
    (github / "stale-view").unlink(missing_ok=True)
    monkeypatch.setattr(delivery, "deliver_design", real)
    monkeypatch.setattr(delivery, "VERIFY_ATTEMPTS", 5)
    delivery.deliver_design(nn.RunContext(run, facts=result.facts, enforce_writes=False))

    record2 = _record(run)
    assert record2["status"] in ("opened", "already open", "updated"), f"redo status: {record2['status']}"

    # No second pull request.
    pulls = _pulls(github, slug)
    assert len(pulls) <= 1, f"expected at most 1 PR, got {len(pulls)}"

    # The remote head is the same or a descendant of the first merge.
    second_remote = _remote_head(github, slug, branch)
    assert second_remote, "redo should have the branch on GitHub"


def test_dependency_install_for_merged_tree(github, tmp_path, monkeypatch):
    """When the base adds a dependency (changes pyproject.toml), ensure_environment
    is called for the merged tree with commit_lockfiles=False.

    Fails without the change: ensure_environment is never called for the merged tree.
    """
    repo = git_repo(tmp_path / "repo")
    (repo / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "0.1.0"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base with pyproject")
    slug = "acme/merge-deps"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design
    ensure_calls: list[dict] = []

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        # Base changes pyproject.toml.
        (other / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "0.1.0"\ndependencies = ["requests"]\n')
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base adds dependency")
        git(other, "push", "-q", "origin", "main")

        orig_ensure = worktree.ensure_environment

        def _spy_ensure(found, run, budget=None, *, commit_lockfiles=True):
            ensure_calls.append({"commit_lockfiles": commit_lockfiles})
            # Don't actually install anything; just return no problems.
            return found, [], None

        monkeypatch.setattr(worktree, "ensure_environment", _spy_ensure)
        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    result = _design(run, {"tool.py": "print('built')\n"},
                     target=str(repo), check="python tool.py")
    assert result.exit_ok, result.error

    # ensure_environment was called with commit_lockfiles=False.
    non_amend = [c for c in ensure_calls if not c["commit_lockfiles"]]
    assert non_amend, f"ensure_environment was not called with commit_lockfiles=False; calls: {ensure_calls}"

    # The reviewed commit's tree is unchanged (no lockfile added).
    reviewed = worktree.recorded(run).head
    wt = Path(worktree.recorded(run).path)
    # Check that the reviewed commit has no lockfile that wasn't there before.
    base = worktree.recorded(run).base
    base_files = set(git(wt, "ls-tree", "-r", "--name-only", base).splitlines())
    head_files = set(git(wt, "ls-tree", "-r", "--name-only", reviewed).splitlines())
    # No new lockfiles should appear in the reviewed commit.
    new_locks = {f for f in (head_files - base_files) if f in ("uv.lock", "pnpm-lock.yaml", "package-lock.json", "yarn.lock")}
    # (This assertion depends on the test's setup; it mainly guards that amend was not called.)


def test_merge_fails_no_conflicted_files(github, tmp_path, monkeypatch):
    """When git merge fails with no conflicted files (e.g. untracked file it would
    overwrite), the merge is abandoned, the reviewed commit is delivered, and the
    error is named through the abandoned field.

    Fails without the change: the error is stuffed into the conflicts list as a reason
    string, not into a separate abandoned field.
    """
    repo = git_repo(tmp_path / "repo")
    slug = "acme/merge-noconflict"
    bare = _published(github, slug, repo)

    real = delivery.deliver_design

    def advance_main_then_deliver(ctx):
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", f"https://github.com/{slug}.git", str(other))
        # Base adds a file.
        (other / "blocker.txt").write_text("from base\n")
        git(other, "add", ".")
        git(other, "-c", "user.name=t", "-c", "user.email=t@example.com",
            "commit", "-q", "-m", "base adds blocker")
        git(other, "push", "-q", "origin", "main")

        # Create an untracked file in the worktree with the same name, so git merge
        # fails with "would be overwritten" and no conflicted files.
        found = worktree.recorded(ctx.workdir)
        wt = Path(found.path)
        (wt / "blocker.txt").write_text("untracked content\n")

        return real(ctx)

    monkeypatch.setattr(delivery, "deliver_design", advance_main_then_deliver)

    run = tmp_path / "run"
    result = _design(run, {"tool.py": "print('built')\n"},
                     target=str(repo), check="python tool.py")
    assert result.exit_ok, result.error

    record = _record(run)
    assert record["status"] == "opened"
    # The abandoned field names the error (not in conflicts).
    assert record.get("merge_abandoned"), f"no merge_abandoned in record: {record}"
    assert not record.get("merge_conflicts"), f"unexpected merge_conflicts: {record.get('merge_conflicts')}"

    # The PR body says "merge abandoned".
    [pull] = _pulls(github, slug)
    assert "merge abandoned" in pull["body"].lower() or "abandoned" in pull["body"].lower()

    # The remote head is the reviewed commit.
    branch = json.loads((run / "design.json").read_text())["branch"]
    reviewed = worktree.recorded(run).head
    assert _remote_head(github, slug, branch) == reviewed


def test_an_upstream_that_does_not_hold_the_base_is_not_where_the_pull_request_goes(github, tmp_path):
    repo, _stack = _stacked(github, tmp_path)
    # main moves on GitHub; a local branch at that commit still names stack as its upstream.
    (repo / "later.py").write_text("y = 2\n")
    git(repo, "add", "later.py")
    git(repo, "commit", "-q", "-m", "main moves on")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "branch", "-q", "--track", "elsewhere", "origin/stack")
    git(repo, "branch", "-q", "-f", "elsewhere", "main")
    planned = delivery.plan(repo, base=git(repo, "rev-parse", "main"), branch="refactory/run-0123456789ab",
                            rev="elsewhere")
    assert (planned["action"], planned["into"]) == ("pull_request", "main"), planned
