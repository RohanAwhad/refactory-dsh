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
fidelity fix, and is not done. Design's `emit` still needs none: an `emit.txt`
`materialise` cannot apply is sent back to `emit` by the check (#361).

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
stranger actually opens did not lead anywhere. The README now carries a Documentation
table — architecture, tutorials, API, tool packing, hardening, contributing.

**Also regenerated.** `docs/api/reference.md` is generated from the code and had
drifted from it; the new `usage()` export made that visible. `test_docs_api.py` fails
when the two disagree, so the reference is checked rather than trusted.

**And the tracker's own commit field was not checked.** The gate asked a done item for
a non-empty `commit`, which `"(pending)"` satisfies, so six closed items carried one
and the field proved nothing. A recorded commit exists so a reader can go and look at
the change; `test_a_recorded_commit_is_one_git_actually_knows` now runs `git cat-file
-e` on every one, and all 23 done items resolve. Verified both ways: planting a
made-up hash fails the gate, and removing it passes.

**The install itself was run, not inferred.** `./install.sh` is the one command a
stranger executes, and everything above only proves the commands *after* it work. It
was run against this checkout: `pnpm install --frozen-lockfile`, `pnpm run build`,
the venv, the editable installs, and the registry listing all
completed with status 0. The build was then checked for the damage a build can do —
`git status` is clean, the Python suite is unchanged, the DSH-side specs pass, and the
doc gates pass — because "it exited 0" and "it left the tree working" are different
claims and only the second one matters.

**What this still does not prove.** It is not a clean clone: the submodules were
already initialised and the venv already existed, so the script's fresh-clone branches
were not exercised. A machine with neither was not available here.

---

## 7. The sandbox made an installed toolchain unreachable (#30)

Found by installing the tool and running the path, which is the only way it could be
found. Every Lean test in the repository **skipped** on this machine, with a reason
naming the missing binary — an honest skip, and one that hid a bug, because the
skipped tests were the only thing that would have executed the checker.

**What happened.** Lean 4.33.1 was installed. `registry smoke lean_pack/prover`
returned **score 1.0**: a real proof, accepted by the real checker, in one attempt.
`sandbox.smoke lean_pack/prover` returned **score 0.0** with `exit_code 1` and:

```text
error: no default toolchain configured. run `elan default stable` to install
```

Same mode, same machine, same theorem, two answers.

**The cause.** `run_sandboxed` sets `HOME` to the run's working directory. That is
correct containment — code we did not write should not read a user's dotfiles — but
elan finds its toolchains through `HOME`, so the sandbox pointed the checker at
`<workdir>/.elan`, which is empty. Proven by isolating the variable: `lake env lean`
on a trivial file succeeds with the real `HOME` and fails with `HOME=<workdir>`, and
in the failing case it *creates* `<workdir>/.elan/settings.toml` as a side effect.

This is not a Lean bug. rustup, pyenv, nvm, gvm and sdkman all locate themselves
through `HOME`, so containment alone breaks every per-user toolchain manager, and the
failure is a wrong statement about the machine: "no default toolchain configured" on a
machine where the toolchain is installed and working.

**The fix, in the declaration.** The manifest already existed to answer "can this
package run here"; it now also carries what the tool needs *at run time*. `Tool`
gains `env`, a tuple of `(name, value)` pairs, and `toolchain.tool_env(package)`
collects them from the package's **present** tools. `sandbox.smoke` forwards them into
the run. `lean_pack` declares `ELAN_HOME`.

Three properties make it hold rather than paper over:

| property | why it is the right shape |
|---|---|
| the value is resolved where the declaration is read | package code cannot compute it — by the time an op runs, `HOME` is the working directory |
| absent tools contribute nothing | forwarding for a tool that is not installed adds environment a run cannot use |
| applied *after* containment, and still secret-filtered | `HOME` stays the working directory, and a declaration cannot smuggle a credential in |

**Held by.** Four tests. One drives the mechanism on a synthetic package, so it holds
on a machine without Lean. One asserts the same probe sees **no** `ELAN_HOME` without
forwarding — the half that makes the first mean something. One asserts the secret
filter still applies to a declaration. One runs `sandbox.smoke` on the real mode with
the real checker. Every one of them was checked against the unfixed code: disabling
forwarding reproduces `score 0.0` and the elan message, so none passes vacuously.

**A test that had to change, and why that is the finding rather than a nuisance.**
`test_a_declared_tool_that_is_missing_names_the_fix_when_required` asserted that
`preflight("lean_pack")` raises `ToolMissing`. It passed on a bare machine and failed
the moment the declared tool was actually installed — it had quietly asserted that
Lean is *not* here. It now asserts the conditional truth, and the mechanism is tested
against a synthetic absent tool. `test_the_lean_declaration_names_the_toolchain_home`
is machine-dependent by design and carries `@needs_lean`.

**Verified, finally.** The Lean path had never run. It has now: `verified: true`,
`attempts: 1`, strategy `omega`, `exit_code 0`, and the axiom audit reports
`[propext, Quot.sound]` — no `sorryAx`, so the `#print axioms` check that separates a
proof from a `sorry` is exercising real output rather than a stub's. The suite runs
**562 passed, 7 skipped with Lean** and **558 passed, 11 skipped without it**; both
were run, because CI has no Lean and this checkout now does.

**Still unverified.** mathlib is not installed, so the mathlib problem and its
`[[require]] path` remain untested. The core-library problem is the one that ran.

---

## 8. Two defects mathlib found, and the optimizer SRF ships that we did not run

Closing #30 left two things unverified and one thing unported. All three are the same
kind of gap: a claim that was true of the tests and not yet true of the code.

### `mathlib` was installed, and the problem it exists for did not run

The package declared mathlib, the tool probe reported it, and the mathlib problem had
**never been executed** — the tests skipped with a reason naming the missing path. So
mathlib was installed: a `mathlib4` checkout, `lake exe cache get`, 7.4 GB. The
problem then scored **0.0**, and the reason had nothing to do with mathlib.

**Defect one: the statement did not parse.**

```lean
theorem sum_odd_eq_sq (n : Nat) : ∑ i in Finset.range n, (2 * i + 1) = n ^ 2 := by
```

```text
error: unexpected token 'in'; expected ','
```

`∑ i in s, f i` is the form most introductions show. This Mathlib does not accept it:
Lean reads `∑ i` as an ordinary binder and then wants a comma. The form that compiles
is `∑ i ∈ s, f i`, alongside the explicit `s.sum (fun i => ...)`, which was checked
side by side in one file to be certain which of them was wrong.

Worth being exact about why nothing caught it. Every test of this mode used a **stub
checker**, and a stub accepts whatever it is handed. A syntax error in the statement
is invisible to a test that never parses the statement.

**Defect two: the timeout abandoned the checker, and a timeout reads like a refutation.**

`timeout_s` defaulted to 60s. The first invocation against mathlib pays for importing
the library, which took longer than 60s cold; the same proof then took 22s warm. So
the run reported `verified: false` for a theorem that is provable, on a machine where
it was about to be proved. The default is now 180s, with the reason recorded at the
knob: a fact that costs a run its score must clear the cost of starting.

**A third thing, found while reading the record it produced.** The run's
`lean_version` fact came from probing `lean` on the PATH, and the project's checker is
not that compiler: a project requiring mathlib takes a `lean-toolchain` from the
dependency, so `lake env lean` ran **4.34.0-rc2** while the PATH had **4.33.1**. The
record named a compiler that decided nothing. `scaffold_project` now asks the checker
in the project directory once, right after the project exists, and `result.json`
carries `checker_version` — the compiler that actually decided.

**Held by.** Three tests: one pins the `∈` form and the `BigOperators` preamble with
neither Lean nor mathlib installed, so the syntax cannot drift back; one pins the
timeout default against the cost of a large import; one runs the real problem and
requires `statement_ok == 1`, `verified == true`, no `sorryAx`, and a
`checker_version` in the durable record. The first two are ungated — they are the
regression guards. The third is gated on `MATHLIB_PATH` and **ran**, not skipped.

### SRF's own optimizer was vendored and never used

`srf/lab/map_elites.py` is not one of SRF's thirteen modes; it is an optimizer SRF
keeps beside them — a multi-emitter MAP-Elites over mode and knob space, whose
distinguishing property is that it keeps **one champion per cell of a feature grid**
rather than one best candidate overall. Nothing in this repository referenced it. The
thirteen modes were ported and this was not, so the count of "SRF ports" was complete
against a list that omitted it.

Ported on the loop's own vocabulary: features are graph size, whether the candidate
was reached by moving structure or only settings, and the step budget it spends; the
archive refuses a weaker candidate in an occupied niche and accepts one in an empty
niche; selection draws parents from across occupied niches.

**It was put in the wrong bucket, and the count papered over it.** It was added as a
fifteenth `OuterLoop`, and `SRF_LAB_PORT_COUNT = 1` existed to make the totals add up
around that placement. MAP-Elites is a search policy, not a control flow.

### The two buckets were not what the code said

`outerloop.py` says *"most research ideas live here [in the proposer]"*. The registry
disagreed: every SRF-derived name was an `OuterLoop`, and the `Optimizer` registry
held two hand-written entries and no port at all.

The cause was structural, and finding it is what made the fix possible. `Loop`
handed a proposer `select_parents(self.config.elite)` — the top few — and
`Candidate.describe()` carries a score and the edits that produced it but **not the
state**. A proposer therefore saw only the elite's states. Island selection, a niche
grid and a tournament over an archive all need candidates the loop was not handing
over, so those five could not have been proposers however they were filed.

**So the contract changed, not the filing.** The loop hands over the evaluated
population and the optimizer selects; `ProposalContext.elite` carries the count an
optimizer with no parent policy should take, so widening the pool does not silently
change what the existing two propose. Four of SRF's thirteen modes — `scs`, `evox`,
`openevolve`, `karpathy` — moved to `optim/policies.py` as proposers, and
`map_elites` joined them there.

**The split is asserted** rather than described: `13 - len(SRF_OPTIMIZER_PORTS)` is
the loop count, every optimizer port must be in the optimizer registry, no name may
be in both buckets, and every registered name must be one or the other.

**Two defects came out of doing it**, both found by the new tests rather than by
reasoning:

- `MapElitesSelector.features` read `state["nodes"]`, and the nodes are at
  `state["graph"]["nodes"]`. `.get` returns `None` rather than raising, so every
  candidate landed in one cell — a niche grid that is a single bucket and looks like
  it is working. Caught by a test that aims at a known-empty niche.
- `island_of` derived the island from `hash(candidate.id)`, and Python salts string
  hashing per process, so the same candidate sat in a different island on a resumed
  run than it did when it was evaluated. Inherited from the original `IslandArchive`;
  invisible until a run is resumed or a test is run twice. It uses a digest now.

**Held by.** `test_optimizer_policies.py` — one test per policy, each naming the
difference that is the reason it exists, plus the two defect regressions and a test
that the loop still hands over the population. On the standard §3 standard, an
optimizer with no parent policy is asserted to breed from the elite *by id*, because
that ordering is what the loop used before and getting it subtly wrong changed the
search enough to lose a refusal a layer early.

---

## 9. Half of a fix, closed as if it were whole (#36)

Worth recording because it is the same failure the rest of this document audits, made
while auditing.

**What was claimed.** Issue #36's acceptance criteria list seven things, the first
being *"`lowering` can emit `user` (and `agent`) gates; a mode can declare where it
wants one"*. The ledger, the transform, the seeding guard, the fidelity gate and the
inert-when-unused property were all built and tested. The gates were not, and the
issue was closed with "landed in …" above a list that read as complete.

**What the code said.** `lowering.py` hardcoded `evaluator_type="fn"`, and `nn.Node`
had no gate kind at all. **A mode could not declare a pause point**, which is the
mechanism the whole steering design rests on: an intent ledger nobody can be asked
about is a file.

**The fix.** `Node.gate_kind` — `fn`, `user`, or `agent` — with three rules that make
the choice real rather than decorative:

- `fn` requires an evaluator, and the other two **refuse** one, because a gate with
  two answerers has an answer that depends on the runtime.
- `lowering` emits the declared kind and emits a command **only** for `fn`.
- A walker that meets a `user` gate **refuses**, naming the node, rather than
  inventing a verdict. A run that pauses cannot quietly decide for the person; an
  invented verdict is indistinguishable from one they gave. The answer arrives through
  explicit `RunContext.verdicts` standing authorization, or a one-use ledger
  decision scoped to the current review inputs. Interactive answers are consumed;
  a later gate visit asks again rather than replaying historical approval.

**And a second defect, found by an existing test rather than by this work.** The first
version of `Node.to_state` serialized `gate_kind` unconditionally, which changed
**every mode's state dict** — and therefore every candidate id the loop has ever
recorded, so a resumed run would no longer match itself and the search moved for
reasons that had nothing to do with the search. `test_no_batch_slot_is_silently_lost`
caught it as a vanished batch slot. A field at its default is now omitted.

**Held by.** Six tests: a mode can declare a person-answered gate and it reaches the
IR; a headless walk refuses one; a supplied verdict lets it proceed; an unknown
verdict is still refused; a gate cannot have two answerers; and the default is not
serialized. Verified both ways — restoring the hardcoded `fn` fails the first.

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
- The GUI is not audited here at all. Its defects were tracked as issues #60–#73, and the panel's behaviour is held by `packages/client/ui-refactory/tests/` and [tutorial 7](tutorials/07-pick-and-watch-from-the-panel.md).

Two things that were on this list have moved off it, which is the point of keeping it:

- **mathlib is no longer untested.** It was installed and the mathlib problem ran, and
  doing so found two defects that no stub could have found (§8). The mathlib tests are
  gated on `MATHLIB_PATH`: they run where mathlib is present and skip with the reason
  where it is not, so a CI machine without it is honest rather than green by omission.
- **SRF's own optimizer is no longer unused.** `map_elites` is ported, counted
  separately from the thirteen modes, and asserted by name (§8).
