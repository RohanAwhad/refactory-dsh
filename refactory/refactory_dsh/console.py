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


def _parser() -> argparse.ArgumentParser:
    def with_common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """The shared options, on every subcommand.

        Repeated per subcommand rather than declared once on the parent, because a
        caller assembling argv naturally appends them: ``console status --run X``
        is the order anyone writes, and a parser that only accepted them before
        the verb would reject the obvious call.
        """
        parser.add_argument("--project", default=None, help="repository root (default: found)")
        parser.add_argument("--runs-root", default=None, help="where runs live")
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

    add(sub.add_parser("stop", help="ask a run to stop"))
    return parser


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
    project = Path(args.project) if args.project else project_root()
    try:
        if args.verb == "modes":
            payload: Any = catalogue(project=project)
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
        elif args.verb == "stop":
            payload = stop(_root_for(args, project))
        else:  # pragma: no cover - argparse rejects an unknown verb first
            raise ConsoleError(f"unknown verb {args.verb!r}")
    except (ConsoleError, LoopError, PackageError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stdout)
        return 2
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
