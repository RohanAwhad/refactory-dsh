"""Driving a run from a GUI: the catalogue, a live run, and what it produced.

A user watching a search must not need a terminal. This is the surface they act
through, and it is deliberately *not* the loop: it starts runs, tails them, and
reads their artifacts, all through the files the loop already writes. A GUI, a
notebook and a shell all drive the same call, and none of them can see inside the
loop that they could not see on disk.

The loop stays Pythonic. What this adds is a process boundary the loop already
implied — a run is a directory of artifacts, so anything that can read a
directory can watch one.

A run outlives the console: ``start`` launches it in its own process, writes a
``console.json`` naming it, and returns. ``status`` reads that run's progress
file, so a GUI that was restarted mid-run picks the run back up instead of
orphaning it.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from refactory_dsh import observe
from refactory_dsh import registry
from refactory_dsh.history import find_runs
from refactory_dsh.loop import Loop, LoopConfig, LoopError
from refactory_dsh.nn import PackageError
from refactory_dsh.paths import project_root

#: What the console records about a run it started.
CONSOLE_FILE = "console.json"

#: Default place runs live, and the catalogue a GUI lists.
RUNS_ROOT = Path(".factory/loop")

#: The run settings a caller may pass at start, as (name, help, type). One table,
#: so the command line and :func:`start` cannot disagree about what is settable --
#: a setting the loop accepts but the console cannot name is a setting a GUI
#: cannot offer.
SETTINGS: tuple[tuple[str, str, type], ...] = (
    ("epochs", "how many epochs to run", int),
    ("batch_size", "how many candidates per batch", int),
    ("optimizer", "which optimizer proposes the next batch", str),
    ("strategy", "which outer loop drives the search", str),
    ("evaluator", "which runtime runs a candidate: walker or dsh", str),
    ("seed", "seed for the run's randomness, so a run repeats", int),
    ("elite", "how many parents each batch is proposed against", int),
    ("attempts", "retries per batch after a refusal", int),
    ("preset", "which edits the optimizer may use", str),
    ("max_evaluations", "hard ceiling on inner runs", int),
    ("holdout", "the fraction of the Task's items held out of the search", float),
    ("parsimony", "what each node of a candidate's graph costs its score", float),
)

#: The audit a run writes of its winner against the items no candidate saw.
HOLDOUT_FILE = "holdout.json"


class ConsoleError(RuntimeError):
    """The request cannot be served, and why."""


# ── the catalogue ──────────────────────────────────────────────────────────


def catalogue(*, project: Path | None = None) -> list[dict[str, Any]]:
    """Every mode a run can be started from, with the surface it exposes.

    The list is the registry's, not a hand-kept one: a mode added to a package
    appears here without an edit. A package that cannot be imported is listed
    with the reason rather than silently omitted, because "the mode I want is
    missing from the list" is otherwise indistinguishable from a typo.
    """
    project = Path(project or project_root())
    grouped = registry.catalogue(project)
    found: list[dict[str, Any]] = []
    for package_name, modes in grouped.items():
        enabled = registry.is_enabled(project, package_name)
        for entry in modes:
            identifier = f"{package_name}/{entry['name']}"
            try:
                package, klass = registry.resolve(project, identifier)
            except PackageError as error:
                found.append({
                    "id": identifier,
                    "package": package_name,
                    "mode": entry["name"],
                    "enableable": entry.get("package_enabled", enabled),
                    "enabled": enabled,
                    "startable": False,
                    "reason": str(error),
                })
                continue
            # One `describe()` per mode, not three: the surface a start dialog
            # shows is the surface the optimizer already reads, and a catalogue
            # that reshaped it would be a second answer to the same question.
            mode = klass()
            described = mode.describe()
            found.append({
                "id": identifier,
                "package": package_name,
                "mode": entry["name"],
                "description": entry.get("description", ""),
                "startable": enabled,
                "enabled": enabled,
                "trust": package.trust,
                "scored": mode.is_scored(),
                # Which verb runs this mode. An unscored mode is an authoring mode:
                # `start` refuses it, and a caller that has to fail to find that out
                # is a caller that picked the wrong tool from the catalogue.
                "action": (
                    "start" if mode.is_scored()
                    else entry["name"] if entry["name"] in ("create", "edit", "design")
                    else "start"
                ),
                "guidance": described["guidance"],
                "preferred_edits": described["preferred_edits"],
                "parameters": described["knobs"],
                "nodes": described["nodes"],
                "ports": described["ports"],
                # The second route to every mode: the workflow spine lowers the same
                # mode and hands it over one node at a time, which is how a run that
                # stops at a person's gate gets that person asked. Reported rather
                # than assumed, because a caller that only knows `action` picks the
                # headless route and pre-answers a gate the person could have been
                # asked at (#92, #93).
                "interactive": True,
                # Where the run will stop for a person, before it is opened. `fn` and
                # `agent` gates are answered by the runtime; `user` gates are put to
                # the person, and no verdict may be passed for them.
                "gates": [
                    {"id": node["id"], "user": node.get("gate_kind") == "user"}
                    for node in described["nodes"]
                    if node["kind"] == "gate"
                ],
            })
    return sorted(found, key=lambda item: item["id"])


# ── starting a run ─────────────────────────────────────────────────────────


def start(
    mode: str,
    *,
    project: Path | None = None,
    runs_root: Path | None = None,
    run_id: str | None = None,
    **config: Any,
) -> dict[str, Any]:
    """Begin a run in its own process, and hand back the id to watch it with.

    In a separate process on purpose: a search runs for hours and a GUI request
    does not, and a run that died with its window would be a run nobody could
    start and walk away from.
    """
    project = Path(project or project_root())
    # An authoring mode (`create`, `edit`, `design`) declares no `score()`, so the
    # loop has no signal to search and refuses it. Said here rather than in the run's
    # log, because a caller told "this mode is for authoring; use the create action"
    # picks the right thing, and one told "the run exited 1" does not.
    try:
        _package, mode_class = registry.resolve(project, mode)
    except Exception:
        mode_class = None
    if mode_class is not None and not mode_class().is_scored():
        verb = mode.split("/", 1)[-1]
        action = verb if verb in ("create", "edit", "design") else "the console's authoring verb"
        raise ConsoleError(
            f"{mode} declares no score(), so there is nothing for a run to search; "
            f"it is an authoring mode — run it with the `{action}` action instead of "
            "`start`"
        )
    runs_root = Path(runs_root) if runs_root else project / RUNS_ROOT
    run_id = run_id or _run_id(mode)
    root = runs_root / run_id
    if root.exists():
        raise ConsoleError(f"{root} already exists; pick another run id")
    root.mkdir(parents=True, exist_ok=True)

    console = {
        "run_id": run_id,
        "mode": mode,
        "root": str(root),
        "started": round(time.time(), 3),
        "config": {key: str(value) for key, value in sorted(config.items())},
        "log": f"{run_id}.log",
    }
    (root / CONSOLE_FILE).write_text(json.dumps(console, indent=2))

    process = _spawn(mode, root, config, project=project)
    console["pid"] = process.pid
    (root / CONSOLE_FILE).write_text(json.dumps(console, indent=2))
    return console


def _spawn(mode: str, root: Path, config: dict[str, Any], *, project: Path) -> subprocess.Popen:
    """The run, in a process that does not die with this one."""
    argv = [sys.executable, "-m", "refactory_dsh.loop", mode, "--runs-root", str(root)]
    for key, value in sorted(config.items()):
        if value is None or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, (list, tuple)):
            for element in value:
                argv += [flag, str(element)]
        else:
            argv += [flag] if value is True else [flag, str(value)]
    log = (root / f"{root.name}.log").open("wb")
    return subprocess.Popen(
        argv,
        cwd=str(project),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _run_id(mode: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{mode.replace('/', '-')}-{stamp}"


# ── lowering a mode for a driver that walks it node by node ────────────────


#: The data node's item loader, as the composition mounts it. Spelled here
#: because the console is the Python owner the Web composition reaches, and the
#: graph bundle names the same module in its own `dataCommand`; the interpreter
#: that resolves the bare `python` leads :func:`_node_environment`'s `PATH`.
DATA_COMMAND = "python -m refactory_dsh.items"


def _node_environment(
    mode: str,
    *,
    project: Path,
    workdir: Path,
    knobs: list[str] | None = None,
) -> dict[str, str]:
    """The environment a node command of **this** run runs under.

    The values are per run -- the work directory and the mode's knobs -- and a Web
    process serves many runs, so they cannot be mount-time configuration. Both
    owners are reused rather than restated: the lowering owns the knob values
    (:func:`refactory_dsh.lowering.knob_environment`) and the node runner owns the
    names it reads (``refactory_dsh/run.py`` reads ``REFACTORY_WORKDIR``,
    ``REFACTORY_PROJECT`` and ``REFACTORY_KNOBS``, and
    ``refactory_dsh/items.py`` reads ``REFACTORY_WORKDIR``).

    ``PATH`` leads with the run's interpreter so a node command's bare ``python``
    resolves to the venv that has ``refactory_dsh`` installed, exactly as the
    graph profile's ``commandEnv`` does.
    """
    from refactory_dsh import interactive, lowering, registry
    from refactory_dsh.paths import venv_bin

    # The same mode with the same knob overrides `interactive.prepare` lowered,
    # through the same owner, so the environment cannot disagree with the graph.
    Mode = registry.resolve(project, mode)[1]
    state = interactive.with_knobs(Mode().state_dict(), list(knobs or []))
    interpreter = str(venv_bin())
    ambient = os.environ.get("PATH", "")
    return lowering.knob_environment(state, extra={
        "REFACTORY_WORKDIR": str(workdir),
        "REFACTORY_PROJECT": str(project),
        "PATH": f"{interpreter}:{ambient}" if ambient else interpreter,
    })


def _open_mode(
    mode: str,
    *,
    project: Path | None = None,
    workdir: Path | str | None = None,
    knobs: list[str] | None = None,
) -> dict[str, Any]:
    """Lower a mode for a caller that will drive its graph one node at a time.

    The web chat session drives a mode through the spine's `wf_*` tools instead
    of the `graph` profile, which lowers its graph once at mount. This verb does
    the lowering -- through :func:`refactory_dsh.interactive.prepare`, the same
    owner the interactive entry point uses -- and prints where the graph landed,
    so the spine can open it after boot.

    The payload's field names are the ones the lowering already uses:

    ``mode``
        the mode asked for, as ``<package>/<name>``.
    ``run``
        the run directory the mode was lowered into, absolute.
    ``name``
        the graph name the lowering chose (``<package>-<mode>``).
    ``graph``
        absolute path of the lowered graph JSON the spine loads.
    ``declarations``
        absolute path of the run's node declarations.
    ``commandEnv``
        the environment this run's node commands run under, an object of
        strings, computed by :func:`_node_environment` from the same lowering.
    ``dataCommand``
        the command that selects and opens a data node's item passes, so a mode
        with a `DataNode` iterates on this run's directory and interpreter.

    ``workdir`` is required: a driven run needs one directory to own, and
    without it there is nowhere to lower the graph or write the run's status.
    A mode that cannot be resolved is refused by name.
    """
    project = Path(project or project_root()).resolve()
    if not workdir:
        raise ConsoleError("open needs a --workdir: the directory this run is lowered into")
    # The interactive entry point owns the lowering (ledger seeding, intent fold,
    # declarations), so this verb delegates to it rather than reimplementing any
    # part of it. Imported here to keep the console's module load light.
    from refactory_dsh import interactive
    from refactory_dsh.lowering import DECLARATIONS, SUFFIX

    root = Path(workdir).expanduser().resolve()
    try:
        name, graphs = interactive.prepare(mode, workdir=root, project=project, knobs=knobs)
        command_env = _node_environment(mode, project=project, workdir=root, knobs=knobs)
    except ValueError as error:
        # A malformed identifier is the caller's to fix, so it is a refusal with
        # the reason rather than a traceback.
        raise ConsoleError(str(error)) from error
    return {
        "mode": mode,
        "run": str(root),
        "name": name,
        "graph": str((graphs / f"{name}{SUFFIX}").resolve()),
        "declarations": str((root / DECLARATIONS).resolve()),
        "commandEnv": command_env,
        "dataCommand": DATA_COMMAND,
    }


# ── watching one ───────────────────────────────────────────────────────────


#: The graph spine's own status file. Its presence is what makes a run directory an
#: authoring run, as opposed to a search: a search writes `progress.jsonl`, and an
#: authoring run writes this.
SPINE_STATUS = Path(".refactory") / "spine-status.json"

#: The spine's states, in the words a reader of `status` already has.
SPINE_STATES = {
    "done": "finished", "ready": "running", "failed": "failed", "refused": "failed",
    "halted": "halted",
}


def _spine(run_root: Path) -> dict[str, Any]:
    """What the graph spine last recorded, or ``{}`` when this is not an authoring run."""
    path = run_root / SPINE_STATUS
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _authoring(run_root: Path, spine: dict[str, Any]) -> dict[str, Any]:
    """An authoring run's own progress: the nodes done, the nodes due, and its facts."""
    facts = _read_json(run_root / "facts.json")
    return {
        "spine": spine.get("status"),
        "completed": spine.get("completed") or [],
        "nodes_due": spine.get("nodes") or [],
        "error": spine.get("error"),
        "name": facts.get("name"),
        "validation": facts.get("validation"),
        "package_path": facts.get("package_path"),
        "files": facts.get("files") or [],
        "before_score": facts.get("before_score"),
        "after_score": facts.get("after_score"),
    }


