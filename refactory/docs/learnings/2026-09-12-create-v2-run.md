# Learnings from the create-v2 run — 2026-09-12

Handoff document. Audience: the session working on refactory-dsh.

This records what one complete `create-v2` run actually did, what broke, and what the run owner
asked for as a result. Everything here is evidence-backed: file paths, line numbers, commands, and
observed output. Where something is inference rather than observation it is labelled **[inferred]**.

Run of record: `/Users/akash/cursor/refactory-fresh-test/refactory/workdirs/create-v2`
User intent: *"create a mode that generates candidate code, evaluates it against the circle_packing
task, and accepts or rejects improvements"*
Outcome: mode `circle_packing_loop` built and verified. 51 of 53 acceptance criteria pass. 2 remain
open for a platform reason (see BUG-20/21).

---

## 1. Requirements raised by the run owner

These are the product asks. They are the reason this document exists.

### R1 — Artifacts are invisible in the GUI

Observed: the strategy text is unreadable; it does not scroll, so the action buttons cannot be
reached. Everything the modes produce is a Markdown file written into `.factory/`, and nothing in the
GUI renders it or even links to it.

Asked for:
- show a link to the artifact file, and/or
- a plugin that renders the artifacts any mode creates (they are Markdown in this system).

Evidence of the volume problem, measured in the run workdir: **29 Markdown files totalling 881 KB**,
out of 114 artefact files totalling 1.1 MB (excluding the booted-run work directories). Examples:
`.factory/strategy/current.md` (65 KB, the specification), `.factory/archive/create-build.md` (34 KB),
`.factory/reviews/qa-plan.json` (67 KB), and five `adversarial-*-latest.md` reports (25-40 KB each).
A user cannot consume that through an unscrollable panel.

### R2 — A created mode has no identity or home

Observed: when `create-v2` creates a mode, it lands in a per-preset workdir
(`refactory/workdirs/create-v2/.factory/modes/circle_packing_loop.graph.json`) and nothing else knows
about it. There is no name, no owner, no destination.

Asked for: **every created mode must always have a package name**, so refactory-dsh knows where to
file it.

### R3 — Created modes are not runnable from the GUI, and there is no grouping

Observed: the new mode is a bare graph JSON. To run it you must hand-type a `dsh --profile science
run` command with six flags (see §8). It is not a preset, so it does not appear in the GUI at all.

Asked for:
- a newly created mode should be wired to run via the GUI out of the box;
- modes should be **grouped by package** — `create-v2`, `design-v2`, `outer-loop`, `ceo` are
  "refactory" package modes; there is a "science" package; and so on;
- the GUI must show this grouping;
- the GUI must allow **importing packages**.

The owner's own words: "this needs a lot of work."

### R4 — a created mode must be registered, and reachable by name

**This is the completion of R2 and R3, and it is a correctness requirement, not a nicety.**

Observed: nothing about a created mode is recorded anywhere. `create-v2` writes a bare graph JSON
into a per-preset workdir and stops. No name, no registry entry, no lookup. Consequence observed
live: the outer loop **cannot find a mode that create-v2 just created**, so the only way to run the
outer loop on it was to hand-edit an absolute path into
`~/.dsh/.agent-presets/outer-loop/agent.cordis.yml`:

```yaml
REFACTORY_TARGET_GRAPH: /Users/akash/.../workdirs/create-v2/.factory/modes/circle_packing_loop.graph.json
```

That is a per-machine, per-mode, hand-edited stopgap. It is exactly the class of thing R3 is about,
and it will silently optimize the wrong graph for anyone who forgets it, because
`load_base_workflow()` falls back to `vendor/srf/graphs/gepa.graph.json` when the variable is unset.

Asked for:
- creating a mode **registers** it;
- every mode is resolvable **by name** (not by absolute path and not by environment variable);
- choosing which mode to optimize is a **UX action in the GUI** (pick from a list), not a YAML edit;
- the same registration makes the mode runnable, so R3's "wired out of the box" follows from it.

**The seam already exists, so this is cheaper than it looks.** The vendored remote-factory ships both
halves:

- `factory/workflow/registry.py::WorkflowRegistry` discovers workflows by scanning
  `.factory/workflows/` (user-global `~/.factory/workflows`, then project-local) for `.py` files
  exposing a `meta` dict and a `workflow()` function, and resolves them by name via
  `get_workflow(name)`.
- `factory/outer_loop/mode_registry.py::EphemeralModeRegistry` already writes the thin `.py` wrapper
  the registry discovers; its own docstring says the wrapper exists "so the `WorkflowRegistry` can
  discover the mode when a sub-CEO runs `--mode <name>`".
- `SwarmEngine.seed()` already consults the registry: when `config.seed_workflow` is set it resolves
  the workflow **by name** (`factory/outer_loop/engine.py:154-160`) and only falls back to the
  passed-in base workflow otherwise.

