# Learnings from the outer-loop optimizer work — 2026-09-12

Handoff document. Companion to
[`2026-09-12-create-v2-run.md`](2026-09-12-create-v2-run.md), which covers the mode-creation run.
This one covers what was found while making the outer loop an actual optimizer: capturing the
gradient, making the optimizer pluggable, and decomposing the loop.

Audience: the session working on refactory-dsh.

Everything here is evidence-backed — a file, a line, or an observed result. Where a finding is shared
with the create-v2 document it is referenced rather than repeated.

---

## 1. The headline: the gradient was severed, and everything downstream was blind

The outer loop is an optimizer loop. Its gradient is what an inner run produces — scores,
per-candidate verdicts, errors, artifacts. The loop could only ever see a float.

`SwarmEvaluator.evaluate()` populates `_cycle_records` **only on the `InnerLoop` path**
(`_evaluate_via_inner_loop`). The DSH integration supplies an `evaluator_fn`, which takes the simpler
path (`evaluator.py:260-268`), and that path computed a composite and returned — never touching the
cycle records. So `get_cycle_record()` returned `None` for every individual.

The consequence was invisible rather than loud. `OuterLoopReflector.reflect()` starts:

```python
valid = [(id_, score, rec) for id_, score, rec in records if rec is not None]
if len(valid) < 2:
    log.warning("reflection_insufficient_data", count=len(valid))
    return ReflectionReport()          # empty, and it returns here
```

Every record was `None`, so the reflection stage — contrastive top-K against bottom-K analysis, with
LLM reflection enabled — returned an empty report and never reached the model. `apply_random_mutation`
only takes its guided path when a report has suggestions, so the mutation stage fell through to blind
random operators. **The whole "reason about what happened" half of the loop was a no-op that logged
one warning.**

**Fixed by** `EvaluationOutcome` (`evaluator.py`): an `evaluator_fn` may return its `CycleRecord`
alongside the score, and `evaluate()` stores it on that path too. Existing evaluators that return a
bare `EvalResult` keep working.

**Verified by** the reflector going from `reflection_insufficient_data count=0` to
`reflection_complete failures=8 successes=2 suggestions=1`, with the next generation applying a guided
`knob_mutate` — a code path that had been unreachable.

### The lesson worth generalising

A silent `return` on insufficient data turned a missing feature into a working-looking loop. Any stage
whose contract is "do nothing when you have no input" will hide its own disconnection. **Fail loud, or
at minimum make the empty path visible in the run's artifacts.**

---

## 2. `CycleAnalyzer` existed but spoke a different dialect

`factory/cycle_analyzer.py::CycleAnalyzer` already did "artifacts → `CycleRecord`" — score trajectory,
per-candidate experiments with keep/revert/error verdicts, keep-rate, cost attribution, node traces.
Nothing needed writing. But it reads a `.factory/events.jsonl` layout plus a results TSV, and science
runs produce neither: they write flat files into the run work directory. So it returned empty records.

**Fixed by** `ScienceRunAnalyzer` in the same module, mapping the science dialect
(`evaluations.jsonl`, `candidates.jsonl`, `budget.jsonl`, `gepa_state.json`,
`accepted_history.json`, `rejection_history.json`) onto the same `CycleRecord`. Both dialects now
converge on one type, so nothing downstream branches on which loop ran.

**Lesson:** before building a collector, check whether one exists and what input format it assumes.
The gap was a dialect mismatch, not a missing component.

---

## 3. Mutation operators that destroy the graph, and a repair pass that hid it

Three defects chained, found by inspecting a trace that showed **1 node and 0 edges** where the parent
had 8:

1. `apply_random_mutation` sampled **two arbitrary nodes** for `parallelize`
   (`random.sample(structurally_mutable, 2)`).
2. `parallelize` splices the fork where the *first* node was and the join where the *last* was, which
   is only correct for a **contiguous chain**. For unrelated nodes it drops the entry edge.
3. `validate_and_repair` then **silently pruned** the unreachable remainder and returned the husk as a
   valid candidate.

So the loop generated destroyed graphs, spent a full inner run on each, and learned they score the
composite floor.

**Fixed:** `parallelize` refuses a non-contiguous set; `validate_and_repair` now rejects a graph it
would have to prune, logging the unreachable nodes by name; and the caller picks an adjacent pair so
the operator stays usable. Verified: 291 forced `parallelize` mutations, **zero graph shrinkage**.

`edge_redirect` had the same class of problem — rewiring the only inbound edge of a node orphans it —
and now prefers edges whose target survives.

**Lesson:** a "repair" step that silently deletes structure converts a detectable bug into a plausible
candidate. Two upstream tests asserted the old pruning behaviour and correctly went red; they now
assert the refusal, with the positive case beside the operator.

---

## 4. Knobs that lie

