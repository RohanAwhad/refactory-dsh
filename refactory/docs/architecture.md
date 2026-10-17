# The refactory system, as built

Audience: someone who has to change this code, and needs to know what the pieces
are and why they are shaped that way. The descriptions follow the implementation; example outputs illustrate their recorded configuration and are not a guarantee for every model or workload.

This describes the system that exists now. It replaces an earlier one built on
`refactory_dsh.ops.scaffold` and `refactory_dsh.ops.outer_loop`, which are gone.
The requirements that earlier system failed to meet are recorded at the end, under
[Why it looks like this](#why-it-looks-like-this).

---

## 1. The one-sentence version

A **mode** is a Python class whose architecture and parameters are serializable
data, and the **outer loop** is a program that searches over those data for a
better one. The vocabulary is PyTorch's, deliberately.

## 2. The PyTorch mapping

| PyTorch | here | where |
|---|---|---|
| `nn.Module` | `Mode` | `refactory_dsh/nn/mode.py` |
| `nn.Parameter` | `Knob` | `refactory_dsh/nn/knob.py` |
| `state_dict()` | `Mode.state_dict()` | `mode.py` |
| `load_state_dict()` | `Mode.load_state_dict()`, strict | `mode.py` |
| `torch.optim` | `Optimizer` | `refactory_dsh/optim/` |
| forward pass | `Mode.run()` | `mode.py` |
| **loss / cost function** | **`Evaluator`** | `refactory_dsh/evaluators.py` |
| loss value | `Loop.loss_step()` | `refactory_dsh/loop.py` |
| backward pass | `Loop.backward()` | `loop.py` |
| `optimizer.step()` | `Loop.step()` | `loop.py` |
| training loop | `Loop` | `loop.py` |
| `torch.save` | `history.read_run` / the journal | `history.py`, `loop.py` |

**Three extension points, all seams.** `Optimizer` proposes and cannot run
anything; `OuterLoop` owns the search; `Evaluator` answers the one question the
loop's loss is built on — *given a mode and a state, run it and tell me what it
scored*. Each is a base class with a registry, so a new one is a new class and not
a new branch. The evaluator was a string compared against a literal inside
`Loop._evaluate` until issue #34.

**One deliberate divergence.** In PyTorch a state dict is parameters only; the
class is the architecture. Here the state dict carries **architecture as well as
parameters** — `{version, graph, knobs, ports, memory}` — because the outer loop
searches structure too. The consequence is stated once and matters everywhere:

> After genesis, the **state** is the authority, not the class.
> `Mode.build()` runs once, to produce the starting state; from then on the
> optimizer edits the state as data and never re-runs `build()`.

A knob is therefore consumed by a node at run time and is never read by `build()`
to choose structure. The validator refuses a knob that no node consumes (`V7`), so
the two mechanisms cannot be confused.

`score()` and `invariants()` are **methods**, and are not in the state. No
proposal can reach them. That protection is structural, not defensive.

## 3. The state model

```python
state = {
  "version": 1,
  "graph":   {"start": "task_input", "nodes": {...}, "edges": [...]},
  "knobs":   {"grid": {"value": 10, "spec": {...}}},
  "ports":   [...],   # what the run publishes
  "memory":  [...],   # named namespaces, each with a retention policy
}
```

Serialize/parse is **canonical**: nodes sorted by id, edges by
`(source, target, condition)`. That is what makes `build() -> state -> build()` an
identity rather than an approximation — `refactory_dsh/nn/graph.py`.

The layer below a `Mode` is refactory's own language — `Graph`, `Node`, `Edge`,
`Workflow.to_dict()`. The graph is *data*: an optimizer edits this, never Python
source, which is what makes a candidate revertible, diffable and exactly validatable.

## 4. The validator

`refactory_dsh/nn/validate.py`. A candidate is judged *before* anything runs, so
an invalid proposal costs no evaluation budget. The codes are published to the
optimizer's prompt via `rules_text()`.

| code | rule |
|---|---|
| `V0` | the state must be readable: it needs a graph, and the graph must parse |
| `V1` | the graph must name a start node, and it must exist |
| `V2` | every node must be reachable from the start node |
| `V3` | the graph must have a terminal (a node with no out-edge) |
| `V4` | a non-gate node has at most one out-edge, and it carries no verdict |
| `V5` | a gate has at least two out-edges, each with a condition, and no unconditional one |
| `V6` | every file a node reads must be written by a node on a path from start to a terminal |
| `V7` | every knob a node consumes must be declared, and every declared knob must be consumed |
| `V8` | every call and evaluator must resolve to a real callable |
| `V9` | every knob value must be of the declared kind and within its bounds |
| `V10` | every cycle passes a gate; after explicit `reloop` edges are removed, each cyclic region has one gate that owns its retry budget |
| `V11` | every declared port must be a file some node on a path writes |
| `V12` | a fork's targets, a join's sources and a data node's subgraph must name nodes that exist |
| `I:` | the mode's own rule, stated in `Mode.invariants()` |

Two properties of the implementation are themselves tested:

- The `RULES` table and the codes the code actually emits are the **same set, in
  both directions** — an AST test, so a new rule cannot be added without
  publishing it (`refactory/tests/test_integrity.py`).
- `on_path_nodes` is what `V6` and `V11` are computed against, so a node that is
  unreachable neither satisfies a read nor validates a port.

The validator is **naive on purpose**: it rejects, it never repairs. Repairing
hides the edit that caused the problem.

## 5. The edit algebra

`refactory_dsh/nn/edits.py`. Edits are typed data, applied to a state:

- **17 operations**: `add_node`, `remove_node`, `replace_node`, `set_node_field`,
  `add_edge`, `remove_edge`, `set_knob`, `add_knob`, `remove_knob`, `insert_after`,
  `insert_before`, `wrap`, `bypass`, `duplicate`, `split`, `reorder`, `loop`.
- The first nine are primitives; the rest are **macros** — literal compositions of
  primitives, so a macro cannot express anything a sequence of primitives could not.
- **4 presets** declare which operations a run may use: `broad` (17),
  `structure` (17), `conservative` (4), `knob_only` (3).

```bash
python -c "from refactory_dsh.nn import edits as E; print(E.names())"
```

## 6. The extension points

There are three kinds of thing that can be plugged into a run, and they are separate
on purpose:

- **`Optimizer`** — proposes. `propose()` returns `Proposal`s; the harness
  validates and evaluates them. It cannot run anything, so it cannot waste budget.
  Seven ship: `random`, `hill_climb`, `evolutionary`, `archive`, `islands`, `map_elites`, and `autoresearch` (which asks a model).
- **`OuterLoop`** — owns the search. `run()` is `init -> Run -> strategy ->
  report`, and every step a strategy would change (`select_parents`, `propose`,
  `batch_size`, `accepts`, `update`, `should_stop`, `on_epoch_end`) is a method to
  override rather than code to copy.
- **`Evaluator`** — runs a mode and reports what it scored: the loop's loss. Two
  ship. `walker` runs the graph in this process and is the fast one a search needs;
  `dsh` lowers the state to IR and runs it on the runtime that ships, at the cost of
  a process boot per candidate. `out_of_process` says which is which without
  matching on a name.

```bash
python -c "from refactory_dsh import evaluators as E; print(E.names())"
# ['dsh', 'walker']
```

All three take the same shape: a base class, a `REGISTRY` keyed by `name`, a
`create(name, ...)` that fails loud on an unknown one, and a `register()` for a new
implementation. `tests/test_evaluators.py` asserts that a third evaluator needs no
edit to `loop.py`, because that is what a seam is for.

`Run` is what a strategy is handed: `attempt` (apply a typed edit and validate it,
microseconds, no budget) and `evaluate` (one inner run, one unit of budget). The
API makes it impossible to blur those two.

**SRF's thirteen modes split across both buckets**, and the split is asserted so a
port cannot go missing or be filed wrongly:

```bash
python -c "import refactory_dsh.strategies as S, refactory_dsh.optim as O; \
print('loops  ', S.names()); print('propose', O.names())"
# loops   ['adaptive_meta', 'autoscientists', 'best_of_n', 'default', 'reflective',
#          'reflective_sampler', 'research_then_optimize', 'staged_curriculum',
#          'staged_v1', 'tree_search']
# propose ['archive', 'autoresearch', 'evolutionary', 'hill_climb', 'islands',
#          'map_elites', 'random']
```

Four of the thirteen are **optimizers** — `scs`, `evox`, `openevolve` and `karpathy`
— plus `map_elites`, which lives in `srf/lab/` rather than among the modes and is
counted by `SRF_LAB_PORT_COUNT`. The other nine are outer loops. `SRF_PORT_COUNT` is
the total, so `13 - len(SRF_OPTIMIZER_PORTS)` is the loop count rather than a number
kept in step by hand.

They were all loops until issue #32, and the cause was the contract rather than the
filing: `Loop.optimizer_step` passed a proposer only the elite, and
`Candidate.describe()` carries a score and the edits that produced it but **not the
state**. A policy that needs a wider pool — islands, a niche grid, a tournament over
an archive — could not be written as a proposer. The loop hands over the evaluated
population now and the optimizer selects, which is why `ProposalContext` carries
`elite`: an optimizer with no parent policy of its own takes that many, so widening
the pool does not silently change what it proposes.

## 7. The loop

`refactory_dsh/loop.py`. The eight steps are named like a training loop, and they
are also the nodes of `loop_mode/loop` (see §9), which is what lets a further loop
search the loop.

| step | signature | what it does |
|---|---|---|
| `init` | `() -> None` | seed the population, refuse a target that cannot run |
| `forward` | `() -> int` | evaluate the batch; the only step that spends budget |
| `loss_step` | `() -> dict` | turn the batch into one number |
| `backward` | `() -> dict` | read the traces into the gradient |
| `optimizer_step` | `(run=None) -> (candidates, rejections)` | propose, validated before anything runs |
| `step` | `(candidates, run=None) -> list` | install offspring that are genuinely new points |
| `scheduler` | `(*, evaluated=0, proposed=0) -> bool` | close the epoch, say what bound the run |
| `report` | `() -> dict` | the visibility surface |

A candidate is a state dict plus its lineage. Evaluating one is a forward pass:
materialize the mode from the state, execute its graph, score the run. An invalid
candidate never reaches `forward` at all — it is refused with a reason, and the
reason is reported (`summarise_reasons`, `I:` first).

Confirmed by running the running example:

```bash
uv run refactory optimize circle_packing/adaptive \
  --epochs 2 --batch-size 4 --optimizer random --seed 5 --runs-root /tmp/walk
```

seed `0.795` → best `0.875` (`raw_best_score` `0.955`: the compared score is the raw one minus `0.01` per node), bound `epochs`, 8 inner runs, 1 structural win
(`set_knob grid=18` + `duplicate accept`), and refusals across
`V2 V3 V4 V5 V6 V7 V10`, `I:fact_produced`, `E-shape` — none of which cost a run.

## 8. Durability, and what a run leaves

A run is a **directory**, because a reader should not need a service to read it.
`refactory_dsh/history.py`, `observe.py`.

The loop's `--runs-root` names that directory. The console's `--runs-root` names a
**parent** of run directories, because `console start` gives every run a
subdirectory; to read a run the loop wrote directly, pass `console --run <the
directory>`.

- `journal.jsonl` — every event, append-only. This is the run's durable state, and
  the **only** place a candidate's whole state is written down. Resume replays it
  (`Loop(config.resume=True)`).
- `run.json` — the layout version. A reader fails loudly on a foreign layout
  rather than half-reading it (`RunVersionError`, `RUN_VERSION`).
- `report.json` / `report.md` — the visibility surface.
- `gradient.json`, `loss.json`, `state.json`, `config.json`.
- `progress.jsonl` — one line per candidate evaluated, not per epoch, so a reader
  can tail a run that is still going.
- `manifest.json` — what to render and as what; `rejections.jsonl`.
- `runs/<candidate-id>/candidate.json` — per-candidate measurement.

The exception worth knowing: `report.json`'s `best` is a `describe()` — id, parent,
score, edits — and carries **no state dict**. To get a candidate's architecture you
read the journal. That is a known gap; see §11.

## 9. The two runtimes, and the tower

- The **walker** (`refactory_dsh/nn/executor.py`) is a debug runtime: it executes a
  graph in-process.
- **DSH** is the runtime that ships. `refactory_dsh/lowering.py` lowers a mode
  state to the IR `refactory_dsh/ir.py` owns and writes `<mode>.graph.json` for the
  science bundle, which mounts `workflow-spine` and runs each node. The reference
  that IR was copied from is a test-time oracle: `tests/test_ir_parity.py` compares
  the two byte for byte over every shipped mode and asks the reference's validator
  to accept the result, recording the two modes it refuses as limits of that
  validator. It is installed only with `uv sync --group reference`.

A debug runtime that disagrees with the real one is worse than none, so
`refactory/tests/test_equivalence.py` runs the same mode both ways and compares.

**A run that stops is a failure with a name, on both.** On DSH the graph runner reads
the cursor's own status after the CEO's turn: anything but `done` writes a line naming
the status and the nodes still due and exits non-zero, so an abandoned graph cannot
report a score as if it had run. The CEO cannot abandon one in the first place — the
graph profile mounts the subagent tool foreground-only, because backgrounding a node's
subagent is what abandoning a graph looks like from the inside. On the walker the same
rule is structural: a node that fails ends the run with `exit_ok` false and the node's
name in `error`.

```bash
uv run python -m pytest refactory/tests/test_equivalence.py -q --run-integration
# 7 passed
```

**The tower.** `refactory_packages/loop_mode` expresses the loop itself as a mode:
eight step-nodes, a gate that loops back to `forward` or leaves for `report`, and
`score()` reading the best score the inner run reached. Because it is scorable, a
further loop can search the loop's own settings. It reaches the same best score and
evaluation count as `Loop` on the same seed, and runs end to end on DSH:

```bash
uv run python -m pytest refactory/tests/test_loop_as_mode.py -q --run-integration
# 21 passed
```

**And it has been searched.** `batch_size` is a real lever on the inner result —
deterministic on seed 0, a wider batch reaches a better inner score — so a run over
the tower has something to find:

```bash
uv run refactory optimize loop_mode/loop --epochs 1 --batch-size 3 \
  --optimizer random --seed 0 --runs-root /tmp/tower
# seed 0.705 -> best 0.87, raw 0.96, by `set_knob batch_size 4 -> 8`
```

That is D14's claim demonstrated rather than described: the loop's own settings are a
search surface, and searching them moved the inner result. `test_loop_as_mode.py`
runs the search and asserts the lever, because the test that stood here asserted only
that the knobs were *visible* — expressiveness, not use.

## 10. The meta-modes

Three modes whose *output* is a mode or an artifact, in the package `default_pack`,
which is the package a checkout starts with.
All three produce something **new** and never mutate their input.

| mode | also an entry point | input | output |
|---|---|---|---|
| `default_pack/create` | `uv run refactory ceo "…" --mode create --task <module:Class>\|--single` | a brief, and which of the two things it is building | a package: `__init__.py`, `mode.py`, `ops.py` |
| `default_pack/edit` | `uv run refactory ceo "…" --mode edit` | a brief + an existing mode | a revised package, recording `revision_of` **and the state it came from** |
| `default_pack/design` | `uv run refactory ceo "…" --mode design` | a brief | a checked software artifact |

They are discovered and validated like any other mode, and they are also entry
points because a creation is a thing you run once from a shell as often as from a
loop.

`create` and `edit` are remote-factory's **create-v2**, and `design` is its
**design-v2**: a research director chooses what has to be learned and has a
subagent answer each question, a strategy director chooses the perspectives and
records which asks each one covers, `synthesize_strategy` compiles them into one
intent-faithful plan, and design-v2 adds `design_doc` — the design document the
code is written from. Between `design_doc` and `gate_strategy`, a **design review**
step checks the document: a `design_review` agent reads the design, the brief and
the code the design cites, and writes `reviews/design-review.md` listing open
alternatives and unconfirmed claims as `BLOCKING:` findings. `gate_design_review`
reads that review using the same verdict and blocking-findings parsing as
`gate_overwatch`, and adds two mechanical checks with no false positives: every
`path:line` citation names an existing file with that many lines, and every
quotation the design attributes to the brief appears verbatim in the brief. On
failure it reloops to `design_doc`; when `design_review_attempts` runs out, it
proceeds to `gate_strategy` with the remaining findings shown — it never fails the
run. A **person answers `gate_strategy`** at that point, because a
run that can approve its own plan is not reviewed at all, so a headless run refuses
that gate unless the caller supplies the verdict. The build then passes structural admission and canonical runtime validation inside the bounded repair cycle. Registration supplies execution visibility, not completion. Then three
reviews (a health check, a code review and an adversarial QA pass) run **in
parallel**, merge into one synthesis whose blocking findings the QA gate reads, and
are audited by Overwatch against the brief, accepted criteria/design, latest intent ledger and actual final validation receipts. Its verdict and requirement evidence map are checked at the last gate. A QA or
overwatch reloop goes back to the emit step, not to the plan a person approved.

All authoring review uses the original brief, approved design and explicit user steering
as its acceptance baseline. User-approved non-goals remain valid; an author or
reviewer cannot waive a requested behavior by documenting a new limitation.
Observed noncompliance stays blocking unless the user explicitly changes that
requirement. Coordinators pass the returned node prompt in full, and the three
reviews receive both the brief and design. These are prompt and context safeguards,
not automatic semantic enforcement: fresh modeled acceptance is needed to assess
whether reviewers follow them, and existing artifacts/reviews are not repaired.

The [authoring validation contract](authoring-validation.md) defines execution ownership, current-source/intent bindings, requirement maps, unavailable dependencies and the Edit comparison branch.

The parallel reviews are a **fork/join** pair, and a mode may also declare a
**`data`** node that runs a subgraph once per item; both are node kinds in the mode
vocabulary (`Node.targets`, `Node.sources`, the DataNode's subgraph fields), lowered
to the IR's own fork/join/data nodes and checked by `V12`. Both runtimes execute a
fork by running each branch to its barrier. The Python walker schedules up to four
independent flat branches concurrently, isolates their contexts, refuses conflicting
declarations, and merges successful facts in target order before the join. A failed
fork keeps its pre-fork facts and resume position; its completed file artifacts remain
available for audit. See the [fork limits](concepts.md) for unsupported nested and
shared-memory scheduling fallback. Both runtimes execute a `data` node by loading the items
(`inline_items`, or a `directory`, `jsonl` or `csv` source, then the node's limit)
and running the subgraph once per item with the item written to `current_item.json`.
The loader is one implementation (`nn.executor.load_items`); the walker runs the whole
item loop in one process, while DSH's spine drives it from outside through
`refactory_dsh.items list|begin|end`, because a subgraph the cursor enters has to be
walked one node at a time.

### The exceptions this package needs

`default_pack` is where the mode contract is deliberately stretched, and each stretch
is a declaration rather than a special case in the library:

- **Unscored, on purpose.** `create` and `design` do not override `score()`, because
  a creation is judged by whether what it produced works. Because `is_scored()` is
  derived, an unscored mode would otherwise be indistinguishable from one whose
  author forgot, so a mode says why with `Mode.unscored_reason`. The registry and
  the sandbox accept a stated reason and still refuse silence, and the outer loop
  still refuses such a mode as a search target — you cannot hill-climb on "did it
  work".
- **Their ops live in the library.** `create_ops` and `design_ops` ship with
  `refactory_dsh`, because a mode that writes a package needs the validator and the
  registry. A package may name any importable ops module. Where a design run works,
  and how its change lands and is checked, is `repo_change`'s, which any mode that
  changes a repository attaches (`fix_pack` is one); design and design_fast are two
  such modes, not special cases of the engine.
- **They need a model.** No knob makes them run without one, so a run with no client
  fails at the first agent node rather than producing nothing.

## 11. Steering a run you are using

Two ways to change a run, and they are different acts on different things:

| | what changes | whose job |
|---|---|---|
| **structure** | the mode's graph and knobs | the outer loop, and a result is adopted as a new mode |
| **steering** | what the agents know | the person, while *using* the mode |

**The graph does not change when you steer.** That is what steering means during use:
you add context, correct something, or forbid something, and the run accounts for it.

The carrier is the **intent ledger** — an append-only, timestamped file in the run
directory, seeded from `--intent` and appended to while the run goes:

```bash
uv run refactory optimize circle_packing/adaptive --intent "make it better, no new deps"
uv run refactory steer --run <run> --note "no numpy" --kind constraint
uv run refactory steer --run <run>          # read it back
```

Three kinds, because they are not the same act: `info` (context), `correction`
(something is wrong), `constraint` (must be respected, not merely known).

**How it reaches the agents.** `steering.for_run` folds the ledger into **every**
agent node's prompt before a run, and **both runtimes call it**: the walker in
`execute`, and DSH as the transform `write_mode_graph` applies. Every node, not one
the author remembered to wire, so a mode author cannot be the thing that makes
steering work — which is also why this is a transform over the IR and not a field on
`nn.Node`. Modes stay Pythonic and unchanged, and `to_workflow` knows nothing about
any of it. A note added to the ledger mid-run therefore reaches the agent nodes that
run afterwards, which is what `console steer --note` is for.

**A search carries the ledger into each candidate.** `for_run` folds in the ledger of
the work directory it is handed, and the loop evaluates a candidate in
`runs/<id>/` while the ledger sits at the run root, so `Loop._carry_ledger` copies it
down before each evaluation. Without that copy an `optimize --intent` or a
`steer --note` was written, reported by `steer`, and read by nothing — in the one
place steering is most wanted, because a search is what runs for hours. The copy is
per candidate so the directory says what the person had said when it ran.

**A person's gate answer is recorded and consumed for that review.** The ledger
keeps the decision and the hashes of the gate's declared review inputs. An
interactive answer or public `steer --verdict` authorizes one matching gate visit;
changed inputs expire an unused answer, and a later visit asks again even when the
files are identical. Legacy unscoped answers remain history, not authorization.
Explicit upfront `--approve` or `RunContext.verdicts` remains standing authorization
from the caller. A ledger-backed upfront approval remains active when that same
work directory is reopened; it has no separate invocation identifier. Without a
current scoped answer or standing authorization, a
headless run **refuses** at the gate and names it.

**It is inert when unused.** An empty ledger renders the empty string and `annotate`
returns the workflow it was given. A run nobody steered is the run it would have been,
which is what lets the interactive and headless paths be compared at all.

**Steering context travels beside artifacts, not inside their syntax.**
`fidelity(ledger)` reports a ledger that is missing, empty, or holding the
placeholder; `Ledger.seed` refuses the old `"Build something useful."` default.
At agent dispatch, the runtime supplies the current ledger and records that context
under `.refactory/agent-delivery/<id>/`, alongside the exact prompt. Repeated DSH
node views retain the original file snapshot and archive any changed prompt/ledger
context; those receipts show what the runtime supplied to the CEO, not proof that
the CEO forwarded it verbatim to a worker. Completion
records the declared outputs' hashes and whether final text or a tool write supplied
them. `console steer` exposes these receipts and reports whether their output fingerprints still
match (a later node may legitimately enrich them); outputs that disappeared are
reported separately as missing. This establishes context delivery, not that the model read, understood, or
satisfied it. Old runs' inline `Serves:` citations remain readable, but new nodes are
not asked to append prose to Python, JSON, or other structured artifacts.

**Tool-written artifacts and final responses are separate.** The walker snapshots
declared outputs before calling the model. A newly written or changed output remains
unchanged when the model returns a status message. If the first declared output was
not freshly written, the walker retains its existing final-text capture contract for
that first file; additional declared outputs must have been delivered. A file left
untouched from an earlier pass cannot satisfy that requirement. A real rewrite to
identical bytes is recognized from file metadata as well as its hash. The exact final
response is kept separately as `final-response.txt`, never stripped or repaired, and
a text fact is populated from the delivered first artifact when one is declared
(binary or directory outputs retain the original final-response fact).
The file-writing DSH path checks declared delivery before accepting agent completion;
its original worker response remains in the DSH session transcript rather than being
invented or copied by the CEO into a receipt. Neither path automatically certifies the
meaning or format of arbitrary file contents; downstream checks still own that work.
Explicit structural controls such as skip and director expansion retain their existing
semantics; a graph edit is not an ordinary verified agent delivery.

### A run with a person at a gate — the command and what it showed

`refactory_packages/review_pack` exists for this: `review` is
`task_input -> draft -> check(fn) -> approve(user) -> publish|reject`. No earlier
mode declared a gate a person answers, so the path had nothing to exercise it.

```bash
uv run refactory ceo review_pack/review \
  --knob 'topic=circle packing' \
  --intent "draft a note about circle packing and let me approve it"
```

Run against this checkout, with the terminal answering `1` (proceed) at
`[gate approve]`:

- **The IR carries who answers.** `approve` lowers to `evaluator_type="user"` with
  **no** `evaluator_command`; `check` lowers to `"fn"` **with** one.
- **The runtime asks, the model cannot answer.** The spine refuses to route a `user`
  gate without an answerer, and ignores the model's `verdict` argument: a headless
  run of the same mode stops at the gate with
  `the graph did not finish: gate approve is answered by a person and could not be
  asked: no user-questions answerer accepted the request`. Before this, the CEO's
  invented `proceed` was routed and the run reported "approved by the person".
- **The answer is recorded without granting future approval.** The terminal and
  browser answer is recorded as consumed in the run's ledger. Reopening the same
  work directory asks a fresh question. If the draft changes while a question is
  pending, the old answer is refused and the current draft needs a new answer.
  Explicit upfront approval is the supported way to grant standing authorization.
- **A mid-run note reaches the agents.** `console steer --run <workdir> --note …
  --kind constraint` appends to the ledger, and `for_run` is what folds it into the
  agent nodes of a run that starts afterwards.

## 12. Known gaps

Recorded here rather than in a backlog, because each one is a place the system
promises more than it delivers.

- **The fidelity gate cannot judge the work.** `steering.fidelity` checks the ledger
  precondition and current delivery receipts (or legacy inline citations). A receipt
  establishes the context supplied and the artifact bytes delivered, not semantic
  compliance. Whether the work **satisfies** the intent still requires reviewing the
  actual output against the request.
- **Two suites are load-flaky.** `packages/boot/app-boot/tests/hmr-config.spec.ts`
  and `user-patches.spec.ts` poll file watchers with 20s and 10s deadlines and fail
  under full-suite contention while passing standalone; `hmr-config` is not in
  `processBoundTests` while its sibling `app-boot.spec.ts` is.

**Not a gap, but worth knowing.** Four strategies — `research_then_optimize`,
`staged_curriculum`, `staged_v1`, `autoscientists` — make a decision that is only
readable by a proposer that reads `ProposalContext.strategy`. `random` does not read
it, by definition: it samples the catalogue. So under `--optimizer random` those four
behave as `default` does. That is the optimizer doing its job, not the strategy
failing; a decision needs a reader, and a sampling proposer is not one. Pair them with
`--optimizer autoresearch` to see the difference.

## Why it looks like this

The system this replaced failed in ways worth remembering, and the shape above is
the response to them. These are the findings that survived the rewrite:

- **A search has to be given something *informative*, not merely something
  *valid*.** The first real run was correct in every structural sense and useless
  as a search: the starting population was sampled across the whole edit
  catalogue, so `set_knob` — three of the operations — was drowned by structural
  edits, and epoch one evaluated candidates that differed only in structure and
  all scored the same. A validator can only reject what is invalid; it cannot make
  a batch worth learning from. The response is that `init` **sweeps** instead of
  sampling: one candidate per movable knob, each at a value the default is not, so
  the first epoch produces a gradient with contrast in it. The same distinction is
  why `knob_attribution` carries `untried` — a value that failed and a value
  nobody tried are different facts.
- **The gradient was severed and everything downstream was blind.** The optimizer
  was not given what the run had learned. The response is that `backward()`
  produces a gradient that is an explicit part of the API, `knob_attribution`
  includes **untried** values (a value that failed and a value nobody tried are
  different facts), and the report says what the optimizer actually received.
- **Knobs that lie.** A declared parameter nothing consumed looked like a search
  axis and was not. The response is `V7`, and `unreachable_knobs()` reported as a
  warning naming the lens it is invisible through.
- **Mutation operators that destroy the graph.** Some rewrites produced structures
  that could not run, and a repair pass hid it. The response is that macros are
  literal compositions of primitives, the validator is naive and refuses rather
  than repairs, and every candidate is validated *before* it costs a run.
- **Visibility defects that made the surface lie.** Structural wins counted failed
  runs; duplicates silently vanished, so a batch tried fewer points than it
  claimed. The response is `E-duplicate` refused at the optimizer layer
  (`attempt == 0` asserted), `structural_wins` requiring `exit_ok`, and the
  progress/report split.
- **Requirements the run owner raised** while using the deleted system: artifacts
  invisible in the GUI; a created mode with no identity or home; created modes not
  runnable from where they land; nothing registered by name. Those are what
  `#14` (package lifecycle and registry), `#17` (the GUI), and the `ports`/
  `manifest` surface answer.

The two handoff notes those findings were extracted from lived in
`refactory/docs/learnings/`. They were 933 lines describing the mechanics of the
replaced system — module paths, presets, and run directories that no longer exist
— so they were removed once this section had absorbed what was still true: the
requirements they raised, and the lessons above. Keeping them would have made a
newcomer's first reading a description of the wrong system.

## Where to go next

- [The concepts](concepts.md) — the nouns first: a package, a mode, the outer loop
  and the GUI, with pointers into the detail here.
- [API reference](api/index.md) — every public module and symbol.
- [Tutorials](tutorials/index.md) — the running example, built up.
- [Packing a tool that is not Python](packing-tools.md) — how a package declares a
  toolchain it cannot import, how that requirement is checked before a run spends
  budget, and how its absence is reported.
- [Contributing](contributing.md) — for a coding agent or a person changing this:
  the commands, the environment and what it hides, the invariants, and what not to do.
- [Hardening](hardening.md) — what an adversarial audit of the finished work found,
  and what holds each fix in place.

### Fresh graph invocation ownership

Terminal `ceo` and console/GUI `wf_open` share `interactive.prepare`. Preparation
stages a complete lowering before retiring any previous state. Prior declared
outputs and runtime facts, run-retention memory, graph declarations/status and agent-delivery receipts
are archived under `.refactory/invocations/<id>/artifacts/`, with an `archive.json`
manifest; a failed installation restores the prior paths. Current facts start empty.
The new mode's external inputs, explicit item selection and intent ledger remain
in place, including documented standing upfront approvals. This is a fresh graph
invocation, not a resume or an implicit redo. Unmanaged files and declared cross-run
memory are not swept away; only recorded output ownership and runtime paths are
retired. An old run lacking ownership declarations refuses reuse rather than
guessing which files to move.

## Public capability coverage

See [discovery](discovery.md) for the shared parser/mode contract, semantic review
metadata, offline generation and extension checklist. Run `pnpm run docs:generate`
after reviewed changes and `pnpm run docs:check` before building. New public
operations, mode schemas and worker capabilities require current explanations,
examples and explicit CLI/chat/panel routes.