So name-based resolution is implemented and already reachable from the outer loop. What is missing is
that **`create-v2` never registers what it creates**, and `build_config()` never sets
`seed_workflow`. The work is: register the emitted mode at creation time under a real package name
(R2), and let the outer loop select it by that name instead of by `REFACTORY_TARGET_GRAPH`.

### R5 — grouping must be enforced, and modes must be manageable

**The owner's requirement, verbatim:** "we need to make sure the grouping is enforced so we don't have
a flat list of random modes created by everyone. we also need to be able to manage them i.e. import,
delete, etc."

R4 makes modes *findable*. R5 is what keeps the result from becoming an unmanaged pile. It has two
halves, and neither is optional.

**Half 1 — a package is an address, not a label.**

- A mode's identity is `<package>/<mode>`. Unqualified names are not a valid identity for a created
  mode.
- The package is required **at creation** (R2) and validated **at registration**. A mode with no
  package is either rejected outright or quarantined into an explicit "unassigned" bucket that is
  never presented as normal — it must not simply appear in the flat list.
- Two packages may each own a mode with the same short name, and both must resolve unambiguously.
  Today this is impossible: the registry is keyed by bare name (A6).
- The package itself is a first-class object with metadata: name, description, origin (built-in,
  installed, or project-local), and the set of modes it owns. Grouping is derived from that object,
  not from a naming convention on individual modes.

**Half 2 — lifecycle management, as first-class operations.**

These verbs must exist and be reachable from the GUI, not from hand-edited YAML:

| Verb | What it must do | What it must refuse or warn about |
|---|---|---|
| **List** | Show modes grouped by package, with package origin shown | — |
| **Import** | Install a package (from a path or a source) and surface its modes under its group | A package whose modes shadow an existing `<package>/<mode>` |
| **Delete / uninstall** | Remove a mode or a whole package | Deleting something a preset, an outer-loop target, or an archive entry still references — must be reported with the referrers named, not done silently |
| **Enable / disable** | Take a mode out of the runnable list without deleting it | — |
| **Rename / move** | Move a mode between packages | Same referrer check as delete |
| **Export / show provenance** | Where did this mode come from: which creation run, when, from which package; and export it for sharing | — |

**Candidate modes must be quarantined.** The outer loop generates a throwaway mode per mutated
candidate (`EphemeralModeRegistry`, naming them `evolve-gen{N}-<id[:8]>`) and writes them into the
**same** `.factory/workflows/` directory as everyone's permanent modes. Without a quarantine
namespace and cleanup, a single outer-loop run floods the user's mode list with dozens of
machine-generated candidates. They belong in a scratch namespace that never appears in the
user-visible list.

**Why "enforced" is the operative word.** Grouping that depends on contributors choosing to comply
will not hold. The enforcement points are: creation (no package, no mode), registration (a
package-less entry is rejected or quarantined), and collision (a name clash between packages is
resolved by namespace, never by silent precedence).

---

## 2. BUGS — confirmed, with evidence

### Scaffold-layer declaration drift

The pattern in every one of these: a graph node **declares** it reads or writes one path, and the op
that runs **writes a different path**. Nothing validates it. Downstream nodes then read a file that
does not exist, and agents silently work from stale or empty input.

| # | Node declares | Op actually writes | Where |
|---|---|---|---|
| BUG-01 | `.factory/strategy/user-intent.md` | `.factory/user-intent.md` | `refactory_dsh/ops/scaffold.py::intent_init` |
| BUG-02 | `.factory/eval_profile.json` | `.factory/discovery.json` | `scaffold.py::discover` |
| BUG-03 | `graph.json` | `.factory/graph.updated` + `.factory/modes/*.graph.json` | `scaffold.py::graph_update` |
| BUG-04 | `.factory/reviews/qa-synthesized.md` | `.factory/qa-synthesis.md`, a 45-byte stub | `scaffold.py::qa_synthesis` |

**BUG-04 is the worst of the four.** Three gates (`gate_qa`, `gate_doc_freshness`, `gate_precheck`)
declare `qa-synthesized.md` as their input, and the "merge all adversarial reports" op writes a stub
containing `# QA synthesis\n\n(generated by refactory-dsh)\n`. So the merged QA report the pipeline is
designed around **never exists**. Every gate in this run had to be evaluated by hand from the raw
reports.

### BUG-05 — the user's intent never reaches the pipeline

`scaffold.py::_intent()` reads `REFACTORY_INTENT`, defaulting to `"Build something useful."`. The
create-v2 preset's `commandEnv` sets `PATH`, `PYTHONPATH` and `REFACTORY_WORKDIR` but **not**
`REFACTORY_INTENT`. Verified in `~/.dsh/.agent-presets/create-v2/agent.cordis.yml`.

