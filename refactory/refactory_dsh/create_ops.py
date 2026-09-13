"""The operations create-mode's own graph calls.

Creating a package is a mode like any other: it has a graph, it runs through the
same executor, and its gate refuses a package that will not run. The difference
is only what it produces — a directory of Python that becomes a new package.
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from refactory_dsh import registry
from refactory_dsh.nn import Package, PackageError, validate_state

STAGING = "staging"

#: The marker the emit agent uses to separate files. JSON would need every
#: newline escaped, which is where a generated source file usually dies.
FILE_MARKER = re.compile(r"^===FILE:\s*([A-Za-z0-9_./-]+)\s*===\s*$", re.MULTILINE)

REQUIRED_FILES = ("__init__.py",)

API = """\
You are writing a package for the refactory mode library. The API is:

    from refactory_dsh import nn

    class MyMode(nn.Mode):
        name = "shortname"                 # the mode's name inside the package
        description = "one line"

        @classmethod
        def declare(cls):                  # the parameters the outer loop may move
            return {"rounds": nn.Knob(3, "int", lo=1, hi=8, description="..."),
                    "order": nn.Knob("desc", "enum", enum=("desc", "asc")),
                    "seed": nn.Knob(7, "int", frozen=True, description="...")}

        def build(self):                   # pure: no I/O, no randomness, no clock
            graph = nn.Graph()
            graph.add(nn.Node("first", "op", call="my_pkg.ops:first",
                              reads=(), writes=("a.json",), produces=("a",),
                              knobs=("rounds",), notes="what it does"))
            graph.add(nn.Node("check", "gate", evaluator="my_pkg.ops:check",
                              reads=("a.json",), produces=("ok",), max_iterations=8))
            graph.add(nn.Node("done", "op", call="my_pkg.ops:done",
                              reads=("a.json",), writes=("best.json",), produces=("best",)))
            graph.connect("first", "check")
            graph.connect("check", "first", condition="retry")
            graph.connect("check", "done", condition="proceed")
            return graph

        def score(self, run):              # read run.facts here, never a knob
            return float(run.facts["best"])

        def invariants(self):              # what the validator must keep true
            return (nn.fact_produced("best"),)

        def insertable(self):              # blocks a structural edit may add
            return (nn.Node("observe", "op", call="my_pkg.ops:observe",
                            reads=("a.json",), writes=("observations.json",),
                            produces=("spread",)),)

    PACKAGE = nn.Package(name="my_pkg", description="...", modes=(MyMode,),
                         ops=("my_pkg.ops",))

An operation is a plain function of the run context:

    def first(ctx):
        value = int(ctx.knob("rounds", 3))      # read a declared knob
        ctx.write_json("a.json", {"value": value})   # write a file you declared
        return {"a": value}                     # return the facts you declared

The context offers `ctx.knob(name, default)`, `ctx.read_json(name, default)`,
`ctx.write_json(name, payload)`, `ctx.write_text`, `ctx.append_text`,
`ctx.read_text`, `ctx.exists`, `ctx.set_fact(name, value)`, `ctx.facts`,
`ctx.workdir`, `ctx.iteration` and `ctx.model`.

Rules the validator enforces on every candidate, so a violation is a refusal:
- every node is reachable from the start node, and the graph has a terminal
- a non-gate node has at most one out-edge, and it carries no verdict
- a gate has at least two out-edges, each with a ``condition``, and every cycle
  passes through a gate
- every file a node reads is written by some node (declare `reads`/`writes`)
- every fact a node returns is declared in its `produces`
- every knob a node lists in `knobs` is declared by the mode, and every declared
  knob is consumed by at least one node
