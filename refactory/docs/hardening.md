# Hardening: what the audit found, and what holds it now

Every issue closed before this pass was re-read against the code and probed for its
own acceptance criteria — by running them, not by grepping for identifiers. Five
were not finished, four of those while being marked done. This records each one, the
evidence, the fix, and the test that keeps it fixed, so the same claim cannot be
made twice.

Two probes also came back **clean**, and they are recorded because an audit that
only finds fault is not an audit.

---

## The method

For each issue: read its acceptance criteria as written, then write a probe that
would fail if the criterion were unmet, and run it. Grepping for a name proves a
name exists. Running the behaviour is what found these.

```bash
cd refactory && ../.venv/bin/python -m pytest tests/ -q
```

---

## 1. `check_package` executed generated code in the driver (#23)

**What the issue said.** create-mode writes Python and then something decides
whether it is usable, "which means loading it, which means executing model-written
code". The table named `create_ops.check_package: imports the generated package to
validate it — none — in-process, full access`.

**What the code did.** The docstring said *"A generated package is **never
imported here**"*. The body's tail was:

```python
package = load_fresh(name)              # importlib.import_module
for ops_module in package.ops:
    importlib.import_module(ops_module)  # model code, again
for mode_class in package.modes:
    problems.extend(_check_mode(mode_class))  # constructs the mode
```

Only the *structural* half ran in a subprocess. The semantic half — is the name
right, do the ops import, is the mode constructible, does it satisfy every
validator rule — ran in the driver with full access.

**The fix.**

- `sandbox.verify_sandboxed` now performs the whole check in its subprocess,
  including the package name, each declared ops module, and each mode's
  construction, score, knobs and validator rules.
- `register` and `smoke` needed the manifest only to *name* what was registered and
  which mode to smoke, and were loading the package to get it. They use the new
  `sandbox.describe_package`, which reads it out of process.
- `load_fresh` and `_check_mode` are deleted. No function in the driver loads a
  generated package any more.

**Held by.** `tests/test_create.py`: a test patches `importlib.import_module` to
record any import of the package's module prefix, runs `check_package` to a clean
result, and asserts the recording is empty. A second does the same for
`describe_package`.

**Known limit, stated rather than implied.** `registry.catalogue` and `registry.load`
still import packages to describe them, so *listing* a generated package still runs
it. That is a different boundary from create-mode's validation, and it is not fixed
here.

---

## 2. Run retention was declared and never applied (#24)

**What the issue said.** `memory(namespace, kind, retention)` where `retention` is
`run`, `project` or `global` — "the difference between a memory that dies with the
run and one that accumulates".

**What the code did.** `MemoryStore.forget_run_scoped` existed, with a docstring
reading *"Called when the run ends"*, and **nothing called it**. So a run-scoped
namespace accumulated exactly like a project one.

The cost is sharper than untidy files. `_evaluate` re-evaluates candidates when a
run resumes, and a run-scoped memory left on disk is read the second time — so a
resumed run would not be the run it resumed.

**A second gap, found while fixing it.** Memory did not cross to DSH **at all**:
`lowering` dropped it and `run.py` built its `RunContext` without a store. A mode
declaring a cross-run prior therefore worked in the walker and silently kept nothing
on the runtime that ships. Nothing reported the difference, because the running
example declares no memory.

**The fix.**

- `Loop._evaluate` forgets in a `finally`, so a crash or a stop applies the policy
  too, not only a clean finish.
- `lowering.write_declarations` carries the mode's memory declarations beside the
  node facts; `lowering.read_memory_declarations` reads them back; and `run.py`
  builds the store from them, with the same roots the walker uses.

**Held by.** `tests/test_declarations.py`: a run-scoped namespace is gone after
`forget_run_scoped`; project and global namespaces survive it; the loop's
`_evaluate` contains the call **and** a `finally`; the declarations survive the
lowering; and the node runner builds a store.

---

## 3. The strategies recorded their decision instead of applying it (#20)

**What the issue said.** "The deliverable includes all the SRF candidates
implemented on the new API. The abstraction is proven by the ports, not by the tests
for the abstraction."

**What the code did.** Five strategies took their distinguishing branch, counted it,
logged it, and called the ordinary path:

```python
def epoch_step(self, run=None, *, branch="mutate"):
    if branch == "merge": self.merges += 1
    else:                 self.mutations += 1
    run.log(f"branch={branch}")
    return super().epoch_step(run)     # the merge is not a merge