Observed consequence: `.factory/user-intent.md` contained the literal default. Every downstream node
whose prompt says "read user-intent.md — what the user ACTUALLY asked for" read that default. The
user's real request survived only because the CEO quoted it by hand into the subagent briefs.

This also means the graph's mandatory "intent fidelity check" is checking nothing when the env var is
unset.

### BUG-06 — `gate_precheck` can never fail

`scaffold.py::precheck()` is `print("PROCEED")` unconditionally. The gate exists to stop a bad build
and is structurally incapable of doing so.

### BUG-07 — the created mode is not invocable where it lands

`--graphs` defaults to `vendor/srf/graphs`. A mode emitted to `.factory/modes/` therefore dies at
startup (`loadGraph` throws) until the caller passes `--graphs <dir>` explicitly. The default
`workdir` is also a temp dir, so artifacts become unreadable unless `--workdir` is passed too.

### BUG-08 — the created mode is not registered

`factory workflow validate circle_packing_loop` returns `Unknown workflow: circle_packing_loop`
(exit 1). Correct behaviour for an unregistered bare graph IR, but it means the remote-factory
validation/registry/`SKILL.md` surface has nothing to say about any mode `create-v2` produces. See R2
and R3.

### BUG-09 — the emitter disagrees with the graph about what the mode is

`graph_update` declares it writes `graph.json`. It does not; it calls `_emit_basic_loop()`, which
writes a hardcoded 8-node circle-packing loop to `.factory/modes/`. `.factory/graph.updated`
containing the text `ok` is the only trace of `graph.json`.

### BUG-10 — `accept` was a gate that printed nothing

`srf.ops.gepa.acceptance:accept_or_reject` contains no `print`, and the `run.py` adapter prints
nothing on success. stdout is the gate verdict channel, so the spine reported
`"accept printed no verdict"` and **froze the cursor at `accept`** — each subsequent `wf_next`
re-ran a non-idempotent state mutation. Fixed in this run by converting `accept` to an `FnNode`.

### BUG-11 — `eval` clobbered the budget counter

`sandbox.run_eval` picks its result filename as the alphabetically-first `.json` in the node's
declared `writes`. The IR declared `["score.json", "budget_state.json"]`, so the score overwrote the
budget counter and `record_eval` then died with `KeyError: 'eval_count'` (exit 1). Fixed.

### BUG-12 — the accept/reject decision never fired

Under `acceptance_mode: strict`, `accept_or_reject` compares the child score against
`selected_parent.json`, which **no node in the IR wrote**. So `parent_score` was always `0.0` and
every candidate was "accepted". The user's core ask ("accepts or rejects improvements") was
non-functional in the shipped IR. Fixed by adding a `select_parent` node.

### BUG-13 — dead declarations

- `temperature` was declared as a knob but has **no consumer anywhere** on this code path
  (`ctx.knobs` has no `"temperature"` reader in `srf/ops`; `AgentOptions` has no temperature field).
  Keeping it is not neutral: the outer loop's surrogate scorer sums numeric knob values and
  MAP-Elites hashes all knob values, so a dead knob moves the score exactly where it is inert.
- `knob_specs[k].expandable` / `expansion_hint` are dead metadata; the live copy is the top-level
  `knob_expandable`. Two sources of truth, one of them inert.

### BUG-14 — an empty `system_prompt` silently empties the brief

`spine.ts:285` is `node.system_prompt ?? node.instance_prompt ?? ''`. `??` does not fall through an
empty string, and the IR set `"system_prompt": ""`. So `prompt_template` was `""` and the `generate`
node had **no instructions at all**. Note the trap: deleting the key does not help, because
`to_dict()` serializes every field via `model_dump(mode="json")` and re-emits `system_prompt: ""` on
any round-trip.

### BUG-15 — `SRF_ITERATION` is never set

`ctx.iteration` comes from `SRF_ITERATION` (`run.py:122`, default `0`) and `startup.ts` sets no such
variable. Every acceptance record and trace row therefore carries `iteration: 0` and
`state.iteration` never advances. Deferred, not fixed.

### BUG-16 — budget-gate exhaustion is a silent success

`budget_check.max_iterations` only bounds *reloop* verdicts. When it is exhausted the cursor is
emptied and the run reports `status: done` with no `exit` node executed. A capped run is
indistinguishable from a completed one in the status field.

### BUG-17 — QA fixtures and harnesses that overstate themselves

- The replayed harnesses (`drain_replay.mjs`, `dup_report_replay.mjs`) import the real `Spine`,
  `commandOf`, `verdictOf` and `NODE_ENV_KEY`, and run the real `srf` ops, but they **replicate**
  `runDeterministic` rather than driving a real `ShellExecutor`. Their docstrings claimed to mirror
  `manifestOf` while passing graph-declared `reads` that a real `FnNode` never receives.
