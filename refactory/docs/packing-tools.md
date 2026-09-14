# Packing a tool that is not Python

Audience: someone writing a package of modes whose work cannot be done in Python.
Every command below was run against this checkout, and the output shown is the
output it produced.

The library assumes a tool is a callable. An op is a function of the run context,
the validator resolves a node's `call` to a real object, and a package lists the
modules its modes call. Lean is not a callable. It is a proof checker with its own
file syntax, a build tool that gives a per-problem project a library path, and — for
anything past the prelude — a checkout of mathlib that the project names as a
dependency. `sympy_pack` is the same question in miniature: one importable module
that may or may not be installed.

`refactory_packages/lean_pack` and `refactory_packages/sympy_pack` are the worked
answers. They exist as a pair on purpose: one needs a toolchain, the other needs a
Python module, and a reviewer should be able to check that the *same* mechanism
handles both.

## The declaration lives in its own file

A package that needs something from the machine puts a `tools.py` beside its
`__init__.py` and assigns `TOOLS` to a tuple of `Tool`:

```python
from refactory_dsh.toolchain import Tool

TOOLS = (
    Tool(
        name="sympy",
        kind="python",
        probe="sympy",
        install=".venv/bin/pip install sympy",
        why="the exact algebra engine that decides whether a conjectured closed form is the right one",
    ),
)
```

A `Tool` has three required parts beyond its name — `probe`, `install` and `why` —
and `Tool.__post_init__` refuses a declaration that omits any of them. A declaration
that can say "absent" and nothing else is the failure this exists to replace, so it
is not a declaration the library accepts. `kind` is one of `binary` (a command on
`PATH`), `python` (an importable module) or `path` (a directory that must exist, where
`$NAME` expands from the environment). `optional=True` marks a tool only some of the
package's modes need.

`Package` gains no field for any of this, and the reason is not taste. Every field on
`Package` is validated by `Package.__post_init__` as a property of the package, and
whether Lean is installed is a property of the *machine*: it changes without the
package changing, and putting it on the manifest would mix a host fact into a value
the registry compares, copies for provenance and describes to a GUI. A separate file
also lets the registry read the requirement **before** the package is imported,
which is the case that matters. The usual way a missing tool announces itself is an
`ImportError` raised while importing the package's own ops, and the registry reports
that as `cannot import package sympy_pack: No module named 'sympy'` — a message
naming the symptom rather than the fix, and a false accusation whenever the package
is fine and the machine is not. `refactory_dsh/toolchain.py:read_tools` therefore
loads `tools.py` by path and never executes the package. A declaration may not use
relative imports for the same reason it may not import its own package: it has to be
readable when nothing else about the package is.

There is already a `tools` field on `Node`, and it is not this one. In the lowered IR
it is the agent's tool allowlist, which is a fact about what a model call may do
during a run. A package's requirement is a fact about the machine before the run
starts. Two unrelated facts behind one name is worse than two names.

## A declaration, not a cordis plugin

DSH composes capabilities as plugins, and a Lean capability would be one there:
something has to put a proof checker in front of the model, and that belongs in
`cordis.yml`. That seam answers *what can the running agent do*, and it is decided by
whoever writes the composition file. A package cannot reach it — the registry
discovers packages by scanning a directory, and the library is not allowed to know
any package by name — and it answers a different, earlier question: *can this
package's ops run on this machine at all?* That has to be answerable statically, by a
reader that is only reading, before any budget is spent. The two compose rather than
compete: the plugin gives the agent a Lean tool, and the declaration gives the package
a way to refuse to start without one.

## What verification does, and when

Three readers, one answer each, all of them static:

```bash
.venv/bin/python -c "
from refactory_dsh import toolchain
for status in toolchain.report('lean_pack'):
    print(status)
"
```

```text
lean: not on PATH as 'lean'
lake: not on PATH as 'lake'
mathlib: missing: MATHLIB_PATH is not set, so $MATHLIB_PATH names nothing
```

