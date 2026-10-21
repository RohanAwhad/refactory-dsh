"""Fixtures shared by the tests that need a generated package.

Kept in one module so `test_create.py` and `test_example.py` exercise the same
package rather than two copies that drift apart. The DSH profile helpers
``_profile`` and ``_link_bundle`` are shared by test_dsh_profile.py,
test_cli.py and test_interactive.py.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from refactory_dsh import dsh_profile


def _profile(home: Path, name: str, bundles: list[str]) -> Path:
    """Create a minimal DSH profile directory with a manifest."""
    directory = home / "profiles" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(
        json.dumps({
            "name": f"dsh-profile-{name}",
            "dependencies": {
                bundle: "link:/checkout"
                for bundle in bundles
                if bundle in {entry.bundle for entry in dsh_profile.LAYERS.values()}
            },
            "dsh": {"profile": {"bundles": bundles}},
        })
    )
    return directory


def _link_bundle(home: Path, name: str, bundle: str, target: Path) -> None:
    """Create the node_modules symlink that ``dsh plugin add`` creates.

    The target is the directory the symlink resolves to — either this
    checkout's ``packages/bundle/graph`` or a different directory that
    simulates another checkout.
    """
    link = home / "profiles" / name / "node_modules" / bundle
    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

_INIT = '''\
"""A generated package."""
from refactory_dsh.nn import Package

from .mode import Counter

PACKAGE = Package(
    name="{name}",
    description="count to a target",
    modes=(Counter,),
    ops=("refactory_packages.{name}.ops",),
)
'''

_OP_HEAD = '''\
"""Operations for the counter mode."""
'''

_OPS = '''

def start(ctx):
    target = int(ctx.knob("target", 3))
    ctx.write_json("state.json", {"value": 0, "target": target})
    return {"value": 0}


def step(ctx):
    state = ctx.read_json("state.json", {"value": 0, "target": 3})
    state["value"] += 1
    ctx.write_json("state.json", state)
    return {"value": state["value"]}


def check(ctx):
    state = ctx.read_json("state.json", {"value": 0, "target": 3})
    done = state["value"] >= state["target"]
    ctx.set_fact("done", done)
    return "proceed" if done else "retry"


def finish(ctx):
    state = ctx.read_json("state.json", {"value": 0, "target": 3})
    ctx.write_json("best.json", {"best": state["value"]})
    return {"best": state["value"]}
'''

_MODE_HEAD = '''\
"""The counter mode."""
from refactory_dsh import nn

OPS = "refactory_packages.{name}.ops"


class Counter(nn.Mode):
'''

_MODE_TAIL = '''\
    name = "counter"
    description = "count to a target"

    @classmethod
    def declare(cls):
        return {"target": nn.Knob(3, "int", lo=1, hi=9, description="where to stop")}

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("start", "op", call=OPS + ":start",
                          writes=("state.json",), produces=("value",), knobs=("target",)))
        graph.add(nn.Node("step", "op", call=OPS + ":step",
                          reads=("state.json",), writes=("state.json",), produces=("value",)))
        graph.add(nn.Node("check", "gate", evaluator=OPS + ":check",
                          reads=("state.json",), produces=("done",), max_iterations=20))
        graph.add(nn.Node("finish", "op", call=OPS + ":finish",
                          reads=("state.json",), writes=("best.json",), produces=("best",)))
        graph.connect("start", "step")
        graph.connect("step", "check")
        graph.connect("check", "step", condition="retry")
        graph.connect("check", "finish", condition="proceed")
        return graph

    def score(self, run):
        return float(run.facts["best"])

    def invariants(self):
        return (nn.fact_produced("best"),)
'''


def good_files(name: str) -> dict[str, str]:
    """A working generated package under the given name."""
    return {
        "__init__.py": _INIT.format(name=name),
        "ops.py": _OP_HEAD + _OPS,
        "mode.py": _MODE_HEAD.format(name=name) + _MODE_TAIL,
    }


_DATA_INIT = '''\
"""A generated package whose one mode iterates a Task's items."""
from refactory_dsh.nn import Package

from .mode import Chore

PACKAGE = Package(
    name="{name}",
    description="process every item the Task lists",
    modes=(Chore,),
    ops=("refactory_packages.{name}.ops",),
)
'''

_DATA_OPS = '''\
"""Operations for the per-item harness."""