- A fixture named `eval-prerepair.graph.json` was a hand-built hybrid (pre-repair in one field,
  already-repaired in others). Grading regression criteria against it gave the wrong answer depending
  on which file a reader picked. Regenerated from HEAD's emitter in this run.

### BUG-18 — the probe's "pass" is meaningless on its own

`spine_probe.mjs:40` early-returns `steps: 0`, so `missed: []` is satisfied by a graph that was never
walked. The sound assertion is `ok && missed == [] && steps > 0 && reached == nodes`. Separately, the
trap graph that silently strands two nodes is declared **VALID** by Plane A, Plane B *and* the real
CLI; only the probe catches it. Any gate that treats "validates cleanly" as correctness is unsound.

### Platform bugs (not in the mode — these need a fix in `packages/`)

**BUG-20 — `Spine.complete()` resurrects a finished graph.**
`spine.ts:328-361`: the guard at `:333-336` only prevents a second append to `completed`; it does not
return, and `:345` calls `advance(node)`. `:357-361` then assigns `replacement =
[unconditionalTarget(node)]` to the cursor **even when the cursor was empty**. A second report for an
already-completed node therefore restarts the walk. Reproduced deterministically against the real
`Spine`: `complete('eval')` after `done` leaves the cursor at `["accept"]` with no error.

**BUG-21 — subagents keep the `wf_*` tools.**
`packages/workflow/workflow-spine/src/index.ts:268` returns early for subagents
(`if (agent.session.header.origin === 'subagent') return`), and the module comment at `:11-13` states
that node-work subagents "keep every tool". Combined with BUG-20, a `generate` subagent can call
`wf_done` on the **parent** graph. This happened in two booted runs. Node ids are not secret:
`wf_state` publishes `eval` and `budget_check` in `completed`/`log`, so a subagent can target a node
without guessing.

Measured effect: `eval_count 3` against `--budget 2`, three lines in each JSONL trace, a duplicated
rejection id, `len(accepted) + len(rejection) = 3`.

**A fix already exists and is unwired.** `tool-subagent`'s Config exposes `toolFilter.deny`, wired
end-to-end: `packages/subagent/tool-subagent/src/index.ts:86-93` → `:514` →
`subagent-in-process-driver:126` → `child-agent.ts:217` (capability at
`packages/subagent/subagent-spawn-in-process/src/index.ts:46`). It is currently **unset** in
`packages/bundle/base/cordis.patch.yml:349-354`. This is roughly a six-line YAML change.

**BUG-22 — a mode can absorb the observed injection but not the whole class.**
A 4-edge reorder (putting the budget gate immediately downstream of `generate`, the only node the
driver is handed) absorbed the injection that was actually observed booted. It cannot absorb a report
targeted at `eval`, because `accept` has in-degree 1 and its only predecessor is `eval`, so `eval`
always re-runs. Two independent reviews plus a third attempt by the overwatch (re-targeting the
back-edge to `generate`) confirmed there is **no shippable in-artifact fix** at the 8-node/1-gate
ceiling; the last variant leaves `eval` and `accept` unreachable from `start_node`.

**BUG-23 — reloop feedback is keyed to the wrong node.**
`spine.ts:394` keys reloop feedback under the gate's target rather than the node the feedback is
about. Inert in this run, unmentioned anywhere.

---

## 3. ARCHITECTURAL findings — the big ones

### A1 — `create-v2`'s builder does not build the created mode

This is the most important structural finding of the run.

The created mode is emitted by a **hardcoded Python function**, `scaffold.py::_emit_basic_loop()`,
which returns a fixed 8-node dict. It was written before the run started. Meanwhile the run spends
its entire pipeline — discovery, 5 research directions, 4 strategy perspectives, a 65 KB
specification, a builder iteration, 5 adversarial testers, an overwatch — producing research,
strategy, and **53 acceptance criteria** that have **no path into the artifact** except through
editing that hardcoded dict by hand.

So: the `builder` node's `writes` is `.factory/reviews/builder-latest.md` — a *report* — not the mode.
The mode is a side effect of a scaffold op. The pipeline's intellectual output is, in the end,
decorative.

The owner's R2 ("where does it go?") is the visible symptom of this. There is no answer today because
nothing in the architecture decides where a created mode lives, what it is called, or who owns it.

### A2 — the builder's instructions describe a different system

`builder`'s `prompt_template` is the **remote-factory** checklist: create a plugin package with
`pyproject.toml`, `register_plugin()`, `factory workflow validate`, `SKILL.md`, then open a PR. None
of that applies to what this run produces (a bare graph IR). Every run must override the template by
hand, which means the prompt in the graph and the work actually done have diverged. A future builder
that follows its instructions literally would do the wrong thing.

### A3 — no package identity, no registry, no GUI wiring

Directly behind R2 and R3. Concretely, today:

