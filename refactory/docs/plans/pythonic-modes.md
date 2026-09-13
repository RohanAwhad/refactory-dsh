# Pythonic modes and the optimizer loop

Status: **active**. This document is the contract for the implementation and the
drift checklist for it. Re-read it at every phase boundary and before claiming a
phase done. If the implementation and this document disagree, one of them is
wrong and the disagreement is the bug.

## 1. Objective

Build "the PyTorch of the harness world": a mode is a Python class, its
architecture and parameters are a serializable `state_dict`, and optimizers
propose typed updates to that state. Then run the outer loop for 2 epochs on a
real problem with real model calls and get a leaderboard out.

Acceptance test (the end state, not an illustration):

```
epochs=2, batch_size=4 on the ported circle_packing mode
→ leaderboard with at least one accepted structural win
→ a rejection-cause histogram from the optimizer's internal validation loop
→ the whole thing driven by real model calls in the `optimizer` step
```

## 2. Non-goals

- **No JSON compatibility.** JSON is the serialization format for state and run
  records. It is never an authoring or mutation surface. There is no importer
  from legacy JSON modes; the one existing mode we care about is hand-ported.
- **No GUI work.** Deferred until the Python path is implemented, tested, shipped.
- No new DSH core packages. Everything lands under `refactory/`.

## 3. Decisions

| # | Decision |
|---|---|
| D1 | Python is the source of truth. A mode is a `Mode` subclass. |
| D2 | `state_dict` is `{version, graph, knobs}` — architecture **and** parameters. This is the divergence from PyTorch (`nn.Module` keeps architecture in the class), and it is deliberate: we do architecture search. |
| D3 | Author writes `build() -> Graph`, pure, no side effects; loops and conditionals over knobs are allowed. Genesis captures `build()`'s output as the architecture state; thereafter the optimizer edits that state. |
| D4 | `score()` and `invariants()` are **methods**, never in the state dict. No optimizer can reach them. This is structural cheat protection, not defensive coding. |
| D5 | Structural change is allowed and is a first-class search operator. It is expressed as **typed data** (an edit), never as code. |
| D6 | The validator replaces the guarantee `load_state_dict(strict=True)` gives PyTorch, because we put architecture in the state. It is load-bearing. |
| D7 | Mutation operators are **naive**: no repair, no auto-fix, no invariant maintenance. Invalid edits are rejected with reasons, never corrected. Repair corrupts the gradient (score would be attributed to an edit that was not applied). |
| D8 | The edit vocabulary is **closed and enumerable**: a small primitive algebra, plus macros that are literally compositions of primitives. Broad by default, restrictable per run via named presets. |
| D9 | The generate→validate→regenerate loop is **optimizer-owned**; the validator is one shared deterministic service. |
| D10 | Op callables are resolved by the runtime from `"module:function"` strings. No package name is hardcoded anywhere in the library or the engine. |
| D11 | An invalid proposal costs zero evaluation budget: it is validated for microseconds before any inner run. |
| D12 | Contract policy: universal invariants always apply; mode-declared `invariants()` cover metric integrity. There is no frozen seed contract — removing work is a legitimate win when the score stays meaningful. |

## 4. Vocabulary (PyTorch mapping)

| PyTorch | refactory |
|---|---|
| `nn.Module` | `nn.Mode` — `build()`, `score()`, `invariants()` |
| `nn.Parameter` | `nn.Knob` |
| `state_dict()` / `load_state_dict(strict=)` | same names |
| `model.parameters()` | `mode.parameters()` — the write surface |
| `optim.Optimizer` | `optim.Optimizer` (`Random`, `Autoresearch`) |
| `loss.backward()` | `backward()` — trace facts |
| `DataLoader` / batch | population / `batch_size` |
| `lr_scheduler` | `scheduler()` |
| `forward()` | the inner run: execute the built graph once |

Loop names: `init → forward → loss → backward → optimizer → step → scheduler → report`.

## 5. Module map

