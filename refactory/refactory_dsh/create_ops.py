"""The operations create-mode's own graph calls.

Creating a package is a mode like any other: it has a graph, it runs through the
same executor, and its gate refuses a package that will not run. The difference
is only what it produces — a directory of Python that becomes a new package.
"""

from __future__ import annotations

import importlib
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from refactory_dsh import registry
from refactory_dsh.paths import project_root, project_for_nodes
from refactory_dsh.nn import Package, PackageError, validate_state

STAGING = "staging"

#: The marker the emit agent uses to separate files. JSON would need every
#: newline escaped, which is where a generated source file usually dies.
FILE_MARKER = re.compile(r"^===FILE:\s*([A-Za-z0-9_./-]+)\s*===\s*$", re.MULTILINE)

REQUIRED_FILES = ("__init__.py",)

#: The node field that would bake a data node's items into the graph. The loader reads
#: it before the Task or the source, so setting it closes the harness's input port.
BAKED_ITEMS_KEY = "inline_items"

#: Why a creation with no Task and no `single_shot` is refused rather than defaulted.
#:
#: A harness is for a class of work, not for one instance of it. A Task is how a class
#: of work is named, and naming one is what binds the produced graph's `data` node to
#: the items the run iterates, so `create` requires the caller to say which it is
#: instead of quietly producing a single-shot harness or inventing a Task.
CREATION_DIRECTIVE = (
    "create-mode needs to know what it is building. Name the class of work with a Task "
    "— `--task <module:Class>` on the command line, or the `task_ref` knob — and the "
    "produced harness binds a `data` node to that Task and runs its subgraph once per "
    "item the Task lists. Or say `--single` (the `single_shot` knob) for a harness that "
    "runs once, for one input, with no Task binding. The class of work is the default "
    "because a harness built for one hardcoded example is the special case, not the "
    "product, and only naming a Task makes the per-item binding real."
)

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


def _mode_contracts(name: str) -> dict[str, list[dict[str, Any]]]:
    """Read actual data bindings where loading generated code is already contained."""
    from refactory_dsh import sandbox

    result = sandbox.describe_package(name, project=project_for_nodes(), include_contracts=True)
    if not result.ok:
        raise ValueError(f"cannot read the mode's Task bindings: {result.problem()}")
    return result.payload["mode_contracts"]


def _required_bindings(ctx: Any, revision: str) -> tuple[list[str], bool]:
    """Explicit directives win; otherwise a revision preserves the source contract."""
    explicit = str(ctx.knob("task_ref", "") or "").strip()
    requires_data = bool(ctx.knob("data_node", 0)) or bool(explicit)
    if explicit or bool(ctx.knob("single_shot", 0)) or not revision:
        return ([explicit] if explicit else []), requires_data
    package, _mode = registry.split(revision)
    contracts = _mode_contracts(package)
    if revision not in contracts:
        raise ValueError(f"the source package has no mode {revision!r}")
    nodes = contracts[revision]
    refs = sorted({node["task_ref"] for node in nodes if node.get("task_ref")})
    return refs, requires_data or bool(nodes)