`.venv/bin/python -m refactory_dsh.registry tools lean_pack` is the same answer as
JSON, with a `fix` field per entry holding the sentence the failure is built from:
`the tool 'lean' is not on PATH as 'lean'; the proof checker that decides whether a
candidate solution is a proof. Install it with: curl https://elan.lean-lang.org/elan-init.sh -sSf | sh`.

`refactory_dsh.toolchain.check` probes every declared tool, `report` does the same
per package, `preflight` refuses over the *required* tools that are missing, and
`require` answers for one tool and raises `ToolMissing` with the fix in the message.
`registry.tool_report` and the `tools` verb above are the same answer through the
registry, because the registry is what users already ask what a package is.

A missing tool is deliberately **not** a finding of `registry verify`. That command
answers "is this package well formed", and a package whose tool is absent is well
formed, listable and runnable-in-principle — reporting it there would make "this
checkout has no Lean" indistinguishable from "this package is broken". A `tools.py`
that cannot be read *is* a package defect, and that one does appear in `verify`. The
listing carries the difference where a person will actually see it:

```text
lean_pack/  Prove theorems in Lean 4 and score what the proof checker accepted.  [needs lean]
  lean_pack/prover                       prove a theorem in Lean and score what the Lean checker accepted
```

Absence must not make a package invisible. It must make it labelled.

## Provisioning: refuse, and name the fix

Nothing here installs anything. `Tool.install` is the exact command, and it is
quoted in every failure message, which is the whole provisioning story: the package
loads, the tool is declared, and the run that would have used it refuses at the point
of use.

Installing on demand was rejected for three reasons. A toolchain is not a per-package
affair — mathlib is gigabytes, and every package that needed it would fetch its own.
A run's budget is evaluation budget, and a download is not an evaluation. And a
package that mutates the machine it lands on is a package nobody can audit.

The same reasoning reaches inside the Lean project. `ops.py` writes no
`lean-toolchain` file, because pinning one makes `elan` download that exact compiler
as a side effect of evaluation; the project uses whichever toolchain the machine
already has. It names mathlib by `path` rather than by a git dependency, for the same
reason: a checkout the user has already built, never a fetch.

## The worked Lean example

`lean_pack/prover` states a theorem, writes a per-problem Lean project, attempts a
proof, and lets the proof checker decide. Its graph is:

```text
task_input -> tool_check -> scaffold -> attempt -> verify -> repair_gate
                                            ^                   |
                                            +---- reloop --------+
                                                                |
                                                      proceed -> finish
```

`tool_check` is a node rather than a comment because absence is the interesting
failure and it has to happen at step two:

```bash
.venv/bin/python -c "
from pathlib import Path
from refactory_dsh import registry
r = registry.resolve(Path('.'), 'lean_pack/prover')[1]().evaluate(Path('/tmp/pack-absent'))
print(r.exit_ok, r.iterations, r.error)
"
```

```text
False 2 tool_check: ToolMissing: the package lean_pack declares the tool 'lean' (the proof checker that decides whether a candidate solution is a proof), and it is not on PATH as 'lean'; the fix is: curl https://elan.lean-lang.org/elan-init.sh -sSf | sh
```

Two steps, no project directory, and a message that names the tool, what it is for,
and the command that fixes it. That is the difference between this and a
`FileNotFoundError` raised forty minutes into a run.

`scaffold` writes the project. The statement is its own file and stays a `sorry`,
because a candidate that could edit the statement it is judged against would be
proving a different theorem:

```lean
-- project/Problem.lean
theorem three_consecutive_sum (n : Nat) : n + (n + 1) + (n + 2) = 3 * (n + 1) := by
  sorry
```

