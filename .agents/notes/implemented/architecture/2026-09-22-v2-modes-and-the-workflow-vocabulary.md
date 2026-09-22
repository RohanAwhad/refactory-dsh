# Agent Note: The v2 modes and the workflow vocabulary they needed

Status: implemented

English | [中文](2026-09-22-v2-modes-and-the-workflow-vocabulary.zh.md)

## Problem

The default package's `create`, `edit` and `design` modes were a single pass: survey, choose, review, emit, validate. remote-factory's `create-v2` and `design-v2` do more, and the parts they add — a research phase that chooses its own directions, a strategy phase that records which asks each perspective covers, a person's approval before anything is built, three reviews in parallel, and an overwatch pass that audits the evidence behind them — could not be expressed at all. The mode vocabulary had exactly three node kinds (`op`, `agent`, `gate`): no barrier, and no iteration. The runtime had executed `ForkNode`, `JoinNode` and `DataNode` since it was written, but a mode could not declare one.

## Decision

**The vocabulary gained `fork`, `join` and `data`.** A fork reaches its branches through `targets` rather than one out-edge per branch, because one out-edge per branch would let the successor run as soon as the first branch finished; a join waits on `sources`; a data node names the subgraph it iterates. `to_state` emits the new fields only when they are set, so no existing state dict — and therefore no candidate id — changes. The lowering emits the IR's own nodes, `V12` refuses a name that is not a node, and `Graph.reachable` follows a fork's targets and a data node's entry, without which every branch and every subgraph looked unreachable.

**`create` and `edit` are create-v2; `design` is design-v2, including `design_doc`.** Research director, strategy director, `synthesize_strategy`, the design document, then a build whose result three reviews attack in parallel, a merged synthesis whose blocking findings the QA gate reads, and an overwatch pass whose stated verdict the last gate reads.

**A person approves the plan, and a headless run refuses that gate.** `gate_strategy` is a `user` gate in both modes, as it is in remote-factory: a run that can approve its own strategy is not reviewed. A headless caller passes the verdict explicitly, and the tests state who approved rather than inventing one.

**A `data` node executes, and a Task binding supplies its items.** The walker runs the subgraph once per item with the item written to `current_item.json`; a node bound to a Task takes its items from `instances()` and is judged by `verify()` per pass, recorded as `item_scores`. A creation asked for a data node or a Task is now *checked* for one by the validate gate rather than merely asked in its prompt.

## Consequences

The walker's fork is sequential where DSH's is concurrent: each branch runs to the same barrier, in order, so the result agrees and the concurrency does not. The DSH spine still hands a data node to the driving agent instead of iterating it, which is the one place the two runtimes still disagree about what a mode means.

## Alternatives considered

Keeping the added phases in one agent's prompt was rejected: the order, the barrier and the gate have to be owned by the runtime rather than by the model. Making `gate_strategy` a function was rejected for the reason the person's gate exists. The walker first refused a data node by name; executing it is what removed the difference on the walker side.