def _binding_metadata(task_refs: list[str], data_node: bool) -> dict[str, Any]:
    """Describe the actual or requested bindings without calling data input single-shot."""
    refs = sorted(set(task_refs))
    if refs:
        class_of_work = (
            "a harness for the class of work named by the Task " + refs[0]
            if len(refs) == 1 else "a harness for the classes of work named by the Tasks " + ", ".join(refs)
        ) + (
            ": a `data` node bound to each Task runs its subgraph once per item, "
            "folding each `verify` verdict into the run's facts; runtime sources may "
            "supply other items of that class"
        )
    elif data_node:
        class_of_work = "a harness with data nodes fed from runtime sources, with no Task binding"
    else:
        class_of_work = "a harness for one run of one thing, with no Task binding"
    return {"task_ref": refs[0] if len(refs) == 1 else "", "task_refs": refs,
            "class_of_work": class_of_work, "data_node": "yes" if data_node else "no"}


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
    revision = str(task.get("mode") or ctx.knob("revision_of", "") or "").strip()
    intent = str(task.get("intent") or "Make a new package of modes.").strip()
    # The creation directives: a Task class the produced mode must satisfy, whether
    # the caller asked for the single-shot special case, and whether the produced
    # graph is a DataNode that runs a subgraph once per item. A Task names a class of
    # work, so naming one implies the data node rather than asking for it twice.
    task_ref = str(ctx.knob("task_ref", "") or "").strip()
    single_shot = bool(ctx.knob("single_shot", 0))
    # A revision is not a creation: `edit` walks this same graph with a mode to
    # revise, and the creation directive is about what a *new* harness is for.
    if not revision and not task_ref and not single_shot:
        raise ValueError(CREATION_DIRECTIVE)
    # The inputs the brief says the mode must take. Published as a fact so the design
    # and emit prompts state them, and read by `validate` so a mode that hardcodes a
    # fixture instead is refused rather than shipped.
    inputs = declared_inputs(ctx.knob("requires_input", ""))
    from refactory_dsh.sandbox import DEFAULT_MODEL_SMOKE_TIMEOUT_S
    smoke_timeout_s = int(ctx.knob("smoke_timeout_s", DEFAULT_MODEL_SMOKE_TIMEOUT_S))
    if not 1 <= smoke_timeout_s <= 86400:
        raise ValueError("smoke_timeout_s must be between 1 and 86400 seconds")
    # Fail before research, using the exact downstream HOME rewrite and scrub.
    # Other ModelClient implementations own their own readiness contract.
    from refactory_dsh.model import DshModelClient
    from refactory_dsh.sandbox import model_readiness

    client = getattr(ctx, "model", None)
    if client is None or isinstance(client, DshModelClient):
        readiness = model_readiness(
            ctx.workdir / ".model-readiness",
            model_context=client.context() if isinstance(client, DshModelClient) else None,
        )
        if readiness.get("warning"):
            import sys

            print(f"refactory: warning: {readiness['warning']}", file=sys.stderr)
    task_refs, data_node = _required_bindings(ctx, revision)
    metadata = _binding_metadata(task_refs, data_node)
    task_ref = metadata["task_ref"]
    reference = _revision_source(revision) if revision else _reference(extends)
    ctx.write_json("brief.json", {
        "name": name, "brief": brief, "extends": extends, "mode": revision,
        "task_ref": task_ref, "task_refs": task_refs,
        "single_shot": single_shot, "data_node": data_node,
        "requires_input": list(inputs), "smoke_timeout_s": smoke_timeout_s,
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
        "task_ref": task_ref,
        "class_of_work": metadata["class_of_work"],
        "data_node": metadata["data_node"],
        "requires_input": (
            "The brief says this mode takes these as input: " + ", ".join(f"`{name}`" for name in inputs)
            + ". It must receive each one as a declared knob named after it, or by "
            "reading a file under that name from the run directory. A mode that "
            "hardcodes a fixture instead of accepting the input is refused by "
            "validation."
        ) if inputs else "(the brief named no inputs this mode must accept)",
    }


def _plan(text: str) -> list[dict[str, str]]:
    """The plan's entries, or an empty list when the answer is not a plan.

    Tolerant about the fences and prose around the array — a model that writes
    "here is the plan:" before it has still written a plan — and intolerant about
    everything else, because the alternative is a downstream stage that runs on a
    stage which did nothing.
    """
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    entries: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        entries.append({
            key: str(item.get(key) or "").strip() for key in ("focus", "slug", "finding")
        })
    return entries


def _excerpt(text: str, limit: int = 200) -> str:
    """The start of an answer, on one line, for a retry prompt to read."""
    flat = " ".join(str(text).split())
    return f"{flat[:limit]}{'…' if len(flat) > limit else ''}" if flat else "(empty)"


def record_findings(ctx: Any) -> dict[str, Any]:
    """Put every direction's answer on disk, so the evidence exists before it is used.

    The research director's subagents write `research/<slug>.md` themselves; this fills
    in a direction that has no note from the finding the plan carries. It is what makes
    the plan's `finding` field load-bearing rather than decorative, and it is why a
    walker run — where the agent's answer *is* the plan, and no subagent exists — leaves
    the same artifacts a DSH run does.
    """
    recorded: list[str] = []
    for entry in _plan(ctx.read_text("research-plan.json")):
        slug = entry["slug"]
        if not slug or not RESEARCH_SLUG.fullmatch(slug):
            continue
        path = f"research/{slug}.md"
        if ctx.exists(path) and ctx.read_text(path).strip():
            continue
        ctx.write_text(path, f"# {entry['focus']}\n\n{entry['finding']}\n")
        recorded.append(path)
    return {"findings_recorded": recorded}


def gate_research(ctx: Any) -> str:
    """Proceed only when the plan is a plan and its findings are on disk.

    The stage exists to ground the design in facts, and a node that narrates its
    intent instead of producing the array satisfies ``writes=("research-plan.json",)``
    with any bytes at all: two real runs wrote a prose status message about subagents
    "running in the background", left ``research/`` empty, and the design proceeded on
    unverified assumptions. The reasons go to `research-review.txt` and back into the
    next attempt's prompt, exactly as the design review's do.
    """
    raw = ctx.read_text("research-plan.json")
    allowed = int(ctx.knob("research_attempts", 3))
    problems: list[str] = []
    entries = _plan(raw)
    if not entries:
        problems.append(
            "research-plan.json is not a JSON array of {focus, slug, finding} objects; "
            f"it holds: {_excerpt(raw)}"
        )
    else:
        if not 3 <= len(entries) <= 5:
            problems.append(
                f"the plan has {len(entries)} direction(s); three to five are required"
            )
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            label = entry["slug"] or f"entry {index + 1}"
            for field in ("focus", "slug", "finding"):
                if not entry[field]:
                    problems.append(f"{label}: `{field}` is empty")
            slug = entry["slug"]
            if slug and not RESEARCH_SLUG.fullmatch(slug):
                problems.append(
                    f"{label}: `slug` must be lower-case letters, digits and dashes"
                )
            if slug and slug in seen:
                problems.append(f"{label}: `slug` is used twice")
            seen.add(slug)
            path = f"research/{slug}.md"
            if slug and RESEARCH_SLUG.fullmatch(slug) and not (
                ctx.exists(path) and ctx.read_text(path).strip()
            ):
                problems.append(f"{label}: {path} is missing or empty")
    used = int(ctx.facts.get("research_attempts_used", 0)) + 1
    report = "\n".join(f"- {problem}" for problem in problems)
    ctx.write_text("research-review.txt", report or "ok\n")
    ctx.set_fact(
        "research_review", report or "(every direction is answered and written down)"
    )
    if not problems:
        return "proceed"
    if used >= allowed:
        raise ValueError(
            f"the research is still not usable after {used} attempt(s):\n{report}"
        )
    ctx.set_fact("research_attempts_used", used)
    return "reloop"


def _revision_source(identifier: str) -> str:
    """The source of the mode being revised, so a revision starts from it."""
    try:
        package, mode_class = registry.resolve(project_for_nodes(), identifier)
    except PackageError as exc:
        raise ValueError(f"cannot revise {identifier!r}: {exc}") from exc
    root = registry.where(package.name, project_for_nodes())
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
    root = registry.where(package_name, project_for_nodes()) or registry.packages_root() / package_name
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


RESEARCH_SCHEMA = """\
Write one JSON array and nothing else, one object per direction:

[{"focus": "<the question this direction answers>",
  "slug": "<a file-name-safe name for it>",
  "finding": "<what the answer is, in one paragraph>"}]

Rules:
- Between three and five directions. Fewer than three is not research.
- `slug` is lower-case letters, digits and dashes only, and `research/<slug>.md` is
  where that direction's long form belongs.
- `finding` states what the answer *is*. A direction nobody could answer says so in
  the finding ("not answered: <why>") rather than being dropped or invented.
- A plan that says subagents are still working is not a plan: by the time you answer,
  the work is either done or it is not.
"""

#: A slug a research file can be named by.
RESEARCH_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def survey(ctx: Any) -> dict[str, Any]:
    """What already exists, before anything is invented.

    Cheap and deterministic, and it makes "does this already exist?" a question the
    system can answer instead of a question nobody asked. Without it, `--extends` is
    the only reuse channel and it is manual.
    """
    installed: list[dict[str, Any]] = []
    for name in registry.discover_names(project_for_nodes()):
        try:
            package = registry.load(name, project_for_nodes())
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
    for name in registry.discover_names(project_for_nodes()):
        try:
            package = registry.load(name, project_for_nodes())
        except PackageError:
            continue
        ids.extend(package.ids())
    return ids


def _package_relative(relative: str, name: str) -> str:
    """One emit path, as a path inside the package's own directory.

    The prompt asks for a path relative to the package root, and a model that reads
    "relative" as "relative to the checkout" answers with
    `refactory/refactory_packages/<name>/mode.py`. Joined onto the package root, that
    nests the package inside itself and the mistake surfaces four directories away as
    "__init__.py is missing". The prefix is stripped rather than accepted as a nesting,
    and a path that cannot be made package-relative is refused by name instead of
    written somewhere nobody looks.
    """
    from pathlib import PurePosixPath

    raw = str(relative).replace("\\", "/").strip()
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".")]
    if raw.startswith("/") or not parts:
        raise ValueError(
            f"the emit answer names {relative!r}, which is not a path inside the "
            f"package; write it relative to the package root, e.g. `mode.py`"
        )
    # Longest first, so `refactory/refactory_packages/<name>/x.py` loses the whole
    # prefix and `<name>/x.py` only its own.
    for prefix in (
        ("refactory", "refactory_packages", name),
        (".factory", "packages", name),
        ("refactory_packages", name),
        ("refactory", "refactory_packages"),
        (".factory", "packages"),
        (name,),
    ):
        if tuple(parts[: len(prefix)]) == prefix:
            parts = parts[len(prefix):]
            break
    if not parts or ".." in parts:
        raise ValueError(
            f"the emit answer names {relative!r}, which does not resolve to a file "
            f"inside the package root; write paths relative to it, e.g. `mode.py`"
        )
    return str(PurePosixPath(*parts))


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
    root = registry.workspace_root(project_for_nodes()) / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    written: list[str] = []
    for relative, content in files.items():
        inside = _package_relative(relative, name)
        target = root / inside
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"{relative!r} escapes the package directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(inside)
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
    registry.install_workspace(project_for_nodes())
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
    brief = ctx.read_json("brief.json", {}) or {}
    if "task_refs" in brief:
        task_refs, requires_data = list(brief["task_refs"]), bool(brief.get("data_node"))
    elif brief.get("task_ref") or brief.get("single_shot"):
        # Preserve an explicit directive already saved by an older prepare node.
        task_refs = [str(brief["task_ref"])] if brief.get("task_ref") else []
        requires_data = bool(brief.get("data_node")) or bool(task_refs)
    else:
        # Saved authoring runs before this metadata existed still inherit their source.
        revision = str(ctx.facts.get("revision_of") or brief.get("mode") or "")
        task_refs, requires_data = _required_bindings(ctx, revision)
        requires_data = requires_data or bool(brief.get("data_node"))
    try:
        problems = check_package(
            name,
            # The same implication `prepare` publishes as a fact: naming a Task is
            # what asks for a data node, so the gate checks for one either way.
            requires_data_node=requires_data,
            requires_task_refs=tuple(task_refs),
            requires_input=declared_inputs(ctx.knob("requires_input", "")),
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


def declared_inputs(raw: Any) -> tuple[str, ...]:
    """The inputs a brief declared, from the comma-separated knob a run carries.

    A name, not a sentence: the check is whether the produced mode has a *way to
    receive* it — a knob named after it, or a read under that name — so the name has
    to be something a knob or a file could be called. Anything shorter than three
    characters is refused rather than accepted, because `"a" in "package_path"` is not
    a check.
    """
    names = tuple(
        part.strip() for part in str(raw or "").replace(";", ",").split(",") if part.strip()
    )
    for name in names:
        if len(name) < 3:
            raise ValueError(
                f"required input {name!r} is too short to check; name it the way a knob "
                "or a file would be named, e.g. `package` or `source_tree`"
            )
    return names


def baked_item_problems(root: Path) -> list[str]:
    """Every place a package bakes item data into its graph instead of reading it.

    A data node is the harness's input port. Its items come from the Task it binds or
    from the `source_path`/`source_format` it names, loaded at run time by
    :func:`refactory_dsh.nn.executor.load_items`; `inline_items` is read first by that
    loader, so a package that sets it never reads the run's items at all. That is the
    same failure as hardcoding one sample, one level up. The check reads the source
    because the driver must not import model-written code, and it names the file and
    line so the emit step can act on it.
    """
    import ast

    problems: list[str] = []
    seen: set[tuple[str, int]] = set()
    for path in sorted(Path(root).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (SyntaxError, OSError):
            # `ast_check` already reports a file that does not parse.
            continue
        for node in ast.walk(tree):
            baked = (
                isinstance(node, ast.keyword) and node.arg == BAKED_ITEMS_KEY
            ) or (
                isinstance(node, ast.Dict)
                and any(
                    isinstance(key, ast.Constant) and key.value == BAKED_ITEMS_KEY
                    for key in node.keys
                )
            )
            if not baked:
                continue
            where = (path.name, int(getattr(node, "lineno", 0)))
            if where in seen:
                continue
            seen.add(where)
            problems.append(
                f"{where[0]}:{where[1]} bakes a fixed item list into the graph with "
                f"`{BAKED_ITEMS_KEY}`; a data node takes the items of the class of work "
                "from the Task it binds or from the `source_path` it names, loaded at "
                "run time, so the harness can be fed any item and not only the ones the "
                "authoring run seeded"
            )
    return problems


def _empty_literal(value: Any) -> bool:
    """Whether a source expression is a literal that names nothing.

    A keyword the emit step writes as `task_ref=""` or `source_path=""` names no
    source; an expression it computes at import time is left alone, because the
    driver cannot evaluate it and the graph-level checks still run.
    """
    import ast

    return isinstance(value, ast.Constant) and not str(value.value or "").strip()


def _names_a_run_time_source(fields: dict[str, Any]) -> bool:
    """Whether a data node's own fields feed it from a Task or a source."""
    task_ref = fields.get("task_ref")
    if task_ref is not None and not _empty_literal(task_ref):
        return True
    source_path = fields.get("source_path")
    source_format = fields.get("source_format")
    if source_path is None or source_format is None:
        return False
    return not _empty_literal(source_path) and not _empty_literal(source_format)


def unfeedable_data_node_problems(root: Path) -> list[str]:
    """Every data node that names no way to be fed at run time.

    A data node is the harness's input port, and the executor feeds it from the Task
    it binds or from the `source_path` and `source_format` it names. A node that
    declares neither iterates nothing however much the class of work holds, so the
    port is closed and the harness is not a harness. The check reads the source
    because the driver must not import model-written code, and it judges only a
    `Node(...)` call whose kind is the literal `"data"`: a node built by a helper is
    left to the graph-level checks.
    """
    import ast

    problems: list[str] = []
    for path in sorted(Path(root).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if ast.unparse(node.func).rsplit(".", 1)[-1] != "Node":
                continue
            kind = ""
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                kind = str(node.args[1].value)
            for keyword in node.keywords:
                if keyword.arg == "kind" and isinstance(keyword.value, ast.Constant):
                    kind = str(keyword.value.value)
            if kind != "data":
                continue
            fields = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            if _names_a_run_time_source(fields):
                continue
            line = int(getattr(node, "lineno", 0))
            problems.append(
                f"{path.name}:{line} declares a `data` node that names no way to be fed "
                "at run time; it takes the items of the class of work from `task_ref`, "
                "or from `source_path` with a `source_format` of `directory`, `jsonl` "
                "or `csv`, so the harness can be fed from outside the package"
            )
    return problems


def check_package(
    name: str,
    *,
    requires_data_node: bool = False,
    requires_task_ref: str = "",
    requires_task_refs: tuple[str, ...] = (),
    requires_input: tuple[str, ...] = (),
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

    registry.install_workspace(project_for_nodes())
    root = registry.workspace_root(project_for_nodes()) / name
    if not root.is_dir():
        return [f"no directory at {root}"]
    problems: list[str] = []
    for required in REQUIRED_FILES:
        if not (root / required).exists():
            problems.append(
                sandbox.missing_init(root)
                if required == "__init__.py"
                else f"{required} is missing from the package root {root}; a package needs one"
            )
    if problems:
        return problems

    problems.extend(sandbox.ast_check(name, project_for_nodes()))
    if any("does not parse" in problem for problem in problems):
        # Nothing further can be learned from a file that is not valid Python.
        return problems
    # Read before the graph is loaded: a harness whose items are baked in, or whose
    # input port names nothing, is refused for the same reason a hardcoded sample is,
    # and the emit step can fix the source.
    problems.extend(baked_item_problems(root))
    problems.extend(unfeedable_data_node_problems(root))

    checked = sandbox.verify_sandboxed(
        name,
        project=project_for_nodes(),
        requires_data_node=requires_data_node,
        requires_task_ref=requires_task_ref,
        requires_task_refs=requires_task_refs,
        requires_input=requires_input,
    )
    problems.extend(checked.payload.get("problems") or [])
    if not checked.ok and not problems:
        return [checked.problem() or "the structural check could not be run"]
    return problems


def register(ctx: Any) -> dict[str, Any]:
    """Confirm the registry now sees the package under its own name."""
    name = str(ctx.facts["name"])
    registry.install_workspace(project_for_nodes())
    importlib.invalidate_caches()
    if name not in registry.discover_names(project_for_nodes()):
        raise ValueError(f"the registry does not list a package named {name!r}")
    # Read the manifest where it is safe to load it. Reporting what was registered
    # must not run the package in the driver's process to find out.
    from refactory_dsh import sandbox

    described = sandbox.describe_package(name, project=project_for_nodes())
    if not described.ok:
        raise ValueError(
            f"the package is registered but not readable: {described.problem()}"
        )
    ctx.write_json("registered.json", described.payload["describe"])
    return {"registered": ", ".join(described.payload["ids"])}


#: A wheel's mandatory members, beside `RECORD`, for it to be a wheel at all.
WHEEL_REQUIRED = ("METADATA", "WHEEL")

#: How deep to look for the artifacts a run produced.
ARTIFACT_DEPTH = 6


def artifact_problems(root: Path) -> list[str]:
    """What is wrong with the archives a run produced, judged by their own format.

    No domain knowledge and no declaration needed: a file that says it is a wheel has
    to *be* one. This is what the other half of #69 needed — the revision's new input
    path emitted a `.dist-info` holding only `RECORD`, which is not a wheel, and the
    default fixture path emitted all three, so nothing noticed.

    A `.whl` or `.zip` that cannot be opened is a problem; a wheel missing `METADATA`
    or `WHEEL` beside its `RECORD` is a problem. Both name the file so the emit step
    can act on them.
    """
    import zipfile

    problems: list[str] = []
    if not Path(root).is_dir():
        return problems
    for path in sorted(Path(root).rglob("*")):
        if path.suffix.lower() not in (".whl", ".zip") or not path.is_file():
            continue
        if len(path.relative_to(root).parts) > ARTIFACT_DEPTH:
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
        except (zipfile.BadZipFile, OSError) as exc:
            problems.append(f"{path.relative_to(root)} is not a readable archive: {exc}")
            continue
        if path.suffix.lower() != ".whl":
            continue
        for required in WHEEL_REQUIRED:
            if not any(
                name.endswith(f".dist-info/{required}") for name in names
            ):
                problems.append(
                    f"{path.relative_to(root)} is not a valid wheel: it has no "
                    f"{required} beside its RECORD. A wheel needs the metadata that "
                    "names the distribution and the version of the wheel format."
                )
    return problems


def _sample_knobs(raw: Any) -> dict[str, str]:
    """The knob values a validation run should use, as `NAME=VALUE` pairs.

    A person states them when the brief's inputs are real: without them the smoke runs
    the default path, and the path the brief asked for goes untested. The value travels
    as a JSON array of strings in a knob (so a path containing `=`, a comma or a space
    survives), or as a list when a caller passes it directly.
    """
    items: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            items = []
        else:
            try:
                items = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"the sample knobs are not a JSON array of NAME=VALUE strings: {exc}"
                ) from exc
    pairs: dict[str, str] = {}
    for item in list(items or []):
        name, separator, value = str(item).partition("=")
        if not separator or not name.strip() or not value.strip():
            raise ValueError(f"--sample takes NAME=VALUE, not {item!r}")
        pairs[name.strip()] = value.strip()
    return pairs


def _model_context(ctx: Any) -> dict[str, str]:
    from refactory_dsh.model import DshModelClient

    client = getattr(ctx, "model", None)
    return (client if isinstance(client, DshModelClient) else DshModelClient()).context()


def _smoke_timeout_record(result: Any, identifier: str, workdir: Path, *, model: bool) -> dict[str, Any]:
    """Describe an incomplete pass without claiming a score or model request occurred."""
    return {
        "mode": identifier, "ok": False, "score": None, "timed_out": True,
        "returncode": result.returncode, "timeout_s": result.timeout_s,
        "elapsed_s": result.elapsed_s, "progress": result.progress,
        "workdir": str(workdir), "sandboxed": True,
        "model": "requested" if model else "none", "error": result.problem(),
    }


def smoke(ctx: Any) -> dict[str, Any]:
    """Run the new mode once, and compare a revision with what it revises.

    A mode that cannot score is not a deliverable, and a revision that scores lower
    than the mode it came from is not an improvement. The comparison happens here
    rather than in the driver because this is the only step that has just scored the
    new mode; the driver's own comparison only ran for `python -m refactory_dsh.edit`,
    which is not the path a person takes.
    """
    name = str(ctx.facts["name"])
    revision = str(ctx.facts.get("revision_of") or "").strip()
    workdir = ctx.workdir / "smoke"
    # Sandboxed: the smoke run is a real execution of code a model wrote, and even
    # reading which mode to run is done out of process.
    from refactory_dsh import sandbox

    timeout_s = int(ctx.knob("smoke_timeout_s", sandbox.DEFAULT_MODEL_SMOKE_TIMEOUT_S))
    if not 1 <= timeout_s <= 86400:
        raise ValueError("smoke_timeout_s must be between 1 and 86400 seconds")

    def record_timeout(result: Any, *, model: bool) -> None:
        if result.timed_out:
            ctx.write_json(
                "smoke.json", _smoke_timeout_record(result, identifier, workdir, model=model),
            )

    described = sandbox.describe_package(name, project=project_for_nodes())
    if not described.ok:
        raise ValueError(f"the package could not be read: {described.problem()}")
    identifier = described.payload["ids"][0]
    # A sample is how the brief's inputs are exercised at all: without one the smoke
    # runs the default knobs, which for an input-taking mode is the built-in fixture.
    samples = _sample_knobs(ctx.knob("sample_knobs", ""))
    result = sandbox.smoke(
        identifier, workdir, project=project_for_nodes(), knobs=samples or None,
        timeout_s=timeout_s,
    )
    record_timeout(result, model=False)
    if not result.ok:
        with_samples = f" (with {samples})" if samples else ""
        raise ValueError(f"the new mode did not finish a run{with_samples}: {result.problem()}")
    # A mode with an agent node cannot be scored here: the smoke runs keyless on
    # purpose, so the agent node has no model and the mode's own score() never sees a
    # real run. Recording that as 0.0 would say "this scored zero" when the truth is
    # "this was not measured", so the score is absent and the reason is stated.
    agent_nodes = _agent_nodes(name)
    if agent_nodes:
        # A mode with an agent node cannot be scored without a model, and almost every
        # mode worth shipping has one — so supply a model rather than declining to
        # measure the common case. The client reaches one exactly as every other path
        # does, using the trusted home/route while its environment stays scrubbed.
        scored = sandbox.smoke(
            identifier, workdir, project=project_for_nodes(), model=True,
            knobs=samples or None,
            model_context=_model_context(ctx), timeout_s=timeout_s,
        )
        record_timeout(scored, model=True)
        if scored.ok and scored.payload.get("exit_ok") is False:
            # The sandbox exited 0 while the *mode* failed: it ran and raised, so its
            # score is not a measurement of anything. Reporting 0.0 would say "this
            # scored zero" where the truth is "this did not finish".
            raise ValueError(
                "the new mode did not finish a run with a model: "
                f"{scored.payload.get('error') or 'it failed without saying why'}"
            )
        if scored.ok:
            score = float(scored.payload.get("score") or 0.0)
            ctx.write_json(
                "smoke.json",
                {
                    "mode": identifier,
                    "score": score,
                    "iterations": scored.payload.get("iterations"),
                    "sandboxed": True,
                    "model": "used", "timeout_s": timeout_s,
                    "elapsed_s": scored.elapsed_s,
                },
            )
            revised = _revision(
                ctx, revision, score,
                not_comparable=(
                    f"{identifier} calls a model ({', '.join(agent_nodes)}), and one "
                    "sample of a model call is not a score to refuse a revision on"
                ),
            )
            return {
                "smoke_score": score, "smoke_ok": True, "smoke_model": "used", **revised,
            }
        if not _could_not_reach_a_model(scored):
            raise ValueError(f"the new mode did not finish a run: {scored.problem()}")
        # No model on this machine: say "not measured" and name the command that does
        # score it, rather than reporting a zero nobody should read as a score.
        ctx.write_json(
            "smoke.json",
            {
                "mode": identifier,
                "score": None,
                "reason": "no model was reachable, and this mode calls one",
                "agent_nodes": agent_nodes,
                "detail": scored.problem(),
                "sandboxed": True,
                "model": "unavailable", "timeout_s": timeout_s,
                "elapsed_s": scored.elapsed_s,
            },
        )
        revised = _revision(
            ctx, revision, None,
            not_comparable=(
                f"{identifier} calls a model and none was reachable, so there is "
                "nothing to compare"
            ),
        )
        return {
            "smoke_score": None,
            "smoke_ok": True,
            "smoke_reason": (
                f"not measured: {identifier} calls a model ({', '.join(agent_nodes)}) and no "
                "model was reachable; set the provider credentials and use "
                "`refactory ceo " + identifier + "` to score it"
            ),
            **revised,
        }
    if result.payload.get("exit_ok") is False:
        # The sandbox exited 0 while the *mode* failed: it ran and raised, so its
        # score measures nothing. Reporting 0.0 would say "this scored zero" where
        # the truth is "this did not finish".
        with_samples = f" (with {samples})" if samples else ""
        raise ValueError(
            f"the new mode did not finish a run{with_samples}: "
            f"{result.payload.get('error') or 'it failed without saying why'}"
        )
    score = float(result.payload.get("score") or 0.0)
    _check_artifacts(workdir, samples)
    ctx.write_json(
        "smoke.json",
        {
            "mode": identifier,
            "score": score,
            "iterations": result.payload.get("iterations"),
            "sandboxed": True, "timeout_s": timeout_s, "elapsed_s": result.elapsed_s,
            **({"sample_knobs": samples} if samples else {}),
        },
    )
    return {"smoke_score": score, "smoke_ok": True, **_revision(ctx, revision, score)}


def _revision(
    ctx: Any, revision: str, after: float | None, *, not_comparable: str = "",
) -> dict[str, Any]:
    """Score the mode a revision came from, and refuse a revision that scores lower.

    The comparison is only meaningful between two modes a single smoke run can be
    repeated on: one sample of a mode that calls a model is not a score to refuse on,
    and a source mode with no ``score()`` has no number to compare against. Both cases
    are recorded with the reason, because "not compared" and "compared and fine" must
    not read alike.

    The provenance is written next to the revision rather than kept in the run: what a
    package came from, at which state, and what it scored are facts about the package
    that outlive the run directory. ``revision_of_state`` is the source's state dict at
    the moment of the revision, so "revised from X" means a state and not a name that
    may have moved since.
    """
    if not revision:
        return {}
    from refactory_dsh import registry, sandbox

    project = project_for_nodes()
    try:
        package, mode_class = registry.resolve(project, revision)
    except Exception as exc:
        raise ValueError(f"cannot revise {revision!r}: {exc}") from exc
    source = mode_class()
    reason = not_comparable
    if not reason and getattr(mode_class, "unscored_reason", ""):
        reason = f"{revision} is unscored, so there is no score to compare against"
    if not reason and [n for n in source.build().nodes.values() if n.kind == "agent"]:
        reason = f"{revision} calls a model, and one sample of it is not a score"

    before: float | None = None
    if not reason:
        scored = sandbox.smoke(
            revision, ctx.workdir / "smoke-source", project=project,
            model_context=_model_context(ctx),
            timeout_s=int(ctx.knob("smoke_timeout_s", sandbox.DEFAULT_MODEL_SMOKE_TIMEOUT_S)),
        )
        if scored.timed_out:
            # Keep the new mode's completed smoke receipt intact. The source pass
            # is incomplete, so neither non-regression nor provenance is established.
            ctx.write_json("revision.json", {
                **_smoke_timeout_record(scored, revision, ctx.workdir / "smoke-source", model=False),
                "phase": "source-comparison", "revision_of": revision,
                "before_score": None, "after_score": after, "regressed": None,
            })
            raise ValueError(f"the revision source {revision} did not finish a run: {scored.problem()}")
        if scored.ok:
            before = float(scored.payload.get("score") or 0.0)
        else:
            reason = f"{revision} could not be scored: {scored.problem()}"

    state = source.state_dict()
    from refactory_dsh.optim.base import candidate_id

    regressed = before is not None and after is not None and after < before
    allowed = bool(ctx.knob("allow_regression", 0))
    recorded = {
        "revision_of": revision,
        "revision_of_state": state,
        "revision_of_digest": candidate_id(state),
        "before_score": before,
        "after_score": after,
        "regressed": regressed,
        "allow_regression": allowed,
        "note": reason,
    }
    ctx.write_json("revision.json", recorded)
    # Where the package is, when the run has got that far. A node invoked without one
    # (a test, or a run that stopped earlier) must not write provenance into whatever
    # directory it happens to be standing in.
    package = str(ctx.facts.get("package_path") or "")
    if package:
        path = Path(package) / ".origin.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        current.update({key: value for key, value in recorded.items() if key != "note"})
        path.write_text(json.dumps(current, indent=2, default=str))
    if regressed and not allowed:
        raise ValueError(
            f"the revision scores {after} where {revision} scores {before}, so it is not "
            "an improvement; pass `--knob allow_regression=1` if that is intended"
        )
    return {
        "before_score": before,
        "after_score": after,
        "regressed": regressed,
        "revision_note": (
            reason or f"compared with {revision}: {before} → {after}"
        ),
    }


def _check_artifacts(workdir: Path, samples: dict[str, str]) -> None:
    """Refuse a run whose archives are not the format they claim to be.

    The score is not enough: a mode can score above zero while emitting a file that is
    not a wheel. A sample run is the one that produces a real artifact, so this is
    where the check belongs.
    """
    problems = artifact_problems(workdir)
    if not problems:
        return
    with_samples = f" (run with {samples})" if samples else ""
    detail = "\n".join(f"- {problem}" for problem in problems)
    raise ValueError(
        f"the run produced an artifact that is not what it claims to be{with_samples}:\n{detail}"
    )


def _could_not_reach_a_model(result: Any) -> bool:
    """Whether a smoke failed because there was no model, rather than because of the mode.

    The two are different facts: a mode that cannot reach a model is unmeasured, and a
    mode that ran and failed is a defect the emit step has to fix.
    """
    problem = result.problem().lower()
    return any(
        marker in problem
        for marker in ("needs a model client", "model completion failed", "no model", "credentials", "api key")
    )


def _agent_nodes(name: str) -> list[str]:
    """The agent nodes of a package's first mode, or an empty list.

    Read from the package rather than from a prompt: "does this mode call a model" is
    a property of its graph.
    """
    from refactory_dsh import registry

    try:
        package = registry.load(name, project_for_nodes())
        mode = package.modes[0]()
        return [node.id for node in mode.build().nodes.values() if node.kind == "agent"]
    except Exception:
        # A package that will not build is the validate gate's finding, not this
        # one's; saying nothing here keeps the two failures distinguishable.
        return []


def write_result(ctx: Any) -> dict[str, Any]:
    """The run's own summary, written where a person can read it."""
    contracts = _mode_contracts(str(ctx.facts["name"]))
    nodes = [node for mode_nodes in contracts.values() for node in mode_nodes]
    metadata = _binding_metadata([node["task_ref"] for node in nodes if node.get("task_ref")], bool(nodes))
    payload = {
        "package": ctx.facts.get("name"),
        "extends": ctx.facts.get("extends"),
        "files": ctx.facts.get("files", []),
        "registered": ctx.facts.get("registered"),
        "smoke_score": ctx.facts.get("smoke_score"),
        # What kind of harness this run produced: the Task it is bound to, or that it
        # is the single-shot special case. A reader of the run's own result must not
        # have to infer that from the package.
        **metadata,
        "mode_contracts": contracts,
    }
    if ctx.facts.get("revision_of"):
        payload.update({
            "revision_of": ctx.facts.get("revision_of"),
            "before_score": ctx.facts.get("before_score"),
            "after_score": ctx.facts.get("after_score"),
            "regressed": ctx.facts.get("regressed"),
            "revision_note": ctx.facts.get("revision_note"),
        })
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


def marks(line: str, marker: str) -> bool:
    """Whether a line states the marker, however a markdown author decorated it.

    A reviewer that writes `**BLOCKING:** the test is deleted` has marked a blocking
    finding, and `line.startswith("BLOCKING")` does not see it. That is not
    hypothetical: a real run merged three reviews into a synthesis that said "0
    blocking finding(s)" and let the QA gate say `proceed` while the same body held
    four bolded blockers — and it was the overwatch pass, refusing to state PASS,
    that caught it.
    """
    cleaned = line.strip().lstrip("*-+>#\t ").lstrip("*_` ")
    return cleaned.upper().startswith(marker.upper())

#: How the overwatch pass states its own verdict.
PASS_MARK = "VERDICT: PASS"


def blocking_findings(text: str) -> list[str]:
    """Current marked findings, without empty verdicts or appended old reports."""
    findings: list[str] = []
    historical = False
    fence: tuple[str, int] | None = None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        fence_line = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
        if fence is not None:
            if (fence_line and fence_line[1][0] == fence[0]
                    and len(fence_line[1]) >= fence[1] and not fence_line[2].strip()):
                fence = None
            continue
        if fence_line:
            fence = (fence_line[1][0], len(fence_line[1]))
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            title = heading[1].strip(" *_`")
            if re.match(r"^(?:current|latest) (?:review|findings|verdict|result|revision)\b", title, re.I):
                historical = False
            elif re.match(r"^(?:previous (?:review|run|attack)|superseded|historical (?:review|findings)|archived review)\b", title, re.I) or re.search(r"\(superseded\)", title, re.I):
                historical = True
        if historical:
            continue
        cleaned = stripped.lstrip("*-+>#` \t")
        cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned).lstrip("*_` ")
        # Retain existing decorated/no-colon finding syntax. A bare BLOCKING
        # heading or a 'Blocking findings' section title carries no finding.
        marked = re.match(r"^BLOCKING(?:(?:[*_`]*\s*[:—–-][*_`]*\s*)|(?:[*_`]+\s+)|\s+)(.*)$", cleaned, re.I)
        if marked is None:
            continue
        content = marked[1].strip()
        if heading and not re.match(r"^BLOCKING[*_`]*\s*[:—–-]", cleaned, re.I):
            continue
        # 'none.' is a verdict; the following explanation does not turn it into
        # a defect. 'none, except ...' remains actionable instead of being hidden.
        verdict_text = re.sub(r"[*_`]", "", content)
        verdict = re.split(r"[.!](?:\s|$)", verdict_text, maxsplit=1)[0].strip()
        if not content or re.fullmatch(
            r"(?:none|0|no(?: current)?(?: blocking)?(?: findings| issues| defects)?)"
            r"(?:\s*\(current revision\))?", verdict, re.I,
        ):
            continue
        continuation = []
        for following in lines[index + 1:]:
            if not following.strip() or following.lstrip().startswith(("#", "```", "~~~")) or marks(following, BLOCKING_MARK):
                break
            continuation.append(following.strip())
        findings.append("\n".join([content, *continuation]))
    return findings


def _review_snapshot(ctx: Any) -> dict[str, Any]:
    reviews: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in QA_REVIEWS:
        text = ctx.read_text(name) if ctx.exists(name) else ""
        if not text.strip():
            missing.append(name)
            continue
        reviews.append({"path": name, "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "findings": blocking_findings(text)})
    return {"version": 1, "reviews": reviews, "missing": missing}


def synthesize_qa(ctx: Any) -> dict[str, Any]:
    """Preserve the full reports and publish the current findings used by the gate."""
    snapshot = _review_snapshot(ctx)
    blocking = sum(len(review["findings"]) for review in snapshot["reviews"])
    missing = snapshot["missing"]
    sections = [f"## {review['path']}\n\n{ctx.read_text(review['path']).strip()}"
                for review in snapshot["reviews"]]
    body = (
        "# QA synthesis\n\n"
        f"{len(sections)} review(s) merged, {blocking} blocking finding(s), "
        f"{len(missing)} review(s) missing.\n"
        + ("\n## WARNING: NO REVIEW FROM " + ", ".join(missing) + "\n" if missing else "")
        + "\n" + "\n\n".join(sections) + "\n"
    )
    manifest = {"version": 1, "digests": {review["path"]: review["sha256"] for review in snapshot["reviews"]},
                "missing": missing}
    body += "\n<!-- refactory-qa-snapshot " + json.dumps(manifest, sort_keys=True) + " -->\n"
    ctx.write_text("reviews/qa-synthesized.md", body)
    return {"qa": body, "qa_blocking": blocking, "qa_missing": len(missing)}


def _legacy_reviews_match(ctx: Any, text: str, snapshot: dict[str, Any]) -> bool:
    """Compare complete legacy report sections, never a matching substring."""
    headings = list(re.finditer(r"^## (reviews/(?:health-check|code-review|qa)\.md)\s*$", text, re.M))
    names = [heading[1] for heading in headings]
    expected = [review["path"] for review in snapshot["reviews"]]
    if names != expected or len(names) != len(set(names)):
        return False
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        if text[heading.end():end].strip() != ctx.read_text(heading[1]).strip():
            return False
    return True


def gate_qa(ctx: Any) -> str:
    """Route from current findings, checking the synthesis's review snapshots."""
    text = ctx.read_text("reviews/qa-synthesized.md")
    snapshot = _review_snapshot(ctx)
    manifest = {"version": 1, "digests": {review["path"]: review["sha256"] for review in snapshot["reviews"]},
                "missing": snapshot["missing"]}
    recorded = re.search(r"<!-- refactory-qa-snapshot (.+) -->\s*$", text)
    if recorded is not None:
        try:
            fresh = json.loads(recorded[1]) == manifest
        except ValueError:
            fresh = False
    else:
        # Historical synthesis already embeds each report verbatim. Accept that
        # representation only while every present report still matches its text.
        fresh = _legacy_reviews_match(ctx, text, snapshot)
    if not fresh:
        reason = "reloop: review reports changed after synthesis; run fresh reviews and synthesis"
    elif snapshot["missing"]:
        reason = "reloop: a review did not run: " + ", ".join(snapshot["missing"])
    else:
        findings = [f"{review['path']}: {finding}" for review in snapshot["reviews"]
                    for finding in review["findings"]]
        reason = "reloop: " + "\n".join(findings) if findings else ""
    if reason:
        ctx.write_text("reviews/qa-gate.txt", reason + "\n")
        ctx.set_fact("qa_gate", reason)
        return "reloop"
    ctx.write_text("reviews/qa-gate.txt", "proceed: no blocking finding\n")
    ctx.set_fact("qa_gate", "(the reviews found nothing blocking)")
    return "proceed"


def gate_overwatch(ctx: Any) -> str:
    """Proceed only when the overwatch pass stated a PASS verdict."""
    text = ctx.read_text("reviews/overwatch.md")
    if any(marks(line, PASS_MARK) for line in text.splitlines()) or PASS_MARK in text.upper():
        ctx.write_text("reviews/overwatch-gate.txt", "proceed: overwatch passed\n")
        ctx.set_fact("overwatch_review", "(overwatch passed)")
        return "proceed"
    # The audit's own findings are the most useful thing a rebuild can be given, so
    # they travel both ways: the file for a reader, the fact for the next attempt.
    reason = (
        "reloop: overwatch did not state " + repr(PASS_MARK) + ". What it found:\n"
        + _excerpt(text, 2000)
    )
    ctx.write_text("reviews/overwatch-gate.txt", reason + "\n")
    ctx.set_fact("overwatch_review", reason)
    return "reloop"
