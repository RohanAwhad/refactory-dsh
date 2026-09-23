from __future__ import annotations

from refactory_dsh import nn

OPS = "refactory_packages.dataset_probe.ops"
TASK = "refactory_packages.dataset_probe.tasks:CountingTask"


class Dataset(nn.Mode):
    """One data node over a one-node subgraph, bound to a Task."""

    name = "dataset"
    description = "iterate a Task's items and score the passes it verified"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "min_items": nn.Knob(
                1, "int", lo=1, hi=10,
                description="how many items the whole run must have processed before any "
                            "pass counts; the Task's check reads it, so raising it turns a "
                            "pass that processed too little into a failure",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "data", "data", subgraph_entry="work", subgraph_exit="work",
            task_ref=TASK, writes=("current_item.json",), produces=("item_scores",),
        ))
        graph.add(nn.Node(
            "work", "op", call=f"{OPS}:record", knobs=("min_items",),
            reads=("current_item.json",), writes=("seen.json",), produces=("value",),
        ))
        graph.add(nn.Node(
            "finish", "op", call=f"{OPS}:done", writes=("done.json",), produces=("finished",),
        ))
        graph.connect("data", "finish")
        return graph

    def score(self, run: nn.RunResult) -> float:
        scores = [entry["score"] for entry in run.facts.get("item_scores", [])]
        return sum(scores) / len(scores) if scores else 0.0
