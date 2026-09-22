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
)


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


# ── watching one ───────────────────────────────────────────────────────────


def status(run_root: Path, *, after: int = 0) -> dict[str, Any]:
    """Where the run is, and what is new since the reader last looked.

    ``after`` is a line count, not a timestamp: a reader polling this wants the
    events it has not seen, and a clock cannot tell it which those are.
    """
    run_root = Path(run_root)
    if not run_root.is_dir():
        raise ConsoleError(f"{run_root} is not a run directory")
    events = observe.progress(run_root, after=after)
    console = _console(run_root)
    return {
        "run_id": run_root.name,
        "root": str(run_root),
        "mode": console.get("mode"),
        "state": _state(run_root, events, console),
        "events": events,
        "cursor": after + len(events),
        "counters": _counters(events),
        "best_score": _best(run_root, events),
        "finished": (run_root / "report.json").exists(),
        "running": _running(run_root, console),
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


def _steer(
    run_root: Path,
    *,
    seed: str | None = None,
    note: str | None = None,
    kind: str = "info",
    verdicts: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Read the ledger, and optionally add to it.

    Steering is context, not structure: this writes what the person said and nothing
    else. The graph of the running mode is untouched -- that is the outer loop's job,
    and conflating the two is how a run stops being accountable for what it was asked
    to do.
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
    return {
        "run": str(run_root),
        "ledger": str(ledger.path),
        "exists": ledger.exists(),
        "entries": ledger.entries(),
        "constraints": ledger.constraints(),
        "verdicts": ledger.verdicts(),
        "chain": chain(ledger, outputs) if ledger.exists() else [],
        "problems": fidelity(ledger, outputs),
    }


def _best_payload(run_root: Path, *, keep_state: bool) -> dict[str, Any]:
    """The winner, and the state that scored it.

    The state is what makes a winner usable and it is the field `leaderboard` and
    `candidate` do not have: one gives a score, the other a diff, and neither can
    be run. It is large, so it is opt-in — but it is available without reading
    `journal.jsonl` by hand, which was the only way before.
    """
    from refactory_dsh.adopt import best

    winner = best(run_root)
    if not keep_state:
        winner = {key: value for key, value in winner.items() if key != "state"}
        winner["state_available"] = "pass --state"
    return winner


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
    for row in rows[:limit]:
        record = dict(row)
        try:
            record["diff"] = observe.candidate_diff(run_root, row["id"])
        except FileNotFoundError:
            record["diff"] = None
        out.append(record)
    return out


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

    champion = add(
        sub.add_parser("best", help="the winner, with the state that scored it")
    )
    champion.add_argument("--state", action="store_true", help="include the full state")

    take = add(
        sub.add_parser("adopt", help="write the winner as a new package, input untouched")
    )
    take.add_argument("--as", dest="as_name", required=True, help="the new package name")

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
                      help="a Task class the produced mode must satisfy")

    revise = sub.add_parser("edit", help="revise a mode from a brief (model-driven)")
    revise.add_argument("--mode", required=True)
    revise.add_argument("--name", required=True)
    revise.add_argument("--brief", required=True)
    revise.add_argument("--allow-regression", action="store_true")
    revise.add_argument("--attempts", type=int, default=3)

    artifact = sub.add_parser("design", help="design and check a software artifact (model-driven)")
    artifact.add_argument("--name", required=True)
    artifact.add_argument("--brief", required=True)
    artifact.add_argument("--check", default="", help="the command that proves the artifact works")
    artifact.add_argument("--attempts", type=int, default=3)

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
    with contextlib.redirect_stdout(buffer):
        code = int(entry.main(argv) or 0)
    text = buffer.getvalue().strip()
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = {"output": text}
    return {"verb": module, "exit_code": code, "ok": code == 0, "result": payload}


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
            payload = _steer(
                _root_for(args, project),
                seed=args.seed, note=args.note, kind=args.kind,
                verdicts=args.verdict,
            )
        elif args.verb == "best":
            payload = _best_payload(_root_for(args, project), keep_state=args.state)
        elif args.verb == "adopt":
            from refactory_dsh.adopt import adopt as adopt_winner

            payload = adopt_winner(
                _root_for(args, project), as_name=args.as_name, project=project
            )
        elif args.verb == "stop":
            payload = stop(_root_for(args, project))
        elif args.verb == "registry":
            extra = shlex.split(args.args) if args.args else []
            forwarded = [args.registry_verb, "--json"]
            if args.target:
                forwarded.append(args.target)
            forwarded += extra
            payload = _forward("registry", forwarded)
        elif args.verb == "create":
            forwarded = ["--name", args.name, "--brief", args.brief, "--attempts", str(args.attempts)]
            if args.extends:
                forwarded += ["--extends", args.extends]
            if args.data_node:
                forwarded.append("--data-node")
            if args.task_ref:
                forwarded += ["--task", args.task_ref]
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
            if args.allow_regression:
                forwarded.append("--allow-regression")
            payload = _forward("edit", forwarded)
        elif args.verb == "design":
            forwarded = ["--name", args.name, "--brief", args.brief, "--attempts", str(args.attempts)]
            if args.check:
                forwarded += ["--check", args.check]
            payload = _forward("design", forwarded)
        else:  # pragma: no cover - argparse rejects an unknown verb first
            raise ConsoleError(f"unknown verb {args.verb!r}")
    except (ConsoleError, LoopError, PackageError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stdout)
        return 2
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
