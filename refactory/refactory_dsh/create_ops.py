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


def _slug(text: str, *, words: int = 3) -> str:
    """A package name from what the run is for.

    A name is how the result is found later, so a run given only an idea still needs
    one. Three words, lowercased, `_`-joined -- which is the shape a package name
    already has to be, so the same text that asks for something also names it.
    """
    kept = [part for part in re.findall(r"[a-z0-9]+", text.lower()) if part]
    slug = "_".join(kept[:words]) or "created_pack"
    if slug[0].isdigit():
        slug = f"pkg_{slug}"
    return slug[:40]


def prepare(ctx: Any) -> dict[str, Any]:
    """Read the brief the run was given and publish it to the prompts."""
    task = dict(getattr(ctx, "task", {}) or {})
    # The walker passes name/brief in the run's task; a run on DSH carries them as
    # knobs, because DSH's task is the CEO's prompt. Both are the same fact.
    name = str(task.get("name") or ctx.knob("name", "") or "").strip()
    brief = str(task.get("brief") or ctx.knob("brief", "") or "").strip()
    if not name and brief:
        # "an idea" is enough to start: the name is how the result is found later, and
        # a slug of the brief is a better answer than refusing to begin.
        name = _slug(brief)
    if not name:
        raise ValueError(
            "create-mode needs what to build: pass {'name': ..., 'brief': ...} in the "
            "task (the walker) or the `name`/`brief` knobs (a run on DSH)"
        )
    extends = str(task.get("extends") or "").strip()
    revision = str(task.get("mode") or "").strip()
    intent = str(task.get("intent") or "Make a new package of modes.").strip()
    # The two create-v2 directives (its `--task` and `--data-node` flags): a Task
    # class the produced mode must satisfy, and whether the produced graph is a
    # DataNode that runs a subgraph once per item. Both reach the prompts as facts.
    task_ref = str(ctx.knob("task_ref", "") or "").strip()
    data_node = bool(ctx.knob("data_node", 0))
    reference = _revision_source(revision) if revision else _reference(extends)
    ctx.write_json("brief.json", {
        "name": name, "brief": brief, "extends": extends, "mode": revision,
        "task_ref": task_ref, "data_node": data_node,
    })
    return {
        "name": name,
        "brief": brief,
        "extends": extends,
        "revision_of": revision,
        "intent": intent,
        "validation": "(no validation has run yet)",
        "design_review": "(no design has been reviewed yet)",
        "api": API,
        "reference": reference,
        "task_ref": task_ref or "(no Task class was named)",
        "data_node": "yes" if data_node else "no",
    }


def _revision_source(identifier: str) -> str:
    """The source of the mode being revised, so a revision starts from it."""
    try:
        package, mode_class = registry.resolve(Path.cwd(), identifier)
    except PackageError as exc:
        raise ValueError(f"cannot revise {identifier!r}: {exc}") from exc
    root = registry.where(package.name, Path.cwd())
    if root is None:
        raise ValueError(f"cannot find the source of {identifier!r}")
    blocks = [
        f"# --- {path.name} ---\n{path.read_text()}"
        for path in sorted(root.glob("*.py"))
    ]
    ours = root / "modes" / f"{mode_class.name}.py"
    if ours.exists():
        blocks.append(f"# --- modes/{ours.name} (the mode being revised) ---\n{ours.read_text()}")
    return (
        f"This is a revision of `{identifier}`. Keep everything the brief does not ask "
        "to change, and say in the design what you changed and why.\n\n"
        + "\n\n".join(blocks)
    )


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


STRATEGY_SCHEMA = """\
Write one JSON object and nothing else:

{"considered": [{"approach": "...", "why_not": "..."}],
 "chosen": "<the approach>",
 "why": "<why it wins>",
 "will_not": ["<something deliberately out of scope>"],
 "closest_existing": "<package>/<mode>, or 'none'",
 "knobs": [{"name": "...", "kind": "int|float|bool|str|enum", "default": ...,
            "lo": null, "hi": null, "enum": [], "description": "..."}],
 "nodes": [{"id": "...", "kind": "op|agent|gate",
            "call": "<module>:<function>  (op and agent only)",
            "evaluator": "<module>:<function>  (gate only, instead of call)",
            "reads": [], "writes": [], "produces": [], "knobs": [],
            "notes": "..."}],
 "edges": [{"source": "...", "target": "...", "condition": null}],
 "start": "<node id>",
 "score": "<which produced fact score() reads, and why that is the right number>"}

Rules:
- At least two approaches in `considered`. One approach is not a choice.
- `nodes` and `edges` describe the graph you intend to write, so it can be checked
  before any code exists. Use the operations declared in `knobs` on the nodes that
  consume them.
- Every read must be written by some node, and every declared knob consumed.
- `closest_existing` must name a mode from the survey, or be 'none' if none is close.
"""


