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

Every issue is closed, including the ones this session finished: #58 (the v2 port, with
a live end-to-end `create` run), #59 (a data node iterates on both runtimes) and #49
(the picker, demonstrated in `refactory/docs/assets/refactory-picker.gif`). The evidence
for each is in `refactory/.work/todo.json`.

What is genuinely still open, so the next session does not rediscover it:

- **`DSH_SNAPSHOT=replay pnpm run test:web` is red on this machine**, and it is not this
  work's doing: `navigation-panes.e2e.ts` and `seeded-history.e2e.ts` fail identically
  with the picker's two bundle rows removed, so it is a local environment failure (browser
  downloads, aria goldens, timeouts). Linux CI owns that signal.
- **A dataset mode needs a composition that names the loader.** A graph with a `data`
  node is refused at load unless `executeCommands` is on and `dataCommand` is set; the
  `graph` bundle sets it, so a new composition that wants one has to as well.
- **The two runtimes still differ in two stated ways**: DSH starts a process per loader
  verb where the walker runs the loop in-process, and neither honours a data node's
  `parallelism`, so passes run one after another. Both are in the spine README and the
  2026-09-23 Agent Note.
- **The benchmark workflows in remote-factory are still not modes.** They are dataset
  contracts over the `Task` protocol and a `data` node; the seam is what they needed, and
  the survey below says why each was left.

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

The full unit suite is 19,681 tests; the per-file 100% coverage gate is
`pnpm run test:coverage`. On macOS that gate is red on exactly two files whose
platform arms cannot execute there, and this is pre-existing, not a regression:
`packages/subprocess/subprocess-local/src/linux-execve.ts` (its spec is
`describe.skipIf(process.platform !== 'linux')`) and the `/proc` read in
`packages/experimental/code-runtime-python/src/index.ts` (its `v8 ignore` comment
names the Linux coverage lane). Every file this work touches holds per-file 100%;
the two above are green in CI's Linux lane.

## What the create-v2/design-v2 port left to do

`default_pack` (formerly `meta_pack`) is the package a checkout starts with, and
its `create`, `edit` and `design` modes are now remote-factory's **create-v2** and
**design-v2** (the vendored tree, `refactory/vendor/remote-factory` at
`e7eb2490`): research director → strategy director →
`synthesize_strategy` → (design-v2's `design_doc`) → a person's gate → build →
three reviews in parallel → merged synthesis → QA gate → overwatch → its gate.
All three lower to IR with `ForkNode`/`JoinNode` and pass the IR validator.

Two things are left, each written up with its acceptance rather than estimated:

- **#59 — execute a DataNode.** A mode can declare `data` today, the lowering emits
  it, `V12` checks it and the walker *refuses* one by name; but the DSH spine hands
  the DataNode to the driving agent, where remote-factory's executor iterates
  mechanically (item loader, split/shuffle/limit, per-item context, `Task.setup`/
  `verify`). One loader, both runtimes, is the acceptance.
- **#49 — the picker.** The conversation-first half is verified: asked in plain
  language in the GUI, the CEO listed the modes, started the right one and returned
  the run id (which finished at best 1.0). The widget (host remote + a client panel
  in `sidebar.right.pane.tab` + locale dictionaries + coverage + a GIF) is not built.

Three things the next session should not have to rediscover:

- **The port's Agent Note is written**:
  `.agents/notes/implemented/architecture/2026-09-22-v2-modes-and-the-workflow-vocabulary.md`
  (with its `.zh.md` and recorded pair) states the three decisions, the vocabulary
  they needed, and the alternatives rejected. Read it before changing the node
  vocabulary or the default package's modes.
- **The dataset work is done on both runtimes** (#59, closed): the spine iterates a
  `data` node — `startPass`/`endPass` around a subgraph walked by the ordinary cursor
  — and the plugin drives `refactory_dsh.items list|begin|end` around each pass, so
  one loader serves both runtimes. The decision and its alternatives are in
  `.agents/notes/implemented/architecture/2026-09-23-a-data-node-iterates-on-both-runtimes.md`.
  A composition with a `data` node and no `dataCommand` is refused at load.
- **The walker's fork/join execution is a convenience, not the DSH semantics.** DSH
  runs a fork's branches concurrently and meets at the join; the walker runs them in
  order to the same barrier. Same barrier, same result, different concurrency — worth
  stating in the note above rather than discovering later.

Two judgement calls to keep or change deliberately: both v2 modes keep v2's
`gate_strategy` as a **person's** gate, so a headless create/design needs an explicit
`verdicts={"gate_strategy": ...}` (the tests state who approved rather than inventing
it); and a graph with a `data` node is refused at load when the composition mounts no
item loader, because a run that iterated zero items and one that iterated all of them
must not look the same.

## remote-factory's other default modes, surveyed

The registry (`factory/workflow/definitions.py::_get_builtin_registry`) builds
sixteen workflows. Read at the vendored `refactory/vendor/remote-factory`
(`e7eb2490`, six commits past the `f0d28a0c` this port first read — the later
commits decouple the graph language from the runtime and rework the outer loop,
and touch neither the `DataNode` fields, the `Task` protocol nor the v2 graphs),
the ones beyond create-v2 and design-v2 are:

| mode | its own README title | where it stands here |
|---|---|---|
| `create-v2` | create-v2 Workflow | ported as `default_pack/create` |
| `design-v2` | design-v2 Workflow | ported as `default_pack/design` |
| `devopsgym` | DevOps Gym Workflow | a benchmark harness: not ported |
| `featurebench` | FeatureBench Workflow | a benchmark harness: not ported |
| `legacybench` | LegacyBench Workflow | a benchmark harness: not ported |
| `mini-swebench` | mini-swebench | a benchmark harness: not ported |
| `outer-loop` | Outer Loop Workflow | we have `loop_mode/loop` (the loop as a mode) |
| `programbench` | ProgramBench Workflow | a benchmark harness: not ported |
| `salitrap` | SaliTrap Benchmark Workflow | a benchmark harness: not ported |
| `swebench` | SWE-bench Workflow | a benchmark harness: not ported |
| `swebenchifyhard` | SWE-benchify-hard Benchmark Workflow | a benchmark harness: not ported |
| `terminalbench` | TerminalBench Workflow | a benchmark harness: not ported |
| `tomswe` | ToM-SWE Benchmark Workflow | a benchmark harness: not ported |
| `spec-generate` | generate the project specification, gated | not ported; the nearest thing we have is `default_pack/design`, which emits an artifact and checks it |

**Why the benchmark harnesses are not ported as modes.** Each is one dataset's
contract (its instances, its setup, its grader) rather than a general way to work,
and porting them would mean porting ten dataset contracts. What they need from us is
the seam, and that exists now: the `Task` protocol
(`refactory_dsh/task.py`) carries `instances`/`setup`/`prompt`/`verify`, and a
`data` node bound to a Task iterates and is judged per item. A benchmark mode is
therefore a small package over that seam, not a copy of their workflow.

**`outer-loop`** is the loop expressed as a workflow, which is what our
`loop_mode/loop` already is; the two are the same idea, not a missing mode.
