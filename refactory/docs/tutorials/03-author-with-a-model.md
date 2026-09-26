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
  --mode create --name annealing_pack --single --approve gate_strategy=proceed
```

The run ends with a **smoke of the new package**: it is read out of process, its first
mode is run once, and the score is recorded. A mode with an `agent` node is scored by
supplying a model client (the same one every other path uses); if no model is reachable
on the machine, the report says `score: null` with the reason and the commands that do
score it, rather than reporting `0.0`, which would read as "this scored nothing".

`--approve` is the person's answer to `gate_strategy`, carried over from create-v2:
the strategy is a decision a person owns, and a headless run is **told** it rather
than approving its own plan. Drop the flag and the run stops at that gate with the
message saying so. `uv run refactory steer --run <id> --verdict gate_strategy=proceed` records the
same answer on a paused run, and a resumed run replays it instead of asking again.

**A brief that is *about* something the caller supplies should say so.** `--knob
requires_input=package` (or `--input package` on `python -m refactory_dsh.create`, and
`inputs` on the tool's `create` action) declares what the mode must take as input.
`validate` then refuses a mode with no way to receive it — no declared knob named after
it and no read under that name — which is the difference between a mode that operates on
a package and one that hardcodes a fixture shaped like the brief. The design and emit
prompts are told the same list, so the requirement reaches the code rather than the
gate alone.

`--sample target=7` (repeatable) is how a real input reaches the validation run:
without one the smoke exercises the declared defaults, which for an input-taking mode is
the fixture it already ships. The run is also refused when what it produced is not the
format it claims — a `.whl` with no `METADATA` or `WHEEL` beside its `RECORD`, an
archive that will not open. And `--resume .factory/edit/name` continues a run that died
at the node it failed on, keeping every node that had already finished.

What happens, in order — and the gates are the point:

1. **survey** — what already exists, so the model can build on it rather than
   duplicate it. The survey names the installed modes.
2. **research** — between three and five questions the design has to answer, each one
   answered and written to `research/<slug>.md`, collected in `research-plan.json` as
   an array of `{focus, slug, finding}`. A **gate** checks that the plan is a plan and
   that every finding it names is on disk, and reloops with the reasons otherwise: the
   stage exists to ground the design in facts, and a node whose contract is
   `writes=("research-plan.json",)` is satisfied by any bytes at all, including a
   sentence about subagents still working.
3. **strategy** — the model considers **three** approaches and picks one, with a
   reason and a score. The prompt carries the survey, the JSON schema, and the
   distinction between a gate and an op.
4. **review** — a gate that *refuses* a thin design and reloops with its reasons:
   one approach only, no chosen/why/score, a `closest_existing` that is not
   installed, a design with no knobs, a graph that does not hold together (V2), a
   gate declared with a `call` instead of an `evaluator`, or an answer that is not
   JSON.
5. **emit** → **materialise** → **validate** → **register** → **smoke**.

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
  if it got worse, the run fails, naming both scores. Pass
  `--knob allow_regression=1` (or `--allow-regression` on
  `python -m refactory_dsh.edit`) when you want a revision that trades score for
  something else.

  Two modes can only be compared when a single smoke run measures both: a source
  mode with no `score()`, or one whose graph calls a model, is recorded as **not
  compared** with the reason, because one sample of a model call is not a score to
  refuse on. `revision.json` in the run directory, and `revision_of`, `before_score`
  and `after_score` in the package's `.origin.json`, say which of the two happened.
  An unmeasured comparison has `before_score: null` and `regressed: null`; a failed
  deterministic source run fails the revision instead of counting as score zero.

The Python `edit(EditConfig(...))` API and `python -m refactory_dsh.edit` use that
same graph result, including its comparison reason and Task contract. They do not
run extra keyless before/after passes. The API captures the source state before
authoring and refuses if that state changes before comparison; its persisted task
keeps this identity across resume. `--sample NAME=VALUE` reaches the revised smoke
and, when the source accepts those knobs, its comparison pass. A newly introduced
input knob makes the comparison unavailable with an explicit reason rather than
comparing the new input against the source's default input.

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
