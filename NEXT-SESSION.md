# The prompt for the next session

Paste the block below. It assumes nothing from the conversation that produced it.

---

You are picking up a system built over a long session that has now been reset. Everything
you need is in the repository; nothing is in my head. Your job is **not** to trust what
is written — it is to run it and find out.

## What this is

A fork of DeepSeek Harness (DSH, repo root) with **refactory** in `refactory/`. Two halves,
deliberately separated:

- **refactory is the semantics.** A mode is a Python class; its architecture *and*
  parameters are one serializable `state_dict`; optimizers propose typed edits to it. The
  vocabulary is PyTorch's on purpose — `Mode`≈`nn.Module`, `Knob`≈`nn.Parameter`.
- **DSH is the runtime.** It executes the lowered graph and knows nothing about `Mode`,
  `Knob`, or `score()`. It receives commands and nodes.

They meet in exactly one place: `refactory_dsh/lowering.py` turns a mode state into
refactory's IR, and `Workflow.to_dict()` is what DSH runs.

The work is organised in four levels, **done in order**:

1. **semantics** — the language: define, create, modify modes and packages. *Done.*
2. **outerloop** — the autograd: evaluate a mode, propose edits, drive the search. *Done.*
3. **DSH run** — the CEO, interactive use, GUI last. *Partial.*
4. **packages** — specific domains (maths, circle packing, SRF), mostly a testbed. *Optional.*

## Read these first, in this order

```bash
cat refactory/docs/handoff.md          # the map: levels, readiness, commands, traps
cat refactory/docs/architecture.md     # §1–§7 are the mechanics
cat refactory/docs/plans/pythonic-modes.md   # decisions D1–D15, and why
gh issue list                          # all open work
```

Then read every open issue. Each is self-contained and links back to the handoff.

## Do this before changing anything

**Run the walkthrough in issue #40.** `gh issue view 40`.

It is ten commands across levels 1 and 2, and each ends in a number, a diff, or a refusal
you can check. Do it *first* and do it by hand, because the point is to **see** what the
system does rather than read a description of it. You will come out knowing:

- what a mode is and what `state_dict` carries
- how an edit is proposed, validated, evaluated, kept or refused
- what the three level-2 seams are — `Evaluator` (the loss), `Optimizer` (the proposer),
  `OuterLoop` (the search) — and that a new implementation of any of them needs no edit to
  the loop
- how a run is read, and how its winner becomes a new mode

If a step does not do what #40 says, **that is the finding**, not a detour. Report it with
the command and the observed output, and file it.

## Then work the issues, in this order

**#40** verify levels 1–2 by the walkthrough — first, because everything above depends on
those two levels being real.

**#41** the interactive DSH run. No mode has ever been run with a person in the loop: the
intent ledger, the steering verbs and person-answered gates are built and unit-tested, but
never exercised end to end. This is level 3.

**#42** the GUI — and only after #41, deliberately. Building it earlier means building
against a runtime whose behaviour is assumed, and then fixing every defect twice.

**#43, #44, #45** are independent and can be taken any time: the fidelity gate's output
half; making an unrun test visible; making a fresh checkout able to run a mode on DSH.

## Rules of engagement

**Run everything.** Nothing is verified because it is written down. This session's
predecessor found five places where a capability was recorded as done and was not — a
fidelity gate that checked a default, an evaluator seam that was an `if`, an adopting loop
that did not exist, a documented command for a deleted file, and a mode that could not
declare a pause point while the issue that added it was closed as landed. Assume that of
anything you have not executed.

**A skip is not a pass.** The Lean and mathlib tests skip when the tools are absent, and a
green suite on such a machine says nothing about the checker path. Two real defects hid
behind exactly that. Check whether the tools are installed before trusting the count;

```bash
.venv/bin/python -m pytest refactory/tests/ -q --run-integration   # with Lean + mathlib: 639 passed
```

**Evidence or it did not happen.** Every claim about behaviour needs a command and its
output. When you fix something, check the fix both ways: restore the broken version, watch
the test fail, restore the fix, watch it pass. A test that cannot fail is not a test.

**Document what you learn where a reader meets it**, not in a summary at the end. If you
close a gap, remove it from `architecture.md` §12; a document claiming a closed gap is the
same defect as one claiming a capability that is not there.

**Keep the levels in order.** Do not start level 3 to avoid finishing level 1, and do not
touch the GUI before #41 passes.

## The shape to preserve

Level 2 has exactly three seams. Each is a base class, a `REGISTRY` keyed by `name`, a
`create()` that fails loud, and a `register()` for a new implementation:

```bash
.venv/bin/python -c "
from refactory_dsh import evaluators, optim, strategies
print('evaluators', evaluators.names())    # ['dsh', 'walker']
print('optimizers', optim.names())         # 7
print('outer loops', strategies.names())   # 10
"
```

`tests/test_evaluators.py` asserts a third evaluator needs no edit to `loop.py`, and
`tests/test_strategies.py` asserts every SRF port sits in exactly one bucket. Those tests
are what stop the seams collapsing back into `if` statements. If you add a seam, add that
test with it.

## Environment

The venv is at the **repo root** (`.venv/`), not under `refactory/`, and every command runs
from the repo root. `DEEPSEEK_API_KEY` is in the repo-root `.env` and is needed only by
`--optimizer autoresearch`.

Two things that will bite you, both in the handoff in more detail: a **DSH profile is
machine state** and must be added once per machine (#45), and **Lean/mathlib are optional
and silent** when absent (#44).
