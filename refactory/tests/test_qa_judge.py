"""Design mode's judges: the reviews report, and the QA judge and the overwatch decide.

Each judge writes a JSON verdict (`refactory_dsh.judges`) and its gate reads that file
with ``json.loads``; nothing in design mode reads a decision out of a review's prose.
The check is decided first and stays binding. The cases are the runs that failed on the
parser and the audit: b37b and measure4-dm149, whose reviewers quoted the blocking
marker's format and were sent back for it, and b39b, whose correction audit refused a
change the overwatch had passed. Everything here runs through the real design graph on
the walker, with a scripted model, except the last tests, which run a gate the way the
spine does: as its own process, through ``refactory_dsh.run``.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from refactory_dsh import authoring_evidence as evidence
from refactory_dsh import create_ops, delivery, design_ops, judges, lowering, nn
from refactory_dsh.create import CreateMode
from refactory_dsh.design import DesignMode
from refactory_dsh.edit import EditMode
from refactory_dsh.run import FAILURE_MARKER, main as runner_main
from refactory_dsh.steering import Ledger
from tests_support import (DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER, OVERWATCH_JUDGE_MARK,
                           PITFALLS_ANSWER, QA_JUDGE_MARK, RESEARCH_ANSWER, VERIFICATION_ANSWER,
                           FakeModel, Scripted, git, git_repo, overwatch_judgment, qa_judgment,
                           render)

NAME = "judged_tool"
EMIT_MARK = "Write the artifact `"

#: Passing reviews, in the order the review fork asks for them, then the verifier's.
HEALTH, CODE, QA = "health: ran it, it prints built", "code review: matches the design", "qa: ok"


def _round(emission: str, *, reviews: tuple[str, str, str] = (HEALTH, CODE, QA),
           verifier: str = VERIFICATION_ANSWER, judge: str | None = None,
           overwatch: str | None = None) -> list[str]:
    """One attempt's answers: the emission, the three reviews and the verifier beside the
    check, then the judges (each left to its default when not given)."""
    return [emission, *reviews, verifier, *([judge] if judge else []),
            *([overwatch] if overwatch else [])]


def _answers(*rounds: list[str]) -> list[str]:
    head = [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
            DESIGN_DOC_ANSWER, DESIGN_REVIEW_ANSWER]
    return head + [answer for one in rounds for answer in one]


def _design(run: Path, model: FakeModel, repo: Path, *, check: str = "python tool.py",
            **knobs: int) -> nn.RunResult:
    mode = DesignMode()
    state = mode.state_dict()
    for key, value in knobs.items():
        state["knobs"][key]["value"] = value
    mode.load_state_dict(state)
    return mode.run(run, model=model, verdicts={"gate_strategy": "proceed"},
                    task={"name": NAME, "brief": "a tool that prints built", "check": check,
                          "target": str(repo)})


def _prompts(model: FakeModel, mark: str) -> list[int]:
    """The positions, in the order the model was asked, of the prompts carrying ``mark``."""
    return [index for index, prompt in enumerate(model.prompts) if mark in prompt]


def _tool(word: str, code: int = 0) -> str:
    return render({"tool.py": f"print({word!r})\nraise SystemExit({code})\n"})


# ── b37b and measure4-dm149: prose that quotes the marker is not a finding ────
#: Lines from b37b's own reviews, which the parser read as blocking findings (a heading
#: naming the format, and a sentence that wraps onto a line starting with the marker),
#: and a tally line of the kind measure4-dm149's reviewers wrote.
QUOTING_HEALTH = (
    "# Health check\n\n"
    "## 11. BLOCKING: <path>: format preserved in all prompts\n\n"
    "**Check:** every reviewer prompt still contains the format.\n\n**Result:** PASS\n")
QUOTING_CODE = (
    "# Code review\n\n"
    "Ran the parser over each marker form:\n"
    "- `BLOCKING:` → 1 finding ✓\n"
    "- `**BLOCKING:**` → 1 finding ✓\n\n"
    "Nothing in the change is defective.\n")
QUOTING_QA = (
    "# QA\n\n**Adversarial check:** scanned every node for any node that mentions\n"
    "`BLOCKING:` but does NOT contain `MARKER_RULE`. Found zero such nodes.\n\n"
    "**Result:** PASS.\n")
QUOTING_VERIFIER = "BLOCKING: tool.py:1: prints a word the verifier did not expect\n"


def test_prose_quoting_the_marker_does_not_send_the_change_back(tmp_path):
    """b37b: reviews that quote the marker's format, and a verifier with a marked line,
    with a QA judge that proceeds: one emission, no reloop, the judge's dismissals on
    record. The parser the QA gate used before would have sent the same reviews back."""
    repo = git_repo(tmp_path / "repo")
    judge = qa_judgment("proceed", dismissed=[
        {"source": "health_checker", "finding": "## 11. BLOCKING: <path>: ...",
         "why": "a heading naming the format, not a finding"}])
    model = FakeModel(_answers(_round(_tool("built"),
                                      reviews=(QUOTING_HEALTH, QUOTING_CODE, QUOTING_QA),
                                      verifier=QUOTING_VERIFIER, judge=judge,
                                      overwatch=overwatch_judgment("pass"))))
    run = tmp_path / "run"
    result = _design(run, model, repo)

    assert result.exit_ok, result.error
    assert len(_prompts(model, EMIT_MARK)) == 1
    assert not result.facts.get("review_attempts_used")
    gate = (run / "reviews/qa-gate.txt").read_text()
    assert gate.startswith("proceed: the QA judge found nothing that must be fixed"), gate
    assert "dismissed [health_checker] ## 11. BLOCKING: <path>: ..." in gate
    # The verifier went to the judge whole and was not parsed for findings.
    assert QUOTING_VERIFIER.strip() in (run / "reviews/qa-synthesized.md").read_text()
    assert not (run / "reviews/verification-unresolved.md").exists()
    # The same text through the parser the QA gate used: core findings, so a reloop.
    for text in (QUOTING_HEALTH, QUOTING_CODE, QUOTING_QA):
        core, _ = create_ops.split_core(create_ops.blocking_findings(text))
        assert core, text


def test_a_judge_redo_sends_its_must_fix_to_the_builder_once(tmp_path):
    """The QA judge's redo goes back to emit with its must-fix item as the feedback, and
    spends one review attempt; the next attempt proceeds."""
    repo = git_repo(tmp_path / "repo")
    must_fix = {"source": "code_reviewer", "where": "tool.py:1", "what": "prints the wrong word",
                "why": "the brief asks for built"}
    model = FakeModel(_answers(
        _round(_tool("bilt"), judge=qa_judgment("redo", must_fix=[must_fix],
                                                follow_ups=["add a --quiet flag"])),
        _round(_tool("built"), overwatch=overwatch_judgment("pass"))))
    run = tmp_path / "run"
    result = _design(run, model, repo)

    assert result.exit_ok, result.error
    emits = _prompts(model, EMIT_MARK)
    assert len(emits) == 2, "exactly one reloop"
    assert len(_prompts(model, QA_JUDGE_MARK)) == 2
    second = model.prompts[emits[1]]
    assert "[code_reviewer] tool.py:1: prints the wrong word (why: the brief asks for built)" in second
    assert "add a --quiet flag" in second, "follow-ups ride along, to fix only if cheap"
    assert result.facts["review_attempts_used"] == 1
    assert (run / "reviews/qa-gate.txt").read_text().startswith("proceed:")


def test_a_failing_check_goes_back_before_any_judge(tmp_path):
    """The check stays binding: an attempt whose check fails goes back to emit from
    gate_check, and no judge is asked about it. The judge sees only the passing attempt."""
    repo = git_repo(tmp_path / "repo")
    # The first attempt's reviews are answered by default beside its failing check: the
    # script's next answer is the second emission.
    model = FakeModel(_answers([_tool("built", 1)], _round(_tool("built"))))
    run = tmp_path / "run"
    result = _design(run, model, repo)

    assert result.exit_ok, result.error
    emits = _prompts(model, EMIT_MARK)
    judged = _prompts(model, QA_JUDGE_MARK)
    assert len(emits) == 2
    assert len(judged) == 1 and judged[0] > emits[1], "the judge ran only on the passing attempt"
    assert len(_prompts(model, OVERWATCH_JUDGE_MARK)) == 1
    assert result.facts.get("check_attempts_used") == 1
    assert not result.facts.get("review_attempts_used")


# ── b39b: the correction audit is the overwatch's evidence, not a verdict ─────
CALC = "def validate(x):\n    return x > 0\n"
CALC_TEST = "from calc import validate\n\n\ndef test_unrelated():\n    assert validate(5) is True\n"
CORRECTION = "make zero valid, and leave app.py alone"
#: Sites the evidence map names for the correction: app.py, which the change leaves as
#: the base has it (a correction that removes a site leaves that), and the comparison in
#: calc.py, whose cited test passes with the change reverted.
SITES = [{"site": "app.py:1", "tests": ["test_calc.py::test_unrelated"], "scope": "local"},
         {"site": "calc.py:2", "tests": ["test_calc.py::test_unrelated"], "scope": "local"}]


def test_b39b_removed_and_untested_sites_with_a_passing_overwatch_reach_the_report(tmp_path):
    """b39b: the audit reports a removed site and a site no cited test reaches; the
    overwatch reads that, passes, and the run reaches its report and delivers. The audit
    the overwatch gate ran before would have refused the same evidence."""
    repo = git_repo(tmp_path / "repo")
    (repo / "calc.py").write_text(CALC)
    (repo / "test_calc.py").write_text(CALC_TEST)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "calc")
    run = tmp_path / "run"
    run.mkdir()
    Ledger.seed(run, "a calc whose validate accepts zero").note("correction", CORRECTION)
    [key] = [f"ledger-{index + 1}" for index, entry in enumerate(evidence.intent(run))
             if entry["kind"] == "correction"]
    verdict = overwatch_judgment("pass", corrections=[
        {"id": key, "met": True, "evidence": "calc.py:2 returns x >= 0; app.py is unchanged"}])
    emission = render({"calc.py": "def validate(x):\n    return x >= 0\n"})
    model = FakeModel(_answers(_round(emission, overwatch=verdict)))
    model._correction_sites = {CORRECTION: SITES}
    result = _design(run, model, repo,
                     check="python -c 'from calc import validate; assert validate(0)'")

    assert result.exit_ok, result.error
    [item] = json.loads((run / "correction-audit.json").read_text())
    sites = {site["site"]: site for clause in item["clauses"] for site in clause["sites"]}
    assert sites["app.py:1"]["status"] == "removed" and sites["app.py:1"]["removed"] == "unchanged"
    assert sites["calc.py:2"]["status"] == "fail"
    assert [t["revert_result"] for t in sites["calc.py:2"]["tests"]] == ["pass"]
    # The overwatch read the audit, both findings in it, before judging.
    [asked] = [model.prompts[i] for i in _prompts(model, OVERWATCH_JUDGE_MARK)]
    assert "app.py:1: removed (unchanged)" in asked
    assert "site calc.py:2 not reached by any test" in asked
    # It passed, the report ran, and nothing was sent back.
    assert (run / "design.json").exists()
    assert (run / "reviews/overwatch-gate.txt").read_text().startswith("proceed: overwatch passed")
    assert not result.facts.get("review_attempts_used")
    follow_ups = (run / "reviews/follow-ups.md").read_text()
    assert "did not judge" not in follow_ups
    # The pull request lists the audit's by-hand check, and counts the overwatch's pass
    # as the audit's, though the audit has a clause it measured as not met.
    body = (run / delivery.BODY).read_text()
    assert "site app.py:1 is removed from the change" in body
    assert "and the overwatch audit passed" in body, body
    # The audit as the overwatch gate ran it refuses the same evidence.
    ctx = nn.RunContext(run, facts=result.facts, enforce_writes=False)
    old = evidence.correction_audit_problems(ctx)
    assert any("site app.py:1 is not in the diff" in problem for problem in old), old
    assert any("site calc.py:2 not reached by any test" in problem for problem in old), old


def test_an_overwatch_redo_counts_toward_the_review_limit_and_stops_at_it(tmp_path):
    """An overwatch redo goes back to emit with its must-fix item and spends a review
    attempt; at the review limit the next one is a BuildLoopStop."""
    repo = git_repo(tmp_path / "repo")
    redo = overwatch_judgment("redo", must_fix=[
        {"source": "overwatch", "where": "tool.py:1", "what": "no test shows built is printed",
         "why": "the brief's only ask has no evidence"}])
    model = FakeModel(_answers(_round(_tool("built"), overwatch=redo),
                               _round(_tool("built!"), overwatch=redo)))
    result = _design(tmp_path / "run", model, repo, review_attempts=2)

    assert result.exit_ok is False
    assert "BuildLoopStop" in (result.error or ""), result.error
    assert "review reloop limit reached after 2 review cycle(s)" in result.error
    emits = _prompts(model, EMIT_MARK)
    assert len(emits) == 2
    assert "tool.py:1: no test shows built is printed" in model.prompts[emits[1]]


def test_an_unreadable_judge_file_proceeds_with_a_note(tmp_path):
    """A QA judge whose file is not a verdict: the gate proceeds, says why in its status
    and in the follow-ups, and the overwatch still judges."""
    repo = git_repo(tmp_path / "repo")
    model = FakeModel(_answers(_round(_tool("built"), judge=Scripted("Looks fine to me."),
                                      overwatch=overwatch_judgment("pass"))))
    run = tmp_path / "run"
    result = _design(run, model, repo)

    assert result.exit_ok, result.error
    assert (run / judges.QA_JUDGE).read_text() == "Looks fine to me."
    gate = (run / "reviews/qa-gate.txt").read_text()
    assert gate.startswith("proceed: the QA judge's verdict could not be read "
                           "(qa-judge.json is not JSON"), gate
    assert "the change went on to the overwatch without it" in (run / "reviews/follow-ups.md").read_text()
    assert len(_prompts(model, OVERWATCH_JUDGE_MARK)) == 1
    assert not result.facts.get("review_attempts_used")


# ── the other modes keep the parser gates ─────────────────────────────────────
@pytest.mark.parametrize("mode", [CreateMode, EditMode])
def test_create_and_edit_keep_their_gates(mode):
    """create and edit are unchanged: the parser gates, the overwatch writing the evidence
    map, its free audit round, and no judge (their lowering snapshots hold the rest)."""
    graph = mode().build()
    assert graph.nodes["gate_qa"].evaluator == "refactory_dsh.create_ops:gate_qa"
    assert graph.nodes["gate_overwatch"].evaluator == "refactory_dsh.create_ops:gate_overwatch"
    assert "qa_judge" not in graph.nodes and "audit_corrections" not in graph.nodes
    assert [(e.target, e.condition) for e in graph.out_edges("gate_overwatch")
            if e.condition == "audit"] == [("overwatch", "audit")]
    overwatch = graph.nodes["overwatch"]
    assert "Write intent-audit.json as" in overwatch.prompt_template
    assert evidence.AUDIT in overwatch.writes
    assert "correction-audit.json" in graph.nodes["gate_overwatch"].writes


def test_the_design_graph_has_judges_and_no_free_audit_round():
    graph = DesignMode().build()
    assert graph.nodes["gate_qa"].evaluator == "refactory_dsh.design_ops:gate_qa"
    assert graph.nodes["gate_overwatch"].evaluator == "refactory_dsh.design_ops:gate_overwatch"
    assert [e.target for e in graph.out_edges("synthesize_qa")] == ["qa_judge"]
    assert {(e.target, e.condition) for e in graph.out_edges("gate_qa")} == {
        ("emit", "reloop"), ("audit_corrections", "proceed")}
    assert [e.target for e in graph.out_edges("audit_corrections")] == ["overwatch"]
    assert {(e.target, e.condition) for e in graph.out_edges("gate_overwatch")} == {
        ("emit", "reloop"), ("report", "proceed")}
    # The QA judge writes the evidence map the audit measures; the overwatch reads it.
    assert evidence.AUDIT in graph.nodes["qa_judge"].writes
    assert "Write intent-audit.json as" in graph.nodes["qa_judge"].prompt_template
    assert evidence.AUDIT not in graph.nodes["overwatch"].writes
    assert "Write intent-audit.json as" not in graph.nodes["overwatch"].prompt_template
    # On the spine an agent's declared inputs must exist: no judge declares the optional
    # verifier's report, and the overwatch does not declare what a skipped QA judge leaves.
    for node in ("qa_judge", "overwatch"):
        assert "reviews/verification.md" not in graph.nodes[node].reads
    assert judges.QA_JUDGE not in graph.nodes["overwatch"].reads
    assert evidence.AUDIT not in graph.nodes["overwatch"].reads
    assert "correction-audit.json" in graph.nodes["overwatch"].reads


# ── the judges' files ──────────────────────────────────────────────────────────
def test_a_judgment_is_json_and_a_redo_names_what_to_fix():
    fenced = "```json\n" + qa_judgment("proceed", follow_ups=["docs"]) + "\n```"
    assert judges.parse(fenced, judges.QA_VERDICTS).follow_ups == ("docs",)
    for text, why in (("VERDICT: PASS", "is not JSON"),
                      ("Here it is: " + qa_judgment(), "is not JSON"),
                      (json.dumps({"verdict": "maybe"}), "has verdict 'maybe'"),
                      (qa_judgment("redo"), "says redo but names nothing to fix"),
                      (json.dumps({"verdict": "proceed", "must_fix": "x"}), "must_fix is not a list")):
        with pytest.raises(judges.Unreadable, match=why):
            judges.parse(text, judges.QA_VERDICTS)
    unmet = overwatch_judgment("redo", corrections=[{"id": "ledger-3", "met": False, "evidence": ""}])
    assert judges.parse(unmet, judges.OVERWATCH_VERDICTS).unmet == [
        {"id": "ledger-3", "met": False, "evidence": ""}]


def test_the_synthesis_carries_every_review_unread_and_the_verifier_only_when_it_reported(tmp_path):
    ctx = nn.RunContext(tmp_path, facts={"check": "python tool.py", "validation": "(the check passed)"})
    for name, _ in design_ops.DESIGN_QA_REVIEWS:
        ctx.write_text(name, f"{name}: BLOCKING: tool.py: quoted\n")
    ctx.write_text("design.md", "# T\n\n## Changes\n\n- tool.py\n\n## Docs\n\n- none\n")
    body = design_ops.synthesize_qa(ctx)["qa"]
    for name, _ in design_ops.DESIGN_QA_REVIEWS:
        assert f"{name}: BLOCKING: tool.py: quoted" in body
    assert "verification" not in body, "a verifier that left nothing is simply absent"
    assert "## The design's Changes\n\n- tool.py\n" in body
    ctx.write_text(design_ops.VERIFICATION, "the verifier's report\n")
    assert "the verifier's report" in design_ops.synthesize_qa(ctx)["qa"]


def _gate_ctx(tmp_path: Path, judgment: str) -> nn.RunContext:
    """A run directory with this attempt's three reviews and the QA judge's file."""
    ctx = nn.RunContext(tmp_path)
    for name, _ in design_ops.DESIGN_QA_REVIEWS:
        ctx.write_text(name, "ran it; fine\n")
    ctx.write_text(judges.QA_JUDGE, judgment)
    return ctx


def test_a_judge_that_needs_an_unavailable_tool_stops_the_run(tmp_path):
    """A judge says a dependency is missing in a must-fix item, and the run stops for a
    person instead of sending the builder a defect it cannot fix."""
    ctx = _gate_ctx(tmp_path, qa_judgment("redo", must_fix=[
        {"what": "UNAVAILABLE: no browser to render the page; install chromium"}]))
    with pytest.raises(ValueError, match="validation unavailable: UNAVAILABLE: no browser"):
        design_ops.gate_qa(ctx)
    assert not ctx.facts.get("review_attempts_used")


def test_a_review_that_left_nothing_sends_the_change_back_whatever_the_judge_says(tmp_path):
    """An absent review is not a passed one: that stays structural, as it was."""
    ctx = _gate_ctx(tmp_path, qa_judgment("proceed"))
    ctx.write_text("reviews/qa.md", "")
    verdict = design_ops.gate_qa(ctx)
    assert verdict == "reloop" and "a review did not run: reviews/qa.md" in verdict.reason
    assert ctx.facts["review_attempts_used"] == 1


def test_an_unreadable_overwatch_verdict_proceeds_with_a_note_that_quotes_it(tmp_path):
    """Sending the builder back cannot repair a judge's file, so the change goes on, and
    the pull request says acceptance was not judged and what the overwatch wrote."""
    ctx = nn.RunContext(tmp_path)
    ctx.write_text(judges.OVERWATCH_VERDICT, "VERDICT: FAIL\nthe ask has no evidence")
    assert design_ops.gate_overwatch(ctx) == "proceed"
    notes = (tmp_path / "reviews/follow-ups.md").read_text()
    assert "acceptance was not judged" in notes
    assert "It wrote: VERDICT: FAIL the ask has no evidence" in notes
    assert not ctx.facts.get("review_attempts_used")


def test_the_audit_writes_its_file_every_round_even_with_nothing_to_measure(tmp_path):
    """The overwatch declares correction-audit.json as an input, and on the spine a
    declared input must exist: a round with nothing to audit still writes this round's."""
    ctx = nn.RunContext(tmp_path)
    ctx.write_json("correction-audit.json", [{"id": "ledger-9", "clauses": []}])
    produced = design_ops.audit_corrections(ctx)
    assert json.loads((tmp_path / "correction-audit.json").read_text()) == []
    assert produced["correction_audit"].startswith("No correction or constraint")


