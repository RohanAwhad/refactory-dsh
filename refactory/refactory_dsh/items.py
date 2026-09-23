"""The per-item half of a data node, from the command line.

    python -m refactory_dsh.items list  --node <id>
    python -m refactory_dsh.items begin --node <id> --index <n>
    python -m refactory_dsh.items end   --node <id> --index <n>

The walking executor runs a data node's whole loop in one process, because it owns
the graph. DSH does not: the subgraph a data node iterates can contain agent nodes,
so the pass has to be driven from outside the graph by the runtime that owns the
model. That splits the loop into three commands, one per fact the runtime cannot
know on its own:

- ``list`` — how many items the selection holds, so the runtime knows how often to
  walk the subgraph. An empty selection is a fact, reported as zero, not an error.
- ``begin`` — materialise one item as ``current_item.json`` and give the Task its
  ``setup`` hook, before the pass reads anything.
- ``end`` — ask the Task's ``verify`` hook what the pass was worth, and fold the
  score into the run's facts.

Both runtimes load items through :func:`refactory_dsh.nn.executor.load_items`, so a
mode means the same thing on either one. The declarations the IR cannot carry (the
node's ``task_ref``, ``source_path``, ``source_format``, ``inline_items`` and
``limit``) travel in the run directory's declarations file, written by the lowering.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from refactory_dsh.paths import project_root, project_for_nodes
from refactory_dsh.nn import Node
from refactory_dsh.nn.executor import CURRENT_ITEM, load_items
from refactory_dsh.task import Task, TaskError, instance_of, load_task

#: The working directory a node command runs in, when the runtime says so.
WORKDIR_ENV = "REFACTORY_WORKDIR"

#: Where the facts a run produced are collected. The runner writes this per node;
#: the item loop appends to it, so a dataset mode's `item_scores` reaches the same
#: file on either runtime.
FACTS = "facts.json"

#: The fact a data node's per-item verdicts are collected under.
SCORES_FACT = "item_scores"

#: The declaration key the data node's own fields travel under.
DATA_KEY = "data"


class ItemsError(RuntimeError):
    """A data node cannot be iterated: named, because the node id alone is not a reason."""


def _declarations(workdir: Path) -> dict[str, Any]:
    from refactory_dsh.lowering import read_declarations

    return read_declarations(workdir)


def data_node(workdir: Path, node_id: str) -> Node:
    """The data node the declarations describe, as the graph language's own node.

    Rebuilding the node rather than re-deriving its fields from the manifest keeps
    one reader of the declarations: the manifest is what the IR can carry, and the
    data node's source is exactly what it cannot.
    """
    entry = _declarations(workdir).get(node_id)
    if not isinstance(entry, dict):
        raise ItemsError(
            f"node {node_id!r} is not declared in the run directory {workdir}; the "
            "runner writes the declarations before it starts a node, so an absent "
            "entry means the node id does not name a node of this run"
        )
    if entry.get("kind") != "data":
        raise ItemsError(
            f"node {node_id!r} is declared as {entry.get('kind')!r}, not 'data'; only "
            "a data node iterates items"
        )
    fields = entry.get(DATA_KEY)
    if not isinstance(fields, dict):
        raise ItemsError(
            f"node {node_id!r} is a data node but its source is not declared "
            f"(no {DATA_KEY!r} entry); re-lower the mode with this version of refactory"
        )
    return Node(
        node_id,
        "data",
        reads=tuple(entry.get("reads") or ()),
        writes=tuple(entry.get("writes") or ()),
        produces=tuple(entry.get("produces") or ()),
        **fields,
    )


def items_of(node: Node, workdir: Path) -> list[dict[str, Any]]:
    """The data node's items, loaded the one way both runtimes load them.

    A node bound to a Task and naming no source takes its items from the Task, which
    is what makes ``task_ref`` a binding rather than a line in a prompt.
    """
    items = load_items(node, workdir)
    if not items and node.task_ref:
        task = load_task(node.task_ref, workdir)
        items = [
            {"id": instance.id, "path": instance.path, **instance.metadata}
            for instance in task.instances()
        ]
    return items


def _task_of(node: Node, workdir: Path) -> Task | None:
    if not node.task_ref:
        return None
    return load_task(node.task_ref, workdir)


def _write_facts(workdir: Path, fact: str, scores: list[dict[str, Any]]) -> None:
    path = workdir / FACTS
    facts: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            facts = loaded
    previous = facts.get(fact)
    facts[fact] = list(previous) + scores if isinstance(previous, list) else list(scores)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, default=str))


def command_list(node: Node, workdir: Path) -> dict[str, Any]:
    """How many items the node iterates, and what they are called."""
    items = items_of(node, workdir)
    return {
        "count": len(items),
        "ids": [str(item.get("id") or index) for index, item in enumerate(items)],
    }


def command_begin(node: Node, workdir: Path, index: int) -> dict[str, Any]:
    """Materialise one item and prepare the workspace for its pass."""
    items = items_of(node, workdir)
    if index < 0 or index >= len(items):
        raise ItemsError(
            f"item {index} is out of range: node {node.id!r} selected {len(items)} item(s)"
        )
    item = items[index]
    (workdir / CURRENT_ITEM).write_text(json.dumps(item, indent=2, default=str))
    task = _task_of(node, workdir)
    if task is not None:
        task.setup(instance_of(item), workdir)
    return item


def command_end(node: Node, workdir: Path, index: int) -> dict[str, Any]:
    """The Task's verdict on one pass, folded into the run's facts."""
    items = items_of(node, workdir)
    if index < 0 or index >= len(items):
        raise ItemsError(
            f"item {index} is out of range: node {node.id!r} selected {len(items)} item(s)"
        )
    item = items[index]
    task = _task_of(node, workdir)
    if task is None:
        return {}
    verdict = task.verify(instance_of(item), workdir)
    if verdict is None:
        return {}
    score = {
        "id": str(item.get("id") or index),
        "passed": verdict.passed,
        "score": verdict.score,
    }
    if SCORES_FACT in node.produces:
        _write_facts(workdir, SCORES_FACT, [score])
    return score


def _options(argv: list[str]) -> tuple[str, int]:
    """The ``--node`` id and ``--index`` one command line names."""
    node_id = ""
    index = 0
    if "--node" in argv:
        position = argv.index("--node")
        if position + 1 >= len(argv):
            raise ItemsError("--node needs a value")
        node_id = argv[position + 1]
    if "--index" in argv:
        position = argv.index("--index")
        if position + 1 >= len(argv):
            raise ItemsError("--index needs a value")
        try:
            index = int(argv[position + 1])
        except ValueError as exc:
            raise ItemsError(f"--index takes an item number, not {argv[position + 1]!r}") from exc
    if not node_id:
        raise ItemsError("--node is required: the declarations are keyed by node id")
    return node_id, index


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = (
        "usage: python -m refactory_dsh.items "
        "{list|begin|end} --node <id> [--index <n>]"
    )
    if not argv or argv[0] not in ("list", "begin", "end"):
        print(usage, file=sys.stderr)
        return 2
    verb = argv[0]
    workdir = Path(os.environ.get(WORKDIR_ENV) or project_for_nodes())
    try:
        from refactory_dsh.run import install_workspace_packages

        install_workspace_packages()
        node_id, index = _options(argv[1:])
        node = data_node(workdir, node_id)
        if verb == "list":
            produced: dict[str, Any] = command_list(node, workdir)
        elif verb == "begin":
            produced = command_begin(node, workdir, index)
        else:
            produced = command_end(node, workdir, index)
    except (ItemsError, TaskError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # a failing item loop must fail loudly, not traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(produced, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
