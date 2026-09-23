# Tutorials

Seven walkthroughs, in the order a person meets the system. Each one is a sequence
of commands that were run against this checkout, with the output they produced.

The example running through all of them is **circle packing**: place circles on a
grid, score by how many fit. It is small enough to run in seconds and rich enough
to have a real structure to search — which is why it, and the `annealing_pack`
that `create`-mode emits from it, are the source of truth for whether the system
works.

| # | tutorial | what you end with |
|---|---|---|
| 1 | [Write a mode](01-write-a-mode.md) | a mode you can validate and run |
| 2 | [Optimize it](02-optimize-it.md) | a run, its report, and a candidate's diff |
| 3 | [Author with a model](03-author-with-a-model.md) | a package created, revised, and designed |
| 4 | [Ship it on DSH](04-ship-it-on-dsh.md) | the same mode scored by the runtime that ships |
| 5 | [Drive it from the GUI](05-drive-it-from-the-gui.md) | a run started, authored, managed and steered from the conversation |
| 6 | [Run it with a person](06-run-it-with-a-person.md) | a run paused at a gate only a person can answer |
| 7 | [Pick and watch from the panel](07-pick-and-watch-from-the-panel.md) | a run chosen, started and watched from the browser |

---

## The five-minute tour

Everything below runs offline and deterministically. Run it from the repository
root.

**What can this checkout run?**

```bash
.venv/bin/python -m refactory_dsh.registry list
```

```
adopted_exact/  The winner of a run of sympy_pack/exact, as a mode of its own.
  adopted_exact/exact                    establish closed forms of sums with sympy and score what checked out
annealing_pack/  Hill-climb a greedy circle packing by accepting non-worsening single-circle moves.
  annealing_pack/anneal                  hill-climb a greedy circle packing by accepting non-worsening moves
circle_packing/  A population search over circle placements, and the task it scores.
  circle_packing/adaptive                population search over circle placements with a budget gate
counting_pack/  Two counting modes that score how close the count reached lands to a target.  [generated]
  counting_pack/fixed_step               count up to a target in fixed steps; no RNG, so no seed knob
  counting_pack/random_walk              count up to a target in reproducible random steps, one draw per step
counting_tuned/  The winner of a run of counting_pack/fixed_step, as a mode of its own.
  counting_tuned/fixed_step              count up to a target in fixed steps; no RNG, so no seed knob
dataset_probe/  a probe mode that iterates a Task's items
  dataset_probe/dataset                  iterate a Task's items and score the passes it verified
default_pack/  Create a package, revise one, or design an artifact — from a brief.
  default_pack/create                    design, emit and validate a whole package from a brief
  default_pack/design                    design, emit and check a software artifact from a brief
  default_pack/edit                      revise an existing mode from a brief, and check it did not regress
lean_pack/  Prove theorems in Lean 4 and score what the proof checker accepted.
  lean_pack/prover                       prove a theorem in Lean and score what the Lean checker accepted
loop_mode/  The optimizer loop itself as a graph of eight steps, runnable and searchable.
  loop_mode/loop                         The optimizer loop's eight steps as a runnable, searchable graph.
phase_pack/  Count up to a target in reproducible random steps and score the count reached.  [generated]
  phase_pack/countup                     count up to a target in reproducible random steps and score the count reached
review_pack/  Draft a short note, check it, and let a person approve it before it is published.
  review_pack/review                     draft, check, and let a person approve before publishing
revised_pack/  A population search over circle placements that restarts from a jittered sample when it stalls.  [generated]
  revised_pack/adaptive                  population search over circle placements with a budget gate
sympy_pack/  Establish closed forms of sums exactly with sympy, and score what checked out.
  sympy_pack/exact                       establish closed forms of sums with sympy and score what checked out
```

Marks in that listing say what is worth knowing before you run something. `[generated]`
is trust: the package was written by `create`-mode rather than by hand, so it is a
candidate to read before you rely on it. A package that declares a tool this machine
lacks is marked in the `[needs …]` position: `lean_pack` declares the Lean proof
checker in its `tools.py`, and a machine without one sees `lean_pack/  …  [needs lean]` instead of being left to
discover it from a failed run. This machine *has* Lean (`~/.elan/bin/lean`), so the
listing above carries no marker — the mark says what is missing here, not what the
package needs. Installing and declaring a tool that is not Python is
[packing a tool that is not Python](../packing-tools.md).

**Run one inner run** — a mode, evaluated once:

```bash
.venv/bin/python -c "
from pathlib import Path
from refactory_dsh import registry
mode = registry.resolve(Path('.'), 'circle_packing/adaptive')[1]()
result = mode.evaluate(Path('/tmp/tour/inner'))
print('score', result.score, 'exit_ok', result.exit_ok, 'iterations', result.iterations)
"
```

```
score 0.875 exit_ok True iterations 21
```

**Optimize it** — the outer loop searching the mode's architecture and parameters:

```bash
.venv/bin/python -m refactory_dsh.loop circle_packing/adaptive \
  --epochs 2 --batch-size 4 --optimizer random --seed 5 --runs-root /tmp/tour/run
```

```
{
  "mode": "circle_packing/adaptive",
  "bound": "epochs",
  "evaluations": 8,
  "seed_score": 0.875,
  "best_score": 0.955,
  ...
}
```

**Read what it did.** The winner improved on the seed by changing one parameter
*and* duplicating a node:

```bash
.venv/bin/python -m refactory_dsh.console leaderboard --run /tmp/tour/run
```

Each row carries the candidate, its score, and a **diff against its parent** — so
the table says what changed, not just which number is bigger:

```json
[
  {"id": "4caa88db3dbc", "parent": "b6f0d3c9e601", "score": 0.955, "structural": false,
   "edits": [{"op": "set_knob", "name": "grid", "value": 18}],
   "diff": {"diff": {"knobs": {"grid": {"before": 10, "after": 18}}}, ...}},
  {"id": "6061dbf4589f", "parent": "4caa88db3dbc", "score": 0.955, "structural": true,
   "edits": [{"op": "set_knob", "name": "grid", "value": 18}, {"op": "duplicate", "target": "accept"}]}
]
```

And every refusal it did *not* pay for:

```bash
.venv/bin/python -c "
import json
from refactory_dsh import history
print(json.loads(open('/tmp/tour/run/report.json').read())['rejection_codes'])
"
```

```
{'E-shape': 1, 'I:fact_produced': 4, 'V10': 2, 'V2': 3, 'V3': 1, 'V4': 2, 'V5': 4, 'V6': 4, 'V7': 1}
```

That is the whole shape of the system: **a mode**, **a loop that searches it**, and
**a report that says what happened** — including everything it rejected before
spending a run.

---

## Conventions these tutorials follow

- **Run from the repository root.** The project root is resolved independently of
  your working directory (`refactory_dsh.paths.project_root()`), so any directory
  works — but the commands here assume the root, and `/tmp` for scratch output.
- **`/tmp/tour/` is disposable.** Delete it between runs when you want a clean
  start; runs never overwrite each other, they add a directory.
- **Determinism.** Every command sets `--seed`. The same seed and settings produce
  the same leaderboard, which is what makes these instructions testable rather
  than illustrative.

A mode that iterates a dataset, and the Task that supplies and judges its items, is in [Datasets and Tasks](../datasets.md).
