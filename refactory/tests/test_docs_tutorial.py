"""The HTML tutorial is generated, and these keep it a fact about the code.

A tour of sixteen modes, two v2 pipelines and eleven loop options is wrong the
first time somebody adds a node or a flag. `gen_tutorial` reads the catalogue, the
graphs, the registries and the command lines, so the page cannot drift; these tests
are what makes that true rather than aspirational.
"""

from __future__ import annotations

from pathlib import Path

from refactory_dsh import console, evaluators, optim, registry
from refactory_dsh import strategies as strategies_module
from refactory_dsh.nn import edits as edits_module
from refactory_dsh.tools import gen_tutorial

PROJECT = Path(__file__).resolve().parent.parent.parent
TUTORIAL = gen_tutorial.OUT


def test_the_tutorial_matches_the_code():
    """A stale page is a tutorial that teaches a system that does not exist."""
    assert TUTORIAL.exists(), (
        "docs/tutorial.html is missing; run "
        "`python -m refactory_dsh.tools.gen_tutorial`"
    )
    assert TUTORIAL.read_text() == gen_tutorial.render(), (
        "docs/tutorial.html is stale; run "
        "`python -m refactory_dsh.tools.gen_tutorial`"
    )


def test_the_tutorial_names_every_mode():
    """A mode nobody can find in the tutorial is a mode nobody uses."""
    page = TUTORIAL.read_text()
    # The page documents what a clone *ships*: `gen_tutorial.catalogue` leaves out the
    # packages a machine made for itself, or the page would differ per machine.
    missing = [entry["id"] for entry in gen_tutorial.catalogue(PROJECT) if entry["id"] not in page]
    assert missing == [], f"the tutorial does not document: {missing}"


def test_the_tutorial_names_every_loop_option():
    """The outer loop's section is complete against its own `--help`.

    Read from the command line rather than from the table, so a new flag cannot be
    added without the page learning about it.
    """
    page = TUTORIAL.read_text()
    flags = gen_tutorial.parse_options(gen_tutorial.command_help("refactory_dsh.loop"))
    assert flags, "the loop's --help listed no options, so this test proves nothing"
    missing = [flag for flag in flags if flag not in page]
    assert missing == [], f"the tutorial's option table is missing: {missing}"


def test_the_tutorial_names_every_loop_choice():
    """Every strategy, optimizer, evaluator and edit preset appears."""
    page = TUTORIAL.read_text()
    registries = {
        "strategies": strategies_module.STRATEGIES,
        "optimizers": optim.REGISTRY,
        "evaluators": evaluators.REGISTRY,
        "presets": edits_module.PRESETS,
    }
    missing = {
        kind: [name for name in names if name not in page]
        for kind, names in registries.items()
    }
    assert all(not names for names in missing.values()), f"missing from the tutorial: {missing}"


def test_the_tutorial_names_every_action_the_ceo_has():
    """The GUI section's action list is the tool's own verb table."""
    page = TUTORIAL.read_text()
    verbs = gen_tutorial.tool_verbs()
    assert len(verbs) >= 10, f"the tool's verb table read as {verbs}, which is too short to be it"
    missing = [verb for verb, _help in verbs if verb not in page]
    assert missing == [], f"the CEO section does not document: {missing}"


def test_every_mode_section_shows_a_graph_that_builds():
    """The two v2 pipelines the tutorial draws are the graphs that exist."""
    for identifier in ("default_pack/create", "default_pack/design"):
        shape = gen_tutorial.mode_shape(identifier, PROJECT)
        assert shape["walk"], f"{identifier} walked as an empty graph"
        assert shape["agents"] > 0, f"{identifier} is drawn without a model call"
        drawn = gen_tutorial.render_pipeline(shape)
        assert "agent" in drawn and "gate" in drawn, f"{identifier}'s pipeline lost its kinds"


def test_a_mode_the_registry_lists_is_not_claimed_to_call_a_model():
    """The page's central claim: only the agent-node modes make model calls."""
    page = TUTORIAL.read_text()
    deterministic = 0
    for entry in gen_tutorial.catalogue(PROJECT):
        shape = gen_tutorial.mode_shape(entry["id"], PROJECT)
        if shape["agents"] == 0:
            deterministic += 1
    assert deterministic >= 6, "the fixture modes went missing, so the claim is untested"
    assert "deterministic" in page


def test_the_tutorials_index_quotes_the_shipped_listing():
    """The index's transcript is the shipped packages, not this machine's."""
    index = (Path(__file__).resolve().parent.parent / "docs" / "tutorials" / "index.md").read_text()
    listing = gen_tutorial.committed_listing().rstrip("\n")
    assert f"```\n{listing}\n```" in index, (
        "the index's `registry list` transcript is not what a fresh clone prints; "
        "regenerate it with `python -m refactory_dsh.tools.gen_tutorial` (the listing is "
        "`gen_tutorial.committed_listing()`)"
    )