def survey(ctx: Any) -> dict[str, Any]:
    """What already exists, before anything is invented.

    Cheap and deterministic, and it makes "does this already exist?" a question the
    system can answer instead of a question nobody asked. Without it, `--extends` is
    the only reuse channel and it is manual.
    """
    installed: list[dict[str, Any]] = []
    for name in registry.discover_names(Path.cwd()):
        try:
            package = registry.load(name, Path.cwd())
        except PackageError as exc:
            installed.append({"package": name, "problem": str(exc)})
            continue
        entry: dict[str, Any] = {
            "package": name,
            "description": package.description,
            "requires": list(package.requires),
            "modes": [],
        }
        for mode_class in package.modes:
            try:
                mode = mode_class()
            except Exception as exc:
                entry["modes"].append({"name": mode_class.name, "problem": str(exc)})
                continue
            entry["modes"].append({
                "id": f"{name}/{mode_class.name}",
                "description": getattr(mode_class, "description", ""),
                "knobs": sorted(mode.parameters()),
                "nodes": sorted(mode.graph().nodes),
            })
        installed.append(entry)

    ctx.write_json("survey.json", {"packages": installed})
    lines = ["Existing packages and what they offer:", ""]
    for entry in installed:
        lines.append(f"- {entry['package']}: {entry.get('description', '')}")
        for mode in entry.get("modes", ()):
            if "problem" in mode:
                lines.append(f"    {mode['name']} (unusable: {mode['problem']})")
                continue
            lines.append(
                f"    {mode['id']}: knobs {mode['knobs']}; nodes {mode['nodes']}"
            )
    return {"survey": "\n".join(lines), "existing": [e["package"] for e in installed]}


def review_design(ctx: Any) -> str:
    """Gate: check the intended graph before any code is written.

    Catching a bad design here is far cheaper than catching bad code at `emit`, and
    it needs no new machinery: it is the same validator the loop uses, applied to
    the design's declared nodes and knobs rather than to a built graph. Calls are not
    resolved, because the package that will define them does not exist yet.
    """
    raw = ctx.read_text("strategy.json")
    attempts_allowed = int(ctx.knob("design_attempts", 2))
    problems = _review(raw)
    used = int(ctx.facts.get("design_attempts_used", 0)) + 1
    report = "\n".join(f"- {problem}" for problem in problems)
    ctx.write_text("strategy-review.txt", report or "ok\n")
    ctx.set_fact("design_review", report or "(the intended design is coherent)")
    if not problems:
        return "proceed"
    if used >= attempts_allowed:
        raise ValueError(
            f"the design is still not coherent after {used} attempt(s):\n{report}"
        )
    ctx.set_fact("design_attempts_used", used)
    return "reloop"