- every `call`/`evaluator` is a real importable ``module:function``
- score() is a method and is never in the state, so nothing can move it
"""


def prepare(ctx: Any) -> dict[str, Any]:
    """Read the brief the run was given and publish it to the prompts."""
    task = dict(getattr(ctx, "task", {}) or {})
    name = str(task.get("name") or "").strip()
    if not name:
        raise ValueError("create-mode needs a package name in its task, e.g. {'name': 'my_pkg'}")
    brief = str(task.get("brief") or "").strip()
    extends = str(task.get("extends") or "").strip()
    ctx.write_json("brief.json", {"name": name, "brief": brief, "extends": extends})
    return {
        "name": name,
        "brief": brief,
        "extends": extends,
        "validation": "(no validation has run yet)",
        "api": API,
        "reference": _reference(extends),
    }


def _reference(extends: str) -> str:
    """The source of an existing package, when the new one extends it."""
    if not extends:
        return "(no reference package was requested; design from the brief)"
    package_name = extends.split("/", 1)[0]
    root = registry.where(package_name, Path.cwd()) or registry.packages_root() / package_name
    if not root.is_dir():
        return f"(the requested reference package {package_name!r} does not exist)"
    blocks = [
        f"# --- {path.relative_to(root)} ---\n{path.read_text()}"
        for path in sorted(root.rglob("*.py"))
    ]
    return "\n\n".join(blocks)


def materialise(ctx: Any) -> dict[str, Any]:
    """Turn the emit agent's answer into a package directory."""
    answer = ctx.read_text("emit.txt")
    files = parse_files(answer)
    if not files:
        raise ValueError(
            "the answer contained no '===FILE: <name>===' blocks, so there is "
            "nothing to write"
        )
    name = str(ctx.facts["name"])
    # The workspace, not the install tree: a run must not write generated code into
    # the checkout, and the workspace is also the boundary a sandbox needs.
    root = registry.workspace_root(Path.cwd()) / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    written: list[str] = []
    for relative, content in files.items():
        target = root / relative
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"{relative!r} escapes the package directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(relative)
    # What the writer records about where this came from, which outranks anything a
    # generated manifest claims about itself.
    (root / ".origin.json").write_text(
        json.dumps(
            {
                "origin": "create-mode",
                "ref": "",
                "trust": "generated",
                "at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(timespec="seconds"),
            },
            indent=2,
        )
    )
    registry.install_workspace(Path.cwd())
    ctx.write_json(f"{STAGING}/manifest.json", {"package": name, "files": sorted(written)})
    return {"package_path": str(root), "files": sorted(written)}


def parse_files(answer: str) -> dict[str, str]:
    """Split an answer into ``path -> source`` blocks."""
    files: dict[str, str] = {}
    matches = list(FILE_MARKER.finditer(answer))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        body = answer[match.end() : end].strip("\n")
        files[match.group(1)] = body + "\n"
    return files


def validate(ctx: Any) -> str:
    """Gate: import the staged package and say whether it is a usable mode.

    Every reason is written out and fed back into the next emit prompt, because a
    model that is told exactly which line broke can fix it in one attempt.
    """
    name = str(ctx.facts["name"])
    attempts = int(ctx.knob("attempts", 3))
    try:
        problems = check_package(name)
    except Exception as exc:
        problems = [f"{type(exc).__name__}: {exc}"]

    used = int(ctx.facts.get("attempts_used", 0)) + 1
    if not problems:
        ctx.write_text("validation.txt", "ok\n")
        ctx.set_fact("validation", "(the previous attempt validated)")
        return "proceed"
    report = "\n".join(f"- {problem}" for problem in problems)
    ctx.write_text("validation.txt", report + "\n")
    ctx.set_fact("validation", report)
    if used >= attempts:
        raise ValueError(
            f"the package {name!r} still does not work after {used} attempt(s):\n{report}"
        )
    ctx.set_fact("attempts_used", used)
    return "reloop"


def _evict(module_name: str) -> None:
    """Drop a package and its submodules from the import cache.

    A regenerated package must not be judged by the module object an earlier
    attempt left behind: the whole point of the loop is that the second attempt is
    a different program.
    """
    for cached in [
        name
        for name in sys.modules
        if name == module_name or name.startswith(module_name + ".")
    ]:
        del sys.modules[cached]


