"""The DSH profile a mode runs on is machine state, and its absence is legible.

Issue #45: `dsh plugin --profile graph add <package>` creates the profile, nothing
in the install path did, and the first mode run on DSH stopped with a message about
a missing *profile* that reads as a packaging bug. These assert the three states a
caller has to tell apart -- absent, present without the bundle, ready -- and that a
run refuses before spawning rather than letting the child produce the bare error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refactory_dsh import dsh_profile, registry
from refactory_dsh.dsh_runtime import run_on_dsh
from refactory_dsh.paths import REPO_ROOT


def _profile(home: Path, name: str, bundles: list[str]) -> Path:
    directory = home / "profiles" / name
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(
        json.dumps({"name": f"dsh-profile-{name}", "dsh": {"profile": {"bundles": bundles}}})
    )
    return directory


def test_a_missing_profile_names_the_command_and_the_reason(tmp_path, monkeypatch):
    """'does not exist' is not actionable; the command and why are."""
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "home"))
    problem = dsh_profile.requirement()
    assert problem is not None
    assert "machine state" in problem, "the reason the profile is absent is not stated"
    assert dsh_profile.add_command() in problem, "the fix is not copy-pasteable"


def test_the_add_command_names_the_bundle_in_the_checkout():
    command = dsh_profile.add_command()
    assert command.startswith("dsh plugin --profile graph add ")
    assert command.endswith(str(REPO_ROOT / "packages" / "bundle" / "graph"))


def test_a_profile_without_the_bundle_is_a_different_problem(tmp_path, monkeypatch):
    """Present and unready is not the same failure as absent."""
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base"])
    monkeypatch.setenv("DSH_HOME", str(home))
    problem = dsh_profile.requirement()
    assert problem is not None
    assert dsh_profile.GRAPH_BUNDLE in problem
    assert "does not exist" not in problem


def test_a_profile_with_the_bundle_is_ready(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    monkeypatch.setenv("DSH_HOME", str(home))
    assert dsh_profile.is_ready()
    assert dsh_profile.requirement() is None
    dsh_profile.require()


def test_a_blank_dsh_home_falls_back_to_the_default(monkeypatch):
    """Whitespace-only is unset, not a directory named ' '."""
    monkeypatch.setenv("DSH_HOME", "   ")
    assert dsh_profile.dsh_home() == Path.home() / ".dsh"


def test_run_on_dsh_hands_the_runner_an_absolute_workdir(tmp_path, monkeypatch):
    """DSH refuses a relative session cwd, so the adapter must not pass one on.

    The CLI's default run root is relative, which is how a whole `dsh`-evaluated
    run came to fail every candidate with "session header cwd must be an absolute
    path" while the same state scored under the walker.
    """
    state = registry.resolve(REPO_ROOT, "circle_packing/adaptive")[1]().state_dict()
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    seen: dict[str, list[str]] = {}

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "dsh: session header cwd must be an absolute path"

    def _capture(command, **kwargs):  # noqa: ANN001, ANN003 - the runner's own call
        seen["command"] = command
        return _Result()

    monkeypatch.setattr("refactory_dsh.dsh_runtime.subprocess.run", _capture)
    run_on_dsh(state, Path("relative/run"), name="probe")
    argv = seen["command"]
    workdir = argv[argv.index("--workdir") + 1]
    assert Path(workdir).is_absolute(), f"DSH refuses a relative cwd: {workdir!r}"


def test_run_on_dsh_hands_the_runner_the_knob_values_once(tmp_path, monkeypatch):
    """`--knobs` is the knobs JSON, not a JSON string that contains it.

    Double-encoding it makes the node runner fall back to each op's default, so
    DSH runs a different program than the candidate the loop scored.
    """
    state = registry.resolve(REPO_ROOT, "circle_packing/adaptive")[1]().state_dict()
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    monkeypatch.setenv("DSH_HOME", str(home))
    seen: dict[str, list[str]] = {}

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "probe"

    def _capture(command, **kwargs):  # noqa: ANN001, ANN003 - the runner's own call
        seen["command"] = command
        return _Result()

    monkeypatch.setattr("refactory_dsh.dsh_runtime.subprocess.run", _capture)
    run_on_dsh(state, tmp_path / "run", name="probe")
    argv = seen["command"]
    knobs = json.loads(argv[argv.index("--knobs") + 1])
    assert isinstance(knobs, dict), f"--knobs must carry the values, got {knobs!r}"
    assert knobs == {key: entry["value"] for key, entry in state["knobs"].items()}


def test_run_on_dsh_refuses_before_spawning_when_the_profile_is_absent(
    tmp_path, monkeypatch
):
    """The runner checks first, so the error is the actionable one, not the child's."""
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "home"))

    def _no_spawn(*args, **kwargs):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("a subprocess was spawned without a usable profile")

    monkeypatch.setattr("refactory_dsh.dsh_runtime.subprocess.run", _no_spawn)
    run = run_on_dsh({}, tmp_path / "run")
    assert run.returncode == 2
    assert dsh_profile.add_command() in run.stderr
    assert "machine state" in run.stderr
