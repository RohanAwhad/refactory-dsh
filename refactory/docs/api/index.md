# API, by task

The [generated reference](reference.md) is the complete index: every public
module, its real signatures, and the first line of each docstring. This page is
the other direction — the handful of names a given task needs, with a snippet that
runs.

Every snippet below was executed against this checkout.

---

## Write a mode

A mode is a class: parameters from `declare()`, architecture from `build()`, a
number from `score()`.

```python
from refactory_dsh import nn, registry

class Counter(nn.Mode):
    name = "counter"
    description = "count to a target"

    @classmethod
    def declare(cls):
        return {"target": nn.Knob(3, "int", lo=1, hi=9, description="where to stop")}

    def build(self):
        graph = nn.Graph()
        graph.add(nn.Node("count", "op", call="mypkg.ops:count",
                          knobs=("target",), writes=("count.json",), produces=("value",)))
        return graph

    def score(self, run):
        return float(run.facts["value"])
```

- **`nn.Mode`** — subclass this. `build()` is pure: no I/O, no randomness.
- **`nn.Knob`** — a parameter. `frozen=True` removes it from the search surface
  without removing it from the state.
- **`nn.Graph`** / **`nn.Node`** / **`nn.Edge`** — the architecture.
- **`nn.Package`** — the manifest that makes the mode discoverable
  (`Package(name, modes=(Counter,), ops=("mypkg.ops",))`).
