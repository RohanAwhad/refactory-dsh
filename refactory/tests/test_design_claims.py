"""Tests for dm-126 (design claims checker) and dm-127 (review status at gate).

The claims check never sends a design back (one design pass): what it cannot confirm
is an unverified claim, listed first on the approval gate's card.

Each test fails without its fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import nn
from refactory_dsh.design_claims import check_design_claims, _extract_claims

# A minimal contract-compliant design for gate_design_review tests.
_CONTRACT_DESIGN = (
    "# Do the thing\n\n"
    "## Summary\n\nFix it.\n\n"
    "## Failure trace\n\nN/A — this is a new feature.\n\n"
    "## Ask mapping\n\n"
    "| Ask (quoted from brief) | Design section | Test |\n"
    "|---|---|---|\n"
    '| "do a thing" | § Changes | test.py |\n\n'
    "## Rule and sites\n\n"
    "- `file.py:1` — **[changed]**\n\n"
    "## Docs\n\n"
    "- docs/models.md\n\n"
    "## Changes\n\n"
    "- file.py\n\n"
    "## Tests\n\n"
    "- `test.py::test_it` — it does the thing\n"
)


# ── dm-126: claims checker ────────────────────────────────────────────────


#: A design run plans its delivery by asking gh for its login, so it asks the fake (dm-271).
pytestmark = pytest.mark.usefixtures("fake_gh")


def _unverified(ctx) -> str:
    """Run the claims check; its unverified claims as one text."""
    result = check_design_claims(ctx)
    return "\n".join(result["claims_check"]["unverified"])


def test_missing_function_is_unverified(tmp_path):
    """A design naming a missing function is an unverified claim, not a round."""
    (tmp_path / "sample.py").write_text("def existing_fn():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`nonexistent_fn()` at `sample.py:1`: `def existing_fn():`\n"
    ))
    result = check_design_claims(ctx)
    unverified = "\n".join(result["claims_check"]["unverified"])
    assert "nonexistent_fn" in unverified
    assert "not found" in unverified
    assert ctx.facts.get("design_review_attempts_used", 0) == 0
    assert "- unverified claim: `sample.py:1`: `nonexistent_fn` not found" in (
        result["claims_check_feedback"])


def test_misplaced_quote_within_the_window_is_a_note(tmp_path):
    """A quote nine lines from its citation counts; the note says where it was found."""
    lines = [
        "def alpha():",
        "    pass",
        "x = 1",
        "y = 2",
        "class Beta:",
        "    pass",
        "z = 3",
        "w = 4",
        "v = 5",
        "def gamma():",
        "    pass",
    ]
    (tmp_path / "sample.py").write_text("\n".join(lines) + "\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
    })
    # Claim gamma() is at line 1, but it is actually at line 10.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`gamma()` at `sample.py:1`: `def gamma():`\n"
    ))
    result = check_design_claims(ctx)
    assert result["claims_check"] == {
        "unverified": [], "notes": ["`sample.py:1`: cited 1, found at 10"]}


def test_quote_beyond_the_window_is_unverified(tmp_path):
    """A quote more than 20 lines from its citation is not counted; where it is, is said."""
    (tmp_path / "sample.py").write_text("x = 0\n" * 30 + "def gamma():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`gamma()` at `sample.py:2`: `def gamma():`\n")
    unverified = _unverified(ctx)
    assert "quote `def gamma():` not within 20 lines of `sample.py:2`" in unverified
    assert "line 2 is `x = 0`" in unverified
    assert "found at line 31, more than 20 lines away" in unverified


def test_correct_design_proceeds(tmp_path):
    """A design with correct claims goes straight to review."""
    (tmp_path / "sample.py").write_text("def real_function():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "`real_function()` at `sample.py:1`: `def real_function():`\n"
    ))
    result = check_design_claims(ctx)
    assert result == {"claims_check": {"unverified": [], "notes": []},
                      "claims_check_feedback": ""}


def test_subjectless_claim_with_wrong_quote_is_unverified(tmp_path):
    """A claim with no subject but a wrong quote is unverified."""
    (tmp_path / "sample.py").write_text("def alpha():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "At `sample.py:1`: `def beta():`\n"
    ))
    assert "beta" in _unverified(ctx)


def test_design_doc_answer_no_false_positives(tmp_path):
    """DESIGN_DOC_ANSWER uses bare citations (no subject-at-citation-quote), so no claims."""
    from tests_support import DESIGN_DOC_ANSWER
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
    })
    ctx.write_text("design.md", DESIGN_DOC_ANSWER)
    claims = _extract_claims(DESIGN_DOC_ANSWER)
    assert len(claims) == 0, f"DESIGN_DOC_ANSWER should have zero subject-at-citation-quote claims, got {claims}"
    assert _unverified(ctx) == ""


def test_realistic_design_text_no_false_mismatches(tmp_path):
    """Realistic design text with correct claims gives zero mismatches."""
    # Create files matching the claims.
    src = tmp_path / "src"
    src.mkdir()
    (src / "design_ops.py").write_text(
        "from refactory_dsh.nn import Verdict\n"
        "\n"
        "def gate_design_review(ctx):\n"
        "    pass\n"
    )
    (src / "file.py").write_text(
        "def something():\n"
        "    pass\n"
    )
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(src),
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `design_ops.py:3` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- design_ops.py\n\n"
        "`gate_design_review()` at `design_ops.py:3`: `def gate_design_review(ctx):`\n"
    ))
    assert check_design_claims(ctx)["claims_check"] == {"unverified": [], "notes": []}

    # Now add a claim with a nonexistent function.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `file.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- file.py\n\n"
        "`client.register_tool()` at `file.py:1`: `def something():`\n"
    ))
    assert "register_tool" in _unverified(ctx)


def test_a_quote_spanning_a_blank_line_matches(tmp_path):
    (tmp_path / "sample.py").write_text("x = call(\n\n    1)\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`sample.py:1`: `x = call( 1)`\n")
    assert _unverified(ctx) == ""


def test_the_nearest_match_in_the_window_is_the_one_noted(tmp_path):
    lines = ["pass"] * 40
    lines[4] = lines[26] = "value = compute()"
    (tmp_path / "sample.py").write_text("\n".join(lines) + "\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`sample.py:24`: `value = compute()`\n")
    assert check_design_claims(ctx)["claims_check"]["notes"] == [
        "`sample.py:24`: cited 24, found at 27"]


def test_a_quote_that_begins_inside_the_window_counts(tmp_path):
    """A two-line quote beginning on the window's last line counts, though it ends past it."""
    lines = ["pass"] * 30
    lines[20] = "result = run("
    lines[21] = "    fast=True)"
    (tmp_path / "sample.py").write_text("\n".join(lines) + "\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`sample.py:1`: `result = run( fast=True)`\n")
    assert check_design_claims(ctx)["claims_check"] == {
        "unverified": [], "notes": ["`sample.py:1`: cited 1, found at 21"]}