- a created mode is a file in a per-preset workdir;
- there is no registry of created modes;
- modes are not presets, so the GUI cannot see them;
- presets are generated by `refactory/gen_presets.py`, which writes exactly **three**
  (`create-v2`, `outer-loop`, `design-v2`) and treats the `science` preset as a template to copy from;
- the installed `science` preset embeds the **`gepa`** demo graph and points at a **different
  checkout** (`/Users/akash/dsh/refactory-dsh`), so it will not run a mode built here.

### A4 — the delivery form was never decided

The spec this run approved says, in its own words, that the artifact is a faithful emission of the
emitter and that the mode is **not** copied into `vendor/srf/graphs/` (a pinned vendored copy). So
"the mode" exists in exactly one place, unregistered, in an untracked workdir. That is a decision
nobody made on purpose; it is where the code happened to leave it.

### A5 — the registry exists, but nothing registers into it

The library already provides mode discovery and name-based resolution (see R4). What is missing is
wiring at the two ends:

- **At creation:** `create-v2`'s `graph_update` op calls `_emit_basic_loop()`, which writes
  `.factory/modes/<name>.graph.json` and nothing else. It does not write a
  `.factory/workflows/<name>.py` wrapper, does not set a package name, and does not touch any
  registry.
- **At consumption:** `build_config()` builds a `SwarmConfig` with `seed_workflow` unset, so
  `SwarmEngine.seed()` never takes the registry path and `load_base_workflow()` falls back to an env
  var (`REFACTORY_TARGET_GRAPH`) or `vendor/srf/graphs/gepa.graph.json`.

So the system has a name-addressed mode registry that neither endpoint uses, and an absolute-path env
var standing in for it. Connecting the two ends is the whole of R4.

Because nothing is registered, a created mode is also **invisible to every other meta-mode**. `outer-loop`
is the first consumer to hit this, but `design-v2`, the outer loop's own sub-CEO runs, and any future
mode that wants to reference another mode all have the same hole.

### A6 — the registry is flat, and collisions resolve silently

Read `factory/workflow/registry.py` directly. The storage is:

```python
_entries: dict[str, WorkflowEntry] = {}
```

**A flat dict keyed by the bare `meta["name"]`.** There is no package, namespace, owner, or grouping
anywhere in the registry, and this is the concrete basis for R5.

Three consequences, all verified in that file:

1. **Collisions are silent, not namespaced.** `_discover_in_directory` resolves a name clash by
   *source priority* — project-local beats plugin beats user-global beats built-in. A losing entry is
   skipped with only a `log.info("workflow_registry.shadow_skipped")`; a shadowing one gets a
   `log.warning("workflow_registry.shadow")`. Neither reaches the user. Nothing lists *what* got
   shadowed, so a contributor cannot discover that their mode was silently overridden. With a flat
   namespace this is the only possible resolution; with a package namespace it would not arise.
2. **Grouping cannot be expressed by directory nesting.** Discovery is
   `sorted(path.glob("*.py"))` — strictly non-recursive. Modes in a subdirectory are invisible. So
   "group by package" cannot be retrofitted by organising files; it needs an identity change, not a
   layout change.
3. **Outer-loop candidates pollute the same namespace.** `EphemeralModeRegistry` writes each mutated
   candidate into `.factory/workflows/` with a flat machine name (`evolve-gen{N}-<id[:8]>`), the same
   directory every real mode lives in. One outer-loop run therefore adds a large burst of throwaway
   entries to the one flat list the user sees. (There is a `_warn_mode_drift` pass, but it compares
   plugin modes against discovered workflows; it does not detect collisions between discovered
   sources, and it knows nothing about packages.)

One further gap worth noting for R5: the search-path list is a flat list of `(path, source_label)`
with no package metadata at all, so there is currently nowhere to record which package a directory
belongs to, what its description is, or where it came from.

---

## 4. CEO/runner tooling gaps found while driving the run

These cost real time during the run and are cheap to fix.

| # | Gap | Cost observed |
|---|---|---|
| T1 | `wf_state.project_dir` is `""`, so the CEO cannot learn the run workdir from the tool surface. The workdir is only in the preset's `agent.cordis.yml`. | ~10 tool calls of forensics to discover that the run was writing to `refactory/workdirs/create-v2`, not the session workspace |
| T2 | `health_checker` and `code_reviewer` have **empty** `prompt_template` in the graph. | The CEO must invent each node's task from scratch every run; two different runs would test different things |
| T3 | `{project_path}` and `{generation}` placeholders reach the CEO **unsubstituted** in prompt templates. | Every run silently inherits a broken path in its instructions |
| T4 | Gate `reads` frequently name files that do not exist (see BUG-04). | Gates are evaluated from raw reports by hand instead of from their declared input |
| T5 | After a reloop, nodes already completed reappear in `wf_next`'s ready list (the runner dedupes them in the log). | The CEO must re-report nodes and cannot tell "re-run" from "already done" from the tool output alone |
| T6 | No progress or streaming for long node commands. Booted runs took 321 s, 420 s, 1575 s and 2057 s. | No way to tell a slow run from a hung one; one tester's bounded attempt hung >60 s and leaked a scratch process |
| T7 | Nothing renders the Markdown artifacts (R1). | The user cannot read the pipeline's output at all |
| T8 | `factory workflow export-skills` writes `workflow-*/SKILL.md` trees **into the current working directory**. A probe run from the repo root left 16 untracked directories in the git tree. | Repo pollution; had to be cleaned by hand |
| T9 | No way to express "this node cannot be fixed here" as a first-class outcome. | Iteration 1's wrong "not fixable" verdict was only caught because an independent tester was asked to refute it |

