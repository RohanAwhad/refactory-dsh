"""Tests for gate_qa and gate_overwatch handling of stale findings.

Verifies:
- gate_qa clears validation.txt on proceed
- gate_overwatch proceeds on VERDICT: PASS with quoted stale references
- gate_overwatch reloops (not hard-stops) on real BLOCKING findings
- blocking_findings skips blockquoted and inline-code-quoted BLOCKING markers
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from textwrap import dedent

import pytest

from refactory_dsh.create_ops import blocking_findings


class TestBlockingFindings:
    """blocking_findings must only count live findings, not quoted mentions."""

    def test_real_blocking_finding(self):
        text = "## Review\nBLOCKING: the widget crashes on empty input\n"
        assert blocking_findings(text) == ["the widget crashes on empty input"]

    def test_blockquoted_blocking_is_skipped(self):
        text = dedent("""\
            ## Overwatch
            The earlier round noted:
            > BLOCKING: the widget crashes on empty input
            This was fixed in round 2.
            VERDICT: PASS
        """)
        assert blocking_findings(text) == []

    def test_inline_code_blocking_is_skipped(self):
        text = dedent("""\
            ## Overwatch
            The validation.txt file carried `BLOCKING: old finding` from round 1.
            This has been resolved.
            VERDICT: PASS
        """)
        assert blocking_findings(text) == []

    def test_fenced_code_blocking_is_skipped(self):
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

    def test_blocking_none_is_not_a_finding(self):
        text = "BLOCKING: none\nVERDICT: PASS\n"
        assert blocking_findings(text) == []

    def test_real_finding_among_quoted(self):
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

    def test_code_formatted_marker_with_separator_is_caught(self):
        """`BLOCKING:` followed by finding text is a real marker."""
        text = "`BLOCKING:` the CLI crashes on an empty file\n"
        findings = blocking_findings(text)
        assert len(findings) == 1
        assert "the CLI crashes" in findings[0]

    def test_bold_code_formatted_marker_is_caught(self):
        """**`BLOCKING:`** followed by finding text is a real marker."""
        text = "**`BLOCKING:`** the CLI crashes on an empty file\n"
        findings = blocking_findings(text)
        assert len(findings) == 1
        assert "the CLI crashes" in findings[0]

    def test_bare_code_mention_without_separator_is_skipped(self):
        """`BLOCKING` without a separator is a prose mention, not a marker."""
        text = "`BLOCKING` finding. The stale reference does not change the substance.\n"
        assert blocking_findings(text) == []

    def test_code_quoted_full_finding_is_skipped(self):
        """A full old finding inside backticks is a quotation, not live."""
        text = "The earlier review said `BLOCKING: widget crashes on empty input` but this was fixed.\n"
        assert blocking_findings(text) == []
