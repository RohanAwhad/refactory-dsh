# The prompt for the next session

Paste the block below. It assumes nothing from the conversation that produced it.

---

You are picking up a system built over long sessions. Everything you need is in the
repository; nothing is in my head. Your job is **not** to trust what is written — it
is to run it and find out.

## What this is

A fork of DeepSeek Harness (DSH, repo root) with **refactory** in `refactory/`. Two
halves, deliberately separated:

- **refactory is the semantics.** A mode is a Python class; its architecture *and*
  parameters are one serializable `state_dict`; optimizers propose typed edits to it.
  The vocabulary is PyTorch's on purpose — `Mode`≈`nn.Module`, `Knob`≈`nn.Parameter`.
- **DSH is the runtime.** It executes the lowered graph and knows nothing about
  `Mode`, `Knob`, or `score()`. It receives commands and nodes.

They meet in exactly one place: `refactory_dsh/lowering.py` turns a mode state into
refactory's IR, and `Workflow.to_dict()` is what DSH runs.

## Where the work stands

**All four levels are built and were verified by running them.** Read
`refactory/docs/handoff.md` first — it carries the map, the four levels, how to run
everything, the traps, and what each verification found. Then
`refactory/docs/architecture.md` §1–§7 and `plans/pythonic-modes.md`. Then
`gh issue list` and read every open issue.

What was proven, by execution:

- **Levels 1–2** — the #40 walkthrough ran; every step's command and result are in
  the handoff, and the three defects it found were filed and fixed.
- **Level 3** — a mode with a person's gate (`review_pack/review`) runs under the CEO;
  the runtime asks the person, records the verdict in the ledger, and a resume
  replays it. A headless run refuses the gate instead of inventing a verdict.
- **The GUI** — driven in a real browser: selecting a workspace creates the session,
  and the whole system is reachable from the conversation through the `refactory`
  tool (modes, strategies, start/status/leaderboard/candidate, artifacts, steer,
  best/adopt, registry, create/edit/design).

## What is left

**#49 is the only open issue**: a first-class picker for the mode, the loop and the
packages, instead of the conversation. It needs a host RPC seam (the tool is
model-facing only), so read the issue before starting.

## Rules of engagement

**Run everything.** Nothing is verified because it is written down. Assume that of
anything you have not executed, including the claims in this file.

**A skip is not a pass.** The Lean and mathlib tests skip when the tools are absent;
`.venv/bin/python -m pytest refactory/tests -q --toolchain-gate` is the command that
turns that into a report. Check whether the tools are installed before trusting a
green suite.

**Evidence or it did not happen.** Every claim about behaviour needs a command and
its output. When you fix something, check it both ways: restore the broken version,
watch the test fail; restore the fix, watch it pass.

**Document what you learn where a reader meets it.** If you close a gap, remove it
from `architecture.md` §12; a document claiming a closed gap is the same defect as one
claiming a capability that is not there.

**The shape to preserve.** Level 2 has exactly three seams — `Evaluator`, `Optimizer`,
`OuterLoop` — each a base class, a `REGISTRY` keyed by `name`, a `create()` that fails
loud, and a `register()`. `tests/test_evaluators.py` asserts a third evaluator needs
no edit to `loop.py`; `tests/test_strategies.py` asserts every SRF port sits in
exactly one bucket. If you add a seam, add that test with it.

## Environment

The venv is at the **repo root** (`.venv/`), and every command runs from the repo
root. `DEEPSEEK_API_KEY` is in the repo-root `.env`; the model-driven paths need it.
Two things that will bite you, both in the handoff in more detail: a **DSH profile is
machine state** (`install.sh` creates it once per machine; `refactory_dsh/dsh_profile.py`
answers "is it there, and what fixes it"), and **Lean/mathlib are optional and silent**
when absent.

Before trusting a change, the cheap full pass is:

```bash
.venv/bin/python -m pytest refactory/tests/ -q --run-integration
pnpm run typecheck && pnpm run hygiene && pnpm run test:docs
```