---

## 5. What worked well — keep these

1. **Adversarial QA with an explicit mandate to refute.** The single highest-value step in the run. The
   builder twice concluded the budget overshoot was unfixable from the artifact; a tester given the job
   of refuting it produced an executed counter-example (a 4-edge reorder, same 8 nodes) that then
   worked booted. Without that instruction the run would have shipped a wrong conclusion.
2. **Forcing every claim to carry the exact command and its observed output.** It caught a stale-SHA
   citation, a count-based argument that would have concluded the opposite, and a report that
   contradicted itself between §6.3 and §9.
3. **Independent re-measurement by the overwatch.** It re-ran the validation planes, re-measured the
   booted run's numbers itself, and tried the strongest remaining fix to test the CEO's reasoning
   rather than rubber-stamping it.
4. **Deterministic replay with an injection matrix.** Cheap, model-free, and it carried the correctness
   claim when a single booted run could not (boots are model-behaviour-dependent and a non-overshooting
   boot is weak evidence).
5. **Two methodology rules worth making standard:**
   - an escape proof must be **temporal** (a report landing *after* `done`), never a **count** — the
     count-based version balanced 4/4 and would have concluded no escape;
   - evidence must cite the **current** artifact SHA — an iteration cited a stale one and its whole
     evidence section was void.

---

## 6. Recommendations, prioritised

**P0 — blocks the product asks**

1. **Fix BUG-20 + BUG-21.** A no-op guard in `Spine.complete()` for a node not in the cursor, and/or
   set `toolFilter.deny` for `wf_*` at `packages/bundle/base/cordis.patch.yml:349-354`. Until this
   lands, every mode built this way can have its bookkeeping corrupted by a subagent, and no
   artifact-level change can prevent it.
2. **Give created modes a package identity** (R2). Require a package name at creation, define the
   on-disk destination, and record it. This is the precondition for R3.
3. **Wire created modes into the GUI, grouped by package, with package import** (R3). `gen_presets.py`
   is the natural seam: it already writes presets from emitted graphs and only hardcodes the list of
   three.
4. **Render artifacts in the GUI, or at minimum link to the file** (R1). Every mode in this system
   communicates through Markdown files; today none of them is reachable.
5. **Register created modes and resolve them by name** (R4, A5). Write the `.factory/workflows/<name>.py`
   wrapper at creation time using the existing `EphemeralModeRegistry` helper, set a package name, and
   have `build_config()` set `seed_workflow` from a name the user picked. Delete
   `REFACTORY_TARGET_GRAPH` from the flow, or keep it only as a developer override. **This is the item
   that makes R3 true rather than cosmetic**, and it is the one the owner called out as missing after
   this run: "unless we have a way to register a mode that we create, outer loop can't even find it."
6. **Enforce grouping and give modes a lifecycle** (R5, A6). A package is not a label on a mode; it
   is the address the mode lives at, validated at creation and at registration. Ship the management
   verbs (list, import, delete, enable/disable, rename, export) with the grouping, and quarantine
   outer-loop candidate modes out of the user-visible list. Without this, R4 delivers a flat list of
   everyone's modes in one namespace.
7. **Prove the wiring with the functional test in §7**, and do not gate that test on optimization
   quality.

**P1 — correctness of the scaffolding**

8. **Fix the declaration drift class (BUG-01..04).** Either make each op derive its written paths from
   the node manifest it already receives (`DSH_WORKFLOW_NODE` carries `writes`), or add a gate that
   verifies every declared write exists afterwards and every declared read existed before. Four
   instances are known; assume more.
9. **Propagate the user's intent (BUG-05).** Set `REFACTORY_INTENT` in the preset (or from the launcher
   argument). Right now every "intent fidelity check" in the pipeline is vacuous.
10. **Make `precheck` able to fail (BUG-06).**
11. **Make the emitted mode runnable where it lands (BUG-07, BUG-08):** write `--graphs` into the mode's
    own metadata, or default `--graphs` to include `.factory/modes`, and register the mode so
    `factory workflow validate` has something to validate.

