"""The Java build harness: a model writes the build script for a class of source trees.

The graph is four nodes. The `data` node is the input port: it binds `JavaBuildTask`,
which lists one directory per Java source tree, and runs the subgraph once per item.
`read_item` turns the item on disk into the facts the build agent's prompt renders,
`write_build` is the one model call that writes `build.sh`, and `finish` records the
script the run produced.

Nothing here decides whether a build worked. The Task runs the script the pass wrote
and compares what the program printed with the output recorded beside the sources, and
`score()` is the mean of those per-item verdicts. The harness is therefore a claim
about every Java tree the Task lists rather than about the three a checkout ships.
"""

from __future__ import annotations

from refactory_dsh import nn

OPS = "refactory_packages.java_build_pack.ops"
TASK = "refactory_packages.java_build_pack.tasks:JavaBuildTask"

BUILD_PROMPT = """\
Write `build.sh` in this directory for the Java item `{{item_id}}`.

The sources are already copied to `src/`. Its entry point is:
- main class: `{{main_class}}`
- arguments: `{{main_args}}`

`build.sh` must be a POSIX shell script that:
1. compiles every `.java` file under `src/` with `javac --release {{source_release}}` into `out/`;
2. runs the entry point with `java -cp out` and the arguments above;
3. prints nothing of its own and exits non-zero when either step fails, because the
   program's stdout is what the build is judged on.

A person runs it as `sh build.sh` from this directory, and it must work for any Java
source tree with this layout, not only for the one in front of you.
"""


class BuildPack(nn.Mode):
    """Write a javac/java build script for each Java source tree a Task lists."""

    name = "build"
    description = "write a build script that compiles and runs any small Java tree"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {
            "source_release": nn.Knob(
                17, "int", lo=8, hi=24,
                description="the Java release javac targets; the build agent writes it "
                            "into the `--release` flag of the script it produces",
            ),
        }

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        graph.add(nn.Node(
            "items", "data", subgraph_entry="read_item", subgraph_exit="write_build",
            task_ref=TASK, writes=("current_item.json",), produces=("item_scores",),
            notes="run the subgraph once per Java source tree the Task lists",
        ))
        graph.add(nn.Node(
            "read_item", "op", call=f"{OPS}:read_item",
            reads=("current_item.json",), writes=("item.json",),
            produces=("item_id", "main_class", "main_args"),
            notes="publish the current item's entry point for the build prompt",
        ))
        graph.add(nn.Node(
            "write_build", "agent", prompt_template=BUILD_PROMPT,
            reads=("item.json",), writes=("build.sh",), produces=("build_script",),
            knobs=("source_release",),
            notes="write the javac/java build script for this item",
        ))
        graph.add(nn.Node(
            "finish", "op", call=f"{OPS}:finish",
            reads=("build.sh",), writes=("build_record.json",), produces=("built",),
            notes="record the build script this run produced",
        ))
        graph.connect("items", "finish")
        graph.connect("read_item", "write_build")
        return graph

    def score(self, run: nn.RunResult) -> float:
        """The mean of the Task's per-item verdicts: how much of the class built."""
        scores = [entry["score"] for entry in run.facts.get("item_scores", [])]
        return sum(scores) / len(scores) if scores else 0.0

    def invariants(self) -> tuple[nn.Invariant, ...]:
        return (nn.fact_produced("item_scores"),)

    def ports(self) -> tuple[nn.Port, ...]:
        return (nn.Port(
            name="build_script",
            path="build.sh",
            media_type="text/x-shellscript",
            description="the build script the last item's agent wrote",
        ),)