def status(run_root: Path, *, after: int = 0) -> dict[str, Any]:
    """Where the run is, and what is new since the reader last looked.

    ``after`` is a line count, not a timestamp: a reader polling this wants the
    events it has not seen, and a clock cannot tell it which those are.

    A search run's progress is `progress.jsonl`. An authoring run is driven by the
    graph spine and has none, so its own status file is read instead — without that,
    a creation could only be waited on by waiting for the call to return.
    """
    run_root = Path(run_root)
    if not run_root.is_dir():
        raise ConsoleError(f"{run_root} is not a run directory")
    events = observe.progress(run_root, after=after)
    console = _console(run_root)
    spine = _spine(run_root)
    halted = spine.get("status") == "halted"
    # Terminal is not the same as successful: polling stops at a halt, while
    # the state still names the halt rather than claiming the work completed.
    finished = (run_root / "report.json").exists() or spine.get("status") in {"done", "halted"}
    state = _state(run_root, events, console)
    if spine:
        state = SPINE_STATES.get(str(spine.get("status")), state)
        if finished and not halted:
            state = "finished"
    return {
        "run_id": run_root.name,
        "root": str(run_root),
        "mode": console.get("mode"),
        "state": state,
        "events": events,
        "cursor": after + len(events),
        "counters": _counters(events),
        "best_score": _best(run_root, events),
        "finished": finished,
        "running": not finished and (_running(run_root, console) or bool(spine)),
        "authoring": _authoring(run_root, spine) if spine else None,
    }


