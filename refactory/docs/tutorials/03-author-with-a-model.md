# 3. Author with a model

Three modes whose **output is a mode or an artifact**. They are Python entry
points rather than DSH presets, because they write Python and a `graph.json` —
which a workflow graph cannot carry.

All three share one property: they produce something **new** and never mutate
their input. A creation lands in the workspace root; the package it was based on
is untouched.

> These make real model calls. Each command below was run against this checkout
> with a real provider; the outputs shown are the shape they produce, and scores
> will vary with the model.

---

## create: a brief becomes a package

```bash
uv run refactory ceo "hill-climb a greedy circle packing by accepting non-worsening moves" \
  --mode create --name annealing_pack --approve gate_strategy=proceed
```

The run ends with a **keyless smoke**: the new package is read out of process, its
first mode is run once, and the score is recorded. A mode that calls a model cannot be
scored that way — the smoke has no model on purpose, so the agent node never runs — and
the report says so (`score: null`, with the agent nodes named and the command that does
score it) rather than reporting `0.0`, which would read as "this scored nothing".

`--approve` is the person's answer to `gate_strategy`, carried over from create-v2:
the strategy is a decision a person owns, and a headless run is **told** it rather
than approving its own plan. Drop the flag and the run stops at that gate with the
message saying so. `uv run refactory steer --run <id> --verdict gate_strategy=proceed` records the
same answer on a paused run, and a resumed run replays it instead of asking again.

What happens, in order — and the gates are the point:

1. **survey** — what already exists, so the model can build on it rather than
   duplicate it. The survey names the installed modes.
2. **strategy** — the model considers **three** approaches and picks one, with a
   reason and a score. The prompt carries the survey, the JSON schema, and the
   distinction between a gate and an op.
3. **review** — a gate that *refuses* a thin design and reloops with its reasons:
   one approach only, no chosen/why/score, a `closest_existing` that is not
   installed, a design with no knobs, a graph that does not hold together (V2), a
   gate declared with a `call` instead of an `evaluator`, or an answer that is not
   JSON.
4. **emit** → **materialise** → **validate** → **register** → **smoke**.

`annealing_pack` in this repository is the output of exactly that command, and its
`__init__.py` records what it was built on:

```python
PACKAGE = Package(
    name="annealing_pack",
    description="Hill-climb a greedy circle packing by accepting non-worsening single-circle moves.",
    modes=(Anneal,),
    ops=("refactory_packages.annealing_pack.ops",),
    # Declared, because this package imports circle_packing.ops for RADII and
    # score_centers. Without the declaration, removing circle_packing would leave a
    # package that fails at import time and nothing would have warned anyone.
    requires=("circle_packing",),
)
```

`requires` is not decoration: `registry remove circle_packing` refuses while a
dependent names it, instead of leaving a package that fails at import.

Check what came out:

```bash
uv run refactory package show annealing_pack/anneal
.venv/bin/python -m refactory_dsh.sandbox --smoke annealing_pack/anneal
```

The smoke check runs the mode in a scrubbed environment. A generated package is
**AST-checked and never imported** by the checker — running code you did not write
is a decision, not a side effect.

## edit: revise a mode from a brief

```bash
uv run refactory ceo "restart from a jittered sample when the search stalls" \
  --mode edit --revise circle_packing/adaptive --name revised_pack --approve gate_strategy=proceed
```

`EditMode` is `CreateMode`'s graph with a different intent. Two differences that
matter:

- **prepare hands the model the source it is revising**, and the result records
  `revision_of`.
- **a regression is refused by default.** The revision is scored before and after;
  if it got worse, the run fails. Pass `--allow-regression` when you want a
  revision that trades score for something else.

Note what it does *not* do: it writes a **new** package. `circle_packing` is
unchanged, so "did the revision help?" stays a question you can still ask.

## design: an artifact, checked by running it

```bash
uv run refactory ceo "count words, lines and characters in a text file" \
  --mode design --name wordcount --check "python wordcount.py sample.txt"
```

`--check` is required, and that is the design decision: the artifact is judged by
**running it**, not by reviewing it. The graph has no register and no smoke step —
it has a check gate, and a design that never passes its check fails loudly rather
than landing half-built.

The artifact is written beside the workspace, not into it.

---

## Reading the output

All three leave the same kind of run directory as any other mode, so the reading
tools are the same:

```bash
# The console's run root is <project>/.factory/loop, so point it at this run.
uv run refactory runs --runs-root .factory/loop
uv run refactory status --run <run-id>
```

And the analysis the model wrote is kept per generation, which is what makes a
create-run reviewable after the fact:

```bash
cat .factory/loop/<run-id>/analysis/generation-0.md
```

## What to take away

- The authoring modes are **modes**, running through the same executor, the same
  validator, and the same journal as everything else. There is no separate
  pipeline.
- Their gates are strict on purpose: a design that cannot hold together is
  refused *before* it is emitted, because emitting it and discovering that later
  is how the system this replaced produced unreviewable output.
- Everything they create is a **new** artifact with provenance recorded, so a
  deployment can see what was generated and by what.

Next: [ship it on DSH](04-ship-it-on-dsh.md).