def load_fresh(name: str) -> Package:
    """Import a package from disk as it is now."""
    module_name = f"{registry.ROOT_PACKAGE}.{name}"
    _evict(module_name)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise PackageError(
            f"importing {module_name} failed: {type(exc).__name__}: {exc}"
        ) from exc
    package = getattr(module, "PACKAGE", None)
    if not isinstance(package, Package):
        raise PackageError(
            f"{module_name} must define PACKAGE = Package(name={name!r}, modes=(...)) "
            "at module level"
        )
    return package


def check_package(name: str) -> list[str]:
    """Everything wrong with a staged package, as a list of reasons."""
    registry.install_workspace(Path.cwd())
    root = registry.workspace_root(Path.cwd()) / name
    problems: list[str] = []
    if not root.is_dir():
        return [f"no directory at {root}"]
    for required in REQUIRED_FILES:
        if not (root / required).exists():
            problems.append(f"{required} is missing; a package needs one")
    if problems:
        return problems

    try:
        package = load_fresh(name)
    except PackageError as exc:
        return [str(exc)]

    if package.name != name:
        problems.append(f"PACKAGE.name is {package.name!r}, not {name!r}")
    if not package.modes:
        problems.append("PACKAGE.modes is empty; a package with no mode cannot be run")

    for ops_module in package.ops:
        try:
            _evict(ops_module)
            importlib.import_module(ops_module)
        except Exception as exc:
            problems.append(
                f"the declared ops module {ops_module!r} does not import: "
                f"{type(exc).__name__}: {exc}"
            )

    for mode_class in package.modes:
        problems.extend(_check_mode(mode_class))
    return problems


def _check_mode(mode_class: type) -> list[str]:
    label = f"mode {getattr(mode_class, 'name', mode_class.__name__)!r}"
    try:
        mode = mode_class()
    except Exception as exc:
        return [f"{label} cannot be constructed: {type(exc).__name__}: {exc}"]
    problems: list[str] = []
    if not getattr(mode, "is_scored", lambda: False)():
        problems.append(f"{label} does not override score(), so it produces no signal")
    if not mode.parameters():
        problems.append(f"{label} declares no knobs, so there is nothing to optimize")
    issues = validate_state(mode.state_dict(), invariants=mode.invariants(), resolve_calls=True)
    problems.extend(f"{label}: {issue}" for issue in issues)
    return problems


def register(ctx: Any) -> dict[str, Any]:
    """Confirm the registry now sees the package under its own name."""
    name = str(ctx.facts["name"])
    registry.install_workspace(Path.cwd())
    importlib.invalidate_caches()
    if name not in registry.discover_names(Path.cwd()):
        raise ValueError(f"the registry does not list a package named {name!r}")
    package = load_fresh(name)
    ctx.write_json("registered.json", package.describe())
    return {"registered": ", ".join(package.ids())}


def smoke(ctx: Any) -> dict[str, Any]:
    """Run the new mode once. A mode that cannot score is not a deliverable."""
    name = str(ctx.facts["name"])
    try:
        package = load_fresh(name)
    except PackageError as exc:
        raise ValueError(f"the package could not be loaded: {exc}") from exc
    mode_class = package.modes[0]
    workdir = ctx.workdir / "smoke"
    mode = mode_class()
    result = mode.evaluate(workdir, model=ctx.model)
    if not result.exit_ok:
        raise ValueError(f"the new mode did not finish a run: {result.error}")
    ctx.write_json(
        "smoke.json",
        {
            "mode": package.ids()[0],
            "score": result.score,
            "iterations": result.iterations,
            "duration_s": result.duration_s,
        },
    )
    return {"smoke_score": float(result.score), "smoke_ok": True}


def write_result(ctx: Any) -> dict[str, Any]:
    """The run's own summary, written where a person can read it."""
    payload = {
        "package": ctx.facts.get("name"),
        "extends": ctx.facts.get("extends"),
        "files": ctx.facts.get("files", []),
        "registered": ctx.facts.get("registered"),
        "smoke_score": ctx.facts.get("smoke_score"),
    }
    ctx.write_json("create.json", payload)
    return {"created": json.dumps(payload, default=str)}