```
refactory/refactory_dsh/nn/          the library — torch.nn analogue
  __init__.py        public surface
  knob.py            Knob, KnobError, coercion + bounds
  graph.py           Node, Edge, Graph, canonical state
  mode.py            Mode, RunResult, state_dict/load_state_dict
  package.py         Package, mode_id, split
  resolve.py         runtime-owned "module:function" resolution + catalogue
  validate.py        Issue, validate(), universal invariants
  edits.py           primitive algebra, macro catalogue, apply()
  executor.py        executes a built graph in-process
refactory/refactory_dsh/optim/       torch.optim analogue
  __init__.py
  base.py            Proposal, ProposalContext, Rejection, Optimizer (+ internal loop)
  random_optimizer.py
  autoresearch_optimizer.py
refactory/refactory_dsh/loop.py      the engine: init/forward/loss/backward/optimizer/step/scheduler/report
refactory/refactory_dsh/registry.py  package discovery, enable/disable, catalogue
refactory/refactory_dsh/create.py    create-mode: the graph and the driver
refactory/refactory_dsh/create_ops.py  the operations that graph calls
refactory/refactory_packages/<pkg>/  the packages — torchvision analogue
```

Entry points:

```
python -m refactory_dsh.registry list|show <mode>
python -m refactory_dsh.loop <package>/<mode> --epochs N --batch-size N --optimizer ...
python -m refactory_dsh.create --name <pkg> --brief <text> [--extends <package>/<mode>]
```

## 6. The state model

```python
state_dict() == {
  "version": 1,
  "graph": {"start": ..., "nodes": {id: {...}}, "edges": [{source, target, condition}]},
  "knobs": {name: {"value": v, "spec": {"kind","default","lo","hi","enum","frozen","description","consumed_by"}}},
}
```

- Canonical: node ids and edge order are normalized on serialization, so
  `build() → state_dict → load_state_dict → build()` is the identity.
- `load_state_dict(strict=True)`: an unknown node field, an unknown knob, or a
  value out of bounds is an **error**. This is the "the optimizer wrote
  something it shouldn't have" detector.
- `parameters()` returns `{name: Knob}` — the only thing an optimizer may write.

## 7. The validator

Universal invariants (always checked, whatever the run config):

- V1 `start` names an existing node.
- V2 every node is reachable from `start`.
- V3 at least one terminal (node with no outgoing edges).
- V4 a non-gate node has at most one unconditional out-edge.
- V5 a gate node has at least two conditioned out-edges and carries `max_iterations`.
- V6 no dangling reads: a node's `reads` are produced by an upstream node's `writes`, or are run inputs.
- V7 every knob a node consumes exists in `knobs`; every declared knob is consumed by some node (no dead knobs).
- V8 every `call` / `evaluator` resolves to a real callable.
- V9 every parameter value is in bounds and of the declared kind.
- V10 every cycle passes through a gate node.

Mode-declared invariants (`Mode.invariants()`), for metric integrity — e.g. "the
fact `score()` reads must be written by some node on the graph". Declared as
methods, so unreachable from the optimizer (D4).

`validate(graph, knobs, mode) -> list[Issue]`, each `Issue` with `code`,
`message`, and the node/knob it names. The message is what gets fed back to the
optimizer verbatim.

## 8. The edit algebra

Primitives — the algebra; always available; nothing else can express more:

- `add_node` `{node, position: {after}|{before}|{start}|{terminal}}`
- `remove_node` `{id}`
- `replace_node` `{id, node}`
- `set_node_field` `{id, field, value}`
- `add_edge` `{source, target, condition}`
- `remove_edge` `{source, target, condition?}`
- `set_knob` `{name, value}`
- `add_knob` `{name, spec}`
- `remove_knob` `{name}`

Macros — compositions, each with a declared precondition, each expanded into
primitives before application: `insert_after`, `insert_before`, `wrap`, `split`,
`merge`, `parallelize`, `serialize`, `duplicate`, `reorder`, `bypass`.

`apply(edit, state) -> state` is naive (D7). A macro whose precondition fails is
refused with a reason; the graph is untouched.

Presets (configurable subset): `broad` (all), `conservative` (no topology
macros), `knob_only` (parameters only).

## 9. The optimizers

```python
class Optimizer:
    name: str
    def propose(self, ctx: ProposalContext, n: int) -> list[Proposal]   # one round, no validation
    def propose_valid(self, ctx, n, *, mode, validate, k=3) -> tuple[list[Proposal], list[Rejection]]
```

`propose_valid` is the base-class internal loop (D9, D11): propose → expand →
validate → on failure, regenerate with the machine-readable reasons; bounded by
`k`. Records every `Rejection(attempt, edit, reasons)`.

`Proposal` = `{edit, rationale, prediction, confidence, parent}`.
`ProposalContext` = gradient facts, population/leaderboard, knob surface, edit
catalogue, recent rejection reasons.

- `RandomOptimizer`: samples from the enabled catalogue.
- `AutoresearchOptimizer`: asks a model for edits as JSON, with the rejection
  reasons from the previous attempt in the prompt. Real model calls.