def _state(run_root: Path, events: list[dict[str, Any]], console: dict[str, Any]) -> str:
    """One of ``starting``, ``running``, ``finished``, ``died``.

    A run that left a report is finished whatever its process is doing. A run
    with a console record and no live process did not finish, and saying
    "running" would leave a reader waiting forever for a run that is gone.
    """
    if (run_root / "report.json").exists():
        return "finished"
    if _running(run_root, console):
        return "running"
    if console.get("pid"):
        return "died"
    return "running" if events else "starting"


def _counters(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest = events[-1] if events else {}
    return {
        "epoch": latest.get("epoch", 0),
        "generation": latest.get("generation", 0),
        "evaluations": latest.get("evaluations", 0),
        "steps": len(events),
    }


def _best(run_root: Path, events: list[dict[str, Any]]) -> float | None:
    """The best score so far: the report's when it exists, else the progress tail."""
    report = run_root / "report.json"
    if report.exists():
        try:
            return json.loads(report.read_text()).get("best_score")
        except json.JSONDecodeError:
            pass
    scores = [e["score"] for e in events if isinstance(e.get("score"), (int, float))]
    return max(scores) if scores else None


def _console(run_root: Path) -> dict[str, Any]:
    """What the console recorded about a start, or what the run itself did.

    A run started by the loop's own entry point has no console file: nobody
    watched it begin. `run.json` is written by the loop itself and names the mode,
    so a reader learns what a run was optimizing whether or not the console
    started it. The console file still wins, because it also carries the pid and
    the settings the console chose.
    """
    recorded = _read_json(run_root / CONSOLE_FILE)
    if recorded:
        return recorded
    started = _read_json(run_root / "run.json")
    return {"mode": started["mode"]} if isinstance(started.get("mode"), str) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _running(run_root: Path, console: dict[str, Any]) -> bool:
    pid = console.get("pid")
    return bool(pid) and _alive(int(pid))


def _alive(pid: int) -> bool:
    """Whether the process is running.

    ``kill(pid, 0)`` succeeds for a zombie, and a run this process spawned and
    never waited on stays a zombie until it is reaped -- so a finished run would
    read as alive from the very process that started it.
    """
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass  # not our child: the signal probe below is the only answer
    except OSError:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def stop(run_root: Path) -> dict[str, Any]:
    """Ask the run to stop, and say whether it did."""
    console = _console(Path(run_root))
    pid = console.get("pid")
    if not pid or not _alive(int(pid)):
        return {"stopped": False, "reason": "the run is not running"}
    try:
        os.kill(int(pid), signal.SIGINT)
    except OSError as error:
        return {"stopped": False, "reason": str(error)}
    return {"stopped": True, "pid": int(pid)}


# ── reading one ────────────────────────────────────────────────────────────


def artifacts(run_root: Path, *, contents: bool = False) -> dict[str, Any]:
    """What the run left, and — when asked — what is in it.

    Text is returned inline because that is what a GUI shows and what an optimizer
    reads. A reader that had to fetch each file separately would need to know the
    layout, which is the thing the manifest exists to avoid telling it.
    """
    run_root = Path(run_root)
    manifest = observe.artifact_manifest(run_root)
    if not contents:
        return manifest
    for entry in manifest["artifacts"]:
        if entry["media_type"] == "application/x-ndjson" or entry["path"].endswith("/"):
            continue
        path = run_root / entry["path"]
        if not path.exists():
            continue
        entry["content"] = (
            json.loads(path.read_text())
            if entry["media_type"] == "application/json"
            else path.read_text()
        )
    return manifest


def candidate(run_root: Path, candidate_id: str) -> dict[str, Any]:
    """One candidate, with what it changed against the parent it came from."""
    run_root = Path(run_root)
    diff = observe.candidate_diff(run_root, candidate_id)
    record = _measured(run_root, candidate_id)
    return {**diff, "rendered": observe.render_diff(diff["diff"]), **record}


def gates(run_root: Path) -> list[dict[str, Any]]:
    """The `user` gates this run is stopped at, and the verdicts each accepts.

    A run that pauses at a gate a person owns is waiting for an answer, and a
    caller that wants to give it needs two things the ledger cannot supply: which
    gate is due, and which outcome labels route it. Both are the run's own lowered
    graph -- the spine's status names the due node, and that node's outgoing
    conditional edges are the labels the spine accepts (the same derivation the
    spine's own ``acceptedVerdicts`` performs). Reading them here keeps the graph's
    format in the console, the way the ledger's format is, rather than making every
    caller parse a run directory it did not lower.

    Empty for a run with no status (a search, whose graph lives per candidate), for
    a due node that is not a gate, and for a gate a machine answers: only a `user`
    gate is a person's to route.
    """
    spine = _spine(run_root)
    due = [str(node) for node in (spine.get("nodes") or [])]
    if not due:
        return []
    graphs = Path(run_root) / "graphs"
    if not graphs.is_dir():
        return []
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(graphs.glob("*.graph.json")):
        try:
            loaded = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(loaded, dict):
            continue
        nodes = loaded.get("nodes") or {}
        edges = loaded.get("edges") or []
        for node_id in due:
            node = nodes.get(node_id) if isinstance(nodes, dict) else None
            if not isinstance(node, dict):
                continue
            if node.get("_type") != "GateNode" or node.get("evaluator_type") != "user":
                continue
            verdicts = [
                str(edge.get("condition")).strip().lower()
                for edge in edges
                if isinstance(edge, dict)
                and edge.get("source") == node_id
                and edge.get("condition") not in (None, "")
            ]
            found[node_id] = {
                "id": node_id,
                "prompt": str(node.get("gate_prompt") or ""),
                "verdicts": verdicts,
            }
    return [found[node_id] for node_id in due if node_id in found]


def _steer(
    run_root: Path,
    *,
    seed: str | None = None,
    note: str | None = None,
    kind: str = "info",
    verdicts: tuple[str, ...] | list[str] = (),
    with_gates: bool = False,
    control: str | None = None,
    target: str | None = None,
    lineage: bool = False,
    as_name: str | None = None,
) -> dict[str, Any]:
    """Read the ledger, and optionally add to it.

    Steering is context, not structure: this writes what the person said and nothing
    else. The graph of the running mode is untouched -- that is the outer loop's job,
    and conflating the two is how a run stops being accountable for what it was asked
    to do.

    ``with_gates`` adds :func:`gates` to the payload: the `user` gates the run is
    stopped at, so a caller that only knows this verb can offer the answer the gate
    accepts. It is opt-in because the ledger read is what most callers want, and a
    caller that does not route gates should not pay for reading a graph it will not
    use.
    """
    from refactory_dsh.steering import Ledger, chain, fidelity, outputs_in

    ledger = Ledger(run_root)
    if seed is not None:
        Ledger.seed(run_root, seed)
    if note is not None:
        ledger.note(kind, note)
    for pair in verdicts:
        gate, _, value = str(pair).partition("=")
        ledger.verdict(gate, value)
    # The chain is checkable only when the run left agent outputs beside the ledger
    # (a single mode run does; an outer-loop run keeps its candidates in subdirs).
    outputs = outputs_in(run_root) if ledger.exists() else {}
    payload: dict[str, Any] = {
        "run": str(run_root),
        "ledger": str(ledger.path),
        "exists": ledger.exists(),
        "entries": ledger.entries(),
        "constraints": ledger.constraints(),
        "verdicts": ledger.verdicts(),
        "chain": chain(ledger, outputs) if ledger.exists() else [],
        "problems": fidelity(ledger, outputs),
    }
    if with_gates:
        payload["gates"] = gates(run_root)
    if control is not None:
        # Control is not prose: the loop polls a small file at a generation boundary,
        # so a pause, a stop, a keep, a reject or an adopt works from here, from the
        # panel, and from a process that is not the run's parent. A request that
        # cannot be honored as written (a keep with no id) is refused here, where the
        # person can still fix it, rather than written for the loop to guess at.
        from refactory_dsh.control import Control, ControlError

        try:
            payload["control"] = Control(run_root).request(
                control, target=target, lineage=lineage, as_name=as_name
            )
        except ControlError as error:
            raise ConsoleError(str(error)) from error
    elif target or lineage or as_name:
        # `--lineage` or `--as` on their own name no act, and a flag that silently
        # does nothing is worse than one that is refused.
        raise ConsoleError(
            "--keep, --reject or --adopt names the act that --lineage and --as "
            "belong to; none of them was given"
        )
    return payload


def _best_payload(run_root: Path, *, keep_state: bool) -> dict[str, Any]:
    """The winner, the state that scored it, and how it did on the held-out items.

    The state is what makes a winner usable and it is the field `leaderboard` and
    `candidate` do not have: one gives a score, the other a diff, and neither can
    be run. It is large, so it is opt-in — but it is available without reading
    `journal.jsonl` by hand, which was the only way before.

    The held-out audit travels with the winner because the score alone cannot say
    whether the harness works on anything but the items it was tuned against.
    """
    from refactory_dsh.adopt import best

    winner = best(run_root)
    if not keep_state:
        winner = {key: value for key, value in winner.items() if key != "state"}
        winner["state_available"] = "pass --state"
    winner["holdout"] = holdout(run_root)
    if (winner["holdout"] or {}).get("overfit"):
        winner["overfit_finding"] = _overfit_finding(winner["holdout"])
    return winner


def holdout(run_root: Path) -> dict[str, Any] | None:
    """The held-out audit a run recorded, or ``None`` when it recorded none."""
    return _read_json(Path(run_root) / HOLDOUT_FILE) or None


def _overfit_finding(audit: dict[str, Any]) -> str:
    """What an overfit audit means for a reader who is about to keep the winner."""
    return (
        f"the winner scores {audit.get('train')} on the items it was searched on and "
        f"{audit.get('holdout')} on the held-out items, a gap of {audit.get('gap')} "
        f"over the threshold {audit.get('threshold')}; it may not work on items it "
        "was not tuned against"
    )


def _measured(run_root: Path, candidate_id: str) -> dict[str, Any]:
    """What the run directory recorded about the candidate's evaluation."""
    path = run_root / "runs" / candidate_id / "candidate.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def leaderboard(run_root: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """The candidates by score, with each one's diff against its parent.

    The diff is what makes a leaderboard readable: without it the table says which
    number is bigger and nothing about why.
    """
    run_root = Path(run_root)
    report = run_root / "report.json"
    if report.exists():
        try:
            rows = json.loads(report.read_text()).get("leaderboard") or []
        except json.JSONDecodeError:
            rows = []
    else:
        rows = [
            {"id": path.parent.name, **json.loads(path.read_text())}
            for path in sorted((run_root / "runs").glob("*/candidate.json"))
            if _readable(path)
        ]
        rows.sort(key=lambda row: (-(row.get("score") or 0.0), row.get("id") or ""))
    out: list[dict[str, Any]] = []
    # What a person pinned or dismissed, so a reader of a *live* run sees their own
    # acts in the table they are reading. A finished run carries the same two lists
    # in `report.json`'s rows; a live one has them only in `state.json`.
    kept, rejected = _person_lists(run_root)
    for row in rows[:limit]:
        record = dict(row)
        candidate_id = str(row.get("id") or "")
        record["kept"] = candidate_id in kept
        record["rejected"] = candidate_id in rejected
        try:
            record["diff"] = observe.candidate_diff(run_root, row["id"])
        except FileNotFoundError:
            record["diff"] = None
        out.append(record)
    return out


def _person_lists(run_root: Path) -> tuple[set[str], set[str]]:
    """The candidates a person kept and rejected, from the run's own state.

    Absent state is an empty answer rather than an error: a leaderboard is readable
    before the loop has written anything, and before this existed every run's
    leaderboard is exactly what this returns for one.
    """
    state = _read_json(Path(run_root) / "state.json") or {}
    kept = {str(value) for value in state.get("kept") or ()}
    rejected = {str(value) for value in state.get("rejected") or ()}
    return kept, rejected


def _readable(path: Path) -> bool:
    try:
        json.loads(path.read_text())
    except json.JSONDecodeError:
        return False
    return True


def runs(
    *,
    project: Path | None = None,
    runs_root: Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Every run under the root, newest first, each with where it got to.

    A finished run and a run in flight are both listed: a GUI that hid the one it
    was currently watching would empty its own list. Finished runs come from the
    reader that already bounds them; unfinished ones are found by the console file
    that a start writes before the loop's first artifact exists.
    """
    project = Path(project or project_root())
    root = Path(runs_root) if runs_root else project / RUNS_ROOT
    out: list[dict[str, Any]] = []
    for archive in find_runs(root, limit=limit):
        out.append({
            "run_id": archive.path.name,
            "root": str(archive.path),
            "mode": _console(archive.path).get("mode") or archive.mode,
            "best_score": archive.best_score,
            "finished": True,
        })
    started = sorted(root.glob(f"*/{CONSOLE_FILE}")) if root.is_dir() else []
    for entry in started:
        run_root = entry.parent
        if any(item["run_id"] == run_root.name for item in out):
            continue
        console = _console(run_root)
        out.append({
            "run_id": run_root.name,
            "root": str(run_root),
            "mode": console.get("mode"),
            "best_score": _best(run_root, observe.progress(run_root)),
            "finished": False,
        })
    out.sort(key=lambda item: -Path(item["root"]).stat().st_mtime)
    return out[:limit]


# ── the command line a tool shells out to ──────────────────────────────────


def _first_line(cls: Any) -> str:
    """A class's own one-line description, from its docstring."""
    text = (getattr(cls, "__doc__", "") or "").strip()
    return text.split("\n", 1)[0].strip()


def level_two() -> dict[str, Any]:
    """The three level-2 seams by name, each with what it says it is.

    The named choice a GUI needs: a person picks an outer loop, an optimizer or an
    evaluator from a catalogue with descriptions rather than typing a name they have
    to already know. Read from the registries, so a new implementation appears here
    the moment it registers.
    """
    from refactory_dsh import evaluators, optim, strategies

    def entries(registry: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"name": name, "description": _first_line(cls)}
            for name, cls in sorted(registry.items())
        ]

    return {
        "outer_loops": entries(strategies.STRATEGIES),
        "optimizers": entries(optim.REGISTRY),
        "evaluators": entries(evaluators.REGISTRY),
    }


