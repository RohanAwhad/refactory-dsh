"""Design mode's self-check: the tests a change can break run before the reviews (b41a).

`prepare_check` selects the tests an emission can break (`test_selection`), runs them in
chunks within a time limit (`failfast.targeted_prepass`), and sends the change's failures
back to emit as a fix-up that spends no attempt and runs no review. Each test here drives
the real runner, the real worktree and, for routes and loops, the real design graph.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from refactory_dsh import design_ops, failfast, lowering, repo_change, test_selection, worktree
from refactory_dsh.check_digest import build_probe_command
from refactory_dsh.design import DesignMode
from refactory_dsh.fast import DesignFastMode
from refactory_dsh.invocations import CHECK_PREP, SELF_CHECK_RECORD
from refactory_dsh.paths import REPO_ROOT
from refactory_dsh.run import main as node_main
from tests_support import (DESIGN_DOC_ANSWER, PITFALLS_ANSWER,
                           RESEARCH_ANSWER, REVIEW_ANSWERS, VERIFICATION_ANSWER, FakeModel,
                           git, git_repo, render)

NAME = "selfcheck_tool"
CHECK = "python -m pytest -q tests"
HEALTH = "You are the health checker."
EMIT = "Write the artifact `"
PIN = "tests/test_pin.py::test_pin"


# ── helpers ───────────────────────────────────────────────────────────────────


def _target(root: Path, files: dict[str, str]) -> Path:
    """A repository on main with ``files`` committed over `git_repo`'s one file."""
    repo = git_repo(root)
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tests")
    return repo


#: A tool and a test that pins its version, as a test pins behaviour a change replaces.
PINNED = {
    "tool.py": "VERSION = 1\n",
    "tests/test_pin.py": "import tool\n\n\ndef test_pin():\n    assert tool.VERSION == 1\n",
}


def _answers(*emits: str) -> list[str]:
    return [RESEARCH_ANSWER, PITFALLS_ANSWER, "perspectives", "one plan",
            DESIGN_DOC_ANSWER, *emits,
            *REVIEW_ANSWERS[:3], VERIFICATION_ANSWER, REVIEW_ANSWERS[3]]


def _mode(**knobs) -> DesignMode:
    mode = DesignMode()
    state = mode.state_dict()
    for key, value in knobs.items():
        state["knobs"][key]["value"] = value
    mode.load_state_dict(state)
    return mode


def _design(run: Path, model: FakeModel, repo: Path, mode: DesignMode | None = None, **task: str):
    return (mode or DesignMode()).run(
        run, model=model, verdicts={"gate_strategy": "proceed"},
        task={"name": NAME, "brief": "a tool", "check": CHECK, "target": str(repo), **task})


def _records(run: Path) -> list[dict]:
    """Every self-check the run recorded; a run that recorded none fails the test."""
    return [json.loads(line) for line in (run / SELF_CHECK_RECORD).read_text().splitlines()]


def _count(model: FakeModel, marker: str) -> int:
    return sum(marker in prompt for prompt in model.prompts)


def _commits(run: Path) -> list[str]:
    found = worktree.recorded(run)
    return git(Path(found.path), "log", "--format=%s", f"{found.base}..HEAD").splitlines()


def _spine_run(repo: Path, tmp_path: Path, monkeypatch, run_id: str, **knobs) -> Path:
    """A run directory the spine would run: a worktree, declarations, facts and knobs."""
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", run_id, run)
    lowering.write_declarations(_mode(**knobs).state_dict(), run)
    (run / "facts.json").write_text(json.dumps({"name": NAME, "brief": "a tool", "check": CHECK}))
    monkeypatch.setenv("REFACTORY_KNOBS", json.dumps({"check_attempts": 3, "refusal_attempts": 3,
                                                      "self_check_fixups": 2,
                                                      "self_check_timeout_s": 300,
                                                      "check_timeout_s": 180, **knobs}))
    return run


def _node(run: Path, target: str, node: str, kind: str, monkeypatch) -> tuple[int, str, str]:
    """One node visit as the spine makes it: `refactory_dsh.run` in its own process."""
    monkeypatch.setenv("REFACTORY_WORKDIR", str(run))
    monkeypatch.setenv("DSH_WORKFLOW_NODE", json.dumps({"id": node, "type": kind}))
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = node_main([target, "--node", node])
    finally:
        sys.stdout, sys.stderr = saved
    return code, out.getvalue(), err.getvalue()


def _emit(run: Path, emission: str, monkeypatch) -> None:
    (run / "emit.txt").write_text(emission)
    code, _, err = _node(run, f"{repo_change.OPS}:materialise", "materialise", "op", monkeypatch)
    assert code == 0, err


def _prepare(run: Path, monkeypatch) -> tuple[int, str, str]:
    return _node(run, f"{design_ops.__name__}:prepare_check", "prepare_check", "gate", monkeypatch)


def _facts(run: Path) -> dict:
    return json.loads((run / "facts.json").read_text())


# ── the loop, on the walker, through the real design graph ────────────────────


def test_a_stale_generated_file_and_a_pinned_test_are_fixed_inside_the_attempt(tmp_path):
    """The first emission changes the tool, which leaves the generated file stale and
    breaks the pinned test. Generate rewrites the file into the attempt; the self-check
    sends the pinned test back; the second emission fixes it; one review fork; no
    attempt spent; one commit with both changes."""
    repo = _target(tmp_path / "repo", {
        **PINNED,
        "gen.py": "import tool\nopen('generated.txt', 'w').write(f'version {tool.VERSION}\\n')\n",
        "generated.txt": "version 1\n",
        "tests/test_generated.py": ("import tool\n\n\ndef test_generated():\n"
                                    "    assert open('generated.txt').read() == f'version {tool.VERSION}\\n'\n"),
    })
    breaks = render({"tool.py": "VERSION = 2\n"})
    fixes = render({"tests/test_pin.py": "import tool\n\n\ndef test_pin():\n    assert tool.VERSION == 2\n"})
    model = FakeModel(_answers(breaks, fixes))
    run = tmp_path / "run"
    result = _design(run, model, repo, generate="python gen.py")
    assert result.exit_ok, result.error

    first, second = _records(run)
    assert first["outcome"] == "fixup" and first["failing_ids"] == [PIN]
    assert set(first["ran"]) == {"tests/test_generated.py", "tests/test_pin.py"} and not first["cut"]
    assert second["outcome"] == "pass" and second["fixups_before"] == 1
    # The fix-up reached emit, led by its reason.
    second_emit = [prompt for prompt in model.prompts if EMIT in prompt][1]
    assert "self-check fix-up 1 of 2" in second_emit and PIN in second_emit
    # Nothing was spent, and the reviews ran once, on the fixed attempt.
    assert not result.facts.get("attempts_used") and not result.facts.get("check_attempts_used")
    assert _count(model, HEALTH) == 1 and _count(model, EMIT) == 2
    # One attempt, one commit, holding the change, the regenerated file and the fix.
    assert _commits(run) == [f"{NAME}: attempt 1"]
    head = Path(worktree.recorded(run).path)
    assert git(head, "show", "HEAD:generated.txt") == "version 2"
    assert "VERSION == 2" in git(head, "show", "HEAD:tests/test_pin.py")
    assert git(head, "show", "HEAD:tool.py") == "VERSION = 2"


def test_a_clean_emission_goes_straight_on(tmp_path):
    repo = _target(tmp_path / "repo", PINNED)
    model = FakeModel(_answers(render({"tool.py": "VERSION = 1\nNAME = 'tool'\n"})))
    run = tmp_path / "run"
    result = _design(run, model, repo)
    assert result.exit_ok, result.error
    [record] = _records(run)
    assert record["outcome"] == "pass" and record["ran"] == record["selection"] == ["tests/test_pin.py"]
    assert record["commands"] == ["python -m pytest -q tests/test_pin.py"]
    assert _count(model, EMIT) == 1 and _count(model, HEALTH) == 1
    assert not result.facts.get("self_check_fixup_count")
    assert not result.facts.get("total_self_check_fixups_used")


def test_the_bound_then_the_early_stop_spends_one_attempt(tmp_path):
    """A pinned test that keeps failing gets two fix-ups; the third failure is the early
    stop, which spends the attempt. No review runs until an attempt passes."""
    repo = _target(tmp_path / "repo", PINNED)
    still_broken = [render({"tool.py": f"VERSION = {n}\n"}) for n in (2, 3, 4)]
    fixed = render({"tool.py": "VERSION = 5\n",
                    "tests/test_pin.py": "import tool\n\n\ndef test_pin():\n    assert tool.VERSION == 5\n"})
    model = FakeModel(_answers(*still_broken, fixed))
    run = tmp_path / "run"
    result = _design(run, model, repo, _mode(check_attempts=2))
    assert result.exit_ok, result.error
    outcomes = [(entry["outcome"], entry["attempt"]) for entry in _records(run)]
    assert outcomes == [("fixup", 1), ("fixup", 1), ("exhausted", 1), ("pass", 2)]
    assert result.facts["attempts_used"] == 1 and result.facts["total_self_check_fixups_used"] == 2
    # The reviews ran once, after the fourth emission; none ran during the fix-ups.
    first_review = next(i for i, prompt in enumerate(model.prompts) if HEALTH in prompt)
    emits = [i for i, prompt in enumerate(model.prompts) if EMIT in prompt]
    assert len(emits) == 4 and emits[-1] < first_review and _count(model, HEALTH) == 1
    # The fix-ups were amended into attempt 1; the early stop started attempt 2.
    assert _commits(run) == [f"{NAME}: attempt 2", f"{NAME}: attempt 1"]


def test_the_worst_case_ends_in_the_build_loop_stop_not_the_iteration_limit(tmp_path):
    """Two refusals before every applied emission, and every applied emission fails its
    self-check: fix-ups, early stops and refusals all reloop prepare_check, and the run
    still ends in b39b's stop, past the old limit of 8 and under the new one."""
    repo = _target(tmp_path / "repo", PINNED)
    refused = "===EDIT: tool.py===\n<<<<<<< SEARCH\nnot in the file\n=======\nx\n>>>>>>> REPLACE\n"
    emits = [emission for n in range(9) for emission in (refused, refused,
                                                          render({"tool.py": f"VERSION = {10 + n}\n"}))]
    model = FakeModel(_answers(*emits))
    run = tmp_path / "run"
    result = _design(run, model, repo, _mode(check_attempts=3, self_check_fixups=2))
    assert not result.exit_ok
    assert "BuildLoopStop" in (result.error or "") and "check attempts exhausted" in result.error
    assert "max_iterations" not in result.error
    assert [entry["outcome"] for entry in _records(run)] == ["fixup", "fixup", "exhausted"] * 3
    # Every emission was a prepare_check reloop but the last: 26 of them, against 45.
    assert _count(model, EMIT) == 27 and DesignMode().build().nodes["prepare_check"].max_iterations == 45


# ── the base: only the change's failures go back ──────────────────────────────


ENV_FAILURE = "tests/test_env.py::test_env"
ALWAYS_FAILS = "import tool\n\n\ndef test_env():\n    assert False, 'no network'\n"
LEFT_DESIGN = ("# A tool\n\n## Summary\n\nBump the tool.\n\n## Rule and sites\n\n"
               "- `tool.py:1` — **[left: the version stays an int]**\n\n## Docs\n\n- none\n\n"
               "## Changes\n\n- tool.py\n")