- **`registry.resolve(project, "mypkg/counter")`** — `(Package, Mode class)`.
- **`repo_change`** — the steps a mode whose agent changes code attaches: a worktree
  of its target, `emit.txt` applied as one commit per attempt, and its check in the
  worktree's own environment
  ([how](../tutorials/01-write-a-mode.md#if-your-mode-changes-a-repository)).

Run it, then validate the state it produced:

```python
mode = registry.resolve(Path("."), "circle_packing/adaptive")[1]()
print(nn.validate_state(mode.state_dict(), invariants=mode.invariants()))
# []
```

## Understand why a candidate was refused

```python
for issue in nn.validate_state(state):
    print(issue.code, issue.message)
# V6 [eval]: node 'eval' reads 'candidate.json', which no node on a path from
#            'task_input' to a terminal writes
```

- **`nn.validate_state(state, invariants=…, resolve_calls=…)`** — every reason a
  state is not a runnable, scoreable mode. This is what the optimizer's proposal
  loop calls before spending budget.
- **`nn.RULES`** / **`nn.rules_text()`** — the published codes, and the same table
  as text for the optimizer's prompt.
- **`nn.Issue`** — `code`, `message`, optional `node` / `knob`.
- **`nn.Invariant`** / **`nn.fact_produced(*names)`** — a mode's own rule; use
  `fact_produced` to assert the graph produces the facts `score()` reads.

## Edit a state

```python
from refactory_dsh.nn import edits

new_state = edits.apply({"op": "set_knob", "name": "grid", "value": 18}, state)

# `precheck` is cheap, not a dry run: it checks the edit's shape and a macro's
# declared precondition, and returns None for a primitive it cannot judge.
print(edits.precheck({"op": "set_knob", "name": "nope", "value": 1}, state))   # None
print(edits.precheck({"op": "nonsense"}, state))
# "unknown edit 'nonsense'; known: ['add_edge', 'add_knob', ...]"

# `apply` is the authority, and says why:
try:
    edits.apply({"op": "set_knob", "name": "nope", "value": 1}, state)
except edits.EditError as exc:
    print(exc.code, exc)
# E-knob no knob 'nope'; declared knobs: acceptance_mode, budget, grid, order, seed, selector
```

- **`edits.apply(edit, state)`** — apply one edit, leaving the input untouched.
  Raises `EditError` (with a `code`) when it cannot apply. This is the authority.
- **`edits.precheck(edit, state)`** — a cheap refusal reason, or `None`. Only
  macros are checked in full; the proposal loop pairs it with `apply` in a
  `try/except`, which is what `optim.Optimizer.attempt` does.
- **`edits.names()`** / **`edits.is_macro(name)`** — the 17 operations, and which
  are macros.
- **`edits.enabled(preset)`** / **`edits.catalogue(preset)`** — what a run may use,
  and the same thing described for a prompt.
- **`edits.random_edit(state, preset=…, insertable=…, rng=…)`** — one random legal
  edit, or `None`.

## Run an inner run

```python
result = mode.evaluate(workdir, model=client, memory=store, max_steps=2000)
print(result.score, result.exit_ok, result.facts)
```

- **`nn.execute(graph, workdir, knobs=…, model=…, facts=…, memory=…, max_steps=…)`**
  — run a graph once, in-process. The debug runtime.
- **`nn.RunContext`** — what a node's callable sees: `workdir`, `knob(name)`,
  `read_json` / `write_json`, `set_fact`, `model`.
- **`nn.RunResult`** — `facts`, `score`, `exit_ok`, `error`, `iterations`,
  `model_calls`.

## Optimize a mode

```python
from refactory_dsh.loop import Loop, LoopConfig

report = Loop(
    "circle_packing/adaptive",
    LoopConfig(epochs=2, batch_size=4, optimizer="random", seed=5, runs_root=root),
    project=Path("."),
).run()
print(report["seed_score"], "->", report["best_score"])
```

- **`loop.Loop`** — the eight steps. Every step a strategy would change is a
  method: `select_parents`, `propose`, `batch_size`, `accepts`, `update`,
  `should_stop`, `on_epoch_end`.
- **`loop.LoopConfig`** — `epochs`, `batch_size`, `optimizer`, `elite`,
  `attempts`, `max_evaluations`, `frozen`, `resume`, `evaluator`, `runs_root`.
- **`optim.create(name, **kwargs)`** — build an optimizer by name; fails loud on
  an unknown one. Ships: `random`, `autoresearch`.
- **`optim.Optimizer`** — subclass and implement `propose(ctx, n, parents)`. One
  method; the validating loop around it is the harness's.
- **`optim.ProposalContext`** — the gradient: knobs with attribution (including
  `untried`), the leaderboard, traces, the edit catalogue, prior runs, guidance.
- **`outerloop.OuterLoop`** — subclass to own the whole search: `run()` is
  `init -> Run -> strategy -> report`.
- **`strategies.names()`** — the 13 SRF ports plus `default`.

## Read what a run produced

```python
from refactory_dsh import history

archive = history.read_run(root)
print(archive.best_score, len(archive.candidates), archive.bound)
```

- **`history.read_run(path)`** — a finished run as objects. Prefers
  `report.json`, falls back to the per-candidate directories.
- **`history.find_runs(root, limit=…)`** — the sibling runs, newest first; this is
  the prior an optimizer learns from.
- **`history.JOURNAL`** / **`history.RUN_FILE`** — the append-only log, and the
  layout version marker.
- **`observe.progress(root, after=…)`** — the progress tail, so a reader can watch
  a run that has not finished.
- **`observe.diff_states(before, after)`** / **`observe.candidate_diff(root, id)`**
  — what a candidate changed against its parent.
- **`observe.artifact_manifest(root)`** — what to render, and as what.

## Drive a run from outside Python

```bash
python -m refactory_dsh.console modes
python -m refactory_dsh.console start circle_packing/adaptive --run-id demo \
  --epochs 2 --batch-size 4 --optimizer random --seed 5
python -m refactory_dsh.console status --run demo --after 0
python -m refactory_dsh.console leaderboard --run demo
```

- **`console.catalogue()`** — the modes a checkout can start.
- **`console.start(mode, …)`** — begin a run in its own process; returns the id.
- **`console.status(root, after=…)`** / **`stop(root)`**.
- **`console.artifacts`**, **`console.leaderboard`**, **`console.candidate`**.
- Every verb is available over the CLI as JSON — that is the contract the DSH
  `refactory` tool shells out to.

## Put a mode on the runtime that ships

```python
from refactory_dsh import lowering

path, workflow = lowering.write_mode_graph(state, graphs_dir, name="circle_packing")
```

- **`lowering.write_mode_graph(state, out, name=…)`** — the mode's state as
  refactory's IR, `<name>.graph.json`, for the science bundle.
- **`lowering.write_declarations(state, workdir)`** — the facts each node
  produces, which the IR has no field for.
- **`dsh_runtime.evaluate_on_dsh(mode, state, workdir, timeout_s=…)`** — one
  candidate evaluated by DSH instead of the walker.

## Manage packages

```bash
python -m refactory_dsh.registry list
python -m refactory_dsh.registry show circle_packing/adaptive
python -m refactory_dsh.registry verify
python -m refactory_dsh.sandbox --smoke annealing_pack/anneal
```

- **`registry.resolve`** / **`registry.discover`** / **`registry.catalogue`**.
- **`registry.workspace_root(project)`** — the writable package root
  (`.factory/packages`), beside the read-only install root.
- **`registry.provenance(name)`** — where a package came from, and its trust tier.
- **`sandbox.check`** / **`sandbox.verify_sandboxed`** / **`sandbox.smoke`** —
  a generated package is AST-checked and smoke-run; it is never imported.
- **`sandbox.promote(project, name)`** — mark a package trusted, explicitly.

## Author a mode with a model

- **`create.create(CreateConfig(name=…, brief=…, workdir=…), client=…)`** — a brief
  to a registered, smoke-scoring package.
- **`edit.edit(EditConfig(mode=…, intent=…, allow_regression=…), client=…)`** — a
  revision of an existing mode, recording `revision_of`.
- **`design.design(DesignConfig(…), client=…)`** — a brief to a checked artifact.
- Run them from the command line: `python -m refactory_dsh.create|edit|design`.