def test_an_ellipsis_quote_needs_its_parts_inside_the_window(tmp_path):
    lines = ["pass"] * 60
    lines[0] = "def run(self,"
    lines[45] = "        ) -> None:"
    (tmp_path / "sample.py").write_text("\n".join(lines) + "\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`sample.py:1`: `def run(\u2026) -> None:`\n")
    assert _unverified(ctx) == (
        "quote `def run(\u2026) -> None:` not within 20 lines of `sample.py:1`: line 1 is "
        "`def run(self,`; its parts do not all appear, in order, within 3 lines")


# The review's wrong claims (PR 499): an ellipsis quote whose parts were matched anywhere
# in the window, across unrelated code.
_STORE = (
    "class Store:\n"                       # 1
    "    def load(self, path):\n"          # 2
    "        self.path = path\n"           # 3
    "        self.data = read(path)\n"     # 4
    "        return self.data\n"           # 5
    "\n"                                   # 6
    "    def peek(self):\n"                # 7
    "        return self.data\n"           # 8
    "    def close(self) -> None:\n"       # 9
    "        self.data = None\n"           # 10
    "\n"                                   # 11
    "    def save(self, path):\n"          # 12
    "        write(path, self.data)\n"     # 13
)


@pytest.mark.parametrize("claim, why", [
    # `load(` paired with the `) -> None:` of close(), seven lines below.
    ("`sample.py:2`: `def load(\u2026) -> None:`",
     "line 2 is `def load(self, path):`; its parts do not all appear, in order, within 3 lines"),
    # One quote across three methods.
    ("`sample.py:2`: `def load(\u2026 def peek(\u2026 def save(self, path):`",
     "line 2 is `def load(self, path):`; its parts do not all appear, in order, within 3 lines"),
    # Parts that name nothing would match any parenthesised line.
    ("`sample.py:4`: `(\u2026)`",
     "line 4 is `self.data = read(path)`; the part before its first ellipsis names nothing "
     "(`(`), so it would match almost any line; begin the quote with a word"),
], ids=["load-paired-with-close", "three-methods", "only-punctuation"])
def test_an_ellipsis_quote_does_not_pair_unrelated_code(tmp_path, claim, why):
    (tmp_path / "sample.py").write_text(_STORE)
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", claim + "\n")
    quote = claim.split(": ", 1)[1].strip("`")
    cited = claim.split("`")[1]
    assert _unverified(ctx) == f"quote `{quote}` not within 20 lines of `{cited}`: {why}"