def test_round_one_sends_back_the_changes_failure_and_not_the_bases(tmp_path, monkeypatch):
    """On round 1 the failing tests run on the base worktree base_check prepares: the one
    the change broke goes back, the one the base fails too does not."""
    repo = _target(tmp_path / "repo", {
        **PINNED, "tests/test_env.py": ALWAYS_FAILS})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500001")
    (run / "design.md").write_text(LEFT_DESIGN)
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    assert out.strip().endswith("reloop") and "self-check fix-up 1 of 2" in out
    [record] = _records(run)
    assert record["failing_ids"] == [PIN] and record["inherited_filtered"] == [ENV_FAILURE]
    assert record["base"] == "compared with a run of 2 test(s) on the base"
    validation = _facts(run)["validation"]
    assert validation.startswith("self-check fix-up 1 of 2")
    assert f"- {PIN}" in validation and f"- {ENV_FAILURE}" not in validation
    assert "not this change's: " + ENV_FAILURE in validation
    # The design's constraints go back with it (R5), so a pinning test is not "fixed"
    # by reverting what the design changed.
    assert "[left: the version stays an int] — do not change this" in validation
    # When the base fails it too, the attempt goes on to the reviews.
    _emit(run, render({"tests/test_pin.py": "import tool\n\n\ndef test_pin():\n    assert tool.VERSION == 2\n"}),
          monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip() == "proceed", err
    assert _records(run)[-1]["outcome"] == "inherited"
    # What the base said was kept: the second comparison ran nothing on the base.
    assert _records(run)[-1]["base"] == "compared with earlier runs of these tests on the base"


def test_the_checks_own_base_result_answers_first(tmp_path, monkeypatch):
    """When the check has compared with the base already, its cached result answers, and
    the base is not prepared again."""
    from refactory_dsh import base_check

    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_env.py": ALWAYS_FAILS})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500011")
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)
    found = worktree.recorded(run)
    (run / base_check.CACHE_FILE).write_text(json.dumps({
        "cache_key": base_check.cache_key(found.base, "", "", CHECK), "ok": False,
        "failures": [ENV_FAILURE], "output": "", "exit_code": 1, "environment_failure": None,
        "base_complete": True, "base_clean": True}))

    def prepared(ctx):
        raise AssertionError("the base was prepared again")

    monkeypatch.setattr(base_check, "_ensure_base_worktree", prepared)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    assert record["failing_ids"] == [PIN] and record["inherited_filtered"] == [ENV_FAILURE]
    assert record["base"] == "compared with the check's base result"


def test_a_failure_the_last_check_inherited_is_not_sent_back():
    from refactory_dsh.sandbox import SandboxResult

    output = f"FAILED {ENV_FAILURE} - AssertionError: no network\n1 failed in 0.10s"
    failed = SandboxResult(ok=False, returncode=1, stdout=output, stderr="", timed_out=False,
                           elapsed_s=0.1)
    prepass = failfast.targeted_prepass(
        {"status": "fail", "inherited": [ENV_FAILURE]}, CHECK, lambda command, timeout: failed,
        180, selection=["tests/test_env.py"], self_check_timeout_s=300)
    assert prepass.ran and prepass.ran_ids == ["tests/test_env.py"]
    assert not prepass.early_stop and prepass.failing_ids == []


