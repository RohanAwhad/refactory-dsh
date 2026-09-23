"""The one command: `refactory`.

These hold the entry point's own behaviour — what each verb reaches, and the two
refusals that exist to stop a person being confused by a low-level error.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from refactory_dsh import cli
from refactory_dsh.paths import REPO_ROOT


def _run(*argv: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """The console script, as a person runs it."""
    return subprocess.run(
        [str(REPO_ROOT / ".venv" / "bin" / "refactory"), *argv],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=timeout,
    )


def test_doctor_reports_every_prerequisite():
    """One command that answers "is this machine ready", and says what is missing."""
    result = _run("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    for wanted in ("python", "uv", "node", "import factory", "DSH profile 'graph'", "refactory doctor: ready"):
        assert wanted in result.stdout, f"doctor does not check {wanted!r}"


def test_list_is_readable_by_a_person_and_json_to_a_script():
    """The default is for a human; `--json` is the console's own answer."""
    readable = _run("list")
    assert readable.returncode == 0
    assert "circle_packing/" in readable.stdout
    assert not readable.stdout.lstrip().startswith("["), "the listing is a JSON dump again"

    machine = _run("list", "modes", "--json")
    assert machine.returncode == 0
    entries = json.loads(machine.stdout)
    assert any(entry["id"] == "circle_packing/adaptive" for entry in entries)
    assert all("action" in entry for entry in entries)


def test_smoke_refuses_a_mode_that_calls_a_model_naming_the_command_that_works():
    """`package smoke` runs without a model on purpose, so an agent mode cannot be
    smoked — and the failure says which command does supply one, instead of failing
    inside the executor with "agent node 'draft' needs a model client"."""
    problem = cli._smoke_without_a_model("default_pack/create")
    assert "calls a model" in problem
    assert "refactory run default_pack/create" in problem
    assert cli._smoke_without_a_model("circle_packing/adaptive") == ""
    # An identifier nobody can resolve is the registry's error to give, not ours.
    assert cli._smoke_without_a_model("not/a_mode") == ""


def test_the_read_verbs_pass_their_flags_through():
    """`refactory status --run X` is the console's own command, flags and all.

    Argparse cannot express "everything after the verb belongs to somebody else", so
    these are forwarded before it runs; a regression here shows up as
    `unrecognized arguments: --run`.
    """
    result = _run("status", "--run", "/tmp/definitely-not-a-run")
    assert "unrecognized arguments" not in result.stderr
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["error"], "a missing run must be reported, not accepted"


def test_a_name_is_derived_from_what_was_asked_for():
    """`ceo "an idea"` with no `--name` still names the result."""
    assert cli.slug("Count the teapots in a tray") == "count_the_teapots"
    assert cli.slug("2 fast ideas") == "pkg_2_fast_ideas"
    assert cli.slug("!!!") == "new_mode"
    assert len(cli.slug("a " * 40)) <= 40


def test_the_authoring_modes_are_the_ones_the_catalogue_offers():
    """`--mode create|design|edit` names real modes, with real authoring actions."""
    from refactory_dsh import console

    actions = {entry["id"]: entry["action"] for entry in console.catalogue(project=REPO_ROOT)}
    for verb, identifier in cli.AUTHORING.items():
        assert identifier in actions, f"{verb} points at {identifier}, which the catalogue does not list"
        assert actions[identifier] == verb, (
            f"{identifier}'s action is {actions[identifier]!r}, so `--mode {verb}` would not run it"
        )


@pytest.mark.parametrize("argv", [["--help"], ["ceo", "--help"], ["optimize", "--help"], ["package", "--help"]])
def test_help_is_available_for_every_verb_a_person_starts_with(argv):
    result = _run(*argv)
    assert result.returncode == 0
    assert "usage:" in result.stdout
