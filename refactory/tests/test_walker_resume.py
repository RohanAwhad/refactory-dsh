"""A walked run resumes as the spine's does: validated, and acknowledged (dm-160, #436 gap 9).

The walker's progress outcome is mapped to the spine's checkpoint status, and the spine's
own checks then run, with its messages: a failed boundary or one whose last node's outcome
is unknown needs an acknowledgement, a dispatched worker's unknown outcome must be set
aside first, and a declared input or output, or the prepared configuration, that changed
since is refused. ``--acknowledge-failure`` on the walker's mains is the acknowledgement:
it sets aside what the boundary cannot vouch for, as `graph-control acknowledge` does on
the spine, and is consumed by the resume it lets through. A run drives the real
`design.main` with a scripted model; the record it leaves is changed the way a crash, a
failure or an edit would change it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from refactory_dsh import console, design, graph_control, recovery
from test_walker_gate_core import POST_GATE_PROCEED, PRE_GATE, _patch_model
from tests_support import DESIGN_DOC_ANSWER, FakeModel

#: A design run plans its delivery by asking gh for its login, so it asks the fake (dm-271).
pytestmark = pytest.mark.usefixtures("fake_gh")

PROGRESS = ".refactory/progress.json"


def _stopped(tmp_path, monkeypatch) -> tuple[Path, FakeModel]:
    """A real walked design run, stopped at its approval gate."""
    run = tmp_path / "probe_run"
    fake = FakeModel(list(PRE_GATE))
    _patch_model(monkeypatch, fake)
    monkeypatch.chdir(tmp_path)
    assert design.main(["--name", "probe_tool", "--brief", "a tool", "--check", "python tool.py",
                        "--no-worktree", "--workdir", str(run)]) == 2
    return run, fake


def _progress(run: Path) -> dict:
    return json.loads((run / PROGRESS).read_text())


def _rewrite(run: Path, **fields) -> None:
    """The progress record as a crash or a failure leaves it."""
    (run / PROGRESS).write_text(json.dumps({**_progress(run), **fields}))


def _resume(run: Path, *flags: str) -> int:
    return design.main(["--resume", str(run), *flags])


def _spine_message(tmp_path: Path, status: str) -> str:
    """What the spine's recovery says about a checkpoint at ``status``."""
    root = (tmp_path / f"spine-{status}").resolve()
    (root / ".refactory").mkdir(parents=True)
    (root / ".refactory/spine-checkpoint.json").write_text(json.dumps(
        {"run_id": str(root), "graph": {"name": "default_pack-design"}, "status": status, "file_fingerprints": {}}))
    with pytest.raises(ValueError) as refused:
        recovery.prepare(root, "default_pack-design", skip_configuration=True)
    return str(refused.value)


def test_each_walker_outcome_is_read_as_the_spines_status():
    assert recovery.WALKER_STATUSES == {
        "pending": "ready", "completed": "done", "failed": "failed", "running": "executing",
        "unknown": "executing", "refused": "refused", "halted": "halted"}


@pytest.mark.parametrize("outcome, status", [("failed", "failed"), ("running", "executing"),
                                             ("unknown", "executing")])
def test_a_failed_or_unknown_boundary_is_refused_with_the_spines_message(outcome, status, tmp_path, monkeypatch,
                                                                         capsys):
    """The same allowed set and the same words as the spine's refusal; only the command it
    names is the walker's own."""
    run, _ = _stopped(tmp_path, monkeypatch)
    capsys.readouterr()
    _rewrite(run, outcome=outcome, error="gate_strategy: boom" if outcome == "failed" else None)
    assert _resume(run) == 2
    said = json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"].removeprefix("ValueError: ")
    walker_command = f"python -m refactory_dsh.design --resume {run} --acknowledge-failure"
    assert said.endswith(f"\n  {walker_command}"), said
    spine = _spine_message(tmp_path, status)
    assert said.rsplit("\n", 1)[0] == spine.rsplit("\n", 1)[0], "the spine's words, the walker's command"


