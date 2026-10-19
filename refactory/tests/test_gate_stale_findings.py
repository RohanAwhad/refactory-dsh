"""``blocking_findings`` counts live BLOCKING markers, not quoted mentions of them.

A reviewer that cites an earlier round's finding in a blockquote, inline code or a
fenced block is not raising it again; a marker in its own text, however it is
formatted, is a finding.
"""
from __future__ import annotations

from textwrap import dedent

from refactory_dsh.create_ops import blocking_findings


def test_real_blocking_finding():
    text = "## Review\nBLOCKING: the widget crashes on empty input\n"
    assert blocking_findings(text) == ["the widget crashes on empty input"]


def test_blockquoted_blocking_is_skipped():
    text = dedent("""\
        ## Overwatch
        The earlier round noted:
        > BLOCKING: the widget crashes on empty input
        This was fixed in round 2.
        VERDICT: PASS
    """)
    assert blocking_findings(text) == []


def test_inline_code_blocking_is_skipped():
    text = dedent("""\
        ## Overwatch
        The validation.txt file carried `BLOCKING: old finding` from round 1.
        This has been resolved.
        VERDICT: PASS
    """)
    assert blocking_findings(text) == []


def test_fenced_code_blocking_is_skipped():
    text = dedent("""\
        ## Review
        Previous output:
        ```
        BLOCKING: old issue
        ```
        Current status: all clear.
        VERDICT: PASS
    """)
    assert blocking_findings(text) == []


def test_blocking_none_is_not_a_finding():
    text = "BLOCKING: none\nVERDICT: PASS\n"
    assert blocking_findings(text) == []


def test_real_finding_among_quoted():
    text = dedent("""\
        ## Review
        Previous round noted:
        > BLOCKING: old issue from round 1
        Current findings:
        BLOCKING: new widget crash on large input
        VERDICT: FAIL
    """)
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "new widget crash" in findings[0]


def test_code_formatted_marker_with_separator_is_caught():
    """`BLOCKING:` followed by finding text is a real marker."""
    text = "`BLOCKING:` the CLI crashes on an empty file\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_bold_code_formatted_marker_is_caught():
    """**`BLOCKING:`** followed by finding text is a real marker."""
    text = "**`BLOCKING:`** the CLI crashes on an empty file\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_bare_code_mention_without_separator_is_skipped():
    """`BLOCKING` without a separator is a prose mention, not a marker."""
    text = "`BLOCKING` finding. The stale reference does not change the substance.\n"
    assert blocking_findings(text) == []


def test_code_quoted_full_finding_is_skipped():
    """A full old finding inside backticks is a quotation, not live."""
    text = "The earlier review said `BLOCKING: widget crashes on empty input` but this was fixed.\n"
    assert blocking_findings(text) == []


def test_code_marker_separator_after_span_is_caught():
    """`BLOCKING`: with the separator outside the backticks is a marker."""
    text = "`BLOCKING`: the CLI crashes on an empty file\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_bold_code_marker_separator_after_span_is_caught():
    """**`BLOCKING`** — with separator after bold+code is a marker."""
    text = "**`BLOCKING`** — the CLI crashes on an empty file\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


# --- b13a: bare-space / separator case-sensitivity tests ---


def test_b8_category_line_blocking_finding_is_not_a_finding():
    """b8's review listed tests by category; 'Blocking finding:' is not a finding."""
    text = "- Blocking finding: `test_design_review_gate_reloops_on_blocking_finding`\n"
    assert blocking_findings(text) == []


def test_b8_category_line_missing_verdict_is_not_a_finding():
    """b8's '- Missing verdict: ...' does not start with BLOCKING at all."""
    text = "- Missing verdict: `test_qa_gate_fails_when_verdict_missing`\n"
    assert blocking_findings(text) == []


def test_bold_blocking_colon_is_a_finding():
    """**Blocking:** with a separator is still case-insensitive."""
    text = "**Blocking:** the CLI crashes\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_uppercase_blocking_bare_space_is_a_finding():
    """Uppercase BLOCKING followed by bare space is a finding."""
    text = "BLOCKING the CLI crashes\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_lowercase_blocking_bare_space_is_not_a_finding():
    """Title-case 'Blocking' followed by bare space is NOT a finding."""
    text = "Blocking the CLI crashes\n"
    assert blocking_findings(text) == []