def _design_review(project: Path, name: str) -> str:
    """The design review a creation wrote, so the choice it made is visible.

    A create run surveys approaches, picks one and writes `design.md`; without this
    a caller sees only the package it produced and cannot tell which approaches were
    considered or what the review refused.

    The working directory is shared by every create run, so the review is reported
    only when `brief.json` says it belongs to the package being reported: a run that
    stopped before the design step otherwise reports whichever review happens to be
    on disk — another package's.
    """
    for candidate in (project / ".factory" / "create", Path(".factory/create")):
        design = candidate / "design.md"
        brief = candidate / "brief.json"
        if not (design.exists() and brief.exists()):
            continue
        try:
            if str(json.loads(brief.read_text()).get("name") or "") != name:
                continue
            return design.read_text()[:8000]
        except (OSError, json.JSONDecodeError):
            continue
    return ""


def _parser() -> argparse.ArgumentParser:
    def with_common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """The shared options, on every subcommand.

        Repeated per subcommand rather than declared once on the parent, because a
        caller assembling argv naturally appends them: ``console status --run X``
        is the order anyone writes, and a parser that only accepted them before
        the verb would reject the obvious call.
        """
        parser.add_argument("--project", default=None, help="repository root (default: found)")
        parser.add_argument(
            "--runs-root",
            default=None,
            help=(
                "the directory runs live under, one subdirectory per run; this is "
                "not the loop's --runs-root, which is the run directory itself"
            ),
        )
        parser.add_argument("--run", default=None, help="a run directory or run id")
        return parser

    parser = argparse.ArgumentParser(
        prog="python -m refactory_dsh.console",
        description="Drive and read outer-loop runs.",
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    add = with_common

    add(sub.add_parser("modes", help="the catalogue of startable modes"))
    add(sub.add_parser("runs", help="every run under the root"))
    add(sub.add_parser("strategies", help="the named outer loops, optimizers and evaluators"))

    begin = add(sub.add_parser("start", help="begin a run in its own process"))
    begin.add_argument("mode")
    begin.add_argument("--run-id", default=None)
    for setting, help_text, kind in SETTINGS:
        begin.add_argument(
            "--" + setting.replace("_", "-"), type=kind, default=None, help=help_text
        )

    # `run` is the CLI's run: use a mode once, with the person answering any gate.
    # `start` is the outer loop (`refactory optimize`); the two are different acts and
    # the tool's actions are named the same way, so neither is mistaken for the other.
    single = add(sub.add_parser("run", help="use one mode once, with you answering any gate"))
    single.add_argument("mode", help="the mode, as <package>/<name>")
    single.add_argument("--intent", default="", help="what this run is for, in your words")
    single.add_argument("--workdir", default="", help="the run directory")
    single.add_argument(
        "--source", default="", metavar="PATH",
        help="read the items from here instead of the source the mode declares",
    )
    single.add_argument(
        "--source-format", default="", choices=["", "directory", "jsonl", "csv"],
        help="how to read --source (default: the runner's own directory form)",
    )
    single.add_argument(
        "--knob", action="append", default=[], metavar="NAME=VALUE",
        help="set one of the mode's knobs for this run (repeatable)",
    )
    single.add_argument(
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable); a headless "
             "run refuses a user gate it was not told the answer to",
    )
    single.add_argument("--timeout", type=int, default=0, help="seconds; 0 waits for the person")
    single.add_argument("--provider", default="", help="provider route for this run's model")
    single.add_argument("--model", default="", help="model id for this run")

    # `open` lowers a mode for a caller that will drive its graph node by node
    # (a web chat session's spine), rather than running it to completion. It is
    # deliberately not `run`: nothing is executed here, and the caller owns the
    # driving.
    opened = add(sub.add_parser("open", help="lower a mode for a caller that drives it node by node"))
    opened.add_argument("mode", help="the mode to lower, as <package>/<name>")
    opened.add_argument(
        "--workdir", default="",
        help="the run directory this caller drives (required); the graph, the "
             "declarations and the run's status are written here",
    )
    opened.add_argument(
        "--knob", action="append", default=[], metavar="NAME=VALUE",
        help="set one of the mode's knobs for this run (repeatable)",
    )

    begin.add_argument(
        "--knob", action="append", default=[], metavar="NAME=VALUE",
        help="a knob value the search starts from (repeatable); a mode whose inputs "
             "are frozen knobs needs one or every candidate scores the same",
    )
    watch = add(sub.add_parser("status", help="where a run is"))
    watch.add_argument("--after", type=int, default=0)

    read = add(sub.add_parser("artifacts", help="what a run left"))
    read.add_argument("--contents", action="store_true")

    board = add(sub.add_parser("leaderboard", help="the candidates by score"))
    board.add_argument("--limit", type=int, default=20)

    one = add(sub.add_parser("candidate", help="one candidate and its diff"))
    one.add_argument("candidate")

    steer = add(
        sub.add_parser("steer", help="what this run is for, and what has been said since")
    )
    steer.add_argument("--seed", default=None, help="the request this run is for")
    steer.add_argument("--note", default=None, help="something said while it runs")
    steer.add_argument(
        "--kind", default="info", choices=["info", "correction", "constraint"],
        help="a constraint must be respected; the others are context",
    )
    steer.add_argument(
        "--verdict", action="append", default=[],
        help="record a gate verdict as <gate>=<value> (repeatable); the runtime "
             "uses this so a person's answer is durable and a resume replays it",
    )
    steer.add_argument(
        "--gates", action="store_true",
        help="also report the `user` gates this run is stopped at, and the verdicts "
             "each accepts, so a caller can offer the answer the gate routes on",
    )
    control = steer.add_mutually_exclusive_group()
    control.add_argument(
        "--pause", action="store_true",
        help="hold the run at its next generation boundary until resumed or stopped",
    )
    control.add_argument("--resume", action="store_true", help="release a paused run")
    control.add_argument(
        "--stop", action="store_true",
        help="end the run at its next boundary, recording that a person asked",
    )
    control.add_argument(
        "--keep", metavar="ID",
        help="follow this candidate up: it stays a parent even once a newer batch "
             "outscores it, and a rejection on it is cleared",
    )
    control.add_argument(
        "--reject", metavar="ID",
        help="stop searching around this candidate: it leaves the leaderboard, so it "
             "cannot be proposed from, reported best or adopted",
    )
    control.add_argument(
        "--adopt", action="store_true",
        help="write the run's current winner as a package now, without ending the run",
    )
    steer.add_argument(
        "--lineage", action="store_true",
        help="with --keep or --reject, act on the candidate's descendants too, so a "
             "whole branch is followed up or dropped",
    )
    steer.add_argument(
        "--as", dest="as_name", metavar="NAME",
        help="with --adopt, the package name to write (default: <package>_adopted)",
    )

    champion = add(
        sub.add_parser("best", help="the winner, with the state that scored it")
    )
    champion.add_argument("--state", action="store_true", help="include the full state")

    take = add(
        sub.add_parser("adopt", help="write the winner as a new package, input untouched")
    )
    take.add_argument("--as", dest="as_name", required=True, help="the new package name")
    take.add_argument(
        "--allow-overfit", action="store_true", dest="allow_overfit",
        help="adopt even though the winner's held-out audit says it is overfit; "
             "without this, adopting an overfit winner is refused",
    )

    add(sub.add_parser("stop", help="ask a run to stop"))

    # ── the rest of the system, reachable through the same wire ────────────
    # These forward to the entry points that already own the work, so the Web
    # view reaches package management and the authoring modes without a second
    # implementation. `--args` passes a caller's own flags through verbatim.
    packages = sub.add_parser("registry", help="list, verify, add, import, remove, enable or disable packages")
    packages.add_argument("registry_verb", choices=[
        "list", "show", "verify", "smoke", "tools", "add", "import", "remove", "enable", "disable",
    ])
    packages.add_argument("target", nargs="?", default="")
    packages.add_argument("--args", default="", help="extra registry flags, as a shell-quoted string")

    make = sub.add_parser("create", help="create a package of modes from a brief (model-driven)")
    make.add_argument("--name", required=True)
    make.add_argument("--brief", required=True)
    make.add_argument("--extends", default="")
    make.add_argument("--attempts", type=int, default=3)
    make.add_argument(
        "--data-node", action="store_true", default=False, dest="data_node",
        help="build the mode as a DataNode that iterates its subgraph per item",
    )
    make.add_argument("--task", default=None, metavar="TASK_REF", dest="task_ref",
                      help="the class of work: a Task the produced harness binds a data node "
                           "to, so the harness runs its subgraph once per item the Task lists")
    make.add_argument(
        "--single", action="store_true", default=False, dest="single_shot",
        help="build a harness for one run of one thing, with no Task binding; one of "
             "--task or --single is required",
    )
    make.add_argument("--workdir", default="", help="the run directory (default: .factory/create/<name>)")
    make.add_argument("--resume", default="", metavar="RUN_DIR", help="continue a failed run from its directory")
    make.add_argument(
        "--sample", action="append", default=[], metavar="NAME=VALUE",
        help="a knob value the validation run should use (repeatable), so the smoke "
             "exercises the input path rather than the built-in fixture",
    )
    make.add_argument(
        "--input", action="append", default=[], metavar="NAME",
        help="an input the produced mode must accept, named the way a knob or a file "
             "would be (repeatable); validation refuses a mode with no way to receive it",
    )
    make.add_argument(
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable); "
             "gate_strategy is one, so a headless create is told the answer",
    )

    revise = sub.add_parser("edit", help="revise a mode from a brief (model-driven)")
    revise.add_argument("--mode", required=True)
    revise.add_argument("--name", required=True)
    revise.add_argument("--brief", required=True)
    revise.add_argument("--allow-regression", action="store_true")
    revise.add_argument("--attempts", type=int, default=3)
    revise.add_argument("--workdir", default="", help="the run directory (default: .factory/edit/<name>)")
    revise.add_argument("--resume", default="", metavar="RUN_DIR", help="continue a failed run from its directory")
    revise.add_argument(
        "--sample", action="append", default=[], metavar="NAME=VALUE",
        help="a knob value the validation run should use (repeatable)",
    )
    revise.add_argument(
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable)",
    )

    artifact = sub.add_parser("design", help="design and check a software artifact (model-driven)")
    artifact.add_argument("--name", required=True)
    artifact.add_argument("--brief", required=True)
    artifact.add_argument("--check", default="", help="the command that proves the artifact works")
    artifact.add_argument("--attempts", type=int, default=3)
    artifact.add_argument("--workdir", default="", help="the run directory (default: .factory/design/<name>)")
    artifact.add_argument("--resume", default="", metavar="RUN_DIR", help="continue a failed run from its directory")
    artifact.add_argument(
        "--verdict", action="append", default=[], metavar="GATE=VALUE",
        help="answer a gate a person owns, as <gate>=<value> (repeatable)",
    )

    return parser


