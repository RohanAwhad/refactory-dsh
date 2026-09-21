# Handoff: where this is, and how to pick it up

Written because the session that built this is being reset. Everything a new session
needs is here or one link away; nothing in this file depends on having been there.

## What this repository is

A fork of DeepSeek Harness (DSH, at the repo root) with **refactory** in `refactory/`.
The split is deliberate and is the thing to hold on to:

- **refactory is the semantics.** A mode is a Python class; its architecture and
  parameters are one serializable `state_dict`; optimizers propose typed edits to that
  state. The vocabulary is PyTorch's on purpose.
- **DSH is the runtime.** It runs the lowered graph. It knows nothing about `Mode`,
  `Knob`, or `score()` — it receives commands and nodes.

They meet in exactly one place: `refactory_dsh/lowering.py` turns a mode state into
refactory's IR (`factory.workflow`), and `Workflow.to_dict()` is what DSH executes. An
IR that lowers is an IR that loads, because pydantic validates it.

## The four levels

The work is organised in four levels, and **they are done in order**. Do not start a
level before the one below it is verified.

| level | what it is | where | state |
|---|---|---|---|
| **1. semantics** | the language: define, create, modify modes and packages | `refactory_dsh/nn/`, `registry.py`, `refactory_packages/` | **done** |
| **2. outerloop** | the autograd: evaluate a mode, propose edits, drive the search | `loop.py`, `evaluators.py`, `optim/`, `outerloop.py`, `strategies.py` | **done** |
| **3. DSH run** | the CEO, interactive use, GUI last | `lowering.py`, `dsh_runtime.py`, `steering.py`, `packages/bundle/graph`, `tool-refactory` | **partial — #41, #42** |
| **4. packages** | specific domains, mostly for testing | `refactory_packages/` | **works where the tools are installed** |

Level 4 is optional and mostly a testbed. Levels 1 and 2 are the product.

## Level 2 has exactly three seams

This is the shape to preserve. Each is a base class, a `REGISTRY` keyed by `name`, a
`create(name, ...)` that fails loud, and a `register()` for a new implementation:

```bash
.venv/bin/python -c "
from refactory_dsh import evaluators, optim, strategies
print('evaluators', evaluators.names())    # ['dsh', 'walker']
print('optimizers', optim.names())         # 7
print('outer loops', strategies.names())   # 10
"
```

- **`Evaluator`** — runs a mode and reports what it scored. The loop's loss.
- **`Optimizer`** — proposes typed edits against a parent it selects.
- **`OuterLoop`** — owns the search. 11 overridable steps, and `loop_mode/loop` is the
  loop expressed as a mode so a further loop can search it.

`tests/test_evaluators.py` asserts a third evaluator needs no edit to `loop.py`;
`tests/test_strategies.py` asserts every SRF port is in exactly one bucket and no name
is in both. Those tests are what stop the seams collapsing back into `if` statements.

## How to run everything

From the repo root. The venv is at the **repo root**, not under `refactory/`.

```bash
# the whole Python side, including the DSH integration suites
.venv/bin/python -m pytest refactory/tests/ -q --run-integration

# without the DSH suites (fast)
.venv/bin/python -m pytest refactory/tests/ -q

# the work gate: fails while a tracked item is open, or names a commit git cannot resolve
.venv/bin/python -m pytest refactory/tests/test_work_complete.py -q

# documentation gates
pnpm run test:docs
pnpm run verify-md-links && pnpm run verify-md-wrap && pnpm run verify-translation-pairing

# TypeScript
pnpm run typecheck
```

Expected on a machine with Lean and mathlib: **639 passed, 0 failed**. Without them
about 13 tests skip — see below for what a skip is worth.

## Environment, and the two things that bite

**Lean and mathlib are optional, and their absence is silent.** The Lean tests skip
with a reason when `lean` is not on `PATH`; the mathlib tests skip when `MATHLIB_PATH`
is unset. A skipped test cannot fail, so a green suite on a machine without them says
**nothing** about the checker path. That is how two real defects hid. If you change
anything in `lean_pack`, install them or you are not testing what you changed.

**A DSH profile is machine state.** It is created with
`dsh plugin --profile <name> add <package>`; it is not derived from the bundle. A fresh
checkout does not carry one, so:

```bash
node --import tsx/esm apps/cli/src/bin.ts plugin --profile graph add \
  "$PWD/packages/bundle/graph"
```

Without it, anything that runs a mode on DSH stops with `profile "graph" does not
exist`. `--profile graph` is the headless run the DSH evaluator and the equivalence
tests use.

**A model key lives in the repo-root `.env`** as `DEEPSEEK_API_KEY`. Only
`--optimizer autoresearch` needs it, through `dsh --profile headless`.

## The first thing a new session should do

Run the walkthrough in **#40**. Ten commands, each ending in a number you can check.
It is the acceptance test for levels 1 and 2, and nothing else should be built before
it passes, because everything above depends on those two levels being real.

## What remains

All open work is filed. Read the issue, not this file, for the detail:

| # | what |
|---|---|
| **40** | Verify levels 1 and 2 by an end-to-end walkthrough — **do this first** |
| **41** | The interactive DSH run has never been walked through (level 3) |
| **42** | The GUI, to start only once levels 1–3 are verified |
| **43** | The fidelity gate checks the precondition, not the output |
| **44** | A test that did not run is invisible (Lean and mathlib skips) |
| **45** | A DSH profile is machine state, so a fresh checkout cannot run a mode |
| 17, 28 | The GUI proper; #42 orders them after levels 1–3 |

## Invariants that must not break

Each was bought with a real defect, so they are worth re-reading before a change:

- **An invalid edit is never evaluated, and a refusal costs no budget.** `Run.attempt`
  validates before `forward`; `E-duplicate` is refused at the optimizer layer.
- **`score()` and `invariants()` are methods, never in the state.** No optimizer can
  reach its own loss.
- **The state dict carries architecture as well as parameters** — the one deliberate
  divergence from PyTorch, because the loop searches structure.
- **A field at its default must not be serialized.** Adding `gate_kind` unconditionally
  changed every mode's state hash and moved a search for reasons unrelated to it; a
  test caught it as a vanished batch slot.
- **A skipped test is not a passing test.** See #44.
- **A document claiming a closed gap is the same defect as one claiming a capability
  that is not there.** §12 of `architecture.md` claimed the winner was unaddressable
  for a while after #35 had closed it.

## Where the detail lives

- `refactory/docs/architecture.md` — the system as built, its mechanics, and its gaps
- `refactory/docs/plans/pythonic-modes.md` — decisions D1–D15, and why
- `refactory/docs/hardening.md` — nine audited defects, each with its evidence
- `refactory/docs/packing-tools.md` — declaring a tool that is not Python
- `refactory/docs/tutorials/` — five walkthroughs, run against this checkout
- `GUI-HANDOFF.md` at the repo root — the GUI brief
- `refactory/.work/todo.json` — the work gate's list, with evidence per item