def test_an_ellipsis_quote_over_one_or_two_lines_still_verifies(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def run(self, config, *, retries=3) -> int:\n"
        "    return 1\n"
        "def load(self,\n"
        "         path: str) -> None:\n"
        "    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    for claim in ("`sample.py:1`: `def run(\u2026) -> int:`",
                  "`sample.py:3`: `def load(\u2026) -> None:`",
                  "`sample.py:3-4`: `def load(self, \u2026 path: str) -> None:`"):
        ctx.write_text("design.md", claim + "\n")
        assert check_design_claims(ctx)["claims_check"] == {"unverified": [], "notes": []}, claim
    # A drifted one is found where it is, as a quote without an ellipsis is.
    ctx.write_text("design.md", "`sample.py:1`: `def load(\u2026) -> None:`\n")
    assert check_design_claims(ctx)["claims_check"] == {
        "unverified": [], "notes": ["`sample.py:1`: cited 1, found at 3"]}


def test_an_ellipsis_quote_spans_its_cited_lines_plus_two_and_never_more_than_fifteen(tmp_path):
    lines = ["def build(", *[f"    arg{n}," for n in range(1, 20)], ") -> None:", "    pass"]
    (tmp_path / "sample.py").write_text("\n".join(lines) + "\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})

    def verified(cited: str, quote: str) -> bool:
        ctx.write_text("design.md", f"`{cited}`: `{quote}`\n")
        return check_design_claims(ctx)["claims_check"]["unverified"] == []

    # One cited line: the parts may lie across three lines.
    assert verified("sample.py:1", "def build(\u2026 arg2,")
    assert not verified("sample.py:1", "def build(\u2026 arg3,")
    # Two cited lines: across four.
    assert verified("sample.py:1-2", "def build(\u2026 arg3,")
    assert not verified("sample.py:1-2", "def build(\u2026 arg4,")
    # A signature cited by its whole range: that range plus two.
    assert verified("sample.py:1-6", "def build(\u2026 arg7,")
    assert not verified("sample.py:1-6", "def build(\u2026 arg8,")
    # However many lines are cited, never across more than fifteen.
    assert verified("sample.py:1-21", "def build(\u2026 arg14,")
    assert not verified("sample.py:1-21", "def build(\u2026 arg15,")
    assert not verified("sample.py:1-21", "def build(\u2026) -> None:")
    # A six-line signature cited by its range, the review's case.
    (tmp_path / "sig.py").write_text(
        "def build(\n    first,\n    second,\n    third,\n    fourth,\n) -> None:\n    pass\n")
    assert verified("sig.py:1-6", "def build(\u2026) -> None:")


# The review's correct ellipsis quotes (PR 499, second review): a part after the first may
# be punctuation that closes what the first part opened.
_OPS = (
    "def check_artifact(ctx, result, attempts, evidence):\n"                                 # 1
    "    command = ctx.command\n"                                                            # 2
    "    outcome = {\"command\": command, \"status\": evidence.check_failure_kind(result, "
    "attempts)}\n"                                                                           # 3
    "    return outcome\n"                                                                   # 4
    "\n"                                                                                     # 5
    "def lower(node, spec, sys, shlex):\n"                                                   # 6
    "    node.dispatch_command = f'{sys.executable} -m refactory_dsh.worker_node ' + "
    "shlex.quote(str(spec))\n"                                                               # 7
    "\n"                                                                                     # 8
    "@pytest.mark.parametrize(\"a, b\", [\n"                                                 # 9
    "    (1, 2),\n"                                                                          # 10
    "])\n"                                                                                   # 11
    "def helper(x, y):\n"                                                                    # 12
    "    return Verdict(\"proceed\", x)\n"                                                   # 13
    "\n"                                                                                     # 14
    "def run(\n"                                                                             # 15
    "    self,\n"                                                                            # 16
    "    config,\n"                                                                          # 17
    "    *,\n"                                                                               # 18
    "    retries=3,\n"                                                                       # 19
    "    timeout=None,\n"                                                                    # 20
    ") -> int:\n"                                                                            # 21
    "    return 1\n"                                                                         # 22
)


@pytest.mark.parametrize("claim", [
    '`ops.py:3`: `outcome = {..., "status": evidence.check_failure_kind(result, ...)}`',
    "`ops.py:7`: `f'{sys.executable} -m refactory_dsh.worker_node ...'`",
    "`ops.py:9`: `@pytest.mark.parametrize(\u2026)`",
    "`ops.py:12`: `def helper(\u2026):`",
    "`ops.py:13`: `return Verdict(\u2026)`",
    '`ops.py:13`: `return Verdict("proceed", \u2026)`',
    "`ops.py:15-21`: `def run(\u2026) -> int:`",
], ids=["dict-elision", "f-string-tail", "decorator", "def-colon", "call", "call-with-argument",
        "seven-line-signature-by-its-range"])
def test_an_ellipsis_quote_may_end_in_punctuation(tmp_path, claim):
    (tmp_path / "ops.py").write_text(_OPS)
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", claim + "\n")
    assert check_design_claims(ctx)["claims_check"] == {"unverified": [], "notes": []}


def test_a_long_signature_cited_by_one_line_is_unverified(tmp_path):
    """Its parts lie seven lines apart; a claim citing one line gets three."""
    (tmp_path / "ops.py").write_text(_OPS)
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`ops.py:15`: `def run(\u2026) -> int:`\n")
    assert _unverified(ctx) == (
        "quote `def run(\u2026) -> int:` not within 20 lines of `ops.py:15`: line 15 is "
        "`def run(`; its parts do not all appear, in order, within 3 lines")


# Two ellipsis claims from design runs, against the real lines they cite: workers.py at
# run b23c's base and design_ops.py at run b32k's base, at their real line numbers.
_WORKERS_249 = [
    "        attempts = max(1, int(selected_context.get('attempts') or 1))",
    "        per_completion = int(selected_context.get('timeout_s') or DEFAULT_TIMEOUT_S)",
    "        command_timeout_s = node.timeout if node.timeout is not None else "
    "node_session_budget_s(attempts, per_completion)",
    "        node.dispatch_command = f'{sys.executable} -m refactory_dsh.worker_node ' + "
    "shlex.quote(str(spec))",
    "        node.command_timeout_s = command_timeout_s",
    "    return result",
]
_DESIGN_OPS_724 = [
    "    result = repo_change.run_check(ctx, command, check_timeout)",
    '    detail = "\\n".join((result.problem(), result.stdout or "", result.stderr or "")).strip()',
    '    outcome = {"command": command, "status": "pass" if result.ok else evidence.check_failure_kind(',
    '        result, budget=getattr(ctx, "budget", None)),',
    '        "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode,',
    '        "timed_out": result.timed_out, "elapsed_s": result.elapsed_s,',
    '        "check_mode": "full",',
    '        "targeted_command": prepass.targeted_command if prepass.ran else None}',
    "    apply_cwd_escaped(result, outcome)",
]


def test_the_ellipsis_claims_from_design_runs_against_their_real_lines(tmp_path):
    import subprocess

    source = tmp_path / "src"
    pkg = source / "refactory" / "refactory_dsh"
    pkg.mkdir(parents=True)
    (pkg / "workers.py").write_text("\n".join(["pass"] * 245 + _WORKERS_249 + ["pass"] * 30) + "\n")
    (pkg / "design_ops.py").write_text(
        "\n".join(["pass"] * 721 + _DESIGN_OPS_724 + ["pass"] * 30) + "\n")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    (tmp_path / "run").mkdir()
    ctx = nn.RunContext(tmp_path / "run")
    ctx.facts.update({"source": str(source)})

    def check(claim: str) -> dict:
        ctx.write_text("design.md", claim + "\n")
        return check_design_claims(ctx)["claims_check"]

    # Run b23c: the f-string's closing quote after the ellipsis.
    assert check("`workers.py:249`: `f'{sys.executable} -m refactory_dsh.worker_node ...'`") == {
        "unverified": [], "notes": []}
    # Run b32k quoted line 724 without the `"pass" if result.ok else` between "status" and
    # the call: a misquote, unverified for that reason and not for its closing `)}`.
    assert check('`refactory/refactory_dsh/design_ops.py:724`: '
                 '`outcome = {..., "status": evidence.check_failure_kind(result, ...)}`') == {
        "notes": [], "unverified": [
            'quote `outcome = {..., "status": evidence.check_failure_kind(result, ...)}` not '
            'within 20 lines of `refactory/refactory_dsh/design_ops.py:724`: line 724 is '
            '`outcome = {"command": command, "status": "pass" if result.ok else '
            'evidence.check_failure_kind(`; its parts do not all appear, in order, within 3 lines']}
    # The same statement quoted as it is, by its six lines, ends in its closing brace.
    assert check('`refactory/refactory_dsh/design_ops.py:724-729`: '
                 '`outcome = {"command": command, ... evidence.check_failure_kind( ... }`') == {
        "unverified": [], "notes": []}


def test_quote_beyond_the_window_names_at_most_three_lines(tmp_path):
    (tmp_path / "sample.py").write_text("x = 0\n" * 30 + "def gamma():\n" * 5)
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`sample.py:2`: `def gamma():`\n")
    assert _unverified(ctx) == (
        "quote `def gamma():` not within 20 lines of `sample.py:2`: line 2 is `x = 0`; "
        "found at line 31, 32, 33, more than 20 lines away")


def test_a_large_file_is_searched_only_near_the_citation(tmp_path):
    """A 1.2 MB file, where the review timed the whole-file search at 279 s."""
    import signal
    import time

    (tmp_path / "big.py").write_text("def f(x):\n    return x\n" * 52000)
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`big.py:100`: `return x`\n"
                                "`big.py:100`: `def f(\u2026) -> None:`\n"
                                "`big.py:100`: `def g(x):`\n")

    def too_slow(signum, frame):
        raise TimeoutError("the claims check took more than 10 s")

    previous = signal.signal(signal.SIGALRM, too_slow)
    signal.alarm(10)
    try:
        began = time.monotonic()
        result = check_design_claims(ctx)["claims_check"]
        elapsed = time.monotonic() - began
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert elapsed < 2, f"{elapsed:.1f} s"
    assert result == {"notes": [], "unverified": [
        "quote `def f(\u2026) -> None:` not within 20 lines of `big.py:100`: line 100 is "
        "`return x`; its parts do not all appear, in order, within 3 lines",
        "quote `def g(x):` not within 20 lines of `big.py:100`: line 100 is `return x`; "
        "not found anywhere in the file"]}