def _forward(module: str, argv: list[str]) -> dict[str, Any]:
    """Run another entry point's ``main`` and return its output as this verb's payload.

    The console's contract is one JSON object on stdout, and the forwarded entry
    points print their own JSON. Capturing and re-parsing keeps that contract for a
    caller that only knows the console, and a non-JSON line is returned as text
    rather than lost — a forwarded command that half-printed before failing is
    exactly the case a caller needs to see.
    """
    import contextlib
    import importlib
    import io

    entry = importlib.import_module(f"refactory_dsh.{module}")
    buffer = io.StringIO()
    errors = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
        code = int(entry.main(argv) or 0)
    text = buffer.getvalue().strip()
    stderr = errors.getvalue().strip()
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = {"output": text}
    if code != 0 and not (isinstance(payload, dict) and payload.get("error")):
        # The authoring entry points report a handled refusal as its own JSON on
        # *stderr* and print nothing on stdout. Capturing stdout alone made the
        # payload `{"output": ""}`, so the tool could only say "failed (exit 2)".
        payload = {
            "error": _error_from(stderr) or payload.get("output") or f"exited with {code}"
        }
    return {
        "verb": module, "exit_code": code, "ok": code == 0,
        "result": payload, "stderr": stderr,
    }


def _error_from(stderr: str) -> str:
    """The message inside a stderr line an entry point wrote, or the line itself.

    Those entry points print `{"error": "..."}` to stderr. Handing a caller that line
    verbatim shows it a JSON fragment where a sentence belongs, so the message inside
    is preferred; anything else on stderr is passed through as it stands.
    """
    text = stderr.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(loaded, dict) and isinstance(loaded.get("error"), str):
            return loaded["error"].strip()
    return text