**P2 — runner ergonomics**

12. **`wf_state` should report the run workdir, the project dir, and the artifact paths it expects**
    (T1). This alone would have saved the most wasted turns of the run.
13. **Fill the empty `prompt_template`s** (T2) and **substitute the placeholders** (T3).
14. **Make gate `reads` truthful** (T4) — in particular make `qa_synthesis` actually merge the
    adversarial reports instead of writing a stub.
15. **Distinguish "re-run" from "already complete"** in `wf_next`/`wf_state` (T5).
16. **Stream long node commands or emit heartbeats** (T6).
17. **Give the runner a first-class "cannot be fixed at this node" outcome** (T9), so a wrong
    "unfixable" verdict is structurally visible rather than depending on a tester being told to
    refute it.

**P3 — hygiene and spec quality**

18. **Run probe commands in a scratch cwd** (T8).
19. **Add a spec linter.** This run's specification contained five assertions that were structurally
    impossible and shipped anyway: an expected probe `missed` list the new topology cannot produce, a
    probe recipe missing a required `--task` flag, a "failure mode" that does not exist, a literal
    `writes` equality that contradicts the spec's own intent item, and Edge Wiring rows superseded by
    an approved change. None was caught by the pipeline; all were caught by testers reading the spec.
20. **Decide and document the delivery form** (A4), then align the builder's `prompt_template` (A2) and
    the gates' checklists with it. Three different delivery forms are currently described in three
    different places in the same run.

---

## 7. The functional wiring test we need — and it must not depend on optimization quality

The owner's requirement, in their words: build the end-to-end wiring and test it **functionally** —
"we don't need to wait for optimization to beat the prior SOTA, just test the wiring."

That is the right test to ask for, because the failure this run hit is invisible to a
quality-based test. A wiring break does not produce a bad score; it produces a **plausible** score.
Concretely: `REFACTORY_DSH_TIMEOUT` defaults to 900 s while the inner runs take 420-2057 s, so every
candidate is killed and scored `0.0` with `dsh_inner_loop_timeout`. From the outside that looks
exactly like "the optimizer ran and found nothing better". Likewise, leaving
`REFACTORY_TARGET_GRAPH` unset optimizes `gepa.graph.json` and reports a perfectly respectable score
for **the wrong mode**. Both are silent.

### The test

1. Create a mode through the creation entry point (`create-v2`, or whatever replaces it).
2. Assert it is **registered and resolvable by name** — no absolute path, no env var, no file copy.
3. Select it as the outer loop's target **by name from the GUI**.
4. Run exactly **one generation**.
5. Assert all of the following, from the artifacts on disk:
   - the seed loaded from the created mode, proven by its **knob names** appearing (a mode with knob
     `acceptance_mode` must not report `temperature`, which is what `gepa.graph.json` carries);
   - the inner loop actually executed the created mode, *N* times, evidenced by real inner-run
     artifacts (a `work/` directory with `budget_state.json`, `eval_result.json`, the JSONL traces);
   - a numeric score came back for at least one candidate;
   - the archive/population holds an entry derived from the created mode.
6. Assert **nothing was hand-edited between steps 1 and 4** — no preset YAML change, no environment
   variable, no manual file placement. This assertion is the point of the test.

### Passing criteria are structural, not numeric

Pass = "the wiring carried a newly created mode from creation to evaluation and back, unattended".
It is **not** "the optimizer beat the seed". A score of 0.0 caused by a timeout is a FAIL, and the
test must be able to tell the two apart — which means asserting on the inner run's artifacts, not on
the score alone.

### Negative cases worth asserting

- Targeting a mode name that does not exist must **fail loudly**, not silently fall back to
  `gepa.graph.json`.
- Targeting a mode whose timeout budget is too small must report a timeout, not `0.0`.

### Grouping and lifecycle assertions (R5)

The same test must cover the management surface, because a grouping scheme that is not exercised by a
test will drift back to a flat list:

- **Creation without a package is refused** (or lands in an explicit unassigned bucket that never
  appears in the normal list). A mode that can be created without a package has no grouping.
- **Two packages can each own a mode with the same short name**, and both resolve to the right one.
  This is the assertion that proves namespacing is real rather than cosmetic, because today the
  registry would silently keep one and drop the other (A6).
- **A collision is reported, not silent.** Registering a mode that would shadow an existing
  `<package>/<mode>` must surface as an error or an explicit shadow report the user can see. A
  `log.info` is not sufficient.
- **Import a package, then its modes appear under its group; delete it, then they disappear.** Import
  must also report what it would shadow before doing it.
- **Deleting or renaming a mode that something still references fails loudly**, naming the referrers
  (a preset, an outer-loop target, an archive entry).
- **Ephemeral outer-loop candidate modes never appear in the user-visible list, and are cleaned up**
  after the run. Assert this by running the one-generation outer loop from §7 and then listing modes:
  the count of user-visible modes must be unchanged.