def record(ctx):
    item = ctx.read_json("current_item.json", {}) or {}
    seen = ctx.read_json("seen.json", {"ids": []}) or {"ids": []}
    seen.setdefault("ids", []).append(str(item.get("id")))
    seen["min_items"] = int(ctx.knob("min_items", 1) or 1)
    ctx.write_json("seen.json", seen)
    return {"value": len(seen["ids"])}


def done(ctx):
    ctx.write_json("done.json", {"finished": True})
    return {"finished": True}
'''

_DATA_MODE = '''\
"""The per-item harness mode."""
from refactory_dsh import nn

OPS = "refactory_packages.{name}.ops"
TASK = "{task_ref}"


class Chore(nn.Mode):
    name = "chore"
    description = "iterate a Task's items and score the passes it verified"

    @classmethod
    def declare(cls):
        return {{"min_items": nn.Knob(1, "int", lo=1, hi=10)}}

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("data", "data", subgraph_entry="work", subgraph_exit="work",
                          task_ref=TASK, writes=("current_item.json",),
                          produces=("item_scores",)))
        graph.add(nn.Node("work", "op", call=OPS + ":record", knobs=("min_items",),
                          reads=("current_item.json",), writes=("seen.json",),
                          produces=("value",)))
        graph.add(nn.Node("finish", "op", call=OPS + ":done", writes=("done.json",),
                          produces=("finished",)))
        graph.connect("data", "finish")
        return graph

    def score(self, run):
        scores = [entry["score"] for entry in run.facts.get("item_scores", [])]
        return sum(scores) / len(scores) if scores else 0.0