```lean
-- project/Solution.lean
-- the sum of three consecutive naturals is three times the middle one (omega)
theorem three_consecutive_sum (n : Nat) : n + (n + 1) + (n + 2) = 3 * (n + 1) := by
  omega

#print axioms three_consecutive_sum
```

`project/lakefile.toml` is what makes those two files a problem rather than a
snippet: a `Problem` library for the statement, and for the mathlib problem a
`[[require]] name = "mathlib"` with `path` pointing at `$MATHLIB_PATH`.

`verify` accepts a solution on two conditions, and both come from Lean. The exit code
says the file elaborated; the axiom dump from `#print axioms` says what the proof
assumed. A `sorry` closes a goal and elaborates cleanly, so the exit code alone would
accept an unfinished proof — `sorryAx` in that dump is Lean reporting the hole
itself, rather than `ops.py` pattern-matching on tactic text. `score()` then reads a
fact the run produced:

```python
def score(self, run):
    if not run.facts.get("verified"):
        return 0.0
    return round(1.0 - 0.1 * (int(run.facts["attempts_used"]) - 1), 6)
```

The discount is what gives the outer loop a gradient. `verified` alone is binary, so
every candidate that proves the theorem would tie; charging for the attempts it took
makes "found on the first try" a better answer than "found on the fourth", which is
a real claim about a proof search.

The package ships two problems deliberately. `three_consecutive_sum` needs nothing
but the Lean prelude, so the package runs on a machine with a toolchain and no
library, and it is what the tests exercise. `sum_odd_eq_sq` states
`∑ i ∈ Finset.range n, (2 * i + 1) = n ^ 2`, which needs `Finset` and `ring` and
therefore needs mathlib — the problem that shows why the requirement is declared at
all. `MATHLIB_PATH` is what selects a checkout, and nothing downloads one.

Write `∑ i ∈ s, f i`, not `∑ i in s, f i`. The `in` form is what most introductions
show and this Mathlib rejects it — Lean reads `∑ i` as an ordinary binder and reports
`unexpected token 'in'; expected ','`. The package shipped the `in` form until mathlib
was installed and the problem was actually run; every test before that used a stub
checker, and a stub does not parse the statement it is handed.

Two more things mathlib teaches a package that declares it, both recorded in
`refactory/docs/hardening.md` §8: the first invocation pays for importing the library,
so a timeout tuned to a core problem abandons the checker and reports a provable
theorem as unproved; and the project takes its `lean-toolchain` from the dependency, so
`lake env lean` may be a different compiler than `lean` on the PATH — which is why
`result.json` records `checker_version` from the project rather than the PATH.

## The sympy mode, beside it

`sympy_pack/exact` asks for the closed form of a sum in `n` and checks a set of
conjectured forms against it. sympy derives the sum and then reduces the difference
between that and each conjecture; a conjecture is accepted only when the difference
reduces to exactly zero as a symbol in `n`, so a form that agrees at a few sample
points and is wrong is rejected. The seed checks one conjecture per identity and
every first conjecture is wrong, so it scores zero, and `candidates` is the lever:

```bash
.venv/bin/python -c "
from pathlib import Path
from refactory_dsh import registry
mode_class = registry.resolve(Path('.'), 'sympy_pack/exact')[1]
for candidates in (1, 4):
    mode = mode_class()
    state = mode.state_dict()
    state['knobs']['candidates']['value'] = candidates
    print(candidates, mode_class(state=state).evaluate(Path(f'/tmp/pack-sympy-{candidates}')).score)
"
```

```text
1 0.0
4 3.0
```

Two packages, one reader, one failure message. `registry tools sympy_pack` reports
the module as present or absent exactly as it reports the Lean binary, and
`toolchain.import_tool` is how the op asks: it requires the tool and hands back the
module it names, so the requirement and the import are one statement rather than two
that can drift apart.

## Packing your own tool

Write the modes first and let the failure tell you what is missing. When an op needs
something that is not a Python callable, declare it:

1. Add `tools.py` beside the package's `__init__.py`, with a `Tool` per requirement.
   Give each one the command that installs it and the clause saying what it is for;
   the library will refuse the declaration without both.
2. Call `toolchain.require(package, name)` as the first statement of the op that is
   about to use it, or `toolchain.import_tool` for a Python module. Derive the
   package name from `__name__` — `__name__.split(".")[-2]` — so the op keeps working
   under a rename and in the writable package root.
3. Shell out from the op, not from `build()`. `build()` is pure and runs once; the
   tool is used at run time, and the executor enforces that a node writes only what
   it declares, so write the project files through `ctx.write_text`.
4. Make `score()` read a fact the run produced. For a tool whose verdict is the
   answer — a checker, a solver, a verifier — that fact is the tool's own answer, and
   the mode is then optimizing something it did not decide itself.
5. Put the check in the graph if it can fail usefully: a node early in the pipeline
   that requires the tool turns "this machine cannot do this" into a first-step
   refusal with a reason the report carries.
6. Declare the tools *optional* when only some modes need them. A required tool a
   mode does not use makes every other mode in the package unrunnable for no reason.
7. Declare `env` for a tool that is found through the environment. See below — it is
   the difference between a tool that works from a shell and the same tool under the
   sandbox, and the failure is silent.

### The environment a tool needs at run time

A tool that is present is not yet a tool that is *reachable*. A sandboxed run is
contained by rewriting `HOME` to its own working directory, which is right — code
nobody reviewed should not read a user's dotfiles — but every per-user toolchain
manager finds its installation through `HOME`. `elan` reads `$HOME/.elan`; rustup,
pyenv, nvm, gvm and sdkman all do the same. Containment alone therefore makes an
installed tool unreachable, and the run reports the tool absent on a machine that has
it:

```text
error: no default toolchain configured. run `elan default stable` to install
```

That message is not about the package, and it is not about the machine. It is about
where the run happened.

Declare what the tool needs:

```python
Tool(
    name="lean",
    kind="binary",
    probe="lean",
    install=ELAN,
    why="the proof checker that decides whether a candidate solution is a proof",
    env=(("ELAN_HOME", os.environ.get("ELAN_HOME") or str(Path.home() / ".elan")),),
)
```

`tool_env(package)` collects the pairs from the package's **present** tools, and
`sandbox.smoke` forwards them into the run. Two properties are worth stating:

- **The value is resolved where the declaration is read**, in the driver, which can
  still see the real environment. Package code cannot compute it: by the time an op
  runs under a sandbox, `HOME` is already the working directory, so `Path.home()`
  there answers a question about the sandbox rather than about the machine.
- **`env` is applied after containment, and does not weaken it.** `HOME` is still the
  working directory; forwarding adds variables rather than restoring the caller's
  environment. The secret filter still applies, so a declaration cannot smuggle a
  credential into a sandbox — a tool that needed one to *run* would be a different
  design, not an exception to this one.

The worked example is Lean, and it is worth knowing how it fails, because it fails
quietly. `lean_pack/prover` scored **1.0 through the walker and 0.0 through
`sandbox.smoke`** on one machine: the same mode, the same theorem, the same toolchain.
The walker has the real `HOME`; the sandbox does not. Registering the mode and
smoking it are different code paths, so this is exactly the class of defect a single
path's tests cannot see.

The toolchain is checked by `registry verify`, `registry tools`, and the tests in
`refactory/tests/test_math_pack.py`; the tests that need Lean skip with the name of the
missing binary when it is not installed, because a checkout without it has to be
correct.

## Where to go next

- [The architecture overview](architecture.md) — the state model the declarations sit
  on top of.
- [Writing a mode](tutorials/01-write-a-mode.md) — the smallest mode, and the
  validator rules a package has to satisfy.
- [`refactory_dsh.toolchain`](api/reference.md#refactory_dsh-toolchain) — the module
  this page describes.
