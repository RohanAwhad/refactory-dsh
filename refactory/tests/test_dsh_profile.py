"""The DSH profile a mode runs on is machine state, and its absence is legible.

Issue #45: `dsh plugin --profile graph add <package>` creates the profile, nothing
in the install path did, and the first mode run on DSH stopped with a message about
a missing *profile* that reads as a packaging bug. These assert the three states a
caller has to tell apart -- absent, present without the bundle, ready -- and that a
run refuses before spawning rather than letting the child produce the bare error.
"""

from __future__ import annotations

import json
import sys
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


def test_the_web_profile_names_the_preset_bundle_not_a_directory_called_web():
    """The bundle directory is derived from the bundle NAME, not the profile name.

    `graph` and its bundle happened to line up, so the old code derived the path
    from the profile name and got away with it. The web profile's bundle is
    `refactory-presets`; deriving `packages/bundle/web` would point at nothing.
    """
    command = dsh_profile.add_command(dsh_profile.WEB_PROFILE)
    assert command.startswith("dsh plugin --profile web add ")
    assert command.endswith(str(REPO_ROOT / "packages" / "bundle" / "refactory-presets"))


def test_an_unowned_profile_is_an_error_not_a_guess():
    """A profile this checkout does not own must not borrow another one's command.

    Falling back to `graph` would print a copy-pasteable command naming the wrong
    bundle directory: it reads as a fix, and running it creates a profile that
    silently does not carry the bundle the caller asked for.
    """
    with pytest.raises(KeyError):
        dsh_profile.add_command("integration")
    with pytest.raises(KeyError):
        # A bundle the profile does not own is refused too, not turned into a path.
        dsh_profile.add_command(dsh_profile.WEB_PROFILE, dsh_profile.GRAPH_BUNDLE)
    assert dsh_profile.bundle_directory(dsh_profile.WEB_BUNDLE) == "refactory-presets"
    assert dsh_profile.bundle_directory(dsh_profile.WEB_SURFACE_BUNDLE) == "refactory-web"
    assert dsh_profile.bundle_directory(dsh_profile.GRAPH_BUNDLE) == "graph"


def test_the_web_profile_owns_both_of_its_bundles():
    """The preset and the surface are separate layers, and both are installed."""
    graph = dsh_profile.add_command(dsh_profile.WEB_PROFILE, dsh_profile.WEB_BUNDLE)
    surface = dsh_profile.add_command(dsh_profile.WEB_PROFILE, dsh_profile.WEB_SURFACE_BUNDLE)
    assert graph.endswith(str(REPO_ROOT / "packages" / "bundle" / "refactory-presets"))
    assert surface.endswith(str(REPO_ROOT / "packages" / "bundle" / "refactory-web"))
    assert dsh_profile.add_command(dsh_profile.WEB_PROFILE) == graph, (
        "omitting the bundle keeps the long-standing command for the first owned bundle"
    )


def test_the_web_profile_without_the_preset_bundle_is_reported(tmp_path, monkeypatch):
    """A `web` profile that exists but lacks the preset bundle is a gap, not ready.

    This is the state a GUI lands in when the bundle is never installed: the
    composition still names `ceo` as the roster default and the profile has no
    `ceo` preset, so every session created without naming one stops.
    """
    home = tmp_path / "home"
    _profile(home, "web", ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"])
    monkeypatch.setenv("DSH_HOME", str(home))
    problem = dsh_profile.requirement(dsh_profile.WEB_PROFILE, dsh_profile.WEB_BUNDLE)
    assert problem is not None
    assert dsh_profile.WEB_BUNDLE in problem
    assert not dsh_profile.is_ready(dsh_profile.WEB_PROFILE, dsh_profile.WEB_BUNDLE)


def test_missing_bundles_reports_each_profile_that_is_short(tmp_path, monkeypatch):
    """Both profiles are owned here, and the answer says which one is unready.

    The web profile owns two bundles — the preset and the web surface — so an
    absent profile yields one gap per bundle it will need, in declaration order.
    """
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    monkeypatch.setenv("DSH_HOME", str(home))
    gaps = dsh_profile.missing_bundles()
    assert [profile for profile, _bundle, _why in gaps] == ["web", "web"], gaps
    assert [bundle for _profile, bundle, _why in gaps] == [
        dsh_profile.WEB_BUNDLE,
        dsh_profile.WEB_SURFACE_BUNDLE,
    ], "each gap names the bundle it needs"


def test_missing_bundles_is_empty_when_both_profiles_are_ready(tmp_path, monkeypatch):
    """Ready means no work: `install.sh` and the launcher both branch on this."""
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    _profile(
        home,
        "web",
        ["@deepseek-ai/dsh-base", dsh_profile.WEB_BUNDLE, dsh_profile.WEB_SURFACE_BUNDLE],
    )
    monkeypatch.setenv("DSH_HOME", str(home))
    assert dsh_profile.missing_bundles() == []


def test_the_web_profile_without_the_surface_bundle_is_reported(tmp_path, monkeypatch):
    """The preset alone is not a ready web profile.

    Without the surface bundle the console and picker rows never mount, so the GUI
    boots with no refactory panel even though the `ceo` preset resolves. The gap
    must name that bundle and the command that installs exactly it.
    """
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    _profile(home, "web", ["@deepseek-ai/dsh-base", dsh_profile.WEB_BUNDLE])
    monkeypatch.setenv("DSH_HOME", str(home))
    gaps = dsh_profile.missing_bundles()
    assert [(profile, bundle) for profile, bundle, _why in gaps] == [
        ("web", dsh_profile.WEB_SURFACE_BUNDLE)
    ], gaps
    assert dsh_profile.add_command(
        dsh_profile.WEB_PROFILE, dsh_profile.WEB_SURFACE_BUNDLE
    ) in gaps[0][2]
    assert dsh_profile.add_command(
        dsh_profile.WEB_PROFILE, dsh_profile.WEB_SURFACE_BUNDLE
    ).endswith(str(REPO_ROOT / "packages" / "bundle" / "refactory-web"))


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


def test_run_on_dsh_hands_each_output_line_on_while_keeping_the_parse(tmp_path, monkeypatch):
    """A watcher sees the child while it runs, and `best_score` still parses.

    Teeing is not a replacement for capturing: the runner's last JSON object is
    where the score comes from, so both have to happen from one read of the child's
    output.
    """
    home = tmp_path / "home"
    _profile(home, "graph", ["@deepseek-ai/dsh-base", dsh_profile.GRAPH_BUNDLE])
    monkeypatch.setenv("DSH_HOME", str(home))
    script = tmp_path / "stub_dsh.py"
    script.write_text(
        "import json\n"
        "print('node start')\n"
        "print('node done')\n"
        "print(json.dumps({'best_score': 0.75}))\n"
    )
    monkeypatch.setattr(
        "refactory_dsh.dsh_runtime.dsh_command", lambda: [sys.executable, str(script)],
    )
    state = registry.resolve(REPO_ROOT, "circle_packing/adaptive")[1]().state_dict()
    seen: list[str] = []
    run = run_on_dsh(state, tmp_path / "run", name="probe", on_output=seen.append)
    assert run.best_score == 0.75, "the runner's own JSON is still what scores the run"
    assert any("node start" in line for line in seen), seen
    assert "node done" in run.stdout
    assert run.stderr == ""


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