def test_a_pass_that_leaves_a_correction_unmet_lists_it_for_the_pull_request(tmp_path):
    ctx = nn.RunContext(tmp_path)
    ctx.write_text(judges.OVERWATCH_VERDICT, overwatch_judgment("pass", corrections=[
        {"id": "ledger-2", "met": False, "evidence": "calc.py:2"}]))
    assert design_ops.gate_overwatch(ctx) == "proceed"
    assert ("correction ledger-2: NOT met — calc.py:2 (the overwatch passed the change with it)"
            in (tmp_path / "reviews/follow-ups.md").read_text())


# ── the gates on the spine: each its own process, through refactory_dsh.run ───
def _spine_run(tmp_path: Path, judgment: str, **knobs: int) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    mode = DesignMode()
    state = mode.state_dict()
    for key, value in knobs.items():
        state["knobs"][key]["value"] = value
    mode.load_state_dict(state)
    lowering.write_declarations(mode.state_dict(), run)
    (run / "facts.json").write_text(json.dumps({"name": NAME, "brief": "a tool", "check": "true"}))
    for name, _ in design_ops.DESIGN_QA_REVIEWS:
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        (run / name).write_text("BLOCKING: tool.py: quoted in prose\n")
    (run / judges.QA_JUDGE).write_text(judgment)
    return run


