"""Adopt a run's winner as a mode of its own.

D15 in the decision record:

    **The loop's output is a new artifact, never a mutation of its input.**
    `create`, `edit`, `design` and **an adopting loop** all produce something new,
    so the baseline stays measurable.

`create`, `edit` and `design` exist. The adopting loop did not, and the consequence
was that a run's winner was unreachable: `state.json`'s population records what the
best candidate *scored*, `console candidate` returns a **diff** rather than a state,
and there is no command that turns either into something runnable. The full state is
in `journal.jsonl` — re-running it reproduces the score exactly — but reading a
journal by hand is not an interface.

**What adopting produces.** A package whose mode is the winner's state, pinned. The
mode subclasses the one that was searched, so its ops, its knobs and its `score()`
are the same code, and only the state differs. That is what makes the comparison
meaningful: the baseline and the adoption are the same mode at two points in the
search, which is exactly what D15's "so the baseline stays measurable" asks for.

**What it never does.** It does not touch the source package, and it does not touch
the run. Adoption is a read of both. `tests/test_adoption.py` asserts the source is
byte-identical afterwards, because "produces something new" is the whole decision and
a test that only checks a file appeared would not notice it being violated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from refactory_dsh import registry

#: Where the pinned state and the provenance of an adoption live, beside the mode.
ADOPTED = ".adopted.json"

#: A package's source file. Small on purpose: everything else is inherited.
MODE_FILE = "mode.py"


class AdoptionError(RuntimeError):
    """A winner cannot be adopted. Always names what is wrong."""


def _journal_entries(run_root: Path) -> list[dict[str, Any]]:
    from refactory_dsh.history import JOURNAL

    path = Path(run_root) / JOURNAL
    if not path.exists():
        raise AdoptionError(f"no {JOURNAL} in {run_root}; nothing recorded a state")
    entries: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            entries.append(loaded)
    return entries


def _score_by_id(entries: list[dict[str, Any]]) -> dict[str, float]:
    scored: dict[str, float] = {}
    for entry in entries:
        if entry.get("event") == "score" and entry.get("id") is not None:
            try:
                scored[str(entry["id"])] = float(entry.get("score") or 0.0)
            except (TypeError, ValueError):
                continue
    return scored


def best(run_root: Path) -> dict[str, Any]:
    """The winner: the highest-scoring candidate whose state the run recorded.

    Read from the journal, because that is the only artifact that carries a
    candidate's *state*. `report.json` carries the score, and `state.json`'s
    population carries the edits, and neither can be run.
    """
    run_root = Path(run_root)
    entries = _journal_entries(run_root)
    scores = _score_by_id(entries)

    winner_id, winner_score, winner_state, winner_edits = "", None, None, []
    for entry in entries:
        if entry.get("event") != "candidate":
            continue
        state = entry.get("state")
        if not isinstance(state, dict):
            continue
        candidate_id = str(entry.get("id") or "")
        score = scores.get(candidate_id)
        if score is None:
            continue
        if winner_score is None or score > winner_score:
            winner_id, winner_score = candidate_id, score
            winner_state, winner_edits = state, list(entry.get("edits") or ())

    if winner_state is None:
        raise AdoptionError(
            f"{run_root} recorded no candidate that both scored and kept its state; "
            "there is nothing to adopt"
        )
    return {
        "run": str(run_root),
        "candidate": winner_id,
        "score": winner_score,
        "edits": winner_edits,
        "state": winner_state,
    }


def _mode_source(package: str, mode_name: str, state: dict[str, Any]) -> str:
    """The adopted package's mode: the searched class, pinned to the winner's state.

    Inherited rather than regenerated. A regenerated mode would be a *new*
    implementation that happens to score the same, and then a comparison against the
    baseline would be measuring two things at once.
    """
    import pprint

    return (
        '"""An adopted mode: the winner of a run, pinned.\n\n'
        f"Adopted from `{package}/{mode_name}`. The class is the one that was\n"
        "searched, so its ops, its knobs and its `score()` are the same code; only\n"
        "the state differs, and the state is what the search found. Written by\n"
        "`refactory_dsh.adopt`; edit it like any other mode.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n"
        f"from refactory_packages.{package}.mode import {_class_name(package, mode_name)}\n\n"
        "#: The winning state. A dict literal rather than a sidecar file so the mode\n"
        "#: is reproducible from the source alone.\n"
        f"PINNED: dict[str, Any] = {pprint.pformat(state, width=88, sort_dicts=True)}\n\n\n"
        f"class Adopted({_class_name(package, mode_name)}):\n"
        '    """The winner of a run, as a mode of its own."""\n\n'
        f"    name = {mode_name!r}\n"
        "    adopted = True\n\n"
        "    def __init__(self, state: dict[str, Any] | None = None) -> None:\n"
        "        super().__init__(state=state if state is not None else PINNED)\n"
    )


def _class_name(package: str, mode_name: str) -> str:
    """The class named by a mode, read from the package rather than guessed."""
    from refactory_dsh.paths import project_root

    loaded = registry.load(package, None)
    for mode_class in loaded.modes:
        if mode_class.name == mode_name:
            return mode_class.__name__
    raise AdoptionError(
        f"{package} has no mode {mode_name!r}; known: "
        f"{[m.name for m in loaded.modes]}"
    )


def adopt(
    run_root: Path,
    *,
    as_name: str,
    project: Path | None = None,
    mode: str = "",
) -> dict[str, Any]:
    """Write the winner as a new package, and leave both inputs untouched.

    ``mode`` is the identifier the run searched, as ``<package>/<name>``. It is
    read from the run's own journal when not given, so a caller does not have to
    remember what a run was about.
    """
    run_root = Path(run_root)
    project = Path(project or Path("."))
    winner = best(run_root)

    if not mode:
        for entry in _journal_entries(run_root):
            if entry.get("event") == "start" and entry.get("mode"):
                mode = str(entry["mode"])
                break
    if "/" not in mode:
        raise AdoptionError(
            f"cannot tell which mode {run_root} searched; pass it as <package>/<name>"
        )
    package, mode_name = mode.split("/", 1)

    destination = registry.workspace_root(project) / as_name
    if destination.exists():
        raise AdoptionError(
            f"{destination} already exists; adopting never overwrites, so choose "
            "another name or remove it first"
        )

    destination.mkdir(parents=True)
    (destination / "__init__.py").write_text(
        '"""A package holding one adopted mode.\n\n'
        f"Produced by `refactory_dsh.adopt` from the run at {run_root}.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "from refactory_dsh.nn import Package\n\n"
        "from .mode import Adopted\n\n"
        f"PACKAGE = Package(\n"
        f"    name={as_name!r},\n"
        f"    description='The winner of a run of {mode}, as a mode of its own.',\n"
        "    modes=(Adopted,),\n"
        f"    ops={('refactory_packages.' + package + '.ops',)!r},\n"
        ")\n"
    )
    (destination / MODE_FILE).write_text(_mode_source(package, mode_name, winner["state"]))
    (destination / ADOPTED).write_text(
        json.dumps(
            {
                "adopted_from": mode,
                "run": str(run_root),
                "candidate": winner["candidate"],
                "score": winner["score"],
                "edits": winner["edits"],
            },
            indent=2,
        )
    )

    registry.install_workspace(project)
    loaded = registry.load(as_name, project)
    return {
        "package": as_name,
        "mode": f"{as_name}/{loaded.modes[0].name}",
        "adopted_from": mode,
        "candidate": winner["candidate"],
        "score": winner["score"],
        "edits": winner["edits"],
        "package_root": str(destination),
    }