def test_a_path_leading_out_of_the_checkout_is_unverified(tmp_path):
    """A tracked symlink to a file outside the checkout, or a path through `..`, names no
    checkout file: the check never reads it, so no line of it reaches the card."""
    import os
    import subprocess

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("TOKEN = 'outside-value'\n")
    source = tmp_path / "src"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "__init__.py").write_text("")
    (source / "tool.py").write_text("x = 1\n")
    os.symlink(outside / "secret.py", source / "link.py")
    os.symlink(outside / "secret.py", source / "pkg" / "nested.py")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    (tmp_path / "run").mkdir()
    ctx = nn.RunContext(tmp_path / "run")
    ctx.facts.update({"source": str(source)})
    ctx.write_text("design.md", "`link.py:1`: `TOKEN = 'guess'`\n"
                                "`nested.py:1`: `TOKEN = 'guess'`\n"
                                "`pkg/../tool.py:1`: `x = 1`\n")
    assert check_design_claims(ctx)["claims_check"] == {"notes": [], "unverified": [
        "`link.py` resolves outside the checkout",
        "`nested.py` is not in the checkout",
        "`pkg/../tool.py` is not in the checkout"]}


def test_unverified_claims_never_touch_the_review_feedback(tmp_path):
    """The claims check is an op that returns its facts: it writes no feedback file and
    sets no other fact, so nothing it finds can send the design back."""
    (tmp_path / "sample.py").write_text("def existing():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    ctx.write_text("design.md", "`missing_fn()` at `sample.py:1`: `def existing():`\n")
    result = check_design_claims(ctx)
    assert ctx.facts == {"source": str(tmp_path)}
    assert set(result) == {"claims_check", "claims_check_feedback"}
    assert result["claims_check"]["unverified"] == [
        "`sample.py:1`: `missing_fn` not found in `sample.py`"]
    assert not (tmp_path / "reviews").exists()


def test_an_ellipsis_quote_matches_its_parts_in_order(tmp_path):
    (tmp_path / "sample.py").write_text(
        "def run(self, config, *, retries=3) -> int:\n    return 1\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(tmp_path)})
    for quote in ("def run(\u2026) -> int:", "def run(...) -> int:", "def run(self, ... retries=3)"):
        ctx.write_text("design.md", f"`run()` at `sample.py:1`: `{quote}`\n")
        assert check_design_claims(ctx)["claims_check"] == {"unverified": [], "notes": []}, quote
    # The parts must be in order.
    ctx.write_text("design.md", "`run()` at `sample.py:1`: `-> int: \u2026 def run(`\n")
    assert "not within 20 lines" in _unverified(ctx)


def test_a_bare_or_package_relative_path_names_its_one_tracked_file(tmp_path):
    import subprocess

    source = tmp_path / "src"
    (source / "pkg" / "sub").mkdir(parents=True)
    (source / "pkg" / "other").mkdir(parents=True)
    (source / "pkg" / "sub" / "engine.py").write_text("def run():\n    pass\n")
    (source / "pkg" / "sub" / "util.py").write_text("def a():\n    pass\n")
    (source / "pkg" / "other" / "util.py").write_text("def a():\n    pass\n")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({"source": str(source)})
    ctx.write_text("design.md", "`run()` at `engine.py:1`: `def run():`\n"
                                "`run()` at `sub/engine.py:1`: `def run():`\n")
    assert _unverified(ctx) == ""
    ctx.write_text("design.md", "`a()` at `util.py:1`: `def a():`\n"
                                "`b()` at `missing.py:1`: `def b():`\n")
    assert _unverified(ctx).splitlines() == [
        "`util.py` is ambiguous (pkg/other/util.py, pkg/sub/util.py); cite the full path",
        "`missing.py` is not in the checkout"]


def test_ts_identifier_word_boundary(tmp_path):
    """TypeScript identifiers are checked with word-boundary search."""
    (tmp_path / "handler.ts").write_text("export function handleRequest() { return 1; }\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
    })
    # Case 1: existing function.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `handler.ts:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- handler.ts\n\n"
        "`handleRequest()` at `handler.ts:1`: `export function handleRequest()`\n"
    ))
    assert _unverified(ctx) == ""

    # Case 2: nonexistent function.
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `handler.ts:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- handler.ts\n\n"
        "`nonExistentFn()` at `handler.ts:1`: `export function handleRequest()`\n"
    ))
    assert "nonExistentFn" in _unverified(ctx)


# ── dm-127: review status presentation ────────────────────────────────────


def test_passed_review_shows_pass_status(tmp_path):
    """A design that has every section the build needs shows 'Design review: PASS'."""
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "design_review_status": "",
    })
    ctx._declared_facts = (
        "design_review_feedback", "design_review_attempts_used",
        "design_review_findings", "pitfall_coverage", "design_review_status",
    )
    ctx._declared = ("design.md", "brief.json", "reviews/design-review-feedback.txt")
    ctx.write_text("design.md", _CONTRACT_DESIGN)
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})
    import refactory_dsh.design_ops as ops
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    assert ctx.facts["design_review_status"] == "Design review: PASS"
    assert "(none: nothing blocks the build)" in ctx.facts["design_review_findings"]


def test_failed_review_shows_fail_status(tmp_path):
    """A design missing ## Changes goes back once, then reaches the gate as FAIL."""
    ctx = nn.RunContext(tmp_path, knobs={"design_review_attempts": 1})
    ctx.facts.update({
        "source": str(tmp_path),
        "design_review_attempts_used": 0,
        "design_review_status": "",
    })
    ctx._declared_facts = (
        "design_review_feedback", "design_review_attempts_used",
        "design_review_findings", "pitfall_coverage", "design_review_status",
    )
    ctx._declared = ("design.md", "brief.json", "reviews/design-review-feedback.txt")
    ctx.write_text("design.md", _CONTRACT_DESIGN.replace("## Changes\n\n- file.py\n", ""))
    ctx.write_json("brief.json", {"name": "x", "brief": "do a thing"})

    import refactory_dsh.design_ops as ops
    # First visit: the one rewrite.
    verdict1 = ops.gate_design_review(ctx)
    assert verdict1 == "reloop"
    assert ctx.facts["design_review_findings"] == "(no findings yet)"
    assert ops.design_review_status(ctx.facts) == ""

    # Second visit: out of rewrites, the person decides.
    result = ops.gate_design_review(ctx)
    assert result == "proceed"
    status = ctx.facts["design_review_status"]
    assert status.startswith("Design review: FAIL, 1 unresolved\n- the design is missing the "
                             "section the build needs, or it is empty: ## Changes")
    # Render gate_strategy notes.
    from refactory_dsh.nn.executor import render
    from types import SimpleNamespace
    gate_notes = (
        "{{design_review_status}}\n\n"
        "Approve the design before the artifact is written? It is in design.md "
        "in the run directory.\n\n"
        "## Unresolved design review findings\n{{design_review_findings}}"
    )
    rendered = render(gate_notes, SimpleNamespace(knobs={}, facts=ctx.facts))
    assert rendered.startswith("Design review: FAIL")
    assert "## Changes" in ctx.facts["design_review_findings"]


def test_status_shows_review_status(tmp_path):
    """console.status shows review status when a user gate is due."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "facts.json").write_text(json.dumps({
        "design_review_findings": "- finding1\n- finding2",
        "design_review_attempts_used": 2,
        "design_review_status": "Design review: FAIL, 2 unresolved\n- finding1\n- finding2",
    }))
    refactory_dir = run_root / ".refactory"
    refactory_dir.mkdir()
    (refactory_dir / "spine-status.json").write_text(json.dumps({
        "status": "refused",
        "nodes": ["gate_strategy"],
        "completed": ["prepare", "design_doc"],
    }))
    graphs_dir = run_root / "graphs"
    graphs_dir.mkdir()
    (graphs_dir / "test.graph.json").write_text(json.dumps({
        "nodes": {
            "gate_strategy": {
                "_type": "GateNode",
                "evaluator_type": "user",
                "gate_prompt": "{{design_review_status}}\n\nApprove?",
            }
        },
        "edges": [
            {"source": "gate_strategy", "target": "emit", "condition": "proceed"},
            {"source": "gate_strategy", "target": "design_doc", "condition": "reloop"},
        ],
    }))
    from refactory_dsh import console, readable
    st = console.status(run_root)
    text = readable.format_status(st)
    assert "Design review: FAIL" in text


def test_walker_gate_stop_includes_review_status():
    """The design_review_status helper returns the expected FAIL block."""
    from refactory_dsh.design_ops import design_review_status
    result = design_review_status({
        "design_review_attempts_used": 2,
        "design_review_findings": "- finding1\n- finding2",
    })
    assert result.startswith("Design review: FAIL")
    assert "2 unresolved" in result


def test_design_review_status_empty_when_no_review():
    """When no review has run, the status is empty."""
    from refactory_dsh.design_ops import design_review_status
    assert design_review_status({}) == ""
    assert design_review_status({"design_review_attempts_used": 0}) == ""


def test_fenced_code_blocks_not_checked(tmp_path):
    """Claims inside fenced code blocks are not checked."""
    (tmp_path / "sample.py").write_text("def real():\n    pass\n")
    ctx = nn.RunContext(tmp_path)
    ctx.facts.update({
        "source": str(tmp_path),
    })
    ctx.write_text("design.md", (
        "# Design\n\n"
        "## Summary\n\nFix it.\n\n"
        "## Rule and sites\n\n- `sample.py:1` — **[changed]**\n\n"
        "## Docs\n\n- docs\n\n## Changes\n\n- sample.py\n\n"
        "```python\n"
        "`nonexistent()` at `sample.py:1`: `def real():`\n"
        "```\n"
    ))
    assert _unverified(ctx) == ""