def test_mixed_case_blocking_bare_space_is_not_a_finding():
    """Mixed-case 'BLOcking' followed by bare space is NOT a finding."""
    text = "BLOcking the CLI crashes\n"
    assert blocking_findings(text) == []


def test_separator_colon_case_insensitive():
    """Colon separator remains case-insensitive."""
    text = "Blocking: the CLI crashes\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_separator_em_dash_case_insensitive():
    """Em-dash separator remains case-insensitive."""
    text = "blocking\u2014 the CLI crashes\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_separator_en_dash_case_insensitive():
    """En-dash separator remains case-insensitive."""
    text = "blocking\u2013 the CLI crashes\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_separator_hyphen_case_insensitive():
    """Hyphen separator remains case-insensitive."""
    text = "blocking- the CLI crashes\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_quoted_lowercase_blocking_colon_not_counted():
    """Lowercase `blocking: ...` inside backticks is stripped as a mention."""
    text = "The earlier review said `blocking: widget crashes` but this was fixed.\n"
    assert blocking_findings(text) == []


def test_bold_blocking_no_separator_is_not_a_finding():
    """**Blocking** without a separator is the same false positive as b8's line."""
    text = "**Blocking** finding: `x`\n"
    assert blocking_findings(text) == []


def test_bold_uppercase_blocking_no_separator_is_a_finding():
    """**BLOCKING** without a separator is a finding (uppercase required)."""
    text = "**BLOCKING** the CLI crashes\n"
    findings = blocking_findings(text)
    assert len(findings) == 1
    assert "the CLI crashes" in findings[0]


def test_overwatch_quoted_blocking_example_is_not_a_finding():
    """A code span quoting a test input that contains BLOCKING is not a finding."""
    text = "- `- **BLOCKING** x` tested in existing `test_create.py` via `test_a_bolded_blocking_finding_is_not_invisible_to_the_qa_gate` \u2713\n"
    assert blocking_findings(text) == []


# ── finding-scope filter tests ─────────────────────────────────────────────


def test_classify_finding_structured_worktree_file_stays_blocking():
    """``BLOCKING: refactory/x.py: problem`` stays blocking — it names a worktree file."""
    from refactory_dsh.create_ops import classify_finding

    owners = {"design.md": "design_doc", "strategy.json": "synthesize_strategy"}
    is_blocking, step = classify_finding(
        "refactory/refactory_dsh/x.py: does not implement design.md's Rule 2",
        owners,
    )
    assert is_blocking is True
    assert step is None


def test_classify_finding_structured_run_doc_downgraded():
    """``BLOCKING: design.md: markers are in backticks`` is downgraded."""
    from refactory_dsh.create_ops import classify_finding

    owners = {"design.md": "design_doc", "strategy.json": "synthesize_strategy"}
    is_blocking, step = classify_finding(
        "design.md: site markers are in backticks",
        owners,
    )
    assert is_blocking is False
    assert step == "design_doc"


def test_classify_finding_freeform_stays_blocking():
    """``BLOCKING: the change does not do what design.md says`` stays blocking."""
    from refactory_dsh.create_ops import classify_finding

    owners = {"design.md": "design_doc"}
    is_blocking, step = classify_finding(
        "the change does not do what design.md says",
        owners,
    )
    assert is_blocking is True
    assert step is None


def test_classify_finding_no_path_stays_blocking():
    """A finding citing no file paths at all is conservatively blocking."""
    from refactory_dsh.create_ops import classify_finding

    owners = {"design.md": "design_doc"}
    is_blocking, step = classify_finding(
        "the tests are incomplete",
        owners,
    )
    assert is_blocking is True