def test_an_acknowledged_failure_sets_aside_what_it_cannot_vouch_for_and_goes_on(tmp_path, monkeypatch, capsys):
    """`--acknowledge-failure` sets aside an output written after the boundary was
    recorded, records the acknowledgement bound to the progress record, and the resume it
    lets through consumes it."""
    run, fake = _stopped(tmp_path, monkeypatch)
    _rewrite(run, outcome="failed", error="gate_strategy: boom")
    fingerprints = _progress(run)["file_fingerprints"]
    stray = next(name for name, value in fingerprints.items() if value is None and "." in Path(name).name)
    (run / stray).parent.mkdir(parents=True, exist_ok=True)
    (run / stray).write_text("half written\n")
    capsys.readouterr()
    assert _resume(run, "--acknowledge-failure") == 2, "it went on, to the gate it had not been answered at"
    assert "gate_strategy is waiting for a person" in capsys.readouterr().err
    [manifest] = (run / ".refactory/set-aside").glob("*/manifest.json")
    moved = json.loads(manifest.read_text())
    assert moved["checkpoint_status_before"] == "failed"
    assert [entry["original_path"] for entry in moved["files"]] == [stray]
    assert not (run / stray).exists()
    assert not (run / recovery.RESUME_REQUEST).exists(), "the acknowledgement is spent"


def test_an_acknowledgement_is_bound_to_its_boundary_and_spent_once(tmp_path, monkeypatch):
    run, _ = _stopped(tmp_path, monkeypatch)
    _rewrite(run, outcome="running")
    assert recovery.acknowledge_walked(run)["checkpoint_status_before"] == "executing"
    request = json.loads((run / recovery.RESUME_REQUEST).read_text())
    assert request == {"checkpoint_sha256": hashlib.sha256((run / PROGRESS).read_bytes()).hexdigest(),
                       "acknowledge_executing": True}
    recovery.prepare_walked(run, "default_pack/design")
    with pytest.raises(ValueError, match="last node outcome is unknown"):
        recovery.prepare_walked(run, "default_pack/design")
    assert recovery.acknowledge_walked(run) is not None
    _rewrite(run, steps=99)  # the boundary moved on: the old acknowledgement is for another
    with pytest.raises(ValueError, match="last node outcome is unknown"):
        recovery.prepare_walked(run, "default_pack/design")


def test_a_node_whose_outcome_is_unknown_resumes_once_acknowledged(tmp_path, monkeypatch, capsys):
    run, _ = _stopped(tmp_path, monkeypatch)
    _rewrite(run, outcome="running")
    assert _resume(run) == 2
    assert "last node outcome is unknown" in capsys.readouterr().err
    assert _resume(run, "--acknowledge-failure") == 2
    assert "gate_strategy is waiting for a person" in capsys.readouterr().err


def test_a_dispatched_workers_unknown_outcome_is_set_aside_first(tmp_path, monkeypatch, capsys):
    run, _ = _stopped(tmp_path, monkeypatch)
    marker = run / ".refactory/agent-delivery/pending/design_doc.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}")
    capsys.readouterr()
    assert _resume(run) == 2
    assert ("a dispatched worker outcome is unknown; set it aside first with:\\n  "
            f"python -m refactory_dsh.design --resume {run} --acknowledge-failure") in capsys.readouterr().err
    assert _resume(run, "--acknowledge-failure") == 2
    assert not marker.exists()
    assert "gate_strategy is waiting for a person" in capsys.readouterr().err


def test_a_changed_input_is_refused_as_the_spine_refuses_it(tmp_path, monkeypatch, capsys):
    """The person edited `design.md` at the gate instead of answering it: the results
    built on it are not this run's any more."""
    run, fake = _stopped(tmp_path, monkeypatch)
    (run / "design.md").write_text((run / "design.md").read_text() + "\nedited by hand\n")
    assert console.main(["steer", "--run", str(run), "--verdict", "gate_strategy=proceed"]) == 0
    capsys.readouterr()
    assert _resume(run) == 2
    assert ("input/output 'design.md' changed; dependent results need explicit invalidation"
            in capsys.readouterr().err)


def test_a_changed_configuration_is_refused(tmp_path, monkeypatch, capsys):
    run, _ = _stopped(tmp_path, monkeypatch)
    [graph] = (run / "graphs").glob("*.graph.json")
    graph.write_text(graph.read_text() + " ")
    capsys.readouterr()
    assert _resume(run) == 2
    err = capsys.readouterr().err
    assert "prepared configuration" in err and "graphs" in err and "changed" in err