def _review(raw: str) -> list[str]:
    from refactory_dsh.nn import Graph, Issue, Knob, validate

    try:
        payload = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError) as exc:
        return [f"the strategy answer is not a JSON object: {exc}"]

    problems: list[str] = []
    considered = payload.get("considered") or []
    if len(considered) < 2:
        problems.append(
            f"only {len(considered)} approach was considered; a choice needs at least two"
        )
    for field in ("chosen", "why", "score"):
        if not str(payload.get(field) or "").strip():
            problems.append(f"the strategy says nothing about {field!r}")

    existing = set(ctx_existing())
    closest = str(payload.get("closest_existing") or "none")
    if closest != "none" and closest not in existing:
        problems.append(
            f"closest_existing names {closest!r}, which is not an installed mode; "
            f"known: {sorted(existing)}"
        )

    knobs: dict[str, Knob] = {}
    for entry in payload.get("knobs") or ():
        try:
            knobs[str(entry["name"])] = Knob.from_spec({
                "default": entry.get("default"),
                "kind": entry.get("kind", "float"),
                "lo": entry.get("lo"),
                "hi": entry.get("hi"),
                "enum": entry.get("enum") or [],
                "frozen": entry.get("frozen", False),
                "description": entry.get("description", ""),
            })
        except Exception as exc:
            problems.append(f"knob {entry.get('name')!r} is not valid: {exc}")
    if not knobs:
        problems.append("the strategy declares no knobs, so there is nothing to optimize")

    try:
        graph = Graph.from_state({
            "start": str(payload.get("start") or ""),
            "nodes": {str(n["id"]): n for n in payload.get("nodes") or ()},
            "edges": list(payload.get("edges") or ()),
        })
    except Exception as exc:
        return problems + [f"the declared graph does not build: {exc}"]

    for issue in validate(
        graph, {name: knob.default for name, knob in knobs.items()},
        declared_knobs=knobs, resolve_calls=False,
    ):
        problems.append(str(issue))
    return problems


def ctx_existing() -> list[str]:
    """The installed mode ids, for a design to be compared against."""
    ids: list[str] = []
    for name in registry.discover_names(Path.cwd()):
        try:
            package = registry.load(name, Path.cwd())
        except PackageError:
            continue
        ids.extend(package.ids())
    return ids


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
        problems = check_package(
            name,
            requires_data_node=bool(ctx.knob("data_node", 0)),
            requires_task_ref=str(ctx.knob("task_ref", "") or "").strip(),
        )
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


def check_package(
    name: str, *, requires_data_node: bool = False, requires_task_ref: str = ""
) -> list[str]:
    """Everything wrong with a staged package, as a list of reasons.

    A generated package is **never imported here**. It is parsed, and then every
    semantic check — the name, the ops modules, each mode's construction, its score
    and its knobs, every validator rule — runs in a subprocess, because loading a
    package is executing it and the model's code has no business running in the
    driver. See :func:`refactory_dsh.sandbox.verify_sandboxed`.

    The two checks are both run rather than the first that fires: the retry prompt
    is more useful the more of the truth it carries, and parsing and semantics
    catch different things.
    """
    from refactory_dsh import sandbox

    registry.install_workspace(Path.cwd())
    root = registry.workspace_root(Path.cwd()) / name
    if not root.is_dir():
        return [f"no directory at {root}"]
    problems: list[str] = []
    for required in REQUIRED_FILES:
        if not (root / required).exists():
            problems.append(f"{required} is missing; a package needs one")
    if problems:
        return problems

    problems.extend(sandbox.ast_check(name, Path.cwd()))
    if any("does not parse" in problem for problem in problems):
        # Nothing further can be learned from a file that is not valid Python.
        return problems

    checked = sandbox.verify_sandboxed(
        name,
        project=Path.cwd(),
        requires_data_node=requires_data_node,
        requires_task_ref=requires_task_ref,
    )
    problems.extend(checked.payload.get("problems") or [])
    if not checked.ok and not problems:
        return [checked.problem() or "the structural check could not be run"]
    return problems


def register(ctx: Any) -> dict[str, Any]:
    """Confirm the registry now sees the package under its own name."""
    name = str(ctx.facts["name"])
    registry.install_workspace(Path.cwd())
    importlib.invalidate_caches()
    if name not in registry.discover_names(Path.cwd()):
        raise ValueError(f"the registry does not list a package named {name!r}")
    # Read the manifest where it is safe to load it. Reporting what was registered
    # must not run the package in the driver's process to find out.
    from refactory_dsh import sandbox

    described = sandbox.describe_package(name, project=Path.cwd())
    if not described.ok:
        raise ValueError(
            f"the package is registered but not readable: {described.problem()}"
        )
    ctx.write_json("registered.json", described.payload["describe"])
    return {"registered": ", ".join(described.payload["ids"])}