def test_run_doc_owners_from_declarations(tmp_path):
    """``_run_doc_owners`` reads the graph's declarations.json correctly."""
    import json
    from refactory_dsh.create_ops import _run_doc_owners

    decls = {
        "design_doc": {"kind": "agent", "reads": [], "writes": ["design.md"], "produces": []},
        "synthesize_strategy": {"kind": "agent", "reads": [], "writes": ["strategy.json"], "produces": []},
        "prepare": {"kind": "op", "reads": [], "writes": ["brief.json", "research-review.txt"], "produces": []},
        "emit": {"kind": "agent", "reads": [], "writes": ["emit.txt"], "produces": []},
        "memory": ["some_memory"],  # non-dict entry, should be skipped
    }
    (tmp_path / ".refactory").mkdir()
    (tmp_path / ".refactory" / "declarations.json").write_text(json.dumps(decls))

    owners = _run_doc_owners(tmp_path)
    assert owners["design.md"] == "design_doc"
    assert owners["strategy.json"] == "synthesize_strategy"
    assert owners["brief.json"] == "prepare"
    assert owners["emit.txt"] == "emit"
    assert "research-review.txt" in owners


def test_scope_filter_with_custom_graph_document(tmp_path):
    """A graph whose plan document has a different name is routed to that node."""
    import json
    from refactory_dsh.create_ops import scope_filter_findings

    decls = {
        "custom_planner": {"kind": "agent", "reads": [], "writes": ["plan.md"], "produces": []},
        "emit": {"kind": "agent", "reads": [], "writes": ["emit.txt"], "produces": []},
    }
    (tmp_path / ".refactory").mkdir()
    (tmp_path / ".refactory" / "declarations.json").write_text(json.dumps(decls))

    findings = ["plan.md: the plan is incomplete"]
    blocking, notes = scope_filter_findings(findings, tmp_path)
    assert blocking == []
    assert len(notes) == 1
    assert "custom_planner" in notes[0]
    assert "plan.md" in notes[0]


def test_classify_finding_exact_path_match(tmp_path):
    """``design.md`` matches the writes declaration exactly, not ``docs/design.md``."""
    from refactory_dsh.create_ops import classify_finding

    owners = {"design.md": "design_doc"}
    # An exact match is downgraded.
    is_blocking, step = classify_finding("design.md: markers wrong", owners)
    assert is_blocking is False
    assert step == "design_doc"
    # A worktree file with a different path stays blocking.
    is_blocking2, step2 = classify_finding("docs/design.md: wrong content", owners)
    assert is_blocking2 is True
    assert step2 is None


def test_scope_filter_mixed_findings(tmp_path):
    """A mix of run-doc and worktree findings is split correctly."""
    import json
    from refactory_dsh.create_ops import scope_filter_findings

    decls = {
        "design_doc": {"kind": "agent", "reads": [], "writes": ["design.md"], "produces": []},
    }
    (tmp_path / ".refactory").mkdir()
    (tmp_path / ".refactory" / "declarations.json").write_text(json.dumps(decls))

    findings = [
        "design.md: site markers are in backticks",
        "refactory/refactory_dsh/x.py: does not implement Rule 2",
        "the tests are incomplete",
    ]
    blocking, notes = scope_filter_findings(findings, tmp_path)
    assert len(blocking) == 2
    assert "refactory/refactory_dsh/x.py: does not implement Rule 2" in blocking
    assert "the tests are incomplete" in blocking
    assert len(notes) == 1
    assert "design_doc" in notes[0]


def test_reviews_quoting_markers_are_not_findings():
    """b13a's own code review quoted the inputs it checked, some in double-backtick spans."""
    text = (
        "- `` `- **BLOCKING** x` `` \u2192 stripped (the `-` and space are non-decoration"
        " chars before BLOCKING) \u2713\n"
        "- `` `BLOCKING` `` \u2192 kept (no non-decoration char before BLOCKING) \u2713\n"
        "- `` `BLOCKING:` `` \u2192 kept (`:` appears after BLOCKING) \u2713\n"
        "- `` `**BLOCKING**` `` \u2192 kept (only `*` chars before BLOCKING) \u2713\n"
        "- `BLOCKING the CLI crashes` was the old marker\n"
        "- `**BLOCKING** x` is counted\n"
    )
    assert blocking_findings(text) == []
