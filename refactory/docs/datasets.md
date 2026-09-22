# Datasets and Tasks

A mode normally walks its graph once. A mode with a **`data` node** walks a subgraph
once per *item* — one benchmark instance, one row of a file, one directory — and an
item can carry its own prompt and its own verdict. That is how a benchmark or a
dataset becomes a mode instead of a script around one.

## The node

```python
graph.add(nn.Node(
    "data", "data",
    subgraph_entry="work",     # the first node of the per-item subgraph
    subgraph_exit="work",      # the last one, included in every pass
    task_ref="my_pack.tasks:MyTask",   # where the items come from (optional)
    writes=("current_item.json",),
    produces=("item_scores",),
))
graph.connect("data", "finish")        # where the run goes after every item
```

The subgraph is reached **through the data node**, not by an edge: an edge into it
would run it once outside the iteration. Each pass writes the item to
`current_item.json` in the run directory, so an op or an agent node reads the item it
is working on rather than being told it in a prompt.

Items come from, in order of precedence: `inline_items` declared on the node; the
`source_path` read as `source_format` (`directory` — sorted subdirectories; `jsonl` —
one object per line; `csv` — a header row); or the bound Task's `instances()`. The
node's `limit` caps what is used. A source that is named and missing is refused by
name, because an empty selection and an unbound source must not look alike.

## The Task

A Task is four optional hooks, resolved by `module:Class` or by a name registered with
`register_task`. A reference that does not resolve is refused rather than run with
nothing judging the items.

```python
from refactory_dsh.task import Task, TaskInstance, VerifyResult

class CountingTask(Task):
    name = "counting"

    def instances(self):                      # the items
        return [TaskInstance(id="alpha"), TaskInstance(id="beta")]

    def setup(self, instance, workspace):     # before a pass
        ...

    def prompt(self, instance):               # what that item's agent is asked
        return f"process {instance.id}"

    def verify(self, instance, workspace):    # after a pass; None means "no opinion"
        return VerifyResult(passed=True, score=1.0)
```

The verdicts are recorded as `item_scores` when the data node declares that fact, so
`score()` reads what the Task decided rather than a number the graph guessed.

## A worked example

A package with one data node bound to a two-item Task, whose subgraph is one op that
appends the current item's id, scores the mean of the Task's verdicts:

```bash
.venv/bin/python -m refactory_dsh.registry smoke dataset_probe/dataset
```

```json
{"mode": "dataset_probe/dataset", "package": "dataset_probe", "exit_ok": true,
 "error": null, "score": 0.75, "iterations": 4}
```

`0.75` is the mean of `1.0` for `alpha` and `0.5` for `beta`: two passes, each with
its own item on disk, each judged by the Task.

## Creating one

`create` takes the two directives, so a mode can be authored for a dataset rather
than hand-written:

```bash
.venv/bin/python -m refactory_dsh.create --name my_pack \
  --brief "solve the counting instances" \
  --data-node --task my_pack.tasks:CountingTask
```

The validate gate **checks** both: a package that declares no DataNode when one was
asked for, or is not bound to the Task that was named, is refused and told which one
was expected. Asking is not the same as getting.

## On the runtimes

The walker runs the passes itself. DSH's spine does not yet: it hands the data node
to the driving agent, which iterates from the node's view — the difference is tracked
as [#59](https://github.com/akashgit/refactory-dsh/issues/59). Until it is closed, a
dataset mode's items are loaded by the walker and by the agent on DSH.