'''


def good_data_files(name: str, task_ref: str) -> dict[str, str]:
    """A working generated package whose one mode is a data node bound to `task_ref`.

    The Task is the one `dataset_probe` ships, whose `verify` reads what the pass
    wrote: the package is a harness for that class of work, not for one sample.
    """
    return {
        "__init__.py": _DATA_INIT.format(name=name),
        "ops.py": _DATA_OPS,
        "mode.py": _DATA_MODE.format(name=name, task_ref=task_ref),
    }


def baked_data_files(name: str, task_ref: str) -> dict[str, str]:
    """The same package with the items baked into the graph instead of fed at run time.

    `inline_items` is read before the Task, so the harness stops taking the items the
    class of work supplies and iterates only the ones the authoring run seeded.
    """
    files = good_data_files(name, task_ref)
    files["mode.py"] = files["mode.py"].replace(
        'task_ref=TASK, writes=("current_item.json",),',
        'task_ref=TASK, inline_items=[{"id": "alpha"}, {"id": "beta"}],\n'
        '                          writes=("current_item.json",),',
    )
    return files


def unfed_data_files(name: str) -> dict[str, str]:
    """A data node that names no Task and no source: the input port is shut."""
    files = good_data_files(name, "unused:Task")
    files["mode.py"] = files["mode.py"].replace("task_ref=TASK, ", "")
    return files


def strategy_answer(name: str) -> str:
    """A coherent strategy answer, in the shape create-mode's review gate checks."""
    import json

    ops = f"refactory_packages.{name}.ops"
    return json.dumps({
        "considered": [
            {"approach": "count up one at a time", "why_not": ""},
            {"approach": "compute the answer directly", "why_not": "nothing to iterate"},
        ],
        "chosen": "count up one at a time",
        "why": "it is the simplest thing that has a state to watch",
        "will_not": ["threads", "a second package"],
        "closest_existing": "circle_packing/adaptive",
        "knobs": [
            {"name": "target", "kind": "int", "default": 3, "lo": 1, "hi": 9,
             "description": "where to stop"},
        ],
        "nodes": [
            {"id": "start", "kind": "op", "call": f"{ops}:start",
             "writes": ["state.json"], "produces": ["value"], "knobs": ["target"]},
            {"id": "step", "kind": "op", "call": f"{ops}:step",
             "reads": ["state.json"], "writes": ["state.json"], "produces": ["value"]},
            {"id": "check", "kind": "gate", "evaluator": f"{ops}:check",
             "reads": ["state.json"], "produces": ["done"], "max_iterations": 20},
            {"id": "finish", "kind": "op", "call": f"{ops}:finish",
             "reads": ["state.json"], "writes": ["best.json"], "produces": ["best"]},
        ],
        "edges": [
            {"source": "start", "target": "step", "condition": None},
            {"source": "step", "target": "check", "condition": None},
            {"source": "check", "target": "step", "condition": "retry"},
            {"source": "check", "target": "finish", "condition": "proceed"},
        ],
        "start": "start",
        "score": "the fact 'best', produced by finish",
    })


#: The answers create-v2's non-emit agents give, in the order the graph asks.
#:
#: The research answer is the plan array the gate checks, not a sentence about having
#: researched: a prose answer is exactly the failure the gate exists to catch, so a
#: fixture that fed one would be testing the bug.
#: A valid pitfalls answer for the pitfalls_researcher agent node.
PITFALLS_ANSWER = json.dumps([
    {
        "id": "PIT-001",
        "risk": "high",
        "category": "pinning_tests",
        "evidence": "tests/test_example.py:10",
        "prescription": "keep the assertion or deliberately change it with a reason",
    },
])

RESEARCH_ANSWER = json.dumps([
    {
        "focus": "which API surface the package builds against",
        "slug": "api-surface",
        "finding": "the mode API in the checkout: nn.Mode, nn.Graph, ctx.knob/read_json/write_json",
    },
    {
        "focus": "what already exists that this resembles",
        "slug": "existing-modes",
        "finding": "circle_packing/adaptive is the closest existing mode and is not this",
    },
    {
        "focus": "the acceptance criteria the brief asks for",
        "slug": "acceptance",
        "finding": "the mode must count to a target and score how close it landed",
    },
])
PERSPECTIVES_ANSWER = "perspectives: the simplest thing that works, and the criteria"
DESIGN_ANSWER = "a design"
#: The three reviews and the overwatch pass. Overwatch states its verdict because
#: the gate reads that line rather than trusting the review's own summary.
REVIEW_ANSWERS = [
    "health: the package imports, registers and smoke-scores",
    "code review: the ops, nodes and knobs agree with the design",
    "qa: every acceptance criterion has a check and they hold",
    "VERDICT: PASS",
]

#: The design review agent's answer: a passing review that ends with the verdict.
#: design_review is an agent node that consumes a FakeModel answer, placed after
#: design_doc in graph order.
DESIGN_REVIEW_ANSWER = (
    "No open alternatives found. All citations confirmed. "
    "All brief references verified.\n\nVERDICT: PASS"
)

#: The verifier's answer: a passing verification with no blocking findings.
VERIFICATION_ANSWER = "No blocking findings.\n\nVERDICT: PASS"

#: A contract-compliant design.md answer for design-mode's design_doc agent.
DESIGN_DOC_ANSWER = (
    "# Do the thing\n\n"
    "## Summary\n\nFix it properly.\n\n"
    "## Failure trace\n\nN/A — this is a new feature.\n\n"
    "## Ask mapping\n\n"
    "| Ask (quoted from brief) | Design section | Test |\n"
    "|---|---|---|\n"
    '| "a tool" | § Changes | test_tool.py |\n\n'
    "## Rule and sites\n\n"
    "- `tool.py:1` — **[changed]**\n\n"
    "## Docs\n\n"
    "- docs/models.md\n\n"
    "## Changes\n\n"
    "- tool.py\n\n"
    "## Anti-patterns to avoid\n\n"
    "- PIT-001: addressed at `tool.py:1`\n"
)


def create_script(name: str, emits: list[str]) -> list[str]:
    """The whole answer script, with the given emit answers in the order used.

    A reloop back to `emit` consumes the next one, so a test that expects a retry
    passes two; a run whose validation never passes is scripted without the reviews,
    because it never reaches them.
    """
    return [
        RESEARCH_ANSWER,
        PERSPECTIVES_ANSWER,
        strategy_answer(name),
        DESIGN_ANSWER,
        *emits,
        *REVIEW_ANSWERS,
    ]


def create_answers(name: str, files: dict[str, str]) -> list[str]:
    """Every answer create-v2's agents need, in the order the graph asks for them.

    The three reviews and the overwatch pass answer as text; the executor writes
    each to the file the node declares, and the QA synthesis and its gate read them
    back. Overwatch must state its verdict line, because the gate reads it.
    """
    return create_script(name, [render(files)])


def render(files: dict[str, str]) -> str:
    """The emit agent's answer format: `===FILE: name===` blocks."""
    return "\n".join(f"===FILE: {name}===\n{body}" for name, body in files.items()) + "\n"


class FakeModel:
    """Answers with the given texts, in order."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        #: The directory each completion was told to run in; see the walker's `_ask_model`.
        self.workdirs: list[object] = []

    def complete(self, prompt: str, *, system: str = "", workdir: object = None) -> str:
        self.prompts.append(prompt)
        self.workdirs.append(workdir)
        answer = self.answers.pop(0) if self.answers else ""
        # Scripted pitfalls fixtures: agent_delivery's capture_final writes the
        # model's text answer to writes[0] (pitfalls.json), but pitfalls.md
        # (writes[1]) must also exist for delivery to succeed.  Write it as a
        # side-effect, matching the intent-audit fixture below.
        if answer and "pitfalls researcher" in prompt and workdir is not None:
            from pathlib import Path

            root = Path(workdir)
            root.mkdir(parents=True, exist_ok=True)
            (root / "pitfalls.md").write_text(
                "# Pitfalls\n\n(scripted fixture)\n"
            )
        # Scripted review fixtures explicitly deliver the new evidence-map artifact.
        # Adversarial coverage/waiver tests supply their own maps instead.
        if answer and "Write intent-audit.json as" in prompt and workdir is not None:
            from pathlib import Path

            root = Path(workdir)
            baseline = json.loads((root / "authoring-evidence.json").read_text())
            audit_rows = []
            for item in baseline["requirements"]:
                row = {"id": item["id"], "requirement": item.get("text", item.get("path", "")),
                       "status": "pass",
                       "evidence": [{"path": "authoring-evidence.json",
                                     "detail": "scripted fixture inspected the executed result"}]}
                if item.get("kind") in ("correction", "constraint"):
                    text = item.get("text", "")
                    sites = getattr(self, '_correction_sites', {}).get(item["id"], [])
                    clause = {"clause": text,
                              "evidence": [{"path": "authoring-evidence.json",
                                            "detail": "full constraint verified"}],
                              "sites": sites}
                    # A test whose correction is about the design itself can give
                    # its site-less clauses an evidence form, e.g. {"kind": "design",
                    # "heading": "Summary"}.
                    if not sites:
                        clause.update(getattr(self, "_siteless_clause", {}))
                    row["clauses"] = [clause]
                audit_rows.append(row)
            (root / "intent-audit.json").write_text(json.dumps({"requirements": audit_rows}))
        return answer


class StaleEmitModel(FakeModel):
    """The redo's emit worker: an emit.txt already in its run reads to it as the work done."""

    def complete(self, prompt: str, *, system: str = "", workdir: object = None) -> str:
        from pathlib import Path

        answer = super().complete(prompt, system=system, workdir=workdir)
        if "Write the artifact `" in prompt and (Path(str(workdir)) / "emit.txt").exists():
            return "emit.txt already contains the correct content."
        return answer


def git(root, *args: str, env: dict[str, str] | None = None) -> str:
    """What a git command prints in ``root``; a failing command fails the test."""
    import subprocess

    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def git_repo(root):
    """A person's checkout: a repository on `main` whose one commit holds `app.py`."""
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "app.py").write_text("print('app')\n")
    git(root, "add", "app.py")
    git(root, "commit", "-q", "-m", "base")
    return root


def stub(bin_dir, name: str, body: str) -> None:
    """A stand-in for a tool on the PATH, which logs each call to `calls.log` beside it."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/bin/sh\necho \"{name} $*\" >> \"{bin_dir}/calls.log\"\n{body}\n")
    script.chmod(0o755)


@contextmanager
def on_stdin(text: str) -> Iterator[None]:
    """Put ``text`` on this process's stdin, so a child that inherited it could read it."""
    read, write = os.pipe()
    os.write(write, text.encode())
    os.close(write)
    saved = os.dup(0)
    os.dup2(read, 0)
    os.close(read)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)
