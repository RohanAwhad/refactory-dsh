# 2. Optimize it

The loop searches a mode's architecture **and** its parameters. This tutorial runs
it, reads what it found, and reads the diff that explains the result.

Prerequisite: nothing. `circle_packing/adaptive` ships.

## Run it

```bash
uv run refactory optimize circle_packing/adaptive \
  --epochs 2 --batch-size 4 --optimizer random --seed 5 --runs-root /tmp/tour/run
```

```
{
  "mode": "circle_packing/adaptive",
  "optimizer": "random",
  "evaluator": "walker",
  "bound": "epochs",
  "evaluations": 8,
  "seed_score": 0.875,
  "best_score": 0.955,
  "structural_wins": 1,
  "rejection_codes": {"E-shape": 1, "I:fact_produced": 4, "V10": 2, "V2": 3, ...},
  "report": "/tmp/tour/run/report.md"
}
```

Three things to notice:

- **A bound is always named.** `"bound": "epochs"` — a run never ends for an
  unstated reason. The other bound is `max_evaluations`.
- **The score improved**: `0.875` → `0.955`, in 8 inner runs.
- **Refusals cost nothing.** `rejection_codes` lists every proposal the validator
  rejected *before* it could be evaluated.

## The steps, in order

The loop is named after a training loop, and running it with `--verbose`-style
inspection shows the same eight steps a PyTorch loop has:

| step | what it did |
|---|---|
| `init` | seeded the population — the seed, plus one neighbour per movable knob |
| `forward` | ran the batch; the only step that spends budget |
| `loss` | turned the batch into one number |
| `backward` | read the traces into the gradient the optimizer receives |
| `optimizer` | proposed the next batch, validating before anything ran |
| `step` | installed the offspring that were genuinely new points |
| `scheduler` | closed the epoch and recorded what bound the run |
| `report` | wrote the leaderboard, the gradient, and the markdown |

## Read the leaderboard

```bash
uv run refactory leaderboard --run /tmp/tour/run
```

Each row carries the candidate, its score, and a **diff against its parent**:

```json
[
  {"id": "4caa88db3dbc", "parent": "b6f0d3c9e601", "score": 0.955, "structural": false,
   "edits": [{"op": "set_knob", "name": "grid", "value": 18}],
   "diff": {"knobs": {"grid": {"before": 10, "after": 18}}, ...}},
  {"id": "6061dbf4589f", "parent": "4caa88db3dbc", "score": 0.955, "structural": true,
   "edits": [{"op": "set_knob", "name": "grid", "value": 18}, {"op": "duplicate", "target": "accept"}]}
]
```

This is the point of a diff on a leaderboard: without it the table says which
number is bigger and nothing about why.

## Read one candidate in full

```bash
uv run refactory candidate --run /tmp/tour/run 6061dbf4589f
```

The `rendered` field is the human-readable form of the same diff, and the row also
carries what the run measured (`score`, `exit_ok`, `duration_s`).

## Read the report

```bash
cat /tmp/tour/run/report.md
```

```
# Outer loop — circle_packing/adaptive

Optimizer `random`, 2 epoch(s) of 2, batch 4, 8 inner run(s). Stopped because **epochs** bound the run.

Seed score **0.875** → best **0.955**.
```

The markdown carries a leaderboard, the structural candidates that scored, and a
section for why proposals were refused — because a reader needs the refusals as
much as the wins.

## Watch a run that has not finished

A run is a directory of files, so you can read it *while* it runs:

```bash
uv run refactory status --run /tmp/tour/run --after 0
```

`--after` takes a line count, not a timestamp: a reader polling wants the events it
has not seen, and a clock cannot tell it which those are.

`progress.jsonl` has one line **per candidate evaluated**, not per epoch — an epoch
can be an hour.

## Then watch it on DSH

The walker is the debug runtime. The same mode on the runtime that ships:

```bash
uv run python -m pytest refactory/tests/test_equivalence.py -q --run-integration
```

```
6 passed
```

Tutorial [4](04-ship-it-on-dsh.md) walks through that in detail.

## What to take away

- A run is a **directory**, readable without a service — before, during, and after.
- The report says what happened *and* what was refused, and by which rule.
- Every candidate's state is in the journal, and its diff is one command away.

Next: [author with a model](03-author-with-a-model.md).
