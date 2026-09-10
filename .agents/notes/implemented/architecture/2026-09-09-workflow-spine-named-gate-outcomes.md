# Agent Note: Named gate outcomes and multi-node fork branches in the workflow spine

Status: implemented

## Problem

The spine consumed refactory's `Workflow.to_dict()` but executed only part of that vocabulary, in two ways that made real graphs unwalkable.

First, a gate could route only `proceed`, `reloop`, or `halt`. Refactory's own `Conditional` operator restricted branch labels to those three verdicts for the same reason, yet a gate that selects among alternative _forward_ continuations is an ordinary switch: AIDE picks draft/improve/debug, GEPA picks mutate/merge, AdaEvolve picks normal/meta. Encoding those as `reloop` was not merely misnamed — the spine treats `reloop` as a rewind, un-completes the nodes between the target and the gate, and counts against an iteration cap, so an alternative branch routed that way would rewind instead of advancing.

Second, `complete()` advanced the cursor only when it emptied. A `ForkNode` put every branch entry in the cursor at once, so completing one branch entry left siblings in place and never scheduled that entry's own successor. A fork was therefore only walkable when every branch was exactly one node long, which excludes Best-of-N's prompt → generate → eval branches and AutoScientists' prompt → generate → eval scientists.

## Decision

`WorkflowEdge.condition` is a label, not an enum, and the spine compares labels case-insensitively. `proceed`, `reloop`, and `halt` keep their meanings; any other label is a gate-named forward outcome routed to the matching conditional edge. `Spine.verdict` rejects an unlabelled outcome and names the outcomes the gate does accept, and a gate's `NodeView` carries that list as `verdicts`, so the driver answers from the gate's own declaration rather than a fixed set. `GateNode.max_iterations` caps a gate's own reloop when the graph declares one, so a budget-gated loop is not bounded by the runner's default of three.

`complete()` replaces the completed node in the cursor with its unconditional successor, leaving concurrent positions untouched; the `afters` stack still supplies the fork/join barrier, which now fires when every branch has drained. Director expansion no longer emits child → successor edges, because with several children the first completion would schedule the successor before its siblings finished.

`validateGraph` checks the invariants the cursor depends on — a resolvable `start_node`, edge endpoints, at most one unconditional edge per node, unique gate labels, fork targets and join sources, an executable node type, and reachability from the start — and `apply` rejects an invalid graph at load. `LLMNode` joins the executable types: a one-shot model call is handed to the driving agent the same way an agent node is, which is the mapping the graph-spine design already recorded as the v1 choice.

Checked against refactory: `Edge.condition` carries arbitrary labels, `Conditional` accepts any branch label (lowercasing non-verdict names), `OptKnob` carries a human `description`, and `Workflow.to_dict()` sorts `reads`/`writes`, which are sets whose iteration order is not stable across processes and would otherwise make a graph's serialized bytes vary run to run.

## Alternatives considered

**Lower a switch to a ladder of binary gates.** A three-way outcome becomes two nested gates, but the second gate is reachable only along the first gate's `proceed` edge, so the lower rungs still have to be spelled with `reloop` — the same rewind semantics, now hidden.

**Push branch selection into a data artifact and emit one branch.** This keeps the binary gate but moves the branch's prompt, model, and temperature out of the graph and into an op, so the graph stops describing what the mode does.

**Give each cursor entry its own continuation stack instead of one `afters` stack.** Nested forks would then barrier correctly. No graph in the current set nests a fork inside a fork branch, and the single stack is what keeps the barrier's meaning local to a fork.

## Consequences

A gate is now the graph's switch as well as its loop condition, and a fork branch may be any length. Graphs that used `reloop` to mean "the other branch" are wrong under the old reading and must be relabelled — SRF's GEPA evaluator now prints `mutate`/`merge` instead of `PROCEED`/`RELOOP`.

`NodeView.verdicts` and `NodeView.max_iterations` are new model-visible fields on a gate's view, and the `wf_verdict` parameter no longer declares a fixed enum, since the accepted set belongs to the graph. A reloop still rewinds and collapses the cursor to the target, so a reloop gate reached while a fork is mid-flight discards the other active positions; that remains a graph-authoring constraint, not an enforced one.

## Verification

`packages/workflow/workflow-spine/tests/vocabulary.spec.ts` covers named-outcome routing, case-insensitive matching, rejection of an undeclared outcome, `reloop` still rewinding, a gate-declared reloop cap, independent advancement of two-node branch pairs through a join, and one case per `validateGraph` rule. The pre-existing suite covers the linear, gate, director-expansion, fork/join, and skip paths unchanged.

Every graph emitted by the Scientific Research Factory (13 modes, up to 57 nodes) loads into `Spine`, passes `validateGraph`, walks to `done`, and reaches every declared node, including the alternative branches of each switch gate: `srf/tools/spine_probe.mjs`, driven by `srf/tests/test_spine_load.py`.