def test_a_run_is_resumed_only_by_its_own_main(tmp_path, monkeypatch, capsys):
    from refactory_dsh import create

    run, _ = _stopped(tmp_path, monkeypatch)
    capsys.readouterr()
    assert create.main(["--resume", str(run)]) == 2
    assert "checkpoint belongs to a different run or mode" in capsys.readouterr().err


def test_a_waiting_gate_is_not_a_visit(tmp_path, monkeypatch):
    """The walker stops at a user gate with nobody to answer it as the spine does: the
    run waits there (`refused`), and no visit to the gate is started or failed until it is
    answered (#436 gap 7, `walker-refuses-gate`)."""
    run, fake = _stopped(tmp_path, monkeypatch)

    def gate_events() -> list[str]:
        events = [json.loads(line) for line in (run / ".refactory/events.jsonl").read_text().splitlines()]
        return [event["kind"] for event in events if event.get("node_id") == "gate_strategy"
                and event.get("kind", "").startswith("node_")]

    assert gate_events() == []
    assert json.loads((run / ".refactory/spine-status.json").read_text())["status"] == "refused"
    assert console.main(["steer", "--run", str(run), "--verdict", "gate_strategy=proceed"]) == 0
    fake.answers = [DESIGN_DOC_ANSWER, *POST_GATE_PROCEED]
    assert _resume(run) == 0
    assert gate_events() == ["node_started", "node_completed"]


def test_a_finished_run_resumes_to_its_result(tmp_path, monkeypatch, capsys):
    """A completed walk is a boundary of its own, as the spine's `done` is: resuming it
    runs nothing."""
    run, fake = _stopped(tmp_path, monkeypatch)
    assert console.main(["steer", "--run", str(run), "--verdict", "gate_strategy=proceed"]) == 0
    fake.answers = [DESIGN_DOC_ANSWER, *POST_GATE_PROCEED]
    assert _resume(run) == 0
    assert _progress(run)["outcome"] == "completed"
    calls = len(fake.prompts)
    capsys.readouterr()
    assert _resume(run) == 0
    assert json.loads(capsys.readouterr().out)["exit_ok"] is True
    assert len(fake.prompts) == calls


def test_the_walkers_run_config_is_the_spines_record(tmp_path, monkeypatch):
    """The same fields as the spine's (`interactive.prepare` writes them through the same
    `prepare_run`), four of which name the runtime."""
    run, _ = _stopped(tmp_path, monkeypatch)
    config = json.loads((run / ".refactory/run-config.json").read_text())
    assert set(config) == {"mode", "runtime", "command", "resume_command", "owner_argv", "fast", "project",
                           "knobs", "model_context", "resource_limits", "configuration_fingerprints", "driver"}
    assert config["runtime"] == "walker" and config["owner_argv"] == ["-m", "refactory_dsh.design", "--resume",
                                                                       str(run)]
    assert config["knobs"] == ["brief=a tool", "name=probe_tool", "check=python tool.py", "worktree=0"]
    assert config["configuration_fingerprints"] and config["driver"]["checkout"]


def test_graph_control_names_the_walkers_own_resume(tmp_path, monkeypatch):
    run, _ = _stopped(tmp_path, monkeypatch)
    with pytest.raises(ValueError) as refused:
        graph_control.request(run, "acknowledge")
    assert f"python -m refactory_dsh.design --resume {run}" in str(refused.value)
    assert "--acknowledge-failure" in str(refused.value)


def test_the_console_resumes_with_only_what_it_was_given(monkeypatch, tmp_path):
    """A console resume used to forward the new run's defaults (`--attempts 3`), which a
    resume now refuses; it forwards the resume, its acknowledgement and what the person gave."""
    seen: list[list[str]] = []
    monkeypatch.setattr(console, "_forward", lambda module, argv: seen.append(argv) or {})
    for verb in ("design", "create", "edit"):
        assert console.main([verb, "--resume", str(tmp_path), "--acknowledge-failure"]) == 0
    assert seen == [["--resume", str(tmp_path), "--acknowledge-failure"]] * 3