Three variants of the same defect, all found by reading the code the knob was supposed to reach:

- **`kind: "topology"` was declared and consumed by nothing.** The `OptKnob.kind` literal has included
  `"topology"` since the type existed; the only occurrence of the word in the repository was that
  declaration. A topology knob was accepted by the schema and ignored by every optimizer. Now a
  `TopologyRegistry` maps named structural operations, and a topology knob's `bounds` are which
  operations may be applied to its node.
- **`mutate_knob` wrote a topology value without applying the operation.** `loop_shape: none →
  parallelize_next` was recorded with the graph **unchanged (8 nodes)**. The knob reported a change
  that never happened. `mutate_knob` now dispatches topology knobs to `apply_topology`; verified 38
  dispatches, 0 that recorded a change without making one.
- **Prompt knobs never reached the node.** The convention is `_prompt_<node_id>` in `knob_values`, but
  the runner reads the *node's* prompt. Writing the value alone left the mode behaving identically.

**The rule, which we applied by hand during the create-v2 run when we deleted `temperature`:** *a knob
with no consumer is a lie.* It is now enforced — the pre-evaluation contract rejects a candidate whose
declared knob no longer reaches a node.

---

## 5. `remove_node` maintains the invariant, so data-flow validation cannot see the break

This is the subtlest finding of the work, and it invalidated the obvious approach to validation.

`remove_node` strips the deleted node's artifact from its consumers' `reads` as it removes it. So
deleting the node that writes `candidate.py` produces a graph that is **internally consistent** and
passes `validate_workflow` cleanly — while being a mode that can never produce a candidate:

```
remove generate       -> 7n | validate_workflow: NO ISSUES (silent break)
remove seed_run       -> 7n | validate_workflow: NO ISSUES (silent break)
remove select_parent  -> 7n | validate_workflow: NO ISSUES (silent break)
```

"Declared reads have writers" can never catch this, because the declarations are rewritten to agree.

**Fixed by** capturing the contract from the **seed** and comparing candidates against it:
`ModeContract.from_workflow(seed)` records every artifact the seed both produced and consumed (its
node-to-node interfaces), the nodes that own them, and the node each knob's spec names.
`CandidateValidator` rejects a candidate that stops producing one, loses an owning node, or leaves a
knob without a consumer. Verified: `remove generate` is refused with all three reasons, while a
legitimate `parallelize_next` is accepted.

**The contract is pinned from the population's root individual, not the current best.** Rebuilding it
from the best would let the contract drift as the search moves — which is exactly the failure the
guard exists to catch.

**And validation had to cover the seed population too.** `engine.seed()` created mutated variants with
no validation, so an invalid individual admitted there became a **parent**, and every candidate
descended from it inherited the break. This was found live: two valid proposals were rejected because
the sampled parent was a `node_remove` husk that had entered through `seed()`.

---

## 6. The contract is necessary but not sufficient

A `loop_shape: parallelize_next` candidate kept every required node, artifact and knob, passed the
contract — and still scored the composite floor at runtime. Structural and contract validation cannot
see a mode that is well-formed and does nothing.

Open. The honest options are a runtime-failure signal feeding back into validation, or treating floor
scores from structural edits as evidence against that operation. A heuristic here risks rejecting
legitimate structural exploration, so it is deliberately not rushed.

---

## 7. Visibility defects that made the surface lie

- **The same candidate was traced under two keys.** One evaluation wrote its trace under a key
  computed before the individual was persisted. Annotation matched only on the content key, so the
  candidate never annotated — missing its rationale and prediction from the report and dropping out of
  the leaderboard. Matching by the workflow the trace actually recorded fixed it.
- **Duplicates were counted twice.** Once both copies were found, the sweep sat *after* the
  "nothing changed" guard, so an already-annotated candidate never had its stale copy retired. The
  `seed` operator was reporting **2 attempts where there was 1**, corrupting its accept rate and mean
  delta — the numbers a user reads to decide "more of this, less of that".

  **A visibility surface that double-counts is worse than none**, because it looks authoritative.

- **The report read traces from the index**, so any re-keyed or repaired trace silently vanished from
  it. It now reads what is on disk.

- **Composite fitness was reported as if it were the task score.** `_compute_composite` is
  `0.6 * benchmark + 0.2 * hygiene + 0.1 * (1 - norm_cost) + 0.1 * (1 - norm_complexity)`, so at zero
  cost and complexity an inner score of 1.06 is archived as 0.836 and every candidate receives a free
  +0.2 unrelated to the task. The reasoning optimizer **derived this for itself from the traces**
  ("the reported prior best (0.836) is not on an obviously comparable scale"). The report now shows
  `best now (composite fitness)` beside `best task score`. The weighting itself remains a design
  decision — on a hard problem that constant can dominate early fitness.

---

## 8. Smaller findings worth recording