```

`reflective_sampler` stored `last_reflection` and read it nowhere.
`adaptive_meta` computed `meta` and logged it. `research_then_optimize` stored
`research_note` — `grep` found only the two lines that set and logged it.
`staged_curriculum` logged the stage and ran the ordinary step.

Measured, not argued: running all fourteen on identical input, **six produced
byte-identical leaderboards to `default`**.

The tests passed because they asserted the *counter* —
`test_reflective_takes_a_merge_branch_and_a_mutate_branch` checks
`loop.merges >= 1` — and a branch that changes nothing still increments a counter.

**The fix.** `ProposalContext.strategy` is a new channel carrying what the outer
loop decided for this iteration, so a decision reaches the proposer instead of only
the log. Each strategy now acts:

| strategy | what it now does |
|---|---|
| `adaptive_meta` | widens the batch by 2 on a stall — a real budget decision |
| `reflective` | builds a genuine merge: one parent's knobs taken from another, and it falls back to a mutation when the parents differ structurally rather than in their settings, because a no-op merge is refused as `E-noop` and loses the slot |
| `reflective_sampler` | drops a proposal that repeats an edit the run already refused |
| `research_then_optimize` | publishes its conclusion into the context |
| `staged_curriculum` | publishes which stage is running |

**Held by.** Six tests that assert the **effect**: the batch is wider, the merged
proposal takes the other parent's setting, a refused edit is not re-proposed, and
the decision is present in the context a proposer receives.

**Honest limit.** With `optimizer=random` the model-facing decisions (a research
note, a curriculum stage) reach a proposer that cannot read them, so four strategies
still match `default` on the tiny example. The channel is real; showing the
difference needs a model proposer.

---

## 4. A revision recorded a name, not a state (#13)

**What the issue said.** Provenance of *"what it was edited from, **at which
state**"*.

**What the code did.** `_revision_source` correctly handed the model the source
files. The record then said `revision_of: "circle_packing/adaptive"` and nothing
else. A mode is not a fixed thing — the outer loop edits it — so that names a class
which may have changed since, and the revision cannot be reproduced or diffed
against what it actually started from.

**The fix.** `edit()` resolves the source mode once, records its whole
`revision_of_state` plus a `revision_of_digest` (the same `candidate_id` the loop
uses, so a revision and a candidate that are the same state agree), and returns the
digest.

**Held by.** A test that runs a revision, reads the origin file, asserts the
recorded state is a full state dict, and that the digest matches both the result and
`candidate_id(state)`.

---

## 5. The lowering dropped `callable_name` (#21)

**What the issue said.** *"`FnNode.callable_name` already exists; do not invent a
command string."*

**What the code did.** Emitted `command: "python -m refactory_dsh.run <mod>:<fn>"`
and left `callable_name` unset. `callable_name` and `post_checks` appeared zero
times in `lowering.py`.

**Where the issue was right, and where it was wrong.** `callable_name` *is* a real
`FnNode` field — `factory/workflow/primitives.py`, round-tripped by
`factory/workflow/parity.py` — so the lowering was dropping something the language
has. But **DSH's spine does not model it**: `WorkflowNode` has `command`,
`evaluator_type` and `evaluator_command`, and `callable_name` appears nowhere in the
spine's source, which executes the command. A graph carrying only `callable_name`
would be a graph whose nodes never run.

**The fix.** The lowering emits **both**: `callable_name`, so the IR stays faithful
to the language and refactory's own tooling can read it, and `command`, because that
is what the runtime we ship executes.

**Held by.** Two tests: every op node carries both fields, and every declared
`callable_name` resolves.

**Not adopted, stated rather than skipped.** `AgentNode.post_checks` and refactory's
`ForkNode`/`JoinNode` are still unused. `post_checks` would duplicate a check the
executor already performs; adopting fork/join is a real design change, not a
fidelity fix, and is not done.

---

## 6. The documented commands, run as written (#31)

Ship readiness is not a feeling about the code; it is the claim that a person who has
never seen this repository can install it, follow the docs, and get the promised
result. That claim is testable, so it is a test: `tests/test_ship_ready.py` reads the
shipped documents, extracts every command line from their `bash` blocks, and runs it.

The gate checks four things, and each one found something:

| check | what it would catch |
|---|---|
| every `python -m refactory_dsh.X` a doc names imports | a tutorial pointing at a deleted module |
| every module's `--help` accepts the flags the doc shows | a flag renamed in code but not in the prose |
| the README links the architecture and the tutorials | a stranger with nowhere to go next |
| the docs the README promises exist on disk | a link to a file that was moved |

**The bug it found.** `python -m refactory_dsh.sandbox --help` printed its usage line
to **stderr** and exited **2**. Two things were wrong with that, and the second is the
worse one: a reader who asks for help is told they made an error, and a program that
captures stdout — which is how the doc gate itself reads a help page — saw nothing at
all. So the flag table was empty and the gate could not check the sandbox CLI, which
is where an unknown flag had to be caught by hand.

**The fix.** `sandbox.main` gained `usage()`, which is the one string both paths
print, and an explicit branch: `-h`/`--help` prints it to **stdout** and returns
**0**. The two cases that are genuinely misuse keep their old behaviour, because they
are a different decision and were already deliberate:

| invocation | stream | status | why |
|---|---|---|---|
| `-h`, `--help` | stdout | 0 | a question, answered |
| no arguments | stderr | 2 | nothing was asked for; `test_the_sandbox_cli_needs_an_instruction` pins this |
| an unknown flag | stderr | 2 | not a command |

**Held by.** A test that runs `--help` against `sandbox`, `loop` and `console` and
requires both a zero status and non-empty stdout. It is not vacuous: the pre-fix
behaviour (status 2, empty stdout) fails exactly that assertion, which was run both
ways when the fix was made.

**The second finding was in the README, not the code.** It described the layout but
never linked `docs/architecture.md` or `docs/tutorials/`, so the entry point a
stranger actually opens did not lead anywhere. Both READMEs now carry a Documentation
table — architecture, tutorials, API, tool packing, hardening, contributing — and the
pair stays line-aligned (`pnpm run verify-translation-pairing`).

**Also regenerated.** `docs/api/reference.md` is generated from the code and had
drifted from it; the new `usage()` export made that visible. `test_docs_api.py` fails
when the two disagree, so the reference is checked rather than trusted.

---

## Two probes that came back clean

Recorded because the point of an audit is accuracy in both directions.

- **`#16`, str knobs.** Probed by sampling a str knob 300 times through
  `edits.random_edit`, and by reading `Loop._other_value`. Both route through one
  `Knob.sample`, and the docstring says why: *"One sampler for the whole system, so
  the starting sweep and the random optimizer agree about what is reachable."* A str
  knob with declared values samples among them; one without gets `STR_MUTATIONS`.
  Implemented.
- **`#25`, path-aware metric integrity.** `nn.fact_produced`'s docstring and body
  both require the producer to lie on a `start → terminal` path. Implemented. (A
  first probe of mine was inconclusive — the off-path node I built was caught by
  `V2` for being unreachable, which is a different rule — and the probe was the
  thing that was wrong.)

---

## What this does not cover

- Listing a generated package still imports it (§1).
- The reflective/curriculum differences need a model proposer to show (§3).
- `post_checks` and fork/join are unused (§5).
- The GUI is not audited here at all; see `GUI-HANDOFF.md` at the repository root.
