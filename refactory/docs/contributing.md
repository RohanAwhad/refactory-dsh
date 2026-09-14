# Contributing

For anyone — person or coding agent — changing `refactory/`. It assumes you have
read [the architecture overview](architecture.md), and it is about the rules that
are not visible from the code alone: which invariants must hold, what will break
if you move something, and how to check your work.

## The commands

Run from the repository root (`/Users/akash/cursor/refactory-fresh-test`).

```bash
.venv/bin/python -m pytest refactory/tests/ -q                    # the suite
.venv/bin/python -m pytest refactory/tests/ -q --run-integration  # ... plus the real-DSH runs
.venv/bin/python -m refactory_dsh.tools.gen_api_reference --check # docs match the code
```

The DSH side, when you touch `packages/`:

```bash
pnpm run test:docs              # the repo's documentation gates
./node_modules/.bin/tsx scripts/run-oxlint.ts .   # lint
pnpm run typecheck
```

`.work/todo.json` tracks the work, and `tests/test_work_complete.py` **fails while
any item is open**. Each item records its issue number, the command that verifies
it, and the observed result; that test also checks the command names files that
exist, because two items once named test files that never existed and nobody
noticed.

## The invariants

These are the rules the system is built on. Breaking one is not a style problem;
each exists because its absence caused a specific failure.

### The state is the authority after genesis

`build()` runs **once**, to produce the starting state. Every later architecture
comes from the state dict, because that is what the optimizer edits.

- Never read a knob's value in `build()`. A knob is consumed by a node at run time.
- Never make `build()` depend on I/O, the clock, or randomness — it must be pure,
  or `build() -> state -> build()` stops being an identity.
- `score()` and `invariants()` are **methods**, never state. That is what makes
  them unreachable by a proposal, and it is the whole protection. Do not put them
  in the state dict, and do not make a candidate able to influence them.

### A candidate is judged before it costs anything

`Optimizer.attempt` applies and validates the edit *before* the loop evaluates
anything. A proposal that would produce an invalid graph costs no budget.

- A new rule goes in `nn/validate.py` and in the `RULES` table. `tests/test_integrity.py`
  asserts the table and the emitted codes are the same set **in both directions**,
  so a rule that is emitted but unpublished fails the suite.
- The validator **refuses; it never repairs.** A repair hides the edit that caused
  the problem and makes the next failure unattributable.
- Macros are literal compositions of primitives. If you add one, express it as an
  `expand` into primitives rather than as new application logic, or it can produce
  a state no sequence of primitives could.

### Every knob is reachable, and every file is produced

- `V7` refuses a declared knob no node consumes **and** a node consuming an
  undeclared knob. A knob that looks like a search axis and is not is worse than
  no knob: the search spends batches learning nothing.
- `V6` refuses a node reading a file no node on a start-to-terminal path writes.
- `V11` refuses a declared port no node writes.
- `Loop.unreachable_knobs()` reports knobs that *this optimizer* cannot move even
  though they are declared and consumed. It warns rather than refuses, because a
  knob-only optimizer on a structural mode is a legitimate pairing.

### The journal is the only durable state

`journal.jsonl` is append-only and replayed. Do not add a second snapshot of the
population and keep it consistent by hand — that is what the system this replaced
did, and it produced real bugs.

- Anything a later step needs must be **journaled**, not derived. `generation`,
  `refusal_pressure`, and `bound` are all journaled for this reason: a resumed run
  that guessed them would take a different path.
- An invalid candidate is recorded as a `Rejection` with its reasons, never
  dropped in silence. `E-duplicate` exists because a silently dropped duplicate
  loses a batch slot and makes the run report a batch it did not try.

### The two runtimes must agree

The walker is a debug runtime; DSH ships. `tests/test_equivalence.py` runs the
same mode both ways and compares the score.

- If you change the executor, change nothing that makes a node behave differently
  on DSH. The lowering in `lowering.py` is the seam; `validate_ir` is refactory's
  check before DSH sees the graph.
- The IR has **no field for the facts a node produces**, so `write_declarations`
  writes them beside the graph and the runner reads them from the run directory.
  That is a known wart, not an oversight to "clean up" silently.

## The failure modes worth remembering

Each of these happened, and the shape of the code is the response.

- **A severed gradient.** The optimizer was not shown what the run learned.
  `backward()` produces the gradient and it is part of the API; `knob_attribution`
  includes `untried`, because *a value that failed* and *a value nobody tried* are
  different facts and a prompt that conflates them keeps re-proposing what it
  already knows.
- **Mutating operators that destroyed graphs.** Some rewrites produced structures
  that could not run. Hence: macros expand to primitives, the validator refuses
  rather than repairs, and validation happens before budget is spent.
- **A surface that lied.** Structural wins were counted from failed runs, and
  duplicates vanished. `structural_wins` requires `exit_ok`; duplicates are refused
  at the optimizer layer and `attempt == 0` is asserted in the tests.
- **Steps that deleted their own run.** When the loop became a mode, each node
  rehydrates from the journal — and `resume=False` means "start over", so the
  first version deleted the journal between two nodes of the loop's own run. Any
  node that reads the run must resume.

## Where to put a change

| you want to | change |
|---|---|
| a new mode | a package under `refactory_packages/` |
| a new validator rule | `nn/validate.py` `RULES` + the check, then the anti-drift test |
| a new edit operation | `nn/edits.py`: a primitive, or a macro that expands to primitives |
| a new optimizer | subclass `optim.Optimizer`, implement `propose`, register it |
| a new outer loop | subclass `outerloop.OuterLoop`, override the steps |
| a new strategy | `strategies.py`; a port of an SRF mode |
| a new artifact | declare a `Port` on the mode **and** write it from a node |
| a new way to read a run | `observe.py` (readers) or `history.py` (finished runs) |
| the loop's own steps | `loop.py` — and expect `loop_mode/loop` to need matching changes |

## Documentation

- The [API reference](api/reference.md) is **generated**; never edit it. Run
  `python -m refactory_dsh.tools.gen_api_reference`. `tests/test_docs_api.py`
  fails when it is stale, when a documented symbol has no prose, or when a
  document names a module that does not import.
- The architecture overview's rule table is checked against `nn.RULES`, so adding
  a rule means updating the overview too.
- Prose belongs in the overview and the tutorials. A comment belongs where the
  reason for the code is local to it.

## What not to do

- **Do not add a second runtime.** The walker is for debugging; DSH ships. A third
  executor is a third set of behaviours to keep in agreement.
- **Do not silently accept an invalid candidate** to keep a batch full.
- **Do not default a missing referent.** `Package` resolution refuses a bare mode
  name rather than guessing a package, and the registry refuses a name that exists
  in both roots rather than picking one.
- **Do not repair a state.** Refuse it and report the reason.