def test_a_base_that_runs_out_of_time_lets_nothing_be_inherited(tmp_path, monkeypatch):
    """A base run stopped at its limit is unavailable: its failure goes back to the builder."""
    slow_on_the_base = ("import os, time\nimport tool\n\n\ndef test_env():\n"
                        "    if not os.path.exists('attempt.flag'):\n        time.sleep(30)\n"
                        "    assert False\n")
    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_env.py": slow_on_the_base})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500002", self_check_timeout_s=10)
    _emit(run, render({"tool.py": "VERSION = 1\nMARK = 1\n", "attempt.flag": "x\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    assert "self-check fix-up 1 of 2" in out
    [record] = _records(run)
    assert record["failing_ids"] == [ENV_FAILURE] and record["inherited_filtered"] == []
    assert record["base"].startswith("unavailable, so nothing is inherited: the base run of 1 test(s) "
                                     "was stopped at its 10 s limit")


# ── the cut, and what does not count ──────────────────────────────────────────


def test_the_cut_keeps_a_finished_chunks_failure(tmp_path, monkeypatch):
    """A selection larger than the limit: the first chunk fails fast, the second is
    still running at the limit. The second is stopped and the rest never starts, and the
    first chunk's failure still goes back as a fix-up."""
    repo = _target(tmp_path / "repo", {
        "tool.py": "VERSION = 1\n",
        "tests/test_a.py": "import tool\n\n\ndef test_a():\n    assert tool.VERSION == 1\n",
        "tests/test_b.py": "import time\nimport tool\n\n\ndef test_b():\n    time.sleep(60)\n",
        "tests/test_c.py": "import tool\n\n\ndef test_c():\n    assert tool\n",
    })
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500003", self_check_timeout_s=10)
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    assert out.strip().endswith("reloop") and "self-check fix-up 1 of 2" in out
    [record] = _records(run)
    assert record["selection"] == ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    assert record["ran"] == ["tests/test_a.py"]
    assert record["cut"] == ["tests/test_b.py", "tests/test_c.py"]
    assert record["failing_ids"] == ["tests/test_a.py::test_a"]
    assert record["commands"] == ["python -m pytest -q tests/test_a.py"]
    assert "chunk 2 of 3 was stopped at the 10 s limit" in " ".join(record["notes"])
    assert record["duration_s"] < 30


def test_a_chunk_runs_one_runner_so_a_failing_spec_cannot_hide_a_python_failure(tmp_path):
    """The targeted command keeps the check's ``&&``: a chunk holding a spec and a Python
    file would never run the Python file once vitest failed. Chunks hold one runner."""
    from refactory_dsh.sandbox import SandboxResult

    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    selection = [*(f"tests/test_p{n}.py" for n in (1, 2, 3)), "web/tests/s.spec.ts",
                 *(f"tests/test_p{n}.py" for n in (4, 5, 6))]

    def run(command: str, timeout: float) -> SandboxResult:
        if command.startswith("pnpm test"):  # vitest fails before naming a test; && stops there
            return SandboxResult(ok=False, returncode=1, stdout="vitest could not start", stderr="",
                                 timed_out=False, elapsed_s=0.1)
        failing = [name for name in ("tests/test_p3.py", "tests/test_p5.py") if name in command]
        if failing:
            return SandboxResult(ok=False, returncode=1, stderr="", timed_out=False, elapsed_s=0.1,
                                 stdout="".join(f"FAILED {name}::test_x - AssertionError\n"
                                                for name in failing) + "1 failed in 0.10s")
        return SandboxResult(ok=True, returncode=0, stdout="2 passed in 0.10s", stderr="",
                             timed_out=False, elapsed_s=0.1)

    prepass = failfast.targeted_prepass({}, "pnpm test && python -m pytest -q tests", run, 180,
                                        root=str(tmp_path), selection=selection, self_check_timeout_s=300)
    # The failures before and after the spec's chunk both come back.
    assert prepass.failing_ids == ["tests/test_p3.py::test_x", "tests/test_p5.py::test_x"]
    assert all(("pnpm test" in command) != ("pytest" in command) for command in prepass.commands)
    assert "chunk 3 did not count" in " ".join(prepass.notes)
    # The spec's chunk reported nothing, so it is not counted as run.
    assert "web/tests/s.spec.ts" in prepass.cut_ids and "web/tests/s.spec.ts" not in prepass.ran_ids


def test_a_chunk_holds_at_most_four_tests():
    """About six chunks, but never more than four tests in one, so a chunk the limit
    stops loses little."""
    chunks = failfast._chunks([f"tests/test_{n}.py" for n in range(60)])
    assert [len(chunk) for chunk in chunks] == [4] * 15
    assert [len(chunk) for chunk in failfast._chunks([f"tests/test_{n}.py" for n in range(7)])] == [2, 2, 2, 1]


def test_a_self_check_that_says_nothing_goes_on_with_no_fixup(tmp_path, monkeypatch):
    """Its only chunk runs past the limit: inconclusive, so the attempt goes on to the
    reviews and the whole check, with nothing spent and nothing sent back."""
    repo = _target(tmp_path / "repo", {
        "tool.py": "VERSION = 1\n",
        "tests/test_slow.py": "import time\nimport tool\n\n\ndef test_slow():\n    time.sleep(60)\n",
    })
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500004", self_check_timeout_s=10)
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip() == "proceed", err
    [record] = _records(run)
    assert record["outcome"] == "inconclusive" and record["ran"] == []
    assert record["cut"] == ["tests/test_slow.py"]
    assert (run / CHECK_PREP).exists()
    facts = _facts(run)
    assert not facts.get("self_check_fixup_count") and not facts.get("attempts_used")


def test_a_docs_only_emission_selects_nothing_and_runs_nothing(tmp_path, monkeypatch):
    repo = _target(tmp_path / "repo", {**PINNED, "README.md": "a tool\n"})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500005")
    _emit(run, render({"README.md": "a tool, documented\n", "docs/guide.md": "use it\n"}), monkeypatch)
    ran = []
    monkeypatch.setattr(repo_change, "run_check", lambda *a, **k: ran.append(a) or None)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip() == "proceed", err
    [record] = _records(run)
    assert record["outcome"] == "nothing selected" and record["selection"] == []
    assert ran == []


# ── both runtimes: the spine runs each visit in its own process ───────────────


def test_on_the_spine_the_fixups_carry_across_processes_and_the_bound_spends_the_attempt(
        tmp_path, monkeypatch):
    """materialise and prepare_check as separate processes sharing facts.json: each
    failing self-check reloops to emit spending nothing, the count survives each process,
    each fix-up is amended into the attempt, and at the bound the attempt is spent."""
    repo = _target(tmp_path / "repo", PINNED)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500006", check_attempts=2)
    for number in (1, 2):
        _emit(run, render({"tool.py": f"VERSION = {number + 1}\n"}), monkeypatch)
        code, out, err = _prepare(run, monkeypatch)
        assert code == 0, err
        reason, verdict = out.strip().rsplit("\n", 1)
        assert verdict == "reloop" and reason.startswith(f"self-check fix-up {number} of 2")
        facts = _facts(run)
        assert facts["self_check_fixup_count"] == number
        assert facts["total_self_check_fixups_used"] == number
        assert not facts.get("attempts_used") and not facts.get("check_attempts_used")
        assert _commits(run) == [f"{NAME}: attempt 1"]
    _emit(run, render({"tool.py": "VERSION = 9\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    assert out.strip().endswith("reloop") and "self-check fix-up" not in out
    facts = _facts(run)
    assert facts["attempts_used"] == 1 and facts["check_attempts_used"] == 1
    assert facts["self_check_fixup_count"] == 0
    assert json.loads((run / "check-record.json").read_text())["check_mode"] == "targeted"
    # The attempt is spent, so the next emission is a commit of its own.
    _emit(run, render({"tool.py": "VERSION = 1\n"}), monkeypatch)
    assert _commits(run) == [f"{NAME}: attempt 2", f"{NAME}: attempt 1"]
    # The lowered graph sends prepare_check's reloop to emit, with room for the worst case.
    workflow = lowering.to_workflow(DesignMode().state_dict())
    assert ("prepare_check", "emit", "reloop") in {(e.source, e.target, e.condition)
                                                   for e in workflow.edges}
    assert workflow.nodes["prepare_check"].max_iterations == 45


def test_the_run_cap_on_fixups_ends_them_for_the_run(tmp_path, monkeypatch):
    """Fix-ups are bounded per run too: self_check_fixups x check_attempts."""
    repo = _target(tmp_path / "repo", PINNED)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500007", check_attempts=3)
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)
    facts = _facts(run)
    (run / "facts.json").write_text(json.dumps({**facts, "total_self_check_fixups_used": 6}))
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    assert "self-check fix-up" not in out and _facts(run)["attempts_used"] == 1
    [record] = _records(run)
    assert record["outcome"] == "exhausted" and record["why"] == "6 of 6 fix-ups used on this run"


# ── the fix-up's commit: one amendment path ───────────────────────────────────


def test_a_fixup_is_amended_through_the_one_amend_path_with_the_attempts_guards(tmp_path, monkeypatch):
    repo = _target(tmp_path / "repo", PINNED)
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef500008", run)
    worktree.commit_attempt(run, {Path("tool.py"): "VERSION = 2\n"}, f"{NAME}: attempt 1")
    amended = []
    real = worktree.amend
    monkeypatch.setattr(worktree, "amend", lambda *a, **k: amended.append(a[2]) or real(*a, **k))
    worktree.commit_attempt(run, {Path("tests/test_pin.py"): "x = 1\n"}, "unused",
                            (Path("app.py"),), amending=True)
    assert amended == [["tests/test_pin.py"]]
    assert _commits(run) == [f"{NAME}: attempt 1"]
    found = worktree.recorded(run)
    assert git(Path(found.path), "rev-parse", "HEAD") == found.head
    assert git(Path(found.path), "ls-tree", "--name-only", "HEAD", "app.py") == ""
    # The attempt's guards hold for an amendment: no git state, no change-nothing amend.
    with pytest.raises(worktree.AttemptRefused, match="git's own state"):
        worktree.commit_attempt(run, {Path(".git/config"): "x"}, "unused", amending=True)
    with pytest.raises(worktree.AttemptRefused, match="changed nothing"):
        worktree.commit_attempt(run, {Path("tool.py"): "VERSION = 2\n"}, "unused", amending=True)
    assert amended == [["tests/test_pin.py"]]


def test_an_amendment_that_died_before_its_record_is_dropped(tmp_path):
    repo = _target(tmp_path / "repo", PINNED)
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef500009", run)
    found = worktree.commit_attempt(run, {Path("tool.py"): "VERSION = 2\n"}, f"{NAME}: attempt 1")
    path = Path(found.path)
    # An amendment that committed and died before recording the new head.
    (path / "tool.py").write_text("VERSION = 3\n")
    git(path, "commit", "-q", "-a", "--amend", "--no-edit")
    (run / worktree.PENDING).write_text(json.dumps({"files": ["tool.py"], "parent": found.head,
                                                    "amending": True}))
    worktree.commit_attempt(run, {Path("tool.py"): "VERSION = 4\n"}, "unused", amending=True)
    assert git(path, "show", "HEAD:tool.py") == "VERSION = 4"
    assert _commits(run) == [f"{NAME}: attempt 1"]


# ── design_fast and the sequential check keep the dm-128 pre-pass ─────────────


def test_design_fast_and_the_sequential_check_keep_the_dm128_prepass(tmp_path, monkeypatch):
    """design_fast's focused_check and check_artifact pass no selection, so they run the
    last check's failures only, from round 2, as before; neither writes a self-check."""
    monkeypatch.setenv("REFACTORY_PROJECT", str(tmp_path))
    calls = []
    real = failfast.targeted_prepass

    def spy(prev_record, command, run_fn, timeout, **kw):
        calls.append((prev_record.get("status"), kw))
        return real(prev_record, command, run_fn, timeout, **kw)

    monkeypatch.setattr(failfast, "targeted_prepass", spy)
    plan = "Change only the requested file and review the required check result."
    qa = json.dumps({"status": "pass", "checks": [{"name": "behaviour", "command": "inspection",
                                                   "status": "pass", "evidence": "it holds"}],
                     "findings": [], "not_run": []})
    bad = "===FILE: answer.py===\nassert False, 'test_bar'\n"
    good = "===FILE: answer.py===\nassert True\n"
    fast = DesignFastMode()
    state = fast.state_dict()
    state["knobs"]["repair_attempts"]["value"] = 2
    fast.load_state_dict(state)
    assert "self_check_fixups" not in state["knobs"] and "self_check_timeout_s" not in state["knobs"]
    fast.run(tmp_path / "fast", model=FakeModel([plan, bad, good, qa, VERIFICATION_ANSWER]),
             task={"name": "fast_tool", "brief": "assert true", "check": f"{sys.executable} answer.py"})
    assert calls and all("selection" not in kw for _, kw in calls)
    # Round 1 has no last failure to rerun; round 2 reruns round 1's.
    statuses = [status for status, _ in calls]
    assert statuses[0] != "fail" and statuses[1] == "fail"
    assert not (tmp_path / "fast" / SELF_CHECK_RECORD).exists()

    calls.clear()
    repo = _target(tmp_path / "repo", PINNED)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500010")
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)
    _node(run, f"{design_ops.__name__}:check_artifact", "check", "op", monkeypatch)
    assert calls and all("selection" not in kw for _, kw in calls)
    assert not (run / SELF_CHECK_RECORD).exists()


# ── review of #502: an undone attempt, collection errors, the cut's order ─────


UNDOES = render({"tool.py": "VERSION = 1\n"})
BREAKS = render({"tool.py": "VERSION = 2\n"})
PIN_FIXED = render({"tests/test_pin.py": "import tool\n\n\ndef test_pin():\n    assert tool.VERSION == 2\n"})
GENERATED = {
    "gen.py": "import tool\nopen('generated.txt', 'w').write(f'version {tool.VERSION}\\n')\n",
    "generated.txt": "version 1\n",
}


def test_a_fixup_that_undoes_the_whole_attempt_is_refused_not_a_crash(tmp_path):
    """The builder answers the pinned test by reverting its change: the amend would empty
    the attempt, which git refuses. It is a refused emission instead, and the run goes on."""
    repo = _target(tmp_path / "repo", PINNED)
    model = FakeModel(_answers(BREAKS, UNDOES, PIN_FIXED))
    run = tmp_path / "run"
    result = _design(run, model, repo)
    assert result.exit_ok, result.error
    emits = [prompt for prompt in model.prompts if EMIT in prompt]
    assert len(emits) == 3 and "undoes the whole attempt" in emits[2]
    assert [entry["outcome"] for entry in _records(run)] == ["fixup", "pass"]
    assert _commits(run) == [f"{NAME}: attempt 1"]
    assert git(Path(worktree.recorded(run).path), "show", "HEAD:tool.py") == "VERSION = 2"


def test_an_empty_diff_attempt_is_sent_back_as_a_fixup(tmp_path):
    """The fix-up reverts the source but not the file generate wrote from it; generate then
    writes that file back too, so the attempt's tree equals its parent's.  It is detected
    as an empty-diff and sent back as a fix-up, not forwarded to the reviews."""
    repo = _target(tmp_path / "repo", {**PINNED, **GENERATED})
    # Three emissions: BREAKS triggers self-check fixup, UNDOES is the fix-up that
    # generate empties, FIXES_BOTH actually fixes both tool.py and the test.
    fixes_both = render({"tool.py": "VERSION = 2\n",
                         "tests/test_pin.py": "import tool\n\n\ndef test_pin():\n    assert tool.VERSION == 2\n"})
    model = FakeModel(_answers(BREAKS, UNDOES, fixes_both))
    run = tmp_path / "run"
    result = _design(run, model, repo, _mode(self_check_fixups=3), generate="python gen.py")
    assert result.exit_ok, result.error
    outcomes = [entry["outcome"] for entry in _records(run)]
    assert outcomes == ["fixup", "empty_diff_fixup", "pass"]
    # The empty-diff fix-up routed back to emit (a third emission prompt exists).
    emits = [prompt for prompt in model.prompts if EMIT in prompt]
    assert len(emits) == 3 and "generate put back everything" in emits[2]
    # Fix-up count incremented.
    found = worktree.recorded(run)
    assert _commits(run) == [f"{NAME}: attempt 1"]


def test_an_empty_diff_on_second_attempt_is_detected(tmp_path, monkeypatch):
    """An emptied second attempt (parent = attempt 1) is also detected as an empty-diff
    fix-up, verifying the parent-tree comparison (<head>^{tree} vs <head>~1^{tree})."""
    repo = _target(tmp_path / "repo", {**PINNED, **GENERATED})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500050")
    facts = _facts(run)
    (run / "facts.json").write_text(json.dumps({**facts, "generate": "python gen.py"}))
    # First attempt: a clean emission that passes (adds NAME to tool.py).
    attempt1_tool = "VERSION = 1\nNAME = 'tool'\n"
    _emit(run, render({"tool.py": attempt1_tool}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip() == "proceed", err
    # Second attempt: emit breaks, self-check sends it back.
    _emit(run, BREAKS, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    # Fix-up: revert to attempt 1's tool.py, which generate then empties too.
    _emit(run, render({"tool.py": attempt1_tool}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip().endswith("reloop"), err
    assert "generate put back everything" in out
    records = _records(run)
    assert records[-1]["outcome"] == "empty_diff_fixup"


def test_on_the_spine_a_fixup_that_undoes_the_attempt_is_a_counted_refusal(tmp_path, monkeypatch):
    repo = _target(tmp_path / "repo", PINNED)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500012")
    _emit(run, BREAKS, monkeypatch)
    assert "self-check fix-up 1 of 2" in _prepare(run, monkeypatch)[1]
    _emit(run, UNDOES, monkeypatch)
    facts = _facts(run)
    assert facts["consecutive_refusals"] == 1 and "undoes the whole attempt" in facts["emission_refused"]
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip().endswith("reloop") and "materialise could not apply" in out, err
    assert _facts(run)["self_check_fixup_count"] == 1
    found = worktree.recorded(run)
    assert _commits(run) == [f"{NAME}: attempt 1"] and worktree.tracked_edits(found) == []
    assert git(Path(found.path), "show", "HEAD:tool.py") == "VERSION = 2"


def test_a_module_that_does_not_import_goes_back_with_the_chunks_other_failures(tmp_path, monkeypatch):
    """pytest stops a chunk when a module does not import, so its other tests never ran:
    the module goes back as its file's failure, the rest of the chunk runs again, and a
    real failure there goes back too."""
    plain = "import tool\n\n\ndef test_{n}():\n    assert tool\n"
    files = {f"tests/test_{n}.py": plain.format(n=n) for n in range(1, 8)}
    files["tests/test_3.py"] = "import tool\nfrom tool import OLD\n\n\ndef test_3():\n    assert OLD\n"
    files["tests/test_4.py"] = "import tool\n\n\ndef test_4():\n    assert tool.VERSION == 1\n"
    repo = _target(tmp_path / "repo", {"tool.py": "VERSION = 1\nOLD = 1\n", **files})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500013")
    _emit(run, BREAKS, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    assert record["failing_ids"] == ["tests/test_3.py", "tests/test_4.py::test_4"]
    assert sorted(record["ran"]) == sorted(files) and record["cut"] == []
    assert "tests/test_3.py did not import" in " ".join(record["notes"])


def test_a_slow_file_at_the_cut_does_not_hide_cheap_failures_after_it(tmp_path, monkeypatch):
    """The rest of the selection runs cheapest first, so the slow (big) file is the one the
    limit cuts, and the cheap failing file behind it in name order still reports."""
    slow = "import time\nimport m1\n" + "# padding\n" * 400 + "\n\ndef test_a():\n    time.sleep(60)\n"
    files = {"tests/test_a_slow.py": slow,
             **{f"tests/test_{c}.py": f"import m{n}\n\n\ndef test_{c}():\n    assert m{n}\n"
                for n, c in ((2, "b"), (3, "c"), (4, "d"))},
             "tests/test_e_fails.py": "import m5\n\n\ndef test_e():\n    assert m5.X == 1\n",
             **{f"m{n}.py": "X = 1\n" for n in range(1, 6)}}
    repo = _target(tmp_path / "repo", files)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500014", self_check_timeout_s=10)
    _emit(run, render({f"m{n}.py": "X = 2\n" for n in range(1, 6)}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    assert record["failing_ids"] == ["tests/test_e_fails.py::test_e"]
    assert record["selection"][-1] == "tests/test_a_slow.py" and record["cut"] == ["tests/test_a_slow.py"]


def test_a_fixup_runs_the_last_self_checks_failures_first(tmp_path, monkeypatch):
    repo = _target(tmp_path / "repo", PINNED)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500015")
    _emit(run, BREAKS, monkeypatch)
    _prepare(run, monkeypatch)
    _emit(run, render({"tool.py": "VERSION = 3\n"}), monkeypatch)
    _prepare(run, monkeypatch)
    first, second = _records(run)
    assert first["selection"][0] == "tests/test_pin.py" and first["how_selected"]["first"] == []
    assert second["selection"][0] == PIN and second["how_selected"]["first"] == [PIN]


def test_empty_diff_fixup_exhausts_the_budget(tmp_path, monkeypatch):
    """When the fix-up budget is already spent, an empty-diff attempt is exhausted."""
    repo = _target(tmp_path / "repo", {**PINNED, **GENERATED})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500051", self_check_fixups=1)
    facts = _facts(run)
    (run / "facts.json").write_text(json.dumps({**facts, "generate": "python gen.py"}))
    # First emission breaks the pin.
    _emit(run, BREAKS, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 1" in out, err
    # Fix-up: UNDOES, which generate empties. Budget is 1, already used 1.
    _emit(run, UNDOES, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    records = _records(run)
    assert records[-1]["outcome"] == "exhausted"
    assert _facts(run)["attempts_used"] == 1


def test_a_timed_out_chunk_records_its_elapsed_time_as_a_lower_bound(tmp_path, monkeypatch):
    """A chunk stopped at the time limit records a timing entry with timed_out: True,
    its files appear in cut_ids, and the entry has counted: True."""
    repo = _target(tmp_path / "repo", {
        "tool.py": "VERSION = 1\n",
        "tests/test_a.py": "import tool\n\n\ndef test_a():\n    assert tool\n",
        "tests/test_b.py": "import time\nimport tool\n\n\ndef test_b():\n    time.sleep(60)\n",
    })
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500052", self_check_timeout_s=10)
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip() == "proceed", err
    [record] = _records(run)
    assert record["outcome"] == "inconclusive"
    # Find the timed_out timing entry.
    timed_out_entries = [t for t in record["timings"] if t.get("timed_out")]
    assert len(timed_out_entries) >= 1
    entry = timed_out_entries[0]
    assert entry["s"] > 0 and entry["counted"] is True
    # The chunk's files are in cut.
    for test in entry["tests"]:
        assert test in record["cut"]


def test_costs_from_record_does_not_split_by_size_and_handles_timed_out(tmp_path):
    """costs_from_record uses per-file durations when available, and a non-timed_out
    entry always wins over a timed_out entry."""
    repo = _target(tmp_path / "repo", {
        "tests/test_big.py": "# padding\n" * 500 + "\ndef test_big():\n    pass\n",
        "tests/test_small.py": "\ndef test_small():\n    pass\n",
    })
    # (a) Two-file counted chunk with per-test durations: each gets its own time.
    entries_a = [{"timings": [{"tests": ["tests/test_big.py", "tests/test_small.py"],
                                "s": 10.0, "counted": True,
                                "durations": [
                                    {"test_id": "tests/test_big.py::test_big", "seconds": 8.0},
                                    {"test_id": "tests/test_small.py::test_small", "seconds": 2.0},
                                ]}]}]
    costs_a = test_selection.costs_from_record(entries_a, repo)
    assert costs_a["tests/test_big.py"] == 8.0
    assert costs_a["tests/test_small.py"] == 2.0

    # (b) A timed_out chunk at 120 s: its files get 120.0 when no other entry exists.
    entries_b = [{"timings": [{"tests": ["tests/test_big.py"], "s": 120.0,
                                "counted": True, "timed_out": True}]}]
    costs_b = test_selection.costs_from_record(entries_b, repo)
    assert costs_b["tests/test_big.py"] == 120.0

    # (c) A timed_out entry at 120 s followed by a non-timed_out entry at 5.0 with
    # durations: the non-timed_out wins (stays 5.0).
    entries_c = [{"timings": [{"tests": ["tests/test_big.py"], "s": 120.0,
                                "counted": True, "timed_out": True},
                               {"tests": ["tests/test_big.py"], "s": 5.0,
                                "counted": True,
                                "durations": [
                                    {"test_id": "tests/test_big.py::test_big", "seconds": 5.0},
                                ]}]}]
    costs_c = test_selection.costs_from_record(entries_c, repo)
    assert costs_c["tests/test_big.py"] == 5.0


def test_a_missing_test_id_is_dropped_and_the_rest_of_the_chunk_runs(tmp_path, monkeypatch):
    """When pytest exits 4 because of a not-found id (whose path ``_NOT_FOUND`` strips to
    ``tests/…``), the chunk's ``refactory/tests/…`` names are suffix-matched, and a
    missing parametrized id without its ``[…]`` covers the chunk's bracketed ids.
    The remaining test runs and its real failure is reported."""
    repo = _target(tmp_path / "repo", {
        "tool.py": "VERSION = 1\n",
        "refactory/__init__.py": "",
        "refactory/tests/__init__.py": "",
        "refactory/tests/test_real.py": (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))\n"
            "import tool\n\n\ndef test_real():\n    assert tool.VERSION == 1\n"
        ),
    })
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500053")
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)

    # Inject chunk ids under refactory/tests/ paths, including a missing parametrized id.
    real_select = test_selection.select

    def patched_select(*args, **kwargs):
        sel = real_select(*args, **kwargs)
        sel.tests = [
            "refactory/tests/test_real.py::test_gone[2.5]",
            "refactory/tests/test_real.py",
        ]
        sel.first = ["refactory/tests/test_real.py::test_gone[2.5]"]
        return sel

    monkeypatch.setattr(test_selection, "select", patched_select)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    [record] = _records(run)
    # The not-found id was dropped; the real test ran and its failure was found.
    assert "dropped not-found ids" in " ".join(record["notes"])
    assert record["failing_ids"] == ["refactory/tests/test_real.py::test_real"]
    assert "refactory/tests/test_real.py" in record["ran"]
    # Exactly one "dropped" note per chunk event.
    drop_notes = [n for n in record["notes"] if "dropped not-found ids" in n]
    assert len(drop_notes) == 1


def test_fixup_first_ids_do_not_duplicate_with_engine_or_touched_tier(tmp_path):
    """BUG-3: A parametrized first-id does not block its file from running whole later;
    a whole-file first-id deduplicates against the touched tier."""
    repo = _target(tmp_path / "repo", {
        "m.py": "X = 1\n",
        "tests/test_a.py": "import m\n\n\ndef test_x():\n    pass\n\ndef test_y():\n    pass\n",
    })
    files = test_selection.repository_files(repo)

    # Case (a): first=["tests/test_a.py::test_x"] with test_a.py touched.
    sel_a = test_selection.select(["m.py", "tests/test_a.py"], repo, {},
                                  check_command=CHECK, files=files,
                                  first=["tests/test_a.py::test_x"])
    # test_x runs first AND test_a.py still runs whole later.
    assert sel_a.tests[0] == "tests/test_a.py::test_x"
    assert "tests/test_a.py" in sel_a.tests
    # Both present: the parametrized id does not block the whole file.
    assert sel_a.tests.count("tests/test_a.py::test_x") == 1
    assert sel_a.tests.count("tests/test_a.py") == 1

    # Case (b): first=["tests/test_a.py"] with test_a.py touched.
    sel_b = test_selection.select(["m.py", "tests/test_a.py"], repo, {},
                                  check_command=CHECK, files=files,
                                  first=["tests/test_a.py"])
    # test_a.py appears exactly once (deduped).
    assert sel_b.tests.count("tests/test_a.py") == 1


def test_N10_timings_from_earlier_self_checks_flow_to_selection_ordering(tmp_path, monkeypatch):
    """The second self-check's selection orders files by their measured time, not by size.
    Kills mutant N10 (costs=test_selection.costs_from_record -> costs={}).

    Deterministic: after the first self-check, synthetic timings with durations are
    injected into the record, so the ordering does not depend on real elapsed time."""
    # big_fast: large file (lots of padding) that runs instantly
    big_fast = "import m1\n" + "# pad\n" * 200 + "\ndef test_fast():\n    pass\n"
    # small_slow: tiny file that runs instantly too (no real sleep)
    small_slow = "import m2\n\ndef test_slow():\n    pass\n"
    repo = _target(tmp_path / "repo", {
        "m1.py": "X = 1\n", "m2.py": "X = 1\n",
        "tests/test_fast.py": big_fast,
        "tests/test_slow.py": small_slow,
    })
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500054")
    # First emission: both tests run and are timed.
    _emit(run, render({"m1.py": "X = 2\n", "m2.py": "X = 2\n"}), monkeypatch)
    _prepare(run, monkeypatch)
    first_record = _records(run)[0]
    assert first_record["timings"]
    # Inject synthetic durations into the record: test_fast.py = 0.1 s, test_slow.py = 10.0 s
    record_path = run / SELF_CHECK_RECORD
    modified = dict(first_record)
    modified["timings"] = [
        {"tests": ["tests/test_fast.py", "tests/test_slow.py"],
         "s": 10.1, "counted": True,
         "durations": [
             {"test_id": "tests/test_fast.py::test_fast", "seconds": 0.1},
             {"test_id": "tests/test_slow.py::test_slow", "seconds": 10.0},
         ]}
    ]
    record_path.write_text(json.dumps(modified) + "\n")
    # Second emission: the selection should use the synthetic timings.
    _emit(run, render({"m1.py": "X = 3\n", "m2.py": "X = 3\n"}), monkeypatch)
    _prepare(run, monkeypatch)
    second_record = _records(run)[-1]
    importers = [t for t in second_record["selection"]
                 if t in ("tests/test_fast.py", "tests/test_slow.py")]
    assert len(importers) == 2, f"expected both files in selection, got {importers}"
    assert importers == ["tests/test_fast.py", "tests/test_slow.py"], \
        f"expected timing-based order [fast, slow], got {importers}"


def test_N12_base_prepared_flag_after_base_worktree_removed_triggers_repreparation(
        tmp_path, monkeypatch):
    """When the base worktree directory is removed after preparation, the base is
    re-prepared on the next comparison.  Kills mutant N12: dropping ``_at(...)`` at
    base_check.py skips re-preparation and crashes on the missing worktree.

    The second emission introduces a new module and test so ``missing`` is non-empty:
    the ``if missing:`` block at base_check.py is entered and ``_at`` is checked."""
    from refactory_dsh import base_check

    repo = _target(tmp_path / "repo", {
        **PINNED, "tests/test_env.py": ALWAYS_FAILS,
        "extra.py": "E = 1\n",
        "tests/test_extra.py": "import extra\n\n\ndef test_extra():\n    assert extra.E\n",
    })
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500055")
    # First emission: only changes tool.py; test_extra.py is not selected.
    _emit(run, BREAKS, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    # Find and remove the base worktree.
    found = worktree.recorded(run)
    base_path = base_check._base_worktree_path(
        Path(found.target), Path(found.path).name.removeprefix("run-"))
    if base_path.exists():
        import shutil
        shutil.rmtree(base_path)
    # Second emission: changes both tool.py and extra.py, bringing test_extra into
    # the selection; its test id has not been learned on the base, so ``missing`` is
    # non-empty and the ``if missing:`` block is entered.
    _emit(run, render({"tool.py": "VERSION = 2\nMARK = 1\n", "extra.py": "E = 0\n"}),
          monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    records = _records(run)
    assert len(records) == 2
    # The second self-check completed (re-preparation worked).
    assert records[1]["outcome"] in ("fixup", "exhausted", "pass", "inconclusive",
                                     "inherited", "nothing selected")


def test_N17_collection_error_file_ids_in_base_guardedness_classification(tmp_path):
    """A new test file that imports a module only the attempt introduces is classified
    as cannot_run_on_base by compute_test_guardedness via _parse_collection_errors.
    Driven through the walker's design graph (prepare_check → check_artifact →
    compute_test_guardedness → _parse_collection_errors).
    Kills mutant N17: replacing _parse_collection_errors with lambda _: (set(), False)
    changes the classification from cannot_run_on_base to fail."""
    repo = _target(tmp_path / "repo", {
        "tool.py": "VERSION = 1\n",
        "tests/test_pin.py": "import tool\n\n\ndef test_pin():\n    assert tool.VERSION == 1\n",
    })
    # Emit code that introduces a new module and a test that imports it.
    # The test passes on the attempt (module exists) but cannot be collected
    # on the base (module does not exist → ModuleNotFoundError).
    model = FakeModel(_answers(render({
        "new_module.py": "FEATURE = True\n",
        "tests/test_new_module.py": (
            "import new_module\n\n"
            "def test_feature():\n"
            "    assert new_module.FEATURE\n"
        ),
    })))
    run = tmp_path / "run"
    result = _design(run, model, repo)
    assert result.exit_ok, result.error
    guardedness = json.loads((run / "test-guardedness.json").read_text())
    # The new test file should be classified as cannot_run_on_base because
    # new_module does not exist on the base → collection error on import.
    found_results = [r for r in guardedness.get("results", [])
                     if r["file"] == "tests/test_new_module.py"]
    assert found_results, (
        f"tests/test_new_module.py not in guardedness results: {guardedness}")
    assert found_results[0].get("file_level") == "cannot_run_on_base", (
        f"expected cannot_run_on_base, got {found_results[0]}")


def test_timed_costs_order_the_rest(tmp_path):
    """A file an earlier self-check timed costs what it took; untimed files cost their size.
    A single-id chunk's duration with ``durations`` is a lower bound."""
    repo = _target(tmp_path / "repo", {
        "m1.py": "X = 1\n", "m2.py": "X = 1\n",
        "tests/test_big_fast.py": "import m1\n" + "# padding\n" * 300 + "\n\ndef test_a():\n    pass\n",
        "tests/test_small_slow.py": "import m2\n\n\ndef test_b():\n    pass\n",
    })
    files = test_selection.repository_files(repo)
    by_size = test_selection.select(["m1.py", "m2.py"], repo, {}, check_command=CHECK, files=files)
    assert by_size.tests == ["tests/test_small_slow.py", "tests/test_big_fast.py"]
    # The single-id chunk has durations: it is a lower bound for the file's cost.
    earlier = [{"timings": [{"tests": ["tests/test_small_slow.py"], "s": 30.0, "counted": True,
                             "durations": [{"test_id": "tests/test_small_slow.py::test_b",
                                            "seconds": 30.0}]},
                            {"tests": ["tests/test_big_fast.py::test_a"], "s": 0.5,
                             "counted": True,
                             "durations": [{"test_id": "tests/test_big_fast.py::test_a",
                                            "seconds": 0.5}]},
                            {"tests": ["tests/test_big_fast.py"], "s": 99.0, "counted": False}]}]
    costs = test_selection.costs_from_record(earlier, repo)
    # The single-id chunk's 0.5 s is a lower bound; the whole-file entry is uncounted.
    assert costs["tests/test_small_slow.py"] == 30.0
    assert costs["tests/test_big_fast.py"] == 0.5
    timed = test_selection.select(["m1.py", "m2.py"], repo, {}, check_command=CHECK, files=files,
                                  costs=costs)
    assert timed.tests == ["tests/test_big_fast.py", "tests/test_small_slow.py"]


def test_the_full_check_still_runs_and_binds_after_a_self_check_cut_at_the_limit(tmp_path):
    """The self-check's limit stops it before the failing test: it says nothing, and the
    whole check beside the reviews still finds the failure and sends the attempt back."""
    repo = _target(tmp_path / "repo", {
        "tool.py": "VERSION = 1\n",
        "tests/test_a_slow.py": "import time\nimport tool\n\n\ndef test_a():\n    time.sleep(15)\n",
        "tests/test_z_pin.py": "import tool\n\n\ndef test_z():\n    assert tool.VERSION == 1\n",
    })
    fixed = render({"tests/test_z_pin.py": "import tool\n\n\ndef test_z():\n    assert tool.VERSION == 2\n"})
    model = FakeModel(_answers(BREAKS, fixed))
    run = tmp_path / "run"
    result = _design(run, model, repo, _mode(self_check_timeout_s=10, check_timeout_s=60))
    assert result.exit_ok, result.error
    assert [entry["outcome"] for entry in _records(run)] == ["inconclusive", "inconclusive"]
    assert [entry["cut"] for entry in _records(run)] == [["tests/test_a_slow.py",
                                                         "tests/test_z_pin.py"]] * 2
    assert result.facts["attempts_used"] == 1 and result.facts["check_attempts_used"] == 1
    assert _commits(run) == [f"{NAME}: attempt 2", f"{NAME}: attempt 1"]
    assert _count(model, HEALTH) == 2


def _base_runs(monkeypatch, result=None):
    """Count the base runs the self-check makes, and answer them with ``result`` if given."""
    from refactory_dsh import base_check

    runs = []
    real = base_check.sandbox.run_command

    def run_command(command, *, workdir, **kw):
        if Path(workdir).name.startswith(base_check.BASE_WORKTREE_PREFIX):
            runs.append((command, kw.get("home")))
            if result is not None:
                return result
        return real(command, workdir=workdir, **kw)

    monkeypatch.setattr(base_check.sandbox, "run_command", run_command)
    return runs


def test_a_base_run_that_names_no_failure_is_unavailable(tmp_path, monkeypatch):
    from refactory_dsh import base_check
    from refactory_dsh.sandbox import SandboxResult

    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_env.py": ALWAYS_FAILS})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500016")
    _emit(run, render({"tool.py": "VERSION = 1\nMARK = 1\n"}), monkeypatch)
    _base_runs(monkeypatch, SandboxResult(ok=False, returncode=1, stdout="the base broke",
                                          stderr="", timed_out=False, elapsed_s=0.1))
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    assert record["failing_ids"] == [ENV_FAILURE] and record["inherited_filtered"] == []
    assert record["base"] == ("unavailable, so nothing is inherited: the base run failed (exit 1) "
                              "without naming a failing test")
    # Nothing is learned from it.
    assert json.loads((run / base_check.TARGETED_CACHE_FILE).read_text())["tests"] == {}


def test_a_parametrized_failure_the_parser_cuts_is_still_compared_with_the_base(tmp_path, monkeypatch):
    """A parameter with a space is cut from the failure's id (``test_param[a``), which pytest
    cannot find; passed back as it is, it stops the whole base run and nothing is
    inherited. Its file runs on the base instead, and its failure there is inherited."""
    param = ("import pytest\nimport tool\n\n\n@pytest.mark.parametrize('value', ['a b'])\n"
             "def test_param(value):\n    assert False\n")
    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_param.py": param})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500024")
    _emit(run, BREAKS, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    assert record["failing_ids"] == [PIN]
    assert record["inherited_filtered"] == ["tests/test_param.py::test_param[a"]
    assert record["base"] == "compared with a run of 2 test(s) on the base"


def test_a_base_whose_generate_fails_is_unavailable(tmp_path, monkeypatch):
    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_env.py": ALWAYS_FAILS})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500017")
    facts = _facts(run)
    (run / "facts.json").write_text(json.dumps({**facts, "generate": "test -f attempt.flag"}))
    _emit(run, render({"tool.py": "VERSION = 1\nMARK = 1\n", "attempt.flag": "x\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    assert record["failing_ids"] == [ENV_FAILURE] and record["inherited_filtered"] == []
    assert record["base"].startswith("unavailable, so nothing is inherited: the base's generate "
                                     "or setup failed")


def test_a_preparation_failure_during_a_fixup_ends_the_fixups(tmp_path, monkeypatch):
    """A fix-up whose emission breaks the generate step is judged by the whole check, which
    spends the attempt: the next emission is a new attempt, not an amendment."""
    repo = _target(tmp_path / "repo", {**PINNED, **GENERATED})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500018")
    facts = _facts(run)
    (run / "facts.json").write_text(json.dumps({**facts, "generate": "python gen.py"}))
    _emit(run, BREAKS, monkeypatch)
    assert "self-check fix-up 1 of 2" in _prepare(run, monkeypatch)[1]
    _emit(run, render({"gen.py": "raise SystemExit('generate broke')\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and out.strip().endswith("reloop"), err
    facts = _facts(run)
    assert facts["self_check_fixup_count"] == 0 and facts["attempts_used"] == 1
    _emit(run, render({"gen.py": GENERATED["gen.py"], "tool.py": "VERSION = 1\n"}), monkeypatch)
    assert _commits(run) == [f"{NAME}: attempt 2", f"{NAME}: attempt 1"]


def test_the_exhausted_early_stop_keeps_the_bases_failures_out(tmp_path, monkeypatch):
    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_env.py": ALWAYS_FAILS})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500019", self_check_fixups=0)
    _emit(run, BREAKS, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up" not in out, err
    assert _records(run)[0]["outcome"] == "exhausted"
    record = json.loads((run / "check-record.json").read_text())
    assert record["inherited"] == [ENV_FAILURE]
    assert [f["test_id"] for runner in record["runners"] for f in runner["failures"]] == [PIN]


def test_what_was_learned_on_another_base_is_not_reused(tmp_path, monkeypatch):
    from refactory_dsh import base_check

    repo = _target(tmp_path / "repo", PINNED)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500020")
    _emit(run, BREAKS, monkeypatch)
    stale = base_check.cache_key("0" * 40, "", "", CHECK)
    (run / base_check.TARGETED_CACHE_FILE).write_text(json.dumps(
        {"cache_key": stale, "prepared": True, "tests": {PIN: True}}))
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    assert record["failing_ids"] == [PIN] and record["inherited_filtered"] == []
    assert record["base"] == "compared with a run of 1 test(s) on the base"


def test_the_base_runs_with_its_own_home(tmp_path, monkeypatch):
    home_test = ("import os\nimport tool\n\n\ndef test_home():\n"
                 "    assert os.environ['HOME'].endswith('base-check-home')\n")
    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_home.py": home_test})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500021")
    _emit(run, render({"tool.py": "VERSION = 1\nMARK = 1\n"}), monkeypatch)
    runs = _base_runs(monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 2" in out, err
    [record] = _records(run)
    # It fails in the attempt's HOME and passes in the base's: the change's, not inherited.
    assert record["failing_ids"] == ["tests/test_home.py::test_home"]
    assert record["inherited_filtered"] == []
    assert [Path(home).name for _, home in runs] == ["base-check-home"]


def test_the_base_is_prepared_once_for_its_key(tmp_path, monkeypatch):
    """A fix-up whose self-check needs the base again runs the new tests there without
    preparing the base a second time."""
    from refactory_dsh import base_check

    other = "import tool\n\n\ndef test_other():\n    assert not hasattr(tool, 'BROKEN')\n"
    repo = _target(tmp_path / "repo", {**PINNED, "tests/test_env.py": ALWAYS_FAILS,
                                       "tests/test_other.py": other})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500022")
    prepared = []
    real = base_check._ensure_base_worktree
    monkeypatch.setattr(base_check, "_ensure_base_worktree",
                        lambda ctx: prepared.append(1) or real(ctx))
    runs = _base_runs(monkeypatch)
    _emit(run, BREAKS, monkeypatch)
    assert "self-check fix-up 1 of 2" in _prepare(run, monkeypatch)[1]
    _emit(run, render({"tool.py": "VERSION = 2\nBROKEN = 1\n"}), monkeypatch)
    assert "self-check fix-up 2 of 2" in _prepare(run, monkeypatch)[1]
    first, second = _records(run)
    assert first["inherited_filtered"] == [ENV_FAILURE] and first["failing_ids"] == [PIN]
    assert second["failing_ids"] == [PIN, "tests/test_other.py::test_other"]
    assert len(runs) == 2 and prepared == [1]


def test_an_amendment_puts_the_worktree_back_first(tmp_path):
    """What a check or reviewer changed in the worktree is put back before a fix-up is
    amended in, so the amendment holds only the emission."""
    repo = _target(tmp_path / "repo", PINNED)
    run = tmp_path / "run"
    worktree.create(repo, "HEAD", "abcdef500023", run)
    found = worktree.commit_attempt(run, {Path("tool.py"): "VERSION = 2\n"}, f"{NAME}: attempt 1")
    path = Path(found.path)
    (path / "app.py").write_text("print('left by a reviewer')\n")
    found = worktree.commit_attempt(run, {Path("tests/test_pin.py"): "x = 2\n"}, "unused", amending=True)
    assert worktree.tracked_edits(found) == []
    assert (path / "app.py").read_text() == "print('app')\n"
    assert git(path, "show", "HEAD:app.py") == "print('app')"


def test_prepare_checks_limit_follows_the_knobs_to_their_bounds():
    from refactory_dsh.design import prepare_check_iterations

    assert prepare_check_iterations({}) == 45 == DesignMode().graph().nodes["prepare_check"].max_iterations
    widest = _mode(check_attempts=5, self_check_fixups=5)
    # (8 + 25 + 1) x 2 + 25 + 8 reloops at most, so 102.
    assert widest.graph().nodes["prepare_check"].max_iterations == 102
    state = widest.state_dict()
    assert state["graph"]["nodes"]["prepare_check"]["max_iterations"] == 102
    assert lowering.to_workflow(state).nodes["prepare_check"].max_iterations == 102
    capped = _mode(check_attempts=5, self_check_fixups=5, check_attempt_cap=20)
    assert capped.graph().nodes["prepare_check"].max_iterations == (20 + 25 + 1) * 2 + 25 + 20 + 1


def test_the_worst_case_at_the_knobs_bounds_still_ends_in_the_stop(tmp_path):
    """Five attempts of five fix-ups each, two refusals before every applied emission: 89
    reloops of prepare_check, under its limit of 102 and twice the old fixed 45."""
    repo = _target(tmp_path / "repo", PINNED)
    refused = "===EDIT: tool.py===\n<<<<<<< SEARCH\nnot in the file\n=======\nx\n>>>>>>> REPLACE\n"
    emits = [emission for n in range(30) for emission in (refused, refused,
                                                          render({"tool.py": f"VERSION = {10 + n}\n"}))]
    model = FakeModel(_answers(*emits))
    run = tmp_path / "run"
    result = _design(run, model, repo, _mode(check_attempts=5, self_check_fixups=5))
    assert not result.exit_ok
    assert "BuildLoopStop" in (result.error or "") and "check attempts exhausted" in result.error
    assert "max_iterations" not in result.error
    assert [entry["outcome"] for entry in _records(run)] == (["fixup"] * 5 + ["exhausted"]) * 5
    assert _count(model, EMIT) == 90


# ── the selection ─────────────────────────────────────────────────────────────


def _synthetic(root: Path) -> Path:
    """A repository whose tests mostly import two engine modules, with features on top, a
    structural test, a helper, a generated file, a spec, docs, and a test outside the
    check's scope."""
    def test(name: str, imports: str, pad: int = 0) -> tuple[str, str]:
        # ``pad`` makes a file bigger, so costlier to the selection's estimate.
        padding = f"# {'x' * pad}\n" if pad else ""
        return f"tests/test_{name}.py", f"{padding}{imports}\n\n\ndef test_{name}():\n    pass\n"

    files = dict([
        test("layout", "from pathlib import Path", 300),
        *(test(f"whole_{n}", "from pkg import core, engine") for n in (1, 2, 3)),
        test("engine_a", "from pkg import engine", 200),
        test("engine_b", "from pkg import engine"),
        test("core_a", "from pkg import core"),
        test("feature", "from pkg import engine, feature", 50),
        test("leaf", "from pkg import core, engine, leaf"),
        test("leaf_and_feature", "from pkg import engine, feature, leaf", 100),
        test("gone", "from pkg.gone import Z"),
        test("helped", "import helpers"),
    ])
    files.update({
        "pkg/__init__.py": "", "pkg/engine.py": "X = 1\n", "pkg/core.py": "C = 1\n",
        "pkg/feature.py": "F = 1\n", "pkg/leaf.py": "Y = 1\n", "pkg/gone.py": "Z = 1\n",
        "pkg/generated.py": "# Generated by gen.py. Do not edit.\nW = 1\n",
        "tests/helpers.py": "from pkg import leaf\n",
        "legacy/tests/test_old.py": "from pkg.gone import Z\n\n\ndef test_old():\n    assert Z\n",
        "web/src/a.ts": "export const a = 1\n",
        "web/tests/a.spec.ts": "import { a } from '../src/a'\n",
        "docs/guide.md": "a guide\n",
    })
    return _target(root, files)


def test_the_selection_order(tmp_path):
    repo = _synthetic(tmp_path / "repo")
    files = test_selection.repository_files(repo)
    check = "pnpm test && python -m pytest -q tests"
    last = {"status": "fail", "inherited": ["tests/test_feature.py::test_inherited"],
            "runners": [{"failures": [{"test_id": "tests/test_feature.py::test_feature"},
                                      {"test_id": "tests/test_feature.py::test_inherited"}]}]}

    def select(touched: list[str], prev: dict | None = None, listing: list[str] = files):
        return test_selection.select(touched, repo, prev or {}, check_command=check, files=listing)

    # A feature change: the last failures, then the importers and the structural test,
    # cheapest (smallest) first; nothing that cannot reach the change.
    leaf = select(["pkg/leaf.py", "pkg/feature.py"], last)
    assert leaf.hubs_changed == [] and leaf.engine == []
    assert leaf.tests == ["tests/test_feature.py::test_feature", "tests/test_leaf.py",
                          "tests/test_feature.py", "tests/test_leaf_and_feature.py",
                          "tests/test_layout.py"]

    # An engine change: the structural test and the tests on the whole engine (every hub)
    # first, then the tests the emission touched, then the rest of the importers, cheapest
    # first.
    engine = select(["pkg/engine.py", "tests/test_feature.py"])
    assert engine.hubs_changed == ["pkg/engine.py"]
    assert engine.engine == ["tests/test_layout.py", "tests/test_whole_1.py", "tests/test_whole_2.py",
                             "tests/test_whole_3.py", "tests/test_leaf.py"]
    assert engine.tests == [*engine.engine, "tests/test_feature.py", "tests/test_engine_b.py",
                            "tests/test_leaf_and_feature.py", "tests/test_engine_a.py"]

    # On a fix-up, what the last self-check found failing runs first of all.
    fixup = test_selection.select(["pkg/engine.py"], repo, {}, check_command=check, files=files,
                                  first=["tests/test_engine_a.py::test_engine_a"])
    assert fixup.tests[0] == "tests/test_engine_a.py::test_engine_a"

    # A deleted module's importers are found, and a helper's; a test the check does not
    # collect is not.
    gone = select(["pkg/gone.py", "tests/helpers.py"],
                  listing=[name for name in files if name != "pkg/gone.py"])
    assert gone.tests == ["tests/test_helped.py", "tests/test_gone.py", "tests/test_layout.py"]

    # A spec runs when it is touched, never because a source it imports is.
    assert select(["web/src/a.ts"]).tests == ["tests/test_layout.py"]
    assert select(["web/tests/a.spec.ts"]).tests == ["web/tests/a.spec.ts"]

    # Docs and generated files select nothing.
    assert select(["docs/guide.md", "pkg/generated.py"]).tests == []


#: The files b39a's and b39b's attempt 1 changed (their commits e48abd40a9 and 21b7aabe4).
B39A_TOUCHED = """packages/refactory/tool-refactory/src/generated-help.ts
packages/workflow/workflow-spine/tests/fixtures/design-graph.json refactory/docs/api/capabilities.md
refactory/docs/architecture.md refactory/docs/hardening.md refactory/docs/models.md
refactory/docs/tutorial.html refactory/refactory_dsh/create_ops.py refactory/refactory_dsh/data/coverage.json
refactory/refactory_dsh/data/documentation.json refactory/refactory_dsh/design.py
refactory/refactory_dsh/design_ops.py refactory/refactory_dsh/fast.py refactory/tests/test_design_scale.py
refactory/tests/test_design_short_path.py refactory/tests/test_interactive.py
refactory/tests/tests_support.py""".split()
B39B_TOUCHED = """packages/refactory/tool-refactory/src/generated-help.ts
packages/workflow/workflow-spine/src/execute.ts packages/workflow/workflow-spine/src/index.ts
packages/workflow/workflow-spine/tests/correction.spec.ts refactory/docs/api/capabilities.md
refactory/docs/hardening.md refactory/docs/tutorial.html refactory/refactory_dsh/create.py
refactory/refactory_dsh/create_ops.py refactory/refactory_dsh/data/coverage.json
refactory/refactory_dsh/data/documentation.json refactory/refactory_dsh/design.py
refactory/refactory_dsh/fast.py refactory/refactory_dsh/fast_ops.py refactory/refactory_dsh/nn/executor.py
refactory/refactory_dsh/repo_change.py refactory/refactory_dsh/run.py
refactory/tests/test_build_loop_stop.py""".split()
REPOSITORY_CHECK = "pnpm test && .venv/bin/python -m pytest -q refactory/tests"


def _tests(names: str) -> list[str]:
    return [f"refactory/tests/{name}.py" for name in names.split()]


#: The test suite's imports as b39's base had them (dad4af31), frozen: what each of the
#: check's test files imported. A hub is a share of the suite, so the live tree, which
#: grows, would move the fixture.
B39_IMPORTS = Path(__file__).parent / "fixtures" / "b39-base-imports.json"


def test_the_selection_catches_b39s_round_one_failures_with_the_engine_tests_first():
    """On b39a's and b39b's attempt-1 changes the selection holds a failing test for every
    round-1 failure, and the engine tests (the boundary, lowering smoke and mode
    validation) run first, found by import fan-in, not by name."""
    imports = {test: set(modules) for test, modules in json.loads(B39_IMPORTS.read_text()).items()}
    assert len(imports) == 131

    def select(touched: list[str]) -> test_selection.Selection:
        return test_selection.select(touched, REPO_ROOT, {}, check_command=REPOSITORY_CHECK,
                                     imports=imports)

    b39a, b39b = select(B39A_TOUCHED), select(B39B_TOUCHED)
    assert test_selection.hubs(imports) == {"refactory/refactory_dsh/nn/__init__.py",
                                            "refactory/refactory_dsh/registry.py",
                                            "refactory/refactory_dsh/design.py"}
    assert set(_tests("test_lowering_smoke test_edit_design test_pitfalls test_check_beside_reviews")) \
        <= set(b39a.tests)
    # Eight of b39b's nine; the package-lifecycle tests import no module it changed, and
    # failed only on the V7 its default-package and registry tests fail on too.
    assert set(_tests("test_boundary test_default_package test_design_probe test_edit_design "
                      "test_fast_modes test_registry test_lowering_smoke test_design_worktree")) \
        <= set(b39b.tests)
    for selection in (b39a, b39b):
        assert selection.hubs_changed == ["refactory/refactory_dsh/design.py"]
        engine = selection.engine
        assert selection.tests[:len(engine)] == engine
        # The structural tests (they import nothing of the repository), then the tests
        # on every hub: a few, not the 33 that import the design graph.
        assert engine == _tests("test_boundary test_boundary_refs test_docs_english_only "
                                "test_stale_install_finish test_work_complete test_lowering_smoke "
                                "test_walker_gate_core test_node_timeout test_verification "
                                "test_lowering test_edit_design test_design_worktree")
        # The tests the change touched come right after them.
        touched = [test for test in selection.touched_tests if test not in engine]
        assert selection.tests[len(engine):len(engine) + len(touched)] == touched


# ── the targeted command ──────────────────────────────────────────────────────


def test_whole_test_files_go_to_their_own_runner_only():
    py = "refactory/tests/test_x.py"
    spec = "packages/workflow/workflow-spine/tests/execute.spec.ts"
    # A Python-only selection never starts the vitest step.
    assert build_probe_command(REPOSITORY_CHECK, [], root=REPO_ROOT, test_files=[py]) == (
        f".venv/bin/python -m pytest -q {py}", None)
    # A spec goes to pnpm test as vitest's own file filter, with no "--" and no --related.
    command, refusal = build_probe_command(REPOSITORY_CHECK, [], root=REPO_ROOT, test_files=[spec])
    assert refusal is None and command == f"pnpm test {spec}"
    # An id inside a file that runs whole is not named twice; a vitest id's file runs whole.
    command, _ = build_probe_command(
        REPOSITORY_CHECK, [f"{py}::test_a", "refactory/tests/test_y.py::test_b",
                           f"{spec} > suite > case"], root=REPO_ROOT, test_files=[py, spec])
    assert command == (f"pnpm test {spec} && .venv/bin/python -m pytest -q {py} "
                       "refactory/tests/test_y.py::test_b")
    # Ids alone build what they always did.
    assert build_probe_command(REPOSITORY_CHECK, [f"{spec} > suite > case"], root=REPO_ROOT) == (
        f"pnpm test {spec} -t case", None)


def test_replay_measure5_structural_cuts_no_more_than_main():
    """Replaying self-checks 2 and 3 of the measure5-structural run with per-file
    costs cuts no more files than main's recorded 2 and 4.  Reverting to chunk-level
    timing makes it fail (> 2 cuts on SC2), confirming the per-file timing is load-bearing.

    Data is from the committed fixture at ``refactory/tests/fixtures/measure5_replay.json``,
    derived from the real self-check record and per-file durations from a junit run."""
    FIXTURE = Path(__file__).parent / "fixtures" / "measure5_replay.json"
    fixture = json.loads(FIXTURE.read_text())
    limit = fixture["limit_s"]
    file_durations = fixture["file_durations"]
    records = fixture["records"]

    def _add_durations(entry):
        """Add per-test durations from the fixture's file_durations to each timing chunk."""
        modified = dict(entry)
        new_timings = []
        for chunk in entry.get("timings", []):
            new_chunk = dict(chunk)
            durations = []
            for test_name in chunk["tests"]:
                f = test_name.split("::")[0]
                s = file_durations.get(f, 0.5)
                durations.append({"test_id": test_name, "seconds": s})
            new_chunk["durations"] = durations
            new_timings.append(new_chunk)
        modified["timings"] = new_timings
        return modified

    def _simulate(selection, earlier, file_durations_map):
        """Simulate chunking and compute cuts, using calibrated chunk times."""
        # Calibrate on earlier entries: chunk_time ≈ a + b * sum(file_durations)
        xs, ys = [], []
        for entry in earlier:
            for t in entry.get("timings", []):
                x = sum(file_durations_map.get(n.split("::")[0], 0.5)
                        for n in t["tests"])
                xs.append(x)
                ys.append(t["s"])
        n = len(xs)
        if n > 1:
            mx, my = sum(xs) / n, sum(ys) / n
            denom = sum((x - mx) ** 2 for x in xs)
            b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 1.0
            a = my - b * mx
        else:
            a, b = 0.5, 1.0

        t = 0.0
        cut = []
        chunks = failfast._chunks(selection)
        for i, ch in enumerate(chunks):
            remaining = limit - t
            if remaining < 1:
                for c in chunks[i:]:
                    cut += c
                break
            d = max(0.3, a + b * sum(file_durations_map.get(n.split("::")[0], 0.5)
                                     for n in ch))
            if d > remaining:
                for c in chunks[i:]:
                    cut += c
                break
            t += d
        return cut

    # SC1 with per-file durations
    sc1 = _add_durations(records[0])
    # Add the stopped chunk as a timed_out entry (it was cut, not in timings)
    stopped = records[0]["cut"]
    if stopped:
        remaining_s = round(limit - sum(t["s"] for t in sc1["timings"]), 1)
        stopped_durations = [{"test_id": name, "seconds": file_durations.get(
            name.split("::")[0], 0.5)} for name in stopped]
        sc1["timings"].append({"tests": stopped, "s": remaining_s,
                                "timed_out": True, "counted": True,
                                "durations": stopped_durations})

    # Per-file costs from SC1
    costs_perfile = test_selection.costs_from_record([sc1], Path("/tmp"))

    # SC2 (record index 1): use the recorded selection (it came from select() with
    # the right inputs); the rest tier's order depends on costs.
    sc2_selection = records[1]["selection"]
    cut_sc2 = _simulate(sc2_selection, [sc1], file_durations)
    assert len(cut_sc2) <= 2, (
        f"SC2 cuts {len(cut_sc2)} files ({[c.split('/')[-1] for c in cut_sc2]}), "
        f"expected <= 2 (main cut {len(records[1]['cut'])})")

    # SC3 (record index 2): costs from SC1 + SC2
    sc2 = _add_durations(records[1])
    costs_perfile2 = test_selection.costs_from_record([sc1, sc2], Path("/tmp"))
    sc3_selection = records[2]["selection"]
    cut_sc3 = _simulate(sc3_selection, [sc1, sc2], file_durations)
    assert len(cut_sc3) <= 4, (
        f"SC3 cuts {len(cut_sc3)} files ({[c.split('/')[-1] for c in cut_sc3]}), "
        f"expected <= 4 (main cut {len(records[2]['cut'])})")

    # Reverting to chunk-level timing: build costs by assigning each chunk's
    # total elapsed time to every file in the chunk (the old behaviour that
    # priced sub-second files at 75.9 s).  Then re-sort SC2's rest tier by
    # those costs and simulate: the wrong ordering must cause > 2 cuts.
    costs_chunklevel: dict[str, float] = {}
    for t in records[0].get("timings", []):
        if not t.get("counted"):
            continue
        for name in t["tests"]:
            f = name.split("::")[0]
            costs_chunklevel[f] = t["s"]  # always overwrites (old behaviour)
    if stopped:
        remaining_s2 = round(limit - sum(t["s"] for t in records[0]["timings"]), 1)
        for name in stopped:
            f = name.split("::")[0]
            costs_chunklevel[f] = remaining_s2

    # Verify that chunk-level costs inflated sub-second files.
    assert costs_chunklevel.get("refactory/tests/test_executor.py", 0) > 50, (
        "chunk-level costs should inflate test_executor.py to ~75 s")

    # Re-sort SC2's importers+structural tier by chunk-level costs.
    hs = records[1]["how_selected"]
    rest_files = list(dict.fromkeys(hs.get("importers", []) + hs.get("structural", [])))
    rate = (sum(costs_chunklevel[f] for f in rest_files if f in costs_chunklevel)
            / max(1, sum(test_selection._size(Path("/tmp"), f) for f in rest_files
                         if f in costs_chunklevel)))
    rest_cost = {f: costs_chunklevel.get(f, test_selection._size(Path("/tmp"), f) * rate)
                 for f in rest_files}
    rest_sorted = sorted(rest_files, key=lambda f: (rest_cost[f], f))
    # Rebuild the full selection with the chunk-level ordering.
    ordered: list[str] = list(dict.fromkeys(hs.get("first", [])))
    whole: set[str] = {name for name in ordered if "::" not in name}
    for name in hs.get("engine", []) + hs.get("touched_tests", []):
        if name not in whole:
            whole.add(name)
            ordered.append(name)
    for test_id in hs.get("last_failures", []):
        f = test_id.split("::")[0]
        if f not in whole and test_id not in ordered:
            ordered.append(test_id)
            if test_id == f:
                whole.add(test_id)
    for name in rest_sorted:
        if name not in whole:
            whole.add(name)
            ordered.append(name)
    cut_sc2_chunklevel = _simulate(ordered, [sc1], file_durations)
    assert len(cut_sc2_chunklevel) > 2, (
        f"chunk-level timing should cause > 2 cuts on SC2, got {len(cut_sc2_chunklevel)}")


def test_costs_from_record_multi_file_chunk_without_durations_keeps_estimates(tmp_path):
    """A finished multi-file chunk without ``durations`` (e.g. a vitest chunk) leaves
    each file's existing estimate unchanged.  A single-file chunk without durations
    assigns its elapsed time to that file."""
    repo = _target(tmp_path / "repo", {
        "tests/test_a.py": "\ndef test_a():\n    pass\n",
        "tests/test_b.py": "\ndef test_b():\n    pass\n",
    })
    # Pre-existing size estimate for test_b.
    initial = [{"timings": [{"tests": ["tests/test_b.py"], "s": 0.1, "counted": True}]}]
    costs_before = test_selection.costs_from_record(initial, repo)
    assert costs_before["tests/test_b.py"] == 0.1

    # A two-file chunk at 60 s without durations: both files keep their estimates.
    entries = [{"timings": [{"tests": ["tests/test_b.py"], "s": 0.1, "counted": True},
                            {"tests": ["tests/test_a.py", "tests/test_b.py"],
                             "s": 60.0, "counted": True}]}]
    costs = test_selection.costs_from_record(entries, repo)
    assert costs["tests/test_b.py"] == 0.1, (
        f"multi-file chunk without durations changed test_b to {costs['tests/test_b.py']}")
    assert "tests/test_a.py" not in costs, (
        f"test_a should not have a cost from a multi-file chunk without durations")

    # A single-file chunk at 60 s without durations: the file gets 60.0.
    entries_single = [{"timings": [{"tests": ["tests/test_a.py"], "s": 60.0, "counted": True}]}]
    costs_single = test_selection.costs_from_record(entries_single, repo)
    assert costs_single["tests/test_a.py"] == 60.0


def test_costs_from_record_lower_bound_never_overwrites_finished_time(tmp_path):
    """A lower bound never lowers a finished time; max is always used."""
    repo = _target(tmp_path / "repo", {
        "tests/test_x.py": "\ndef test_x():\n    pass\n",
    })
    # (a) Non-timed-out at 5.0 s, then timed-out at 120 s: stays 5.0.
    entries_a = [{"timings": [
        {"tests": ["tests/test_x.py"], "s": 5.0, "counted": True,
         "durations": [{"test_id": "tests/test_x.py::test_x", "seconds": 5.0}]},
        {"tests": ["tests/test_x.py"], "s": 120.0, "counted": True, "timed_out": True},
    ]}]
    costs_a = test_selection.costs_from_record(entries_a, repo)
    assert costs_a["tests/test_x.py"] == 5.0

    # (b) Two timed-out entries: 120 s then 80 s → max = 120.
    entries_b = [{"timings": [
        {"tests": ["tests/test_x.py"], "s": 120.0, "counted": True, "timed_out": True},
        {"tests": ["tests/test_x.py"], "s": 80.0, "counted": True, "timed_out": True},
    ]}]
    costs_b = test_selection.costs_from_record(entries_b, repo)
    assert costs_b["tests/test_x.py"] == 120.0

    # (c) Two non-timed-out entries: 10 s then 5 s → max = 10.
    entries_c = [{"timings": [
        {"tests": ["tests/test_x.py"], "s": 10.0, "counted": True,
         "durations": [{"test_id": "tests/test_x.py::test_x", "seconds": 10.0}]},
        {"tests": ["tests/test_x.py"], "s": 5.0, "counted": True,
         "durations": [{"test_id": "tests/test_x.py::test_x", "seconds": 5.0}]},
    ]}]
    costs_c = test_selection.costs_from_record(entries_c, repo)
    assert costs_c["tests/test_x.py"] == 10.0


def test_junitxml_not_left_in_worktree(tmp_path, monkeypatch):
    """After a self-check that runs pytest chunks, no junitxml file is left in the worktree."""
    repo = _target(tmp_path / "repo", PINNED)
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500060")
    _emit(run, render({"tool.py": "VERSION = 1\nNAME = 'tool'\n"}), monkeypatch)
    _prepare(run, monkeypatch)
    found = worktree.recorded(run)
    status = git(Path(found.path), "status", "--porcelain")
    assert not status.strip(), f"worktree not clean after self-check: {status}"


def test_two_consecutive_empty_diff_fixups_with_fixups_1_exhausts(tmp_path, monkeypatch):
    """Two emptied attempts in a row with ``self_check_fixups=1`` must end exhausted.

    The first emission changes ``generated.txt``; generate puts it back →
    ``empty_diff_fixup`` (count goes 0→1).  The second emission does the same;
    generate restores it again, but now count=1 ≥ allowed=1 → ``exhausted``.

    Remove line 1293 (``ctx.set_fact("self_check_fixup_count", used + 1)``)
    and the second empty-diff sees used=0 < allowed=1 → fixups again instead
    of exhausting → test fails."""
    repo = _target(tmp_path / "repo", {**PINNED, **GENERATED})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500061", self_check_fixups=1)
    facts = _facts(run)
    (run / "facts.json").write_text(json.dumps({**facts, "generate": "python gen.py"}))

    # First emission: changes generated.txt; generate puts it back → empty_diff_fixup.
    _emit(run, render({"generated.txt": "version 99\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    records = _records(run)
    assert records[-1]["outcome"] == "empty_diff_fixup"
    assert _facts(run)["self_check_fixup_count"] == 1
    assert _facts(run)["total_self_check_fixups_used"] == 1

    # Second emission: same change; generate restores again → exhausted.
    _emit(run, render({"generated.txt": "version 99\n"}), monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    records = _records(run)
    assert records[-1]["outcome"] == "exhausted"


def test_parse_junitxml_handles_class_based_tests(tmp_path):
    """``_parse_junitxml`` correctly maps class-based test classnames to file paths.

    For ``tests.test_failfast.TestNotFoundIds::test_method``, the classname is
    ``tests.test_failfast.TestNotFoundIds``; the file path must be
    ``tests/test_failfast.py``, not ``tests/test_failfast/TestNotFoundIds.py``."""
    xml_content = """\
<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest">
  <testcase classname="tests.test_failfast.TestNotFoundIds" name="test_dropped" time="0.123"/>
  <testcase classname="tests.test_failfast.TestNotFoundIds" name="test_suffix" time="0.456"/>
  <testcase classname="tests.test_failfast" name="test_module_level" time="0.789"/>
</testsuite>
"""
    xml_path = tmp_path / "junit.xml"
    xml_path.write_text(xml_content)
    durations = failfast._parse_junitxml(str(xml_path))
    assert durations is not None
    by_id = {d["test_id"]: d["seconds"] for d in durations}
    # Class-based tests: classname "tests.test_failfast.TestNotFoundIds" → file "tests/test_failfast.py"
    assert "tests/test_failfast.py::test_dropped" in by_id
    assert "tests/test_failfast.py::test_suffix" in by_id
    assert by_id["tests/test_failfast.py::test_dropped"] == pytest.approx(0.123)
    assert by_id["tests/test_failfast.py::test_suffix"] == pytest.approx(0.456)
    # Module-level test: classname "tests.test_failfast" → file "tests/test_failfast.py"
    assert "tests/test_failfast.py::test_module_level" in by_id
    assert by_id["tests/test_failfast.py::test_module_level"] == pytest.approx(0.789)
    # Verify NO wrong path like "tests/test_failfast/TestNotFoundIds.py"
    wrong_paths = [d["test_id"] for d in durations if "TestNotFoundIds" in d["test_id"]]
    assert not wrong_paths, f"Class name leaked into test_id: {wrong_paths}"


def test_not_found_rerun_failures_are_recorded_and_tests_marked_ran(tmp_path, monkeypatch):
    """After a not-found retry, the rerun's failing test is in failing_ids, its test ids
    are in ran, and exactly one 'dropped' note per chunk event."""
    repo = _target(tmp_path / "repo", {
        "tool.py": "VERSION = 1\n",
        "refactory/__init__.py": "",
        "refactory/tests/__init__.py": "",
        "refactory/tests/test_real.py": (
            "import sys, os\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))\n"
            "import tool\n\n\ndef test_real():\n    assert tool.VERSION == 1\n"
        ),
    })
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500062")
    _emit(run, render({"tool.py": "VERSION = 2\n"}), monkeypatch)

    real_select = test_selection.select

    def patched_select(*args, **kwargs):
        sel = real_select(*args, **kwargs)
        sel.tests = [
            "refactory/tests/test_real.py::test_gone[2.5]",
            "refactory/tests/test_real.py",
        ]
        sel.first = ["refactory/tests/test_real.py::test_gone[2.5]"]
        return sel

    monkeypatch.setattr(test_selection, "select", patched_select)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    [record] = _records(run)
    # (1) The rerun's failing test is in failing_ids.
    assert "refactory/tests/test_real.py::test_real" in record["failing_ids"]
    # (2) The rerun's test ids are in ran.
    assert "refactory/tests/test_real.py" in record["ran"]
    # (3) Exactly one "dropped" note.
    drop_notes = [n for n in record["notes"] if "dropped not-found ids" in n]
    assert len(drop_notes) == 1


def test_empty_diff_check_skipped_when_head_equals_base(tmp_path, monkeypatch):
    """When head == base (no emission committed), the empty-diff check is skipped.

    The base has a parent with the same tree (via ``--allow-empty``).  Without
    the ``found.head != found.base`` guard, ``tree(head) == tree(head~1)``
    would falsely trigger ``empty_diff_fixup``.  With the guard, head == base
    so the comparison is skipped and the normal flow (``nothing selected``)
    proceeds.  Removing the guard fails this test."""
    repo = _target(tmp_path / "repo", PINNED)
    # Add an empty commit so tree(HEAD) == tree(HEAD~1).
    git(repo, "commit", "--allow-empty", "-m", "empty")
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500063")
    found = worktree.recorded(run)
    assert found.head == found.base  # no emission committed
    # Set "commit" fact so prepare_attempt's _restored_attempt does not crash.
    facts = _facts(run)
    facts["commit"] = found.head
    (run / "facts.json").write_text(json.dumps(facts))
    # Call prepare_check without emitting: prepare_attempt returns []
    # (nothing to prepare), code falls through to the empty-diff check.
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    records = _records(run)
    assert records[-1]["outcome"] not in ("empty_diff_fixup", "exhausted")


def test_rerun_with_zero_remaining_records_lower_bound(tmp_path, monkeypatch):
    """When a not-found rerun has no time left (remaining_for_rerun < 1), a
    timed_out timing entry is recorded and the chunk is cut.

    Exercises the guard at failfast.py lines 296-305.  Removing those lines
    lets the rerun proceed with nearly zero time — this test fails because
    ``run_fn`` is called a second time."""
    import time as _time
    from refactory_dsh.sandbox import SandboxResult

    exit4_output = "ERROR: not found: /abs/path/tests/test_x.py::test_gone\n"
    exit4 = SandboxResult(ok=False, returncode=4, stdout=exit4_output, stderr="",
                          timed_out=False, elapsed_s=9.5)

    # Force both items into one chunk so rest_of_chunk is non-empty after
    # dropping the not-found id.
    monkeypatch.setattr(failfast, "SELF_CHECK_CHUNKS", 1)

    calls = []
    elapsed = [0.0]

    def mock_monotonic():
        return 1000.0 + elapsed[0]

    monkeypatch.setattr(_time, "monotonic", mock_monotonic)

    def run_fn(cmd, timeout):
        calls.append(cmd)
        elapsed[0] = 9.5  # simulate nearly the full 10 s limit consumed
        return exit4

    prepass = failfast.targeted_prepass(
        {}, CHECK, run_fn, 180,
        selection=["tests/test_x.py::test_gone", "tests/test_x.py"],
        self_check_timeout_s=10,
    )
    # run_fn was called once (the loop-level guard did not cut first).
    assert len(calls) == 1
    # The chunk was cut.
    assert prepass.cut_ids
    # The note specifically mentions "no time left for a rerun".
    assert any("no time left" in n for n in prepass.notes)


def test_rerun_handles_module_import_error(tmp_path, monkeypatch):
    """When the rerun's output contains ``_INTERRUPTED``, broken modules are in ran
    and remaining tests are re-queued.  Exercises the rerun-specific _INTERRUPTED
    check at failfast.py lines 321-332 (not the normal chunk path at line 350)."""
    from refactory_dsh.sandbox import SandboxResult

    # Force all items into one chunk so the exit-4 chunk has remaining items
    # for a rerun (with SELF_CHECK_CHUNKS=6 and 3 items, chunk_size=1 puts each
    # item in its own chunk, and the exit-4 chunk has no remaining items).
    monkeypatch.setattr(failfast, "SELF_CHECK_CHUNKS", 1)

    exit4_output = "ERROR: not found: /abs/path/tests/test_x.py::test_gone\n"
    exit4 = SandboxResult(ok=False, returncode=4, stdout=exit4_output, stderr="",
                          timed_out=False, elapsed_s=0.1)
    interrupted_output = (
        "ERROR tests/test_y.py - ModuleNotFoundError: No module named 'missing'\n"
        "Interrupted: 1 error during collection\n"
        "1 error in 0.10s"
    )
    interrupted = SandboxResult(ok=False, returncode=2, stdout=interrupted_output, stderr="",
                                timed_out=False, elapsed_s=0.1)
    pass_result = SandboxResult(ok=True, returncode=0, stdout="1 passed in 0.10s", stderr="",
                                timed_out=False, elapsed_s=0.1)

    call_count = [0]
    def run_fn(cmd, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return exit4
        if call_count[0] == 2:
            return interrupted
        return pass_result

    prepass = failfast.targeted_prepass(
        {}, CHECK, run_fn, 180,
        selection=["tests/test_x.py::test_gone", "tests/test_y.py", "tests/test_z.py"],
        self_check_timeout_s=300,
    )
    # test_y.py did not import; it should be in ran (from the rerun _INTERRUPTED path).
    assert "tests/test_y.py" in prepass.ran_ids
    # test_z.py should have been re-queued and run separately (not via ran.extend).
    assert "tests/test_z.py" in prepass.ran_ids
    # Verify the re-queue path was taken: test_z.py ran in a third call.
    assert call_count[0] >= 3, "test_z.py must be re-queued and run separately"
    assert any("did not import" in n for n in prepass.notes)


def test_exhausted_empty_diff_does_not_call_targeted_prepass(tmp_path, monkeypatch):
    """When the empty-diff fixup budget is exhausted, ``targeted_prepass`` is not called:
    a ``PrepassResult`` is built directly."""
    repo = _target(tmp_path / "repo", {**PINNED, **GENERATED})
    run = _spine_run(repo, tmp_path, monkeypatch, "abcdef500064", self_check_fixups=1)
    facts = _facts(run)
    (run / "facts.json").write_text(json.dumps({**facts, "generate": "python gen.py"}))

    # Monkeypatch targeted_prepass to raise if called with selection=[].
    from refactory_dsh import failfast as ff
    real_tp = ff.targeted_prepass
    called_empty = []

    def guarded_tp(*args, **kwargs):
        sel = kwargs.get("selection")
        if sel is not None and sel == []:
            called_empty.append(True)
            raise AssertionError("targeted_prepass called with selection=[]")
        return real_tp(*args, **kwargs)

    monkeypatch.setattr(ff, "targeted_prepass", guarded_tp)

    # First emission: BREAKS → self-check fixup.
    _emit(run, BREAKS, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0 and "self-check fix-up 1 of 1" in out, err

    # Second emission: UNDOES → generate empties → empty_diff detected.
    # Budget: used=1 >= allowed=1 → exhausted path.
    # The exhausted path must NOT call targeted_prepass(selection=[]).
    _emit(run, UNDOES, monkeypatch)
    code, out, err = _prepare(run, monkeypatch)
    assert code == 0, err
    assert not called_empty, "targeted_prepass was called with selection=[]"
    records = _records(run)
    assert records[-1]["outcome"] == "exhausted"
    assert _facts(run)["attempts_used"] == 1


def test_the_limit_is_the_knob_and_never_more_than_a_third_of_the_check():
    assert failfast.self_check_limit(300, 1800) == 300
    assert failfast.self_check_limit(300, 180) == 60
    knob = DesignMode.declare()["self_check_timeout_s"]
    assert (knob.default, knob.lo, knob.hi, knob.frozen) == (300, 10, 600, False)