# ── integration tests through the graph ───────────────────────────────────


def test_walker_gate_stop_prints_design_review_status(tmp_path, monkeypatch, capsys):
    """design.main prints 'Design review: FAIL, 2 unresolved' to stderr when
    the run stops at gate_strategy after two failing reviews.

    Removing the ``node.kind == "gate" and node.gate_kind == "user"`` branch
    in main() must make this test fail.
    """
    from refactory_dsh import design
    from refactory_dsh.nn import RunResult

    # Simulate a run that stopped at gate_strategy with two failing reviews.
    canned = RunResult(
        exit_ok=False,
        error=(
            "gate_strategy: ExecutionError: gate 'gate_strategy' is answered "
            "by 'user' and this run has no verdict for it"
        ),
        facts={
            "design_review_attempts_used": 2,
            "design_review_findings": (
                "- BLOCKING: path/file.py: finding_alpha\n"
                "- BLOCKING: path/file.py: finding_beta"
            ),
            "design_review_status": "",
        },
    )
    # Stub the model client constructor (it is evaluated before mode.run)
    # and mode.run itself so the test exercises only main()'s stderr path.
    import refactory_dsh.model as model_mod
    monkeypatch.setattr(model_mod, "DshModelClient", lambda **kw: None)
    monkeypatch.setattr(design.DesignMode, "run", lambda self, *a, **kw: canned)
    monkeypatch.chdir(tmp_path)

    rc = design.main([
        "--name", "my_tool", "--brief", "a tool",
        "--check", "true",
        "--workdir", str(tmp_path / "run"),
    ])

    captured = capsys.readouterr()
    assert rc == 2
    assert "Design review: FAIL" in captured.err
    assert "2 unresolved" in captured.err
    assert "finding_alpha" in captured.err
    assert "finding_beta" in captured.err