def _forward_error(payload: dict[str, Any]) -> str:
    """Why a forwarded entry point failed, preferring the words it chose."""
    result = payload.get("result")
    if isinstance(result, dict):
        if isinstance(result.get("error"), str) and result["error"].strip():
            return result["error"].strip()
        if isinstance(result.get("output"), str) and result["output"].strip():
            return result["output"].strip()
    if isinstance(payload.get("stderr"), str) and payload["stderr"].strip():
        return payload["stderr"].strip()
    return f"the {payload.get('verb')} entry point failed (exit {payload.get('exit_code')})"


def _root_for(args: argparse.Namespace, project: Path) -> Path:
    if args.run:
        candidate_path = Path(args.run)
        if candidate_path.is_dir():
            return candidate_path
        base = Path(args.runs_root) if args.runs_root else project / RUNS_ROOT
        return base / args.run
    raise ConsoleError("this verb needs --run <run id or directory>")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # The forwarding verbs carry their own addressing flags, so `--project` is
    # absent on them; a missing one is "the found root", not a crash.
    project = Path(getattr(args, "project", None)) if getattr(args, "project", None) else project_root()
    try:
        if args.verb == "modes":
            payload: Any = catalogue(project=project)
        elif args.verb == "strategies":
            payload = level_two()
        elif args.verb == "runs":
            payload = runs(project=project, runs_root=args.runs_root)
        elif args.verb == "start":
            payload = start(
                args.mode,
                project=project,
                runs_root=args.runs_root,
                run_id=args.run_id,
                # `knob`, not `knobs`: the console forwards each config key as
                # `--<key>`, and the loop's flag is the singular one.
                knob=list(args.knob),
                **{
                    setting: getattr(args, setting)
                    for setting, _help, _kind in SETTINGS
                    if getattr(args, setting) is not None
                },
            )
        elif args.verb == "status":
            payload = status(_root_for(args, project), after=args.after)
        elif args.verb == "artifacts":
            payload = artifacts(_root_for(args, project), contents=args.contents)
        elif args.verb == "leaderboard":
            payload = leaderboard(_root_for(args, project), limit=args.limit)
        elif args.verb == "candidate":
            payload = candidate(_root_for(args, project), args.candidate)
        elif args.verb == "steer":
            # One flag, one act: the mutually exclusive group already makes "pause and
            # adopt" unwritable, so this only names the act the flags selected.
            asked = (
                "pause" if args.pause else "resume" if args.resume
                else "stop" if args.stop else "keep" if args.keep
                else "reject" if args.reject else "adopt" if args.adopt else None
            )
            payload = _steer(
                _root_for(args, project),
                seed=args.seed, note=args.note, kind=args.kind,
                verdicts=args.verdict, with_gates=args.gates,
                control=asked,
                target=args.keep or args.reject,
                lineage=args.lineage,
                as_name=args.as_name,
            )
        elif args.verb == "best":
            payload = _best_payload(_root_for(args, project), keep_state=args.state)
        elif args.verb == "adopt":
            from refactory_dsh.adopt import adopt as adopt_winner

            run_root = _root_for(args, project)
            audit = holdout(run_root) or {}
            if audit.get("overfit") and not args.allow_overfit:
                # The one place a person would otherwise carry an overfit winner into
                # the catalogue without being told the score did not travel.
                raise ConsoleError(
                    f"refusing to adopt an overfit winner: {_overfit_finding(audit)}. "
                    "Pass --allow-overfit to adopt it anyway."
                )
            payload = adopt_winner(run_root, as_name=args.as_name, project=project)
            payload["holdout"] = audit or None
        elif args.verb == "stop":
            payload = stop(_root_for(args, project))
        elif args.verb == "registry":
            extra = shlex.split(args.args) if args.args else []
            forwarded = [args.registry_verb, "--json"]
            if args.target:
                forwarded.append(args.target)
            forwarded += extra
            payload = _forward("registry", forwarded)
        elif args.verb == "run":
            forwarded = [args.mode]
            if args.intent:
                forwarded += ["--intent", args.intent]
            if args.workdir:
                forwarded += ["--workdir", args.workdir]
            if args.source:
                forwarded += ["--source", args.source]
            if args.source_format:
                forwarded += ["--source-format", args.source_format]
            for pair in args.knob:
                forwarded += ["--knob", pair]
            for pair in args.verdict:
                forwarded += ["--approve", pair]
            if args.timeout:
                forwarded += ["--timeout", str(args.timeout)]
            if args.provider:
                forwarded += ["--provider", args.provider]
            if args.model:
                forwarded += ["--model", args.model]
            payload = _forward("interactive", forwarded)
            # The one-shot runner prints its report from the child process, whose
            # stdout is the terminal's rather than this buffer, so the JSON contract
            # would otherwise carry nothing. The run directory is the durable answer:
            # where it is, how it ended and what it recorded -- and it is the
            # directory the runner itself chose, so this reads the run it made rather
            # than a second guess at where a run would have gone.
            if args.workdir:
                root = Path(args.workdir)
            else:
                from refactory_dsh.interactive import default_workdir

                root = default_workdir(args.mode)
            if root.is_dir():
                payload["run"] = {
                    **status(root),
                    "facts": _read_json(root / "facts.json"),
                }
        elif args.verb == "open":
            payload = _open_mode(
                args.mode,
                project=project,
                workdir=args.workdir,
                knobs=args.knob,
            )
        elif args.verb == "create":
            forwarded = ["--name", args.name, "--brief", args.brief, "--attempts", str(args.attempts)]
            if args.workdir:
                forwarded += ["--workdir", args.workdir]
            if args.resume:
                forwarded += ["--resume", args.resume]
            for pair in args.sample:
                forwarded += ["--sample", pair]
            for name in args.input:
                forwarded += ["--input", name]
            if args.extends:
                forwarded += ["--extends", args.extends]
            if args.data_node:
                forwarded.append("--data-node")
            if args.task_ref:
                forwarded += ["--task", args.task_ref]
            if args.single_shot:
                forwarded.append("--single")
            for pair in args.verdict:
                forwarded += ["--verdict", pair]
            payload = _forward("create", forwarded)
            review = _design_review(project, args.name)
            if review:
                # The approaches it considered, not just the package it produced.
                payload["design_review"] = review
        elif args.verb == "edit":
            forwarded = [
                "--mode", args.mode, "--name", args.name, "--brief", args.brief,
                "--attempts", str(args.attempts),
            ]
            if args.workdir:
                forwarded += ["--workdir", args.workdir]
            if args.resume:
                forwarded += ["--resume", args.resume]
            for pair in args.sample:
                forwarded += ["--sample", pair]
            if args.allow_regression:
                forwarded.append("--allow-regression")
            for pair in args.verdict:
                forwarded += ["--verdict", pair]
            payload = _forward("edit", forwarded)
        elif args.verb == "design":
            forwarded = ["--name", args.name, "--brief", args.brief, "--attempts", str(args.attempts)]
            if args.workdir:
                forwarded += ["--workdir", args.workdir]
            if args.resume:
                forwarded += ["--resume", args.resume]
            if args.check:
                forwarded += ["--check", args.check]
            for pair in args.verdict:
                forwarded += ["--verdict", pair]
            payload = _forward("design", forwarded)
        else:  # pragma: no cover - argparse rejects an unknown verb first
            raise ConsoleError(f"unknown verb {args.verb!r}")
    except (ConsoleError, LoopError, PackageError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stdout)
        return 2
    if isinstance(payload, dict) and payload.get("ok") is False:
        # The forwarded call failed and said why; the caller gets the reason at the top
        # level as well, because that is where every console consumer looks for it.
        print(json.dumps({**payload, "error": _forward_error(payload)}, indent=2, default=str))
        return 2
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
