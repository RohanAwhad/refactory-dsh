# Decisions: pythonic modes and the outer loop

This is the **decision record** for the refactory system. It says *why* the thing
is shaped the way it is, and it is the place a disagreement gets settled.

**The mechanics are not here.** How the state model, the validator, the edit
algebra, the loop, and the two runtimes actually work is
[the architecture overview](../architecture.md). This file used to carry both; the
mechanics were removed once they moved, because a document that describes an
implementation it no longer matches is worse than no document.

Status: the decisions below are **in force**. Each names the consequence that
holds it in place; a decision with no consequence is an opinion.

---

## Objective

Build "the PyTorch of the harness world": a mode is a Python class, its
architecture and parameters are a serializable `state_dict`, and optimizers
propose typed updates to that state.

The end state it was written against, and which now holds:

```
epochs=2, batch_size=4 on the ported circle_packing mode
→ a leaderboard with at least one accepted structural win
→ a rejection-cause histogram from the optimizer's internal validation loop
→ the whole thing driven by real model calls in the `optimizer` step
```

```bash
cd refactory && ../.venv/bin/python -m refactory_dsh.loop circle_packing/adaptive \
  --epochs 2 --batch-size 4 --optimizer random --seed 5 --runs-root /tmp/decisions
```

seed `0.795` → best `0.875` raw `0.955`, 1 structural win, rejections across `V2 V3 V4 V5 V6 V7
V10`, `I:fact_produced`, `E-shape`.

## Non-goals

- **No JSON compatibility.** JSON is the serialization format for state and run
  records. It is never an authoring or mutation surface. There is no importer from
  legacy JSON modes.
- **No new DSH core packages.** Everything lands under `refactory/`.
- **No GUI work in the Python system.** The loop is a Python process; the GUI is a
  view over its artifacts (see `GUI-HANDOFF.md` at the repo root for the state of
  that).

## Decisions

| # | Decision | Held in place by |
|---|---|---|
| D1 | Python is the source of truth. A mode is a `Mode` subclass. | `nn/mode.py` |
| D2 | `state_dict` carries **architecture and parameters** — the deliberate divergence from PyTorch, because the loop searches architecture. | `nn/mode.py`, `nn/validate.py` |
| D3 | The author writes `build() -> Graph`, pure. Genesis captures its output; thereafter the optimizer edits the state. | `V7` refuses a knob `build()` read |
| D4 | `score()` and `invariants()` are **methods**, never in the state. No optimizer can reach them. | structural, not defensive |
| D5 | Structural change is a first-class search operator, expressed as **typed data**, never code. | `nn/edits.py` |
| D6 | The validator replaces what `load_state_dict(strict=True)` gives PyTorch, because architecture is in the state. | `nn/validate.py` |
| D7 | Operators are **naive**: they reject, never repair. Repair corrupts the gradient by attributing a score to an edit that was not applied. | every refusal carries a code |
| D8 | The edit vocabulary is closed and enumerable: primitives, plus macros that are literally compositions of them. Broad by default, restrictable by preset. | `edits.names()`, `PRESETS` |
| D9 | The generate→validate→regenerate loop is **optimizer-owned**; the validator is one shared deterministic service. | `optim.Optimizer.attempt` |
| D10 | Op callables resolve from `"module:function"` strings. No package name is hardcoded in the library or the engine. | `nn/resolve.py` |
| D11 | An invalid proposal costs **zero** evaluation budget. | `attempt` validates before `forward` |
| D12 | Universal invariants always apply; a mode's `invariants()` cover what only the author knows. Removing work is a legitimate win when the score stays meaningful. | `nn.validate.fact_produced` |
| D13 | **Two extension points, deliberately separate**: `Optimizer` proposes and cannot run anything; `OuterLoop` owns the search. A proposer plugs into any loop; a loop can override any step. | `optim/base.py`, `outerloop.py` |
| D14 | **The loop is itself a mode.** Its eight steps are nodes, its gate loops back or leaves, and its `score()` reads the inner run's best score — so a further loop can search the loop. | `refactory_packages/loop_mode` |
| D15 | **The loop's output is a new artifact, never a mutation of its input.** `create`, `edit`, `design` and an adopting loop all produce something new, so the baseline stays measurable. | `create.py`, `edit.py`, `design.py` |

D13–D15 were decided after this record was first written, and are the reason the
mechanics sections were removed rather than updated: the shape of the optimizer
layer changed, and a plan that described the older single-class API was actively
misleading.

## Vocabulary

The PyTorch mapping is maintained in
[architecture.md §2](../architecture.md#2-the-pytorch-mapping), with its one
deliberate divergence stated there. It is not repeated here.

## What the first real run changed

Kept because the lesson generalizes, and because the same mistake is easy to make
again.

The first real 2-epoch run produced **no improvement**: seed `0.875`, best `0.875` (raw, before the size cost existed).
It was correct in every structural sense and useless as a search. Three defects,
all in the loop rather than the library:

1. **The starting population had no parameter contrast.** It was sampled across the
   whole catalogue, so `set_knob` was drowned by structural edits and epoch one
   evaluated candidates differing only in structure. All scored the same, so the
   gradient said nothing about which value of anything was worth having.
   *Now:* `init` **sweeps** — one candidate per movable knob, each at a value the
   default is not.
2. **The gradient said what had been tried, not what had not.** *Now:*
   `knob_attribution` carries `untried`.
3. **"Structural wins" counted runs that never ran.** *Now:* a win is a structural
   candidate that ran and scored.

**The lesson:** a validator can only reject what is *invalid*, and a search also
has to be given something *informative*. The first run was proof that those are
different properties, and that the second has to be designed rather than assumed.