def _on_the_spine(run: Path, monkeypatch, node: str, kind: str,
                  knobs: dict[str, int]) -> tuple[int, str, str]:
    """Run one design_ops node as the spine does: its own command, its declaration in
    DSH_WORKFLOW_NODE, its facts read from and written back to facts.json."""
    monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": node, "type": kind}))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps(knobs))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    code = runner_main([f"{design_ops.__name__}:{node}", "--node", node])
    monkeypatch.undo()
    return code, out.getvalue(), err.getvalue()


def _gate_qa_on_the_spine(run: Path, monkeypatch, knobs: dict[str, int]) -> tuple[int, str, str]:
    return _on_the_spine(run, monkeypatch, "gate_qa", "gate", knobs)


def test_gate_qa_on_the_spine_routes_on_the_judge_file(tmp_path, monkeypatch):
    """The spine runs gate_qa as a process reading qa-judge.json: the verdict is the last
    line it prints, the must-fix item the reason before it, and the facts it set are
    written back for the next node."""
    knobs = {"review_attempts": 3, "check_attempts": 3}
    must_fix = {"source": "qa_director", "where": "tool.py:2", "what": "crashes on empty input",
                "why": "the brief asks for any input"}
    run = _spine_run(tmp_path, qa_judgment("redo", must_fix=[must_fix]))
    code, out, err = _gate_qa_on_the_spine(run, monkeypatch, knobs)
    assert code == 0, err
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines[-1] == "reloop"
    assert "tool.py:2: crashes on empty input" in out
    facts = json.loads((run / "facts.json").read_text())
    assert facts["review_attempts_used"] == 1
    assert "crashes on empty input" in facts["qa_gate"]

    (run / judges.QA_JUDGE).write_text(qa_judgment("proceed", follow_ups=["a docs line"]))
    code, out, err = _gate_qa_on_the_spine(run, monkeypatch, knobs)
    assert code == 0, err
    assert out.split()[-1] == "proceed"
    assert "a docs line (qa)" in (run / "reviews/follow-ups.md").read_text()