def smoke(ctx: Any) -> dict[str, Any]:
    """Run the new mode once. A mode that cannot score is not a deliverable."""
    name = str(ctx.facts["name"])
    workdir = ctx.workdir / "smoke"
    # Sandboxed: the smoke run is a real execution of code a model wrote, and even
    # reading which mode to run is done out of process.
    from refactory_dsh import sandbox

    described = sandbox.describe_package(name, project=Path.cwd())
    if not described.ok:
        raise ValueError(f"the package could not be read: {described.problem()}")
    identifier = described.payload["ids"][0]
    result = sandbox.smoke(identifier, workdir, project=Path.cwd())
    if not result.ok:
        raise ValueError(f"the new mode did not finish a run: {result.problem()}")
    score = float(result.payload.get("score") or 0.0)
    ctx.write_json(
        "smoke.json",
        {
            "mode": identifier,
            "score": score,
            "iterations": result.payload.get("iterations"),
            "sandboxed": True,
        },
    )
    return {"smoke_score": score, "smoke_ok": True}


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


# ── the v2 review phases ───────────────────────────────────────────────────
#
# create-v2 runs three reviews in parallel (a health check, a code review, and an
# adversarial QA director), merges them into one synthesis, and then audits the
# synthesis with an overwatch pass. Our fork/join does the parallel part; these
# ops do the merge and the two gates, so the three verdicts a reader cares about
# (did every review run, is anything blocking, did overwatch pass) are decided by
# the runtime rather than by an agent's summary of itself.

#: The reviews the fork produces, in the order the synthesis reads them.
QA_REVIEWS = (
    "reviews/health-check.md",
    "reviews/code-review.md",
    "reviews/qa.md",
)

#: How a review marks a finding it will not pass with.
BLOCKING_MARK = "BLOCKING"

#: How the overwatch pass states its own verdict.
PASS_MARK = "VERDICT: PASS"


def synthesize_qa(ctx: Any) -> dict[str, Any]:
    """Merge the three reviews into one file, and count the blocking findings.

    A review that never ran is reported as missing rather than skipped: an absent
    review is not a review that passed, which is the mistake this phase exists to
    prevent.
    """
    sections: list[str] = []
    missing: list[str] = []
    for name in QA_REVIEWS:
        text = ctx.read_text(name).strip() if ctx.exists(name) else ""
        if not text:
            missing.append(name)
            continue
        sections.append(f"## {name}\n\n{text}")
    # Only a review's own finding line counts. The header below mentions the marker,
    # and a gate that searched the whole file for the word would read its own summary
    # as a finding.
    blocking = sum(
        1
        for section in sections
        if any(line.strip().startswith(BLOCKING_MARK) for line in section.splitlines())
    )
    header = (
        "# QA synthesis\n\n"
        f"{len(sections)} review(s) merged, {blocking} blocking finding(s), "
        f"{len(missing)} review(s) missing.\n"
    )
    if missing:
        header += "\n## WARNING: NO REVIEW FROM " + ", ".join(missing) + "\n"
    body = header + "\n" + "\n\n".join(sections) + "\n"
    ctx.write_text("reviews/qa-synthesized.md", body)
    return {"qa": body, "qa_blocking": blocking, "qa_missing": len(missing)}


def gate_qa(ctx: Any) -> str:
    """Proceed only when every review ran and none named a blocking finding."""
    text = ctx.read_text("reviews/qa-synthesized.md")
    if "WARNING: NO REVIEW FROM" in text:
        ctx.write_text("reviews/qa-gate.txt", "reloop: a review did not run\n")
        return "reloop"
    if any(line.strip().startswith(BLOCKING_MARK) for line in text.splitlines()):
        ctx.write_text("reviews/qa-gate.txt", f"reloop: a {BLOCKING_MARK} finding\n")
        return "reloop"
    ctx.write_text("reviews/qa-gate.txt", "proceed: no blocking finding\n")
    return "proceed"


def gate_overwatch(ctx: Any) -> str:
    """Proceed only when the overwatch pass stated a PASS verdict."""
    text = ctx.read_text("reviews/overwatch.md")
    if PASS_MARK in text.upper():
        ctx.write_text("reviews/overwatch-gate.txt", "proceed: overwatch passed\n")
        return "proceed"
    ctx.write_text(
        "reviews/overwatch-gate.txt",
        f"reloop: overwatch did not state {PASS_MARK!r}\n",
    )
    return "reloop"