## 10. The loop engine

`Loop` owns a run directory and drives the eight steps. A candidate is
`{id, parent, graph, knobs, edits}`. Elite carryover between epochs.

- `init` — genesis state from `Mode().state_dict()`; assert the validator is clean.
- `forward` — for each unevaluated candidate: materialize, execute, score. Invalid candidates never reach here.
- `loss` — aggregate the batch's scores; `loss = -score`, best/mean/improved.
- `backward` — gradient facts: knob attribution, cost, run failures, rejections, accepted-op mix.
- `optimizer` — `propose_valid(...)`; real model calls for autoresearch.
- `step` — apply accepted edits to their parents; create offspring.
- `scheduler` — epoch counter; converge on epochs or budget, and report which bound bit.
- `report` — leaderboard, lineage, rejection histogram, accepted-op mix, yield.

## 11. The circle_packing package

`refactory/refactory_packages/circle_packing/`, a faithful, in-process port of
the `circle_packing_fast` mode (8 nodes, one gate). Nodes become Python callables
in the package; nothing shells out and the runtime knows the package not at all.

Graph: `task_input → seed_run → select_parent → generate → budget_check
(reloop → eval → accept → select_parent; proceed → exit)`.

Score: the task's own `sum(placed radii)` over the 26-circle instance — the same
number the task's evaluator reports. **Measured band: 0.795–1.275** across the
deterministic generator settings, about 1.2 from jittered restarts. The seed
default (grid 10, descending) sits near the bottom at 0.875.

Knobs: `grid` (enum), `order` (enum), `selector` (enum), `acceptance_mode`
(enum), `budget` (int), `seed` (int, frozen).

The seed pipeline carries a defect worth finding, and the outer loop is scored on
finding it: `generate` is deterministic, so after the seed enters the population
every iteration reproduces the same answer, strict acceptance rejects it, and the
ladder spends its whole budget re-evaluating one candidate. Raising `budget`
changes nothing. The fix is a node (`restart_on_stall`, which injects a jittered
sample once the search stalls), not a parameter — and moving `order` to `asc` or
`grid` to 18 is a separate, complementary win.

## 12. create-mode

`refactory_dsh/create.py` emits a whole package from a brief, with the same
generate→validate→regenerate shape one level up:

`design (agent) → emit (agent) → validate_package (fn gate) → {reloop to emit |
proceed → register (fn) → smoke (fn) → done}`

- `validate_package` is deterministic: imports, a `Mode` subclass exists,
  `build()` returns a graph, every `call:` resolves, every knob is typed, the
  derived surface is non-empty, `score()` exists and is unreachable.
- `register` writes the package into the catalogue grouped under its package.
- `smoke` runs the mode once and refuses to register a mode that cannot score.
- `extends: <package>/<mode>` subclasses an existing mode's class.

**The one mode outside a package, deliberately.** `CreateMode` is a `Mode`
subclass defined in the library, so it is the single exception to "no mode exists
outside a package". It is the tool rather than a search target: nobody optimizes
it, and listing it in the catalogue beside the modes a user might run would be
wrong. The alternative — registering it under a `refactory` package — would mean
loosening
`tests/test_registry.py::test_every_installed_package_mode_validates`, which
asserts that every package mode is scored and exposes knobs. That assertion is
worth more than the tidiness of the rule, so the exception stands and is recorded
here rather than hidden.

## 13. Deletions

The JSON-authoring path is replaced, not supported alongside the new one. Removed
because `refactory_dsh/loop.py`, `registry.py`, `create.py` and `nn/` supersede
them:

| Removed | Replaced by |
|---|---|
| `refactory_dsh/ops/outer_loop.py` | `loop.py` + `optim/` |
| `refactory_dsh/ops/scaffold.py` | `create.py` + `create_ops.py` |
| `refactory_dsh/ops/devgen.py` | the package's own `ops.py` |
| `refactory_dsh/packages.py` | `registry.py` + `nn/package.py` |
| `refactory_dsh/knobs.py` | `nn.Knob`, `nn.validate` (V7), `Mode.describe()` |
| `refactory_dsh/report.py` | `Loop.report()` |
| `refactory_dsh/trace.py` | the run directory: `loss.json`, `gradient.json`, `report.json` |
| `refactory_dsh/steering.py` | `Knob(frozen=True)` in the mode, `LoopConfig.frozen` for the run |
| `gen_presets.py`, `graphs/*.json` | the three entry points above |
| `examples/hello_mode.graph.json`, `examples/sortnet/` | `refactory_packages/circle_packing/` |
| their tests: `test_emit_basic_loop`, `test_opt_knob_lifecycle`, `test_outer_loop_faithful`, `test_outer_loop_visibility`, `test_packages` | `test_nn`, `test_executor`, `test_circle_packing`, `test_optim`, `test_loop`, `test_create` |