### Why this test cannot be written today

Steps 2 and 3 have no implementation (R4, A5), and the grouping and lifecycle assertions have no
implementation either (R5, A6). The wiring this run used was a stopgap: an absolute path hardcoded
into `~/.dsh/.agent-presets/outer-loop/agent.cordis.yml` by hand. It works, on this machine, for this
one mode, and it is not a fix.

---

## 8. How to run the mode this run produced

For reference, since R3 is not yet delivered:

```bash
ROOT=/Users/akash/cursor/refactory-fresh-test
WD=$ROOT/refactory/workdirs/create-v2
mkdir -p /tmp/my-run
cd $ROOT
node --import tsx/esm apps/cli/src/bin.ts \
  --profile science run \
  --mode circle_packing_loop \
  --task circle_packing \
  --budget 2 \
  --graphs "$WD/.factory/modes" \
  --workdir /tmp/my-run \
  --python-bin "$ROOT/.venv/bin" \
  --knobs '{"acceptance_mode":"strict"}'
```

Every flag is load-bearing; see BUG-07. A run takes 7-35 minutes because the model is called for real.

---

## 9. Evidence index

Run workdir: `/Users/akash/cursor/refactory-fresh-test/refactory/workdirs/create-v2`

| What | Where |
|---|---|
| The mode (final artifact, SHA-256 `098f15a3f76697c8bbe128c65c10f5c626b9b02917f9ff23430d7963deb3731a`) | `.factory/modes/circle_packing_loop.graph.json` |
| Approved specification, 53 acceptance criteria | `.factory/strategy/current.md` |
| Intent of record, both reloop feedbacks, CEO gate verdicts | `.factory/strategy/user-intent.md` |
| Build history and learnings archive | `.factory/archive/create-build.md` |
| Final verification report | `.factory/reviews/overwatch-latest.md` |
| QA plan and rollup (51 PASS / 2 FAIL) | `.factory/reviews/qa-plan.json` |
| Five adversarial reports | `.factory/reviews/adversarial-*-latest.md` |
| Booted run evidence (final) | `.factory/verification/runs/qa3-booted-20260912T122426/` |
| Deterministic replay harnesses | `.factory/verification/{drain_replay,dup_report_replay}.mjs` |
| Emitter (the source of truth) | `refactory/refactory_dsh/ops/scaffold.py::_emit_basic_loop` |
| Regression test | `refactory/tests/test_emit_basic_loop.py` |

Repo footprint of the run: `refactory/refactory_dsh/ops/scaffold.py`,
`refactory/tests/test_emit_basic_loop.py`, plus two `refactory/graphs/*.json` edits that are **not**
from this run. `packages/**` and `refactory/vendor/**` untouched.

---

## 10. Open items carried out of the run

- **B3 and B6 fail** (of 53 criteria): a subagent can report `eval`, and `Spine.complete()`
  resurrects the accept/reject tail. Root cause is BUG-20 + BUG-21; no artifact-level fix exists.
  This is why §6 item 1 is the top priority.
- **`SRF_ITERATION` is never set** (BUG-15), so iteration numbers in the artifacts are meaningless.
- **`circle_packing_loop` is not in `STATE_FILES`**, so `bestScore()` falls back to scanning every
  `*.json` in the workdir: correct today only because one file happens to carry `best_score`.
- **The 2.634 reference score was never reached** (best observed 1.72). The metric headroom is a
  declared target, not a gate.
- **Five spec errata** need one-line rewordings; they are listed in §6 item 19 and in
  `.factory/strategy/user-intent.md`.

### Stopgap applied during this session — must be reverted when R4 lands

To let the owner try the outer loop on the newly created mode *interactively from the GUI* before R4
exists, these lines were hand-added to the `workflow-spine` `commandEnv` in
`~/.dsh/.agent-presets/outer-loop/agent.cordis.yml`:

```yaml
REFACTORY_TARGET_GRAPH: /Users/akash/.../workdirs/create-v2/.factory/modes/circle_packing_loop.graph.json
REFACTORY_BUDGET: '2'
REFACTORY_POPULATION: '2'
REFACTORY_EVAL_BUDGET: '1'
REFACTORY_DSH_TIMEOUT: '2700'
```

This is a per-machine hack that hardcodes one mode into one preset. It is recorded here for two
reasons: so the next session knows it exists, and so it is the first thing removed when name-based
registration (R4) replaces it. Note also that re-running `python -m gen_presets` regenerates the three
meta-mode presets and will overwrite this file.

`REFACTORY_DSH_TIMEOUT` deserves separate mention: at its 900 s default it kills every candidate and
reports `0.0`, which is indistinguishable from a real low score. Any change to the outer loop must
either raise that default or make the timeout visible in the result.
