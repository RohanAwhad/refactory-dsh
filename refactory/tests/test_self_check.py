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


def test_a_fixup_that_generate_turns_into_no_change_is_kept_not_a_crash(tmp_path):
    """The fix-up reverts the source but not the file generate wrote from it; generate then
    writes that file back too, so its amend would empty the attempt. It is kept, as an
    attempt that changes nothing, and goes on to its check and reviews."""
    repo = _target(tmp_path / "repo", {**PINNED, **GENERATED})
    model = FakeModel(_answers(BREAKS, UNDOES))
    run = tmp_path / "run"
    result = _design(run, model, repo, generate="python gen.py")
    assert result.exit_ok, result.error
    assert [entry["outcome"] for entry in _records(run)] == ["fixup", "pass"]
    found = worktree.recorded(run)
    assert _commits(run) == [f"{NAME}: attempt 1"]
    assert git(Path(found.path), "diff", "--stat", found.base, "HEAD") == ""


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


def test_timed_costs_order_the_rest(tmp_path):
    """A file an earlier self-check timed costs what it took; untimed files cost their size."""
    repo = _target(tmp_path / "repo", {
        "m1.py": "X = 1\n", "m2.py": "X = 1\n",
        "tests/test_big_fast.py": "import m1\n" + "# padding\n" * 300 + "\n\ndef test_a():\n    pass\n",
        "tests/test_small_slow.py": "import m2\n\n\ndef test_b():\n    pass\n",
    })
    files = test_selection.repository_files(repo)
    by_size = test_selection.select(["m1.py", "m2.py"], repo, {}, check_command=CHECK, files=files)
    assert by_size.tests == ["tests/test_small_slow.py", "tests/test_big_fast.py"]
    earlier = [{"timings": [{"tests": ["tests/test_small_slow.py"], "s": 30.0, "counted": True},
                            {"tests": ["tests/test_big_fast.py::test_a"], "s": 0.5, "counted": True},
                            {"tests": ["tests/test_big_fast.py"], "s": 99.0, "counted": False}]}]
    costs = test_selection.costs_from_record(earlier, repo)
    assert costs == {"tests/test_small_slow.py": 30.0, "tests/test_big_fast.py": 0.5}
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


def test_the_limit_is_the_knob_and_never_more_than_a_third_of_the_check():
    assert failfast.self_check_limit(300, 1800) == 300
    assert failfast.self_check_limit(300, 180) == 60
    knob = DesignMode.declare()["self_check_timeout_s"]
    assert (knob.default, knob.lo, knob.hi, knob.frozen) == (300, 10, 600, False)
