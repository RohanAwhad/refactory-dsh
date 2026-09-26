# The concepts

This page is the mental model: what the nouns mean, how the pieces fit, and which
knob on the outside changes what happens inside. It is prose rather than a
walkthrough — the [tutorials](tutorials/index.md) are the walkthroughs, and each
section here points at the one that goes deeper.

Four nouns carry the whole system, and it is worth getting them apart before
anything else:

| noun | what it is |
|---|---|
| **package** | a named collection of modes, plus the Python module the modes' nodes call. One directory; `PACKAGE = Package(...)` in its `__init__.py`. |
| **mode** | an agentic harness for a class of work: a graph of typed nodes, the knobs that parameterize it, and a `score()` over the items. One Python class. |
| **outer loop** | the search over a mode: propose typed edits to its graph and knobs, validate them, evaluate the survivors, keep what improves, and record everything it refused. |
| **CEO** | the agent that authors a mode from your words, runs one you name, and drives a run node by node. It stops at the gates a person owns. |

The rest of this page is those four in order, plus the two surfaces you meet them
through: the command line and the GUI.

1. [The shape of the system](#1-the-shape-of-the-system)
2. [Packages](#2-packages) — what one is, what is in it, how to manage it
3. [Modes](#3-modes) — the node kinds, knobs, gates, the score and the Task
4. [Defining a mode](#4-defining-a-mode) — in Python, and with the authoring tools
5. [The outer loop](#5-the-outer-loop) — an epoch, the edits, the score, what a run leaves
6. [Running it, and every option](#6-running-it-and-every-option)
7. [Steering a run](#7-steering-a-run) — words, and control
8. [The GUI](#8-the-gui) — the panel, and the chat
9. [Where things live](#9-where-things-live)
10. [What to read next](#10-what-to-read-next)

## 1. The shape of the system

A mode is *used* and a mode is *searched*, and they are two different things you
can do with the same mode. Using it runs its graph once over its items and leaves
you the artifacts and a score. Searching it runs it many times, over candidates the
loop invented, and keeps the version that scored best.

```
   your words ──► CEO ──► a package with a mode in it        (create / edit / design)
                            │
              ┌─────────────┴──────────────┐
              ▼                            ▼
        use it once                  search it
   refactory ceo pkg/mode        refactory optimize pkg/mode
   (or the panel's Run once)     (or the panel's Start)
              │                            │
              ▼                            ▼
       artifacts + a score         a leaderboard, a report,
       at a gate: a person         and a winner to adopt
```

Three things decide what a run costs, and they are worth knowing before you start:

- **The brief decides whether a mode has `agent` nodes.** A mode whose graph has
  an agent node calls a model every time it runs; one without is deterministic and
  free. The shipped fixtures (`circle_packing`, `counting_pack`, `sympy_pack`, …)
  are deterministic on purpose, so the loop can be tested and searched without a
  provider. `java_build_pack/build` is the shipped example of a real agentic one.
- **`create`, `edit` and `design` call a model** — they are meta-modes whose nodes
  are agents writing a package.
- **The optimizer decides whether the search calls a model.** `--optimizer random`
  does not; `autoresearch` (the default) asks a model to propose each batch.

## 2. Packages

A package is a Python package under the `refactory_packages` namespace that
declares what it is: a name, a description, the modes it holds, and the module
those modes' nodes call. Everything else in it is plain Python.

```python
# refactory_packages/my_pack/__init__.py
from __future__ import annotations

from refactory_dsh.nn import Package

from .mode import MyMode

PACKAGE = Package(
    name="my_pack",
    description="what this pack is for, in one line",
    modes=(MyMode,),
    ops=("refactory_packages.my_pack.ops",),
)
```

`Package` is a frozen dataclass. The fields that matter:

| field | what it means |
|---|---|
| `name` | lowercase letters, digits and underscores, starting with a letter. It is the first half of every mode id (`my_pack/my_mode`). |
| `description` | one line. It is what `refactory list` shows beside the package. |
| `modes` | the mode classes. Every mode id in the catalogue comes from this tuple. |
| `ops` | the modules whose callables the nodes name. Declared so the package is self-describing rather than relying on import side effects. |
| `extends` | another package this one was built on, recorded for provenance. |
| `requires` | packages it imports from. Declared so `package remove` can refuse to break a dependent instead of leaving one that fails at import. |
| `trust` | `trusted` (it ships and was reviewed) or `generated` (a model wrote it). The tier decides how it may be run, and it is recorded rather than inferred — a directory name is not a trust decision. |
| `origin` | where it came from, for an imported package. |

**Where a package lives decides whose it is.** The ten shipped packages live in
`refactory/refactory_packages/` and are part of the checkout. A package you make
lives in `.factory/packages/` under your project and is yours — gitignored, and
mounted onto the same `refactory_packages` namespace at import time, so a mode in
your workspace and a mode in the checkout are the same kind of thing to everything
downstream. That is why the catalogue is not a hand-kept list: it is a scan.

**A package can be disabled without being deleted.** `refactory package disable
my_pack` takes it out of the catalogue and refuses runs of its modes, which is the
honest way to say "not on this machine" — deleting it would lose the reason.

**Two packages may not share a name.** A name in both the install and the
workspace is refused as a clash rather than resolved by precedence, because which
one a mode id meant would otherwise depend on the order of a path.

### Managing them

| command | what it does |
|---|---|
| `refactory list` | every package and mode, with the route that runs each and the gates that ask a person |
| `refactory list modes --json` | the catalogue as JSON: `action`, `interactive`, `gates`, `parameters`, `guidance`, `nodes` |
| `refactory package show <package>/<mode>` | one mode, described: its knobs, its nodes, its edges, its ports, its guidance |
| `refactory package verify <package>` | import every mode and validate its graph, without running anything |
| `refactory package smoke <package>/<mode>` | run it once, in a sandbox, on its built-in fixture |
| `refactory package tools <package>` | the tools it declares, and whether this machine has them |
| `refactory package add <dir>` / `import <dir-or-url>` | install one, optionally recording where it came from |
| `refactory package enable` / `disable` / `remove <package>` | include it, hide it, or delete it |

`refactory package smoke` is the free check: no model, no network. It is what a
created package is asked to pass before it is registered, and it is why the
shipped fixtures exist at all.

## 3. Modes

A mode is a class, and everything the system knows about it is in that class: the
graph it runs, the knobs that parameterize it, the score it is judged by, and what
it is willing to tell the search about itself.

```python
class FixedStep(nn.Mode):
    name = "fixed_step"
    description = "count up to a target in fixed steps; no RNG, so no seed knob"

    @classmethod
    def declare(cls) -> dict[str, nn.Knob]:
        return {"target": ..., "step": ...}

    def build(self) -> nn.Graph:
        graph = nn.Graph()
        # add nodes, connect them
        return graph

    def score(self, run: nn.RunResult) -> float:
        return float(run.facts["closeness"])
```

That is the whole required surface: `name`, `description`, `declare`, `build`,
`score`. The optional methods are how a mode talks to the search, and they are
covered in [3.5](#35-what-a-mode-tells-the-loop-about-itself).

A **state** is the mode's graph and knob values as data (`state.json` in a
candidate's directory). A mode class *builds* a state; the loop edits states. That
split is the whole reason a search can exist: the class is code a person maintains,
and the state is what gets proposed, validated, evaluated and kept.

### 3.1 The node kinds

A graph is nodes plus edges, and an edge may carry a condition — the verdict a gate
routes on. Nodes are typed, and the type decides what the node needs and who runs
it:

| kind | what it does | what it declares |
|---|---|---|
| `op` | runs a resolved Python callable — your code | `call` (`module:function`) |
| `agent` | makes one model call | `prompt_template`, `role`, optionally `tools` |
| `gate` | routes by a verdict | `gate_kind` (`fn`, `user` or `agent`); `evaluator` when `fn` |
| `fork` | starts independent branches concurrently (up to four on the Python walker) | `targets` |
| `join` | waits for branches | `sources` |
| `data` | runs a subgraph once per item of a bound Task | `subgraph_entry`, `subgraph_exit`, `task_ref` |

On the Python walker, fork branches receive separate copies of facts, knobs, task
inputs and upfront verdicts. Declared facts merge in target order only after every
branch succeeds; the join never runs after a failed branch. Declared file
write/write and write/read conflicts (including resolved path aliases), duplicate
facts and sibling-fact prompt dependencies are refused before dispatch. Outputs
share the run directory: this checks declarations, not arbitrary effects of user
code or agent tools. The shipped DSH client uses separate concurrent subprocesses;
a custom model client or nonempty/custom shared memory store keeps the whole
fork serial on its original caller thread, preserving signature adaptation and
thread affinity. Gates that consume live/ledger answers also use this serial path. Progress states these compatibility fallbacks explicitly.

Python fork branches currently support ordinary ops, agents and gates. Nested
forks/data passes are refused explicitly; place them outside the fork. A missing
gate answer still refuses normally rather than inventing permission. A failed fork waits for all
scheduled work to finish, preserves its files/receipts for audit, and resumes at
the fork with the pre-fork facts, replaying its branches. Progress includes each
branch's queued/running/completed/failed state and the scheduling policy. Serial
fallback stops on the first failure and marks later branches not-started. These restrictions do not add mode
knobs or change the graph's serialized form.

Every node also declares:

| field | what it is for |
|---|---|
| `reads` / `writes` | the files it consumes and produces, relative to the run directory |
| `produces` | the run *facts* it sets — the values `score()` reads |
| `knobs` | the declared knobs this node consumes |
| `notes` | one line for a reader and for the optimizer's prompt |
| `max_iterations` | for a gate: its reloop cap — the bound that makes a cycle terminate |
| `score_effect` | `affects` (default) or `none`: a block that cannot move the score says so, so an optimizer is not invited to try it |

Two of those declarations are load-bearing rather than decorative. `reads` and
`writes` are what the validator checks ("every file a node reads is written by a
node on a path from the start to a terminal"), and `produces` is what makes
`score()` checkable ("the fact it reads is produced by the pipeline the run
actually takes"). The executor enforces both at run time; the validator proves
them before anything runs. `architecture.md` has the rule table.

### 3.2 Knobs

A knob is a declared parameter: a kind, a default, optional bounds or members, an
optional `frozen`, and one line of description.

```python
"step": nn.Knob(
    2, "int", lo=1, hi=4,
    description="the step size: the largest increment one step may add",
),
"seed": nn.Knob(7, "int", frozen=True, description="frozen so a run reproduces"),
```

- **A knob must be consumed.** `declare()` says what exists, `knobs=(...)` on a
  node says what uses it, and the validator refuses a state where the two
  disagree in either direction — a declared knob nothing consumes is a knob the
  search would waste a candidate on.
- **`frozen` means the search may not move it.** You may still set it (`--knob
  seed=3`); that is how a mode whose inputs are frozen knobs (a source path, a
  test command) is searchable at all. Without `--knob`, every candidate would run
  the declared default and the run would report a search that changed nothing.
- **Bounds are part of the contract.** A `set_knob` outside them is refused before
  evaluation, with the reason fed back to the optimizer.

Knobs are the loop's cheapest surface: a candidate that differs by one knob value
runs the same graph and is directly comparable to its parent. That is why
`--preset knob_only` is a real search rather than a toy.

### 3.3 Gates: who answers

A gate is a switch, not only a binary decision: each of its out-edges carries a
condition, and the verdict names which one is taken. The kinds of verdict a gate
accepts come from its own edges, so a gate can route on `proceed`, `reloop`,
`halt` or any label it names.

`gate_kind` says **who answers**, and that is the part that decides whether a run
needs a person:

| `gate_kind` | answered by | how |
|---|---|---|
| `fn` | a function | the gate's `evaluator` runs and its verdict routes |
| `agent` | the running agent | the driving agent routes it |
| `user` | **a person** | the runtime asks them, and routes the answer they give |

A `user` gate is the one place a run waits on a person. Two consequences follow,
and both are enforced rather than documented:

- A headless run refuses a `user` gate it cannot ask anybody about, with the gate
  named — rather than inventing a verdict. There is no way to pass a verdict for
  someone else's gate through the spine's `wf_verdict`; you call it with the gate
  id alone and the runtime puts the question to the person.
- A person may pre-answer a gate for an *unattended* run: `--approve
  gate_strategy=proceed` on the command line, or the `refactory` tool's `approve`
  with an explicit `headless: true` in a session. Both say "nobody is here to
  answer", which is why neither can happen by accident.

`refactory list` marks which gates are a person's (`gate_strategy (user)`), so the
question "will this stop for me, and where" has an answer before the run starts.

### 3.4 The score, and the Task

`score()` is what the outer loop maximizes. It reads the run's **facts** — the
values nodes declared they produce — rather than the files themselves, so a score
cannot depend on something the graph did not promise to produce.

For a mode that processes a class of work rather than one input, the facts come
from a **Task**:

```python
class Task:
    name = ""

    def instances(self) -> list[TaskInstance]:      # the items to process
    def setup(self, instance, workspace) -> None:   # prepare one item (optional)
    def verify(self, instance, workspace):          # judge one pass (optional)
```

A `data` node binds a Task by reference (`task_ref`, either `module:Class` or a
registered bare name), runs its subgraph once per item, and the Task's
`VerifyResult(passed, score, details)` for each pass collects under the run's
`item_scores` fact. The mode's `score()` then decides what to do with them: the
shipped item-bound mode averages them, and a Task whose verdict is pass-or-fail
makes that average the share of items that came out right — because a mode that is
right on one example and wrong on the rest has not been built.

A Task is what makes a harness general, and it is also the unit the holdout
splits: the search sees the train part of the items, and the winner is re-evaluated
on the part it never saw. `datasets.md` is the whole page on this.

### 3.5 What a mode tells the loop about itself

Everything optional here is an honest signal to the search, and every one of them
is checked where it can be:

| method | what it says |
|---|---|
| `invariants()` | a claim that must hold of every state the mode is run in — `nn.fact_produced("closeness")` says the fact the score reads is produced by the pipeline the run takes. A candidate that breaks one is refused, not scored. |
| `guidance()` | what the traces would only show after a batch was spent finding it out: "the generator is deterministic, so raising `budget` alone re-evaluates the same candidate". It goes into the proposer's prompt. |
| `preferred_edits()` | the edits that historically move this mode, so a proposer with a budget tries them first. |
| `insertable()` | the blocks a structural edit may add. A mode that knows the fix is a node (not a parameter) can offer exactly that node. |
| `parameters()` / `describe()` | the derived summary: knobs, nodes, ports. This is what the catalogue, the panel and the optimizer prompt all read, so none of them can disagree. |

### 3.6 A mode you can read end to end

`counting_pack/fixed_step` is small enough to hold in your head and complete
enough to be searched:

```
start -> step -> check --proceed--> finish
           ^        |
           | retry  |
           +--------+
```

Four nodes, three edges, one gate. `start` opens a count at zero and writes
`state.json`; `step` adds the `step` knob to it; `check` is a gate whose evaluator
proceeds once the count has reached the `target` knob and retries otherwise,
bounded by `max_iterations`; `finish` publishes `closeness = 1 / (1 + |count −
target|)`, which is what `score()` returns.

Reading it, you can already see what the search will find: `step = 1` always lands
exactly on the target, while a step that does not divide it overshoots. That is
`guidance()`, said in one sentence so a model does not have to spend a batch
discovering it.

## 4. Defining a mode

Two routes, same artifact. One writes Python; the other describes the work and lets
a model write the Python, then checks it the same way.

### 4.1 In Python

Write the package (`__init__.py` with `PACKAGE`, `mode.py` with the class, `ops.py`
with the callables), then:

```bash
uv run refactory package verify my_pack        # import and validate every mode
uv run refactory package smoke my_pack/my_mode # run one once, offline, in a sandbox
uv run refactory list modes                    # it is in the catalogue now
```

`verify` is static (no run); `smoke` runs the built-in fixture once. Then
`refactory ceo my_pack/my_mode` runs it for real, and `refactory optimize` starts
searching it. [Tutorial 1](tutorials/01-write-a-mode.md) writes one from scratch,
and the API reference has every name.

### 4.2 With the tools

Three meta-modes in `default_pack` author modes, and all three are modes
themselves — graphs of agent nodes whose output is a validated package:

| verb | what it produces | what it guarantees |
|---|---|---|
| `create` | a package with a mode bound to a `Task` (or one input with `--single`) | the package is validated, a smoke run must pass, the mode is refused if it does not declare the `data` node the brief asked for or cannot receive an input it was told to accept |
| `edit` | a revised mode | the revision is refused if it scores worse than the one it came from (unless `--allow-regression`) |
| `design` | a checked artifact (a document, a wheel, a program) rather than a mode | the `--check` command must exit 0 |

Each is run by the CEO, from the terminal or the chat:

```bash
uv run refactory ceo "summarise a directory of markdown notes into one brief" \
  --mode create --name notes_pack --task 'my_tasks:Notes'
uv run refactory ceo "draft a design note and let me approve it" \
  --mode design --check "python -m pytest -q"
uv run refactory ceo "make the generator vary its sample" \
  --mode edit --revise counting_pack/fixed_step
```

The interesting part is the gate. An authoring mode's plan is approved at
`gate_strategy`, which is a **`user` gate** — a person's. Run it live and you are
asked. Run it with nobody there (`--headless`) and you must either answer it up
front (`--approve gate_strategy=proceed`) or it refuses rather than deciding for
you. [Tutorial 3](tutorials/03-author-with-a-model.md) is this end to end.

### 4.3 Driving one by hand, node by node

Every mode — scored or authoring — can be opened on the workflow spine and walked
one node at a time. In a chat session the CEO does it:

```
Open default_pack/design and drive it one node at a time
```

The CEO calls `wf_open <package>/<name>`, then one `wf_next` per node, reporting
each one as it completes; a `user` gate comes back to you as a question, and your
answer is what routes it. The CEO's coding tools are denied while a run is open, so
it cannot do the work itself instead of driving the graph. You can also steer
mid-run: `wf_edit set-prompt`/`append-prompt` corrects a node's prompt and
`wf_redo` re-runs it. That is the route where a person is present, and the reason
`wf_open` exists at all. [Tutorial 6](tutorials/06-run-it-with-a-person.md) does it.

## 5. The outer loop

The outer loop answers one question: given a mode that works, is there a *better*
one — a different architecture, or different knob values — and can we tell honestly
which changes helped? Everything about it follows from wanting the answer to be
believable rather than merely plausible:

- **Changes are typed data, not code edits.** A proposal is an edit from a closed
  catalogue, applied to a state. Nothing rewrites your Python, so the winner is a
  state you can read, and the baseline is still measurable.
- **Nothing is repaired.** An edit that leaves the graph broken produces a broken
  graph, and the validator refuses it. Fixing proposals up before they run would
  make the score belong to something the optimizer never proposed.
- **Refusals are recorded with a code**, so "the optimizer kept proposing
  duplicate states" is a fact in the report rather than an impression.

### 5.1 An epoch

`--epochs` is how many rounds; each round is the same sequence of steps:

1. **Propose** a batch of candidates from the current population (or the seed).
   The optimizer picks the parents and the edits.
2. **Validate** each proposal: apply the edit, then check the result against the
   validator's rules (a real start node, every read produced, every cycle through a
   gate, calls resolve, knobs declared and in range). A refusal carries a code and
   the reasons are fed back to the optimizer, which is why a batch is proposed over
   `--attempts` rounds when the first is refused.
3. **Evaluate** each survivor: run the mode on the train part of the items and take
   its score.
4. **Select** the next parents from the scored population, ranked by score (ties
   broken by id), keeping `--elite` of them.

Then the epoch is closed and summarized: best, mean, whether it improved, how many
proposals were accepted and refused. `--batch-size` is the size of one batch,
`--max-evaluations` a hard ceiling on how many candidates are ever run, and
whichever bound ends the run is named in the report — a run never ends for an
unstated reason.

The seed population deserves a note: the loop does not start from the mode's
default state alone. It sweeps neighbours of it — one candidate per movable knob,
each at a value the default is not — because a first epoch with no contrast in it
produces no evidence about which value of anything is worth having.

### 5.2 The edits it may make

The edit algebra is **nine primitives plus eight macros**, closed and enumerable:

| primitives | macros |
|---|---|
| `set_knob`, `add_knob`, `remove_knob` | `insert_after`, `insert_before`, `wrap` |
| `add_node`, `remove_node`, `replace_node`, `set_node_field` | `bypass`, `duplicate`, `split`, `reorder`, `loop` |
| `add_edge`, `remove_edge` | |

A macro is a literal composition of primitives, so it cannot express anything the
primitives cannot — which is what makes the reachable space reviewable. A macro
whose precondition does not hold is refused before it applies; a macro that applies
and produces a broken graph is a validation failure. Those are two different
messages, and both go back to the optimizer.

`--preset` chooses the subset. `broad` (the default) and `structure` are both the
whole catalogue today — seventeen operations; `structure` is the name for the
structural intent and there is no separate subset behind it yet. `conservative` is
four (`set_knob` plus `insert_after`, `insert_before`, `wrap`), and `knob_only` is
three (`set_knob`, `add_knob`, `remove_knob`) with no structural change at all.
Restricting the catalogue is how you ask a cheap question — "can the parameters
alone fix this?" — without paying for structural candidates.

### 5.3 The score it maximizes

The number the search compares is

```
score = raw_score − parsimony × nodes
```

where `raw_score` is what the mode's `score()` returned and `nodes` is the node
count of the candidate's graph. The size cost is there so a search has no reason to
grow the harness for free, and both numbers are recorded on every row, so a
candidate that won on merit is told from one that won on size. `--parsimony 0`
turns the cost off.

**The winner is then audited on items the search never saw.** The Task's items are
split, the train part is what candidates are scored on, and the winner is
re-evaluated on the held-out part. The result lands in `holdout.json` and in the
report as `{train, holdout, gap, overfit, threshold, instances}`, with `overfit`
true when `train − holdout` exceeds `0.15`. `refactory best` reports the audit, and
`refactory adopt` refuses an overfit winner unless you pass `--allow-overfit` —
because a harness that only works on its training items is not the thing you asked
for. The split travels in the run directory (`subset.json`), not in a knob, so a
search cannot choose its own exam.

### 5.4 What a run leaves behind

A run is a directory. It is also the run's durable state, because the loop is
resumable and a report you cannot re-derive is a report you have to trust:

| file | what it is |
|---|---|
| `journal.jsonl` | the append-only log of everything that happened: the start, every candidate's state, every score, every refusal, every epoch boundary, every control act. `--resume` rebuilds the run by replaying it. |
| `state.json` | the live snapshot a reader polls: epoch, generation, evaluations, the population, and what a person kept or rejected |
| `progress.jsonl` | the same events at the granularity a person watches, one line per candidate |
| `report.json` / `report.md` | the result: the best candidate and its lineage, the leaderboard, per-epoch summaries, the refusal histogram, the proposal outcomes, the cost, the held-out audit |
| `runs/<candidate>/` | one directory per candidate: the files its nodes wrote, its `subset.json`, its own recorded score |
| `config.json`, `console.json`, `run.json` | what the run was configured with, and what started it |

Because the journal is the state, a run is reproducible rather than merely
recorded: with a deterministic optimizer and the same seed, replaying it produces
the same candidates, which is what makes `--resume` a continuation instead of a
second opinion.

### 5.5 The level-two seams

Three seams are chosen per run rather than per mode, and they are the difference
between a cheap search and an expensive one:

| seam | choices | what it decides |
|---|---|---|
| `--strategy` | `default`, `best_of_n`, `reflective`, `reflective_sampler`, `tree_search`, `staged_curriculum`, `staged_v1`, `adaptive_meta`, `autoscientists`, `research_then_optimize` | the *shape* of the outer loop. `default` is the loop described above; the others are named alternative loops, most of them ports of published schemes (`best_of_n`, `gepa`, `aide`, `autoresearch`, …). |
| `--optimizer` | `random`, `hill_climb`, `evolutionary`, `archive`, `islands`, `map_elites`, `autoresearch` | how a batch is proposed. `random` samples the catalogue uniformly (free, and the right choice for a fixture); `hill_climb` takes the best so far; `evolutionary` runs a tournament; `archive`/`islands`/`map_elites` keep diversity; `autoresearch` asks a model to read the traces and propose — the strongest and the only one that costs model calls. |
| `--evaluator` | `walker`, `dsh` | which runtime scores a candidate. `walker` runs the graph in this process — fast, and what a search uses. `dsh` lowers the candidate to IR and runs it on the runtime that ships: the honest measurement, an order of magnitude slower, and what tutorial 4 is about. |

The current names, with one line each, come from the console
(`uv run python -m refactory_dsh.console strategies`), and the generated
[tour](tutorial.html) has a row for each. A strategy is a subclass of the loop that
overrides one step, not a separate program, which is why adding one does not mean
reimplementing the loop.

## 6. Running it, and every option

```bash
uv run refactory optimize counting_pack/fixed_step \
  --epochs 4 --batch-size 6 --optimizer random --seed 5
```

That is the offline version: deterministic, no model calls, and — for this fixture
— under a second for eighteen evaluations. Fixtures are not equally cheap:
`counting_pack` here evaluates in milliseconds, `circle_packing/adaptive` takes
about fifteen seconds a candidate, and a mode with agent nodes pays for a model
call every time it runs. The shipping choices for a real mode are `--evaluator
dsh` (measure on the runtime that ships) and `--optimizer autoresearch` (ask a
model to propose); both are worth their cost once the mode is real.

| option | what it does | default |
|---|---|---|
| `--epochs <n>` | rounds of propose → validate → evaluate → select | 2 |
| `--batch-size <n>` | candidates proposed per epoch | 4 |
| `--optimizer <name>` | how candidates are proposed | `autoresearch` |
| `--strategy <name>` | which outer loop drives the search | `default` |
| `--evaluator walker\|dsh` | which runtime scores a candidate | `walker` |
| `--preset <name>` | which edits are allowed: `broad`, `structure`, `conservative`, `knob_only` | `broad` |
| `--seed <n>` | reproduce a search exactly | 0 |
| `--elite <n>` | how many parents each round proposes against | 3 |
| `--max-evaluations <n>` | a hard ceiling on evaluated candidates | 64 |
| `--holdout <fraction>` | the share of items kept out of the search and used to audit the winner | 0.3 |
| `--parsimony <cost>` | what each node costs a candidate's score | 0.01 |
| `--attempts <n>` | retries per batch after a refusal | 3 |
| `--runs-root <dir>` | the run directory itself (not a parent of runs) | `.factory/loop` |
| `--intent "<words>"` | what the run is for; seeds the ledger agent nodes read | empty |
| `--model <name>` | the model the reasoning optimizer asks (not the mode's own agents) | the configured route |
| `--provider <route>` | the provider route that model is on | your settings default |
| `--knob NAME=VALUE` | a knob value the search starts from (repeatable) | the mode's declared defaults |
| `--verbose` | print every event, plus each candidate's own run output | off |

**The loop says what it is doing while it does it.** Each candidate's score, the
batch's refusals with their codes, each epoch's best, and the held-out audit are
printed as they happen; the final JSON goes to stdout on its own, so a script can
parse it and a person can watch it. `--verbose` adds everything else and the child
processes' own output. `refactory status --run <id>` reads the same records back,
which is what the panel polls.

To read a run back while it is still going, from another terminal:

```bash
uv run refactory status --run .factory/loop/<run id>      # where it is
uv run refactory leaderboard --run .factory/loop/<run id> # the candidates
uv run refactory candidate <id> --run .factory/loop/<run id>  # one candidate's diff
uv run refactory best --run .factory/loop/<run id>        # the winner + holdout
uv run refactory adopt --run .factory/loop/<run id> --as my_tuned
```

`refactory adopt` writes the winner as a package of its own, leaving the run and
the source untouched — the loop's output is a new artifact, never a mutation of its
input, which is what keeps the baseline measurable.

## 7. Steering a run

There are two channels, and they carry different things:

- **Words go to the intent ledger.** The proposer reads the ledger fresh before
  every generation, so a note added mid-run reaches the next one; every agent node
  in an interactive run reads it too. `--intent` seeds it, and `refactory steer
  --note "<words>" --kind info|correction|constraint` appends to it. A correction
  to a *node's prompt* is the other half of this, and it is `wf_edit
  set-prompt`/`append-prompt` plus `wf_redo` in a driven run.
- **Control is a small file, not prose.** It has to work from another process
  (a terminal, the panel), so the loop polls it at a generation boundary. The acts
  are flags of `refactory steer --run <id>` (and buttons in the panel):

| act | what it does |
|---|---|
| `--pause` / `--resume` | hold the run at its next boundary, or release it |
| `--stop` | end the run, recording that a person asked |
| `--keep <candidate>` | pin a candidate as a parent even once a newer batch outscores it (`--lineage` for its descendants too) |
| `--reject <candidate>` | take it out of the leaderboard, so it cannot be proposed from, reported best or adopted (`--lineage` for the branch) |
| `--adopt [--as <package>]` | write the current winner now, without ending the search |

Two properties are worth knowing, because they are deliberate rather than
limitations. **Acts land at a generation boundary** — never inside an evaluation —
so an act asked during a long model call waits for that call to finish. And **one
request at a time**: a one-shot act still in the file has not been honored yet, so
a second one is refused rather than silently replacing it. `pause`/`resume`/`stop`
may overwrite anyway; a stop is not something to hold up behind a candidate act.

After the search finishes, live-run controls are refused and disabled in the panel.
Adopt applies immediately through the same successful-winner and held-out-audit
guards as direct adoption. The panel and `steer` reads expose queued, applied or
refused receipts. Legacy queued Keep/Reject choices are recovered before either
adoption route selects a winner; a changed winner requires its own audit. The
[steering tutorial](tutorials/06-run-it-with-a-person.md) shows how to obtain that
audit without new proposals or epochs.

## 8. The GUI

```bash
uv run refactory ui --port 3081 --no-open
```

It prints a URL with a per-process token; open it, pick this checkout as the
workspace, send one message (a session has to exist before the sidebar appears),
then open the **Modes** tab from the conversation header. The panel and the chat
are two surfaces over the same console — a run started in the browser is the run
`refactory status --run <id>` reads back, and vice versa.

### 8.1 The panel

| section | what it offers |
|---|---|
| **Start a run** | the mode picker (id and description), the chosen mode's declared knobs, the three level-two seams, **Start** (a search) and **Run once** (use the mode once) |
| **Run status** | the run's id, its state, its best score, how many candidates have been evaluated, and **Refresh**/**Stop** |
| **Steering** | the intent ledger and a form to add a note, the gates the run is stopped at with a button per verdict, **Pause**/**Resume**/**Stop**, and the candidates table |
| **Candidates** (inside Steering) | read on demand; one row per candidate with its score and what you have done to it, **Keep**/**Reject** per row, and **Adopt best now** above it |
| **Packages of modes** | every package, with enable/disable, remove and import |

The panel is the clickable version of the four steps: pick → Start (or Run once) →
read the run → adopt the winner. [Tutorial 7](tutorials/07-pick-and-watch-from-the-panel.md)
walks it.

### 8.2 The chat

The chat is the CEO, and the CEO is a normal DSH session with the `ceo` preset: the
coding tools plus the `refactory` tool (the console's verbs, so a run can be
started, watched, steered and adopted from the conversation) and the workflow
spine's `wf_*` tools (so a mode can be opened and driven one node at a time).

What that means in practice:

- **Ask for something and it is authored**: "build a harness that summarises a
  directory of notes" runs the `create` meta-mode, which you approve at its
  strategy gate.
- **Name a mode and it is run**: "run counting_pack/fixed_step" runs it, with you
  answering any gate it reaches.
- **Ask it to drive one and it walks the graph**: `wf_open`, then one `wf_next` per
  node; you can correct a node's prompt and have it re-run.
- **Ask what a run did and it reads it**: `status`, `leaderboard`, `candidate`,
  `best`, `artifacts`, `steer` are all available as tool actions.

[Tutorial 5](tutorials/05-drive-it-from-the-gui.md) is the conversation surface in
full.

## 9. Where things live

### Layout

```
refactory/            the Python glue: the CLI, the mode library and the outer loop
  refactory_dsh/      the loop, the console, the registries, the runtimes
  refactory_packages/ the ten packages of modes this checkout ships
  docs/               this page, the tutorials, the architecture and the API reference
packages/             the six TypeScript packages this repository owns
.factory/             what a run and a created package write (not in git)
```

The Python side owns the modes, the loop and the console; the TypeScript side owns
what a mode runs *on* (the graph bundle), the spine that drives a graph one node at
a time, the model-facing `refactory` tool, and the two browser bundles. A mode is
Python all the way down; the harness it runs on is TypeScript.
[architecture.md](architecture.md) has the layer-by-layer version and says which
layer owns which behaviour.

## 10. What to read next

| question | document |
|---|---|
| do I want to do the next thing, in order? | [the tutorials](tutorials/index.md) |
| what does every node kind, knob and flag do? | [the generated tour](tutorial.html) |
| what are the layers, and who owns a behaviour? | [architecture.md](architecture.md) |
| how do I bind a mode to a class of work? | [datasets.md](datasets.md) |
| how does a mode declare a tool it needs installed? | [packing-tools.md](packing-tools.md) |
| which guarantees are enforced, and by what? | [hardening.md](hardening.md) |
| providers, model ids, `~/.dsh/settings.yaml` | [models.md](models.md) |
| what is every public name for? | [the API reference](api/index.md) |
| how do I change this repo? | [contributing.md](contributing.md) |

### Known rough edges

Three things this page describes are worth knowing before you plan around them:

- **Control waits for a boundary.** A pause asked during `autoresearch`'s model
  call lands when that call returns. It is the price of never interrupting an
  evaluation.
- **The panel's Steering section follows the run the panel started.** A run a chat
  session opened is readable with `refactory status --run <id>`, but the panel will
  not be watching it (issue #89).
- **All ten shipped packages are fixtures except `default_pack` and
  `java_build_pack`.** They are deterministic so the loop can be tested and
  searched offline; they are not examples of what a harness for real work looks
  like. Build one from a brief, or read `java_build_pack/build`.