Deliberately **not** removed yet: `refactory/vendor/remote-factory`'s
`factory/outer_loop/*`. The new implementation imports nothing from it, so it is
dead to refactory, but it is a vendored upstream package and its removal belongs
to remote-factory rather than to this repo. The `outer-loop-optimizer` branch that
carried the JSON-era work is superseded by this plan and is left unmerged.

GUI and preset wiring is deferred by decision: the DSH presets that used to drive
the JSON graphs point at the removed modules, and rewiring them onto the three
entry points is the next piece of work.

## 14. Phases and acceptance gates

Every gate is a command that was actually run, with its output.

| # | Deliverable | Gate |
|---|---|---|
| 0 | This document | Exists; re-read at every phase boundary |
| 1 | `nn/` state, knobs, graph, mode, validator | `pytest tests/test_nn.py` — round-trip identity, strict load, one test per universal invariant |
| 2 | `nn/edits.py` | `pytest tests/test_nn.py -k edit` — property test: random edit streams are never repaired, and nothing the validator accepts fails an independently written audit |
| 3 | `nn/executor.py` | `pytest tests/test_executor.py` — linear runs, gate bounding, declaration enforcement |
| 4 | `refactory_packages/circle_packing/` | `pytest tests/test_circle_packing.py` — the seed validates with calls resolved, a run scores in the measured band, a knob move and a structural insert each beat the seed |
| 5 | `optim/` | `pytest tests/test_optim.py` — the loop refuses with coded reasons, feeds them back, and regenerates up to k times |
| 6 | `loop.py` | `pytest tests/test_loop.py` — two epochs, a bound, and a report; every evaluated candidate validated first |
| 7 | `create.py` | `pytest tests/test_create.py` — brief → registered package that smoke-scores, and a broken package refused then regenerated |
| 8 | **Real runs** | `python -m refactory_dsh.create` produces a registered, smoke-scoring package with real model calls; `python -m refactory_dsh.loop circle_packing/adaptive --epochs 2 --batch-size 4 --optimizer autoresearch` completes with a leaderboard, a structural win and a refusal histogram |
| 9 | Deletions | `pytest tests/` green; `rg` finds no JSON-authoring surface |

## 15. Drift checklist

Answer all of these before declaring any phase done:

1. Does any library/engine module name a package? (must be no)
2. Can any optimizer reach `score()`, `invariants()`, or the edit-expansion code? (must be no)
3. Does any mutation operator repair or auto-fix? (must be no)
4. Is every claim in the phase's gate backed by a command actually run?
5. Is there an untested path that the acceptance test depends on?
6. Is any state written outside `state_dict` that the optimizer can influence?
7. Did we add a knob nothing consumes? (V7 must catch it)
8. Is a rejected proposal still spending evaluation budget? (must be no)

## 16. What the first real run changed

The first real 2-epoch run completed and produced no improvement: seed 0.875,
best 0.875. It was correct in every structural sense and useless as a search, and
the three defects it exposed are all in the loop rather than in the library:

1. **The starting population had no parameter contrast.** It was sampled from the
   whole edit catalogue, so `set_knob` (3 of 19 operations) was drowned by
   structural edits and epoch one evaluated four candidates that differed only in
   structure. Every one scored the same, so the gradient said nothing about which
   value of anything was worth having, and the optimizer had nothing to exploit.
   *Fix:* the starting population is now a **sweep** — one candidate per movable
   knob, each at a value the default is not — with sampled edits only filling
   whatever is left over.

2. **The gradient said what had been tried and not what had not.** An optimizer
   reading only the values that have run cannot tell a value that failed from one
   nobody tried, so it re-proposed what it already knew. *Fix:*
   `knob_attribution` now carries `untried` for every enumerated knob.

3. **"Structural wins" counted runs that never ran.** A candidate that bypassed
   `seed_run` scored 0 and appeared as a win. *Fix:* a win is a structural
   candidate that ran and scored.

The lesson worth keeping: a validator can only reject what is *invalid*, and a
search also has to be given something *informative*. The first run was proof that
those are different properties, and that the second one has to be designed rather
than assumed.
