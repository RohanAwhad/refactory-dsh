"""Run one refactory node, from the command line.

    python -m refactory_dsh.run <module>:<function>

The runtime hands a node its own declaration under ``DSH_WORKFLOW_NODE`` and knows
nothing about the graph that invoked it, so this runner builds the same context the
walking executor builds and invokes the same callable. A deterministic node's whole
contract is: read the files you declared, write the files you declared, return the
facts you declared, and (for a gate) print your verdict.

The verdict is the **last non-empty stdout line**, which is how the spine routes a
gate. Everything else a node prints is for a log.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from refactory_dsh.lowering import DECLARATIONS, KNOBS_ENV
from refactory_dsh.nn import RunContext
from refactory_dsh.nn.resolve import resolve

#: Where the facts a run produced are collected, so the Python side can score a run
#: that DSH executed. This file is the runtime boundary.
FACTS = "facts.json"

#: The spine passes each node its own declaration under this name.
NODE_ENV = "DSH_WORKFLOW_NODE"

#: The working directory the command runs in, if the runtime says so explicitly.
WORKDIR_ENV = "REFACTORY_WORKDIR"


class RunnerError(RuntimeError):
    """The node could not be run."""


def _manifest() -> dict[str, Any]:
    raw = os.environ.get(NODE_ENV, "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _knobs() -> dict[str, Any]:
    for name in (KNOBS_ENV, "SRF_KNOBS"):
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return {}


def _read_facts(workdir: Path) -> dict[str, Any]:
    path = workdir / FACTS
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _memory_store(directory: Path) -> Any:
    """The memory store for one node's run, or ``None`` when nothing is declared.

    Roots follow the walker: this run's own directory for ``run`` retention, the
    project's ``.factory`` for ``project``, and the configured global root — so a
    mode that declares a prior gets it on both runtimes rather than on one.
    """
    from refactory_dsh.lowering import read_memory_declarations
    from refactory_dsh.memory import MemoryStore

    declared = read_memory_declarations(directory)
    if not declared:
        return None
    project = Path(os.environ.get("REFACTORY_PROJECT") or Path.cwd())
    global_root = os.environ.get("REFACTORY_MEMORY_HOME")
    return MemoryStore(
        declared,
        run_root=directory,
        project_root=project / ".factory",
        global_root=Path(global_root) if global_root else None,
    )


def declarations(workdir: Path, node_id: str) -> dict[str, list[str]]:
    """What the node declared: from the run directory, falling back to the manifest.

    The run directory is authoritative because it carries the facts a node produces,
    which `DSH_WORKFLOW_NODE` has no field for.
    """
    path = Path(workdir) / DECLARATIONS
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict) and isinstance(loaded.get(node_id), dict):
            return {
                "reads": list(loaded[node_id].get("reads") or ()),
                "writes": list(loaded[node_id].get("writes") or ()),
                "produces": list(loaded[node_id].get("produces") or ()),
            }
    manifest = _manifest()
    if str(manifest.get("id")) == node_id:
        return {
            "reads": list(manifest.get("reads") or ()),
            "writes": list(manifest.get("writes") or ()),
            "produces": [],
        }
    return {"reads": [], "writes": [], "produces": []}


def run_node(
    target: str, *, workdir: Path | None = None, node_id: str | None = None
) -> Any:
    """Invoke one node's callable with the context the runtime provides.

    The node's identity comes from the runtime's manifest, or from an explicit id.
    It must not fall back to the target: the declarations are keyed by node id, so
    guessing the target would silently look up nothing and stop enforcing the
    declarations the node declares.
    """
    manifest = _manifest()
    directory = Path(workdir or os.environ.get(WORKDIR_ENV) or Path.cwd())
    node_id = str(node_id or manifest.get("id") or "")
    if not node_id:
        raise RunnerError(
            f"no node id: set {NODE_ENV} or pass one, because the declarations this "
            "runner enforces are keyed by node id"
        )
    declared = declarations(directory, node_id)
    ctx = RunContext(
        directory,
        knobs=_knobs(),
        facts=_read_facts(directory),
        task={"node": node_id, "type": manifest.get("type", "")},
        # The store an op receives, built from the mode's own declarations. Without
        # this a mode that keeps a cross-run prior works in the walker and quietly
        # keeps nothing here, which is a difference between the two runtimes that
        # nothing would report.
        memory=_memory_store(directory),
    )
    ctx.node_id = node_id
    ctx._declared = tuple(declared["writes"])  # noqa: SLF001 - the runner owns the context
    ctx._declared_facts = tuple(declared["produces"])  # noqa: SLF001
    produced = resolve(target)(ctx)
    if isinstance(produced, dict):
        for key, value in produced.items():
            ctx.set_fact(key, value)
    ctx._check_produces()  # noqa: SLF001
    path = directory / FACTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ctx.facts, indent=2, default=str))
    return produced


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    node_id = ""
    if "--node" in argv:
        index = argv.index("--node")
        if index + 1 >= len(argv):
            print("--node needs a value", file=sys.stderr)
            return 2
        node_id = argv[index + 1]
        del argv[index : index + 2]
    if len(argv) != 1 or ":" not in argv[0]:
        print(
            "usage: python -m refactory_dsh.run <module.path>:<function> [--node <id>]",
            file=sys.stderr,
        )
        return 2
    try:
        produced = run_node(argv[0], node_id=node_id or None)
    except Exception as exc:  # a failing node must fail loudly, and not be a traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    # A gate's whole output is its verdict, and the spine routes on the last
    # non-empty line, so a verdict is printed and nothing else is.
    if isinstance(produced, str) and produced.strip():
        print(produced.strip())
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