def test_gate_qa_on_the_spine_stops_at_the_review_limit(tmp_path, monkeypatch):
    run = _spine_run(tmp_path, qa_judgment("redo", must_fix=[{"what": "still broken"}]))
    code, _, err = _gate_qa_on_the_spine(run, monkeypatch, {"review_attempts": 1, "check_attempts": 3})
    assert code == 3, err
    assert FAILURE_MARKER in err and "review reloop limit reached after 1" in err


def test_gate_qa_on_the_spine_proceeds_without_a_judge_file(tmp_path, monkeypatch):
    """A judge that left no file does not stall the gate: it proceeds with the note."""
    run = _spine_run(tmp_path, "")
    (run / judges.QA_JUDGE).unlink()
    code, out, err = _gate_qa_on_the_spine(run, monkeypatch, {"review_attempts": 3})
    assert code == 0, err
    assert out.split()[-1] == "proceed"
    assert "qa-judge.json was not written" in (run / "reviews/qa-gate.txt").read_text()


def test_the_qa_tail_runs_node_by_node_on_the_spine(tmp_path, monkeypatch):
    """synthesize_qa, gate_qa, audit_corrections and gate_overwatch, each its own process
    with the lowered graph's declarations: every fact and file they set is declared, and
    gate_overwatch routes on overwatch-verdict.json."""
    knobs = {"review_attempts": 3, "check_attempts": 3}
    run = _spine_run(tmp_path, qa_judgment("proceed"))
    (run / "design.md").write_text("# T\n\n## Changes\n\n- tool.py\n")
    (run / "check-record.json").write_text(json.dumps({"status": "pass"}))
    for node, kind in (("synthesize_qa", "op"), ("gate_qa", "gate"), ("audit_corrections", "op")):
        code, out, err = _on_the_spine(run, monkeypatch, node, kind, knobs)
        assert code == 0, (node, err)
    assert "## The design's Changes\n\n- tool.py" in (run / "reviews/qa-synthesized.md").read_text()
    assert json.loads((run / "correction-audit.json").read_text()) == []
    facts = json.loads((run / "facts.json").read_text())
    assert facts["qa_missing"] == 0 and facts["correction_audit"].startswith("No correction")

    (run / judges.OVERWATCH_VERDICT).write_text(overwatch_judgment("redo", must_fix=[
        {"source": "overwatch", "where": "tool.py:1", "what": "no evidence for the ask"}]))
    code, out, err = _on_the_spine(run, monkeypatch, "gate_overwatch", "gate", knobs)
    assert code == 0, err
    assert out.split()[-1] == "reloop" and "no evidence for the ask" in out
    assert json.loads((run / "facts.json").read_text())["review_attempts_used"] == 1

    (run / judges.OVERWATCH_VERDICT).write_text(overwatch_judgment("pass"))
    code, out, err = _on_the_spine(run, monkeypatch, "gate_overwatch", "gate", knobs)
    assert code == 0, err
    assert out.split()[-1] == "proceed"
    assert (run / "reviews/overwatch-gate.txt").read_text().startswith("proceed: overwatch passed")
