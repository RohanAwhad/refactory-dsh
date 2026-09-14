# 1. Write a mode

A mode is a Python class. This tutorial builds the smallest one that is worth
optimizing, runs it, and then breaks it on purpose so you can see the validator
refuse it.

Everything here is offline and takes a few seconds.

## The shape

A mode declares three things:

- **parameters** in `declare()` — what an optimizer may move;
- **structure** in `build()` — the graph, built once, pure;
- **a score** in `score(run)` — how a run becomes the number being maximized.

The graph is *data*. `build()` produces it once; after that the optimizer edits the
state, never the class ([why](../architecture.md#2-the-pytorch-mapping)).

## Write it

A mode needs a package to live in. Put it in the **workspace root**,
`.factory/packages/counter/` — the writable half of the registry, and the same
place `create`-mode writes its output. The shipped packages live beside it in
`refactory_packages/`, which is read-only.

So this goes at `.factory/packages/counter/__init__.py`:

```python
"""A counter package: one mode, and the ops it calls."""
from refactory_dsh.nn import Package

from .mode import Counter

PACKAGE = Package(
    name="counter",
    description="count up to a target, and score the count reached",
    modes=(Counter,),
    ops=("refactory_packages.counter.ops",),
)
```

`.factory/packages/counter/mode.py`:

```python
"""The counter mode."""
from refactory_dsh.nn import Graph, Knob, Mode, Node


class Counter(Mode):
    name = "counter"
    description = "count up to a target"

    @classmethod
    def declare(cls):
        return {
            "target": Knob(3, "int", lo=1, hi=9, description="where to stop counting"),
            "step": Knob(1, "int", lo=1, hi=3, description="how much each step adds"),
        }

    def build(self):
        graph = Graph()
        graph.add(Node(
            "count", "op", call="refactory_packages.counter.ops:count",
            knobs=("target", "step"), writes=("count.json",), produces=("value",),
            notes="add `step` until the count reaches `target`",
        ))
        return graph

    def score(self, run):
        # What the run fact says, never a knob: a score that reads a knob is a
        # score about the input rather than about the run.
        return float(run.facts["value"])
```

`.factory/packages/counter/ops.py`:

```python
"""The operation the counter's node calls."""
from typing import Any


def count(ctx: Any) -> dict[str, Any]:
    target = int(ctx.knob("target", 3))
    step = int(ctx.knob("step", 1))
    value = 0
    while value < target:
        value += step
    ctx.write_json("count.json", {"value": value})
    return {"value": value}
```

Three rules the code above obeys, each of which the validator enforces if you
break it:

- the node lists `knobs=("target", "step")` — a declared knob no node consumes is
  refused (`V7`);
- the node declares `writes=("count.json",)` — a read of a file nothing writes is
  refused (`V6`);
- `score()` reads `run.facts`, never `ctx.knob(...)`.

## Register and run it

The registry scans `refactory_packages/`, so the package is found by name:

```bash
.venv/bin/python -m refactory_dsh.registry list
```

```
counter/  count up to a target, and score the count reached
  counter/counter                        count up to a target
```

```bash
.venv/bin/python -c "
from pathlib import Path
from refactory_dsh import registry
package, mode_class = registry.resolve(Path('.'), 'counter/counter')
mode = mode_class()
print('state keys:', sorted(mode.state_dict()))
print('score     :', mode.evaluate(Path('/tmp/counter')).score)
"
```

```
state keys: ['graph', 'knobs', 'memory', 'ports', 'version']
score     : 3.0
```

Note the state keys: the architecture (`graph`) travels with the parameters
(`knobs`). That is the divergence from PyTorch this whole system rests on.

## Validate it before running it

Validation is what makes a search cheap: an invalid candidate is refused before it
costs a run.

```bash
.venv/bin/python -c "
from pathlib import Path
from refactory_dsh import nn, registry
mode = registry.resolve(Path('.'), 'counter/counter')[1]()
print(nn.validate_state(mode.state_dict(), invariants=mode.invariants()))
"
```

```
[]
```

An empty list means runnable and scoreable. Now break it deliberately — point the
node at a file nothing produces:

```bash
.venv/bin/python -c "
from pathlib import Path
from refactory_dsh import nn, registry
from refactory_dsh.nn import edits
mode = registry.resolve(Path('.'), 'counter/counter')[1]()
state = mode.state_dict()
broken = edits.apply({'op': 'set_node_field', 'id': 'count',
                      'field': 'reads', 'value': ['nowhere.json']}, state)
for issue in nn.validate_state(broken):
    print(issue)
"
```

```
V6 [count]: reads 'nowhere.json', which no node on a path from 'count' to a terminal produces
```

That message is what an optimizer is shown *instead of* a failed run. It is why
the running example reports refusals as `V6: 4` rather than a batch of zeroes, and
why none of them cost evaluation budget.

The catalogue of edits is discoverable, so you never guess a field name:

```bash
.venv/bin/python -c "
from refactory_dsh.nn import edits
for op in edits.catalogue():
    print(op['op'], op['fields'])
"
```

## What to take away

- A mode is data from `build()` onward. The class is a *seed*, not the truth.
- Declare what a node reads, writes, produces, and consumes. The declarations are
  what the validator checks against, and a node that writes something it did not
  declare is an error rather than a surprise.
- `score()` reads facts. If it read a knob, optimizing would be circular.

Next: [optimize it](02-optimize-it.md).