| Finding | Detail |
|---|---|
| Budget trap | With `budget == population_size` the starting population consumes the entire budget, so no generated candidate is ever evaluated — while the run still reports a new generation. Now warned up front, warned when it happens, and `GenerationSummary.offspring_evaluated` makes it visible in the output. Chosen to warn rather than clamp: silently spending more or searching less than configured is worse than saying so. |
| Two sources of truth for expandability | `knob_specs[k].expandable` and the workflow-level `knob_expandable` map were independent, and older graphs set only the latter. The knob surface read only the former, so it could **under-report what the search is allowed to do** and the optimizer would decline legal proposals. Now either declaration counts. |
| `default_knob_expander` shells out to the `claude` CLI | A model call with a hardcoded transport, bypassing the `ModelClient` seam. Expansion is now reachable through a pluggable client. |
| `evaluate` / `reflect` were placeholder aliases | `_COMMANDS` already carried `"evaluate": evaluate, "reflect": reflect` printing *"evaluation is part of evolve_generation; run evolve"*. Someone had anticipated the decomposition; the graph work filled them in. |
| `AgentNode` requires a `role` from a closed enum | `researcher, strategist, builder, health_checker, code_reviewer, adversarial_tester, failure_analyst, ceo, archivist, refiner` — there is no `optimizer`. A decision node uses `ceo`, as create-v2's director nodes do. `AgentNode` also rejects `notes`, which `FnNode` allows. |
| Trace durability | Inner runs wrote to `tempfile.mkdtemp()` and the directory was never removed: evidence links would rot and every evaluation leaked a directory. Runs now live under `.factory/outer_loop/runs/<key>/`. |
| Timeouts hide as low scores | `REFACTORY_DSH_TIMEOUT` defaulted to 900 s while inner runs took 420-2057 s, so every candidate was killed and scored `0.0` — indistinguishable from a weak candidate. A timeout now produces a record marked `stuck_detected`, so "never ran" and "ran badly" are different facts. |

---

## 9. What was built

Companion to §3 of the create-v2 document, which lists the delivery-form findings.

- **Upstream, remote-factory** (`factory/outer_loop/`): `optimizers.py` (`Optimizer`, `Proposal`,
  `ProposalContext`, `OptimizerRegistry`, `RandomOptimizer`, `ModelClient`, `TraceSource`),
  `autoresearch_optimizer.py`, `file_bridge_optimizer.py`, `topology.py`, `candidate_validation.py`,
  `EvaluationOutcome`, `ScienceRunAnalyzer`, decomposable generation steps
  (`evaluate_population`, `reflect_generation`, `evaluate_offspring`, `validate_and_select`,
  `build_proposal_context`), `frozen_knobs`, and the mutation-safety fixes.
- **Here** (`refactory_dsh/`): `trace.py` (trace facts, durable runs, annotation, `FileTraceSource`),
  `report.py` (the visibility surface), `steering.py`, `model.py` (`DshModelClient`), `ops/devgen.py`
  (the fast example mode's generator), and real `evaluate` / `reflect` / `apply` ops.
- **Mode**: `circle_packing_fast` — 8 nodes, `generate` replaced by a deterministic `FnNode`, **13.6 s
  per inner run against 1575 s** for the LLM-backed mode, with a non-monotonic score surface
  (`grid=4 → 0.545`, `grid=10 → 1.06`, `grid=24 → 0.74`) so the optimizer has something real to search.
- **Graph**: `outer_loop_decomposed.graph.json` — 7 nodes, 7 edges,
  `seed → evaluate → gate_converge → reflect → propose → apply → evaluate`, with `propose` the single
  `AgentNode`, plus a GUI preset.
- **Steering**: `.factory/outer_loop/steering.json` — active optimizer, operator weights, frozen knobs
  and nodes, selection pressure.

Test suites: **refactory 27 passed / 2 skipped**, and the affected remote-factory suites at 92-107
passed at various points of the work.

---

## 10. Open items

1. **The contract is not sufficient** (§6).
2. **Phase 8: no validation on the real LLM-backed `circle_packing_loop`.** The fast mode runs in
   seconds; the real mode took 26 minutes per inner run, so a meaningful outer-loop validation is
   multiple hours of wall-clock. Everything needed is in place — target graph, preset, optimizers — but
   it should be a deliberate, observed run.
3. **Composite fitness remains an affine transform of the task score** and is not configurable. Now
   visible; not yet a decision.
4. **Steering is a file, not GUI controls.** Rendering it is the R1 artifact-rendering work from the
   create-v2 document, still unbuilt.
5. **The orphaned-trace class is only fixed for duplicates of live individuals.** A trace for a
   candidate that no longer exists in the population is retained deliberately, as history, and the
   report reads it — which is correct only as long as the leaderboard is intended to span the whole
   run rather than the current population.