def test_unverified_claims_reach_the_card_through_graph(tmp_path):
    """A design with a claim about a file that does not exist is written once, and the
    claim reaches the approval gate's card.

    Removing ``"claims_check"`` from check_design_claims' produces must make this test
    fail (the executor raises ExecutionError on an undeclared fact write).
    """
    from refactory_dsh.design import DesignMode
    from tests_support import (
        FakeModel,
        PITFALLS_ANSWER,
        RESEARCH_ANSWER,
        REVIEW_ANSWERS,
        VERIFICATION_ANSWER,
        render,
    )

    # A contract-compliant design with a claim about a nonexistent file.
    # The bare citation ``tool.py:1`` is NOT in subject-at-citation-quote
    # form, so it is not checked.  The final line IS a claim and cannot be confirmed.
    _BAD_DESIGN = (
        "# Do the thing\n\n"
        "## Summary\n\nFix it properly.\n\n"
        "## Failure trace\n\nN/A \u2014 this is a new feature.\n\n"
        "## Ask mapping\n\n"
        "| Ask (quoted from brief) | Design section | Test |\n"
        "|---|---|---|\n"
        '| "a tool" | \u00a7 Changes | test_tool.py |\n\n'
        "## Rule and sites\n\n"
        "- `tool.py:1` \u2014 **[changed]**\n\n"
        "## Docs\n\n"
        "- docs/models.md\n\n"
        "## Changes\n\n"
        "- tool.py\n\n"
        "## Anti-patterns to avoid\n\n"
        "- PIT-001: addressed at `tool.py:1`\n\n"
        "`nonexistent_fn()` at `zzz_no_such_file_dm126.py:1`: `import os`\n"
    )

    answers = [
        RESEARCH_ANSWER,                               # research_director
        PITFALLS_ANSWER,                               # pitfalls_researcher
        "perspectives",                                # strategy_director
        "one plan",                                    # synthesize_strategy
        _BAD_DESIGN,                                   # design_doc (once)
        render({"tool.py": "print('built')\n"}),       # emit
        *REVIEW_ANSWERS[:3],                           # health, code, qa
        VERIFICATION_ANSWER,                           # verifier
        REVIEW_ANSWERS[3],                             # overwatch
    ]
    model = FakeModel(answers)
    result = DesignMode().run(
        tmp_path / "run",
        model=model,
        verdicts={"gate_strategy": "proceed"},
        task={"name": "my_tool", "brief": "a tool", "check": "python tool.py"},
    )

    assert "check_design_claims" not in str(result.error), result.error
    assert len([p for p in model.prompts
                if "Write the design document for this artifact" in p]) == 1
    assert "- unverified claim: `zzz_no_such_file_dm126.py`" in result.facts["design_review_status"]
