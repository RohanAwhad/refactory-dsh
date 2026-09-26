---
description: "The interactive workflow runner: a per-session spine plus the wf_* tools that walk a refactory workflow graph node by node, for users and maintainers of CEO-driven workflow runs."
kind: "package-reference"
---

# `@deepseek-ai/dsh-workflow-spine`

English | [中文](README.zh.md)

## Summary

This package turns a refactory workflow graph (the JSON from `Workflow.to_dict()`) into a live, steerable run that the driving agent walks step by step without ever choosing what to do next. A per-session `Spine` owns the position ("what is next"), gate routing, iteration caps, fork/join barriers, director expansion, and graph edits, and exposes that state through the `wf_*` tools. The spine refuses to hand over the next node until the current one is complete, so a run cannot skip, reorder, or bypass a checkpoint. Deterministic nodes — `FnNode` and `Study` commands and `fn` gate evaluators — can run in-process when `executeCommands` is on and a `shell` executor is mounted, and a `DataNode`'s subgraph is iterated once per item through a configured item loader. The main costs are that the graph must validate at load and that the CEO's own coding tools are denied, so node work happens inside the subagents it spawns.

## Table of Contents

- [Why](#why)
- [Tools](#tools)
- [Config](#config)
- [Upstream vocabulary](#upstream-vocabulary)
- [Deterministic nodes](#deterministic-nodes)
- [Node → DSH mapping](#node-dsh-mapping)
- [Model Experience](#model-experience)
- [Known Limitations and Deferred Work](#known-limitations-and-deferred-work)
- [Dev Note](#dev-note)

-----

<a id="why"></a>
## Why

The headless `workflow` tool runs a script top-to-bottom and returns. It cannot pause for the user or change the graph mid-run. The interactive runner fills that gap: a per-session `Spine` owns the position ("what is next"), gate routing, iteration caps, fork/join barriers, director expansion, and graph edits. The agent only fills content at each node, so it cannot skip, reorder, or bypass a checkpoint.

-----

<a id="tools"></a>
## Tools (default prefix `wf`)

| Tool | Purpose |
|------|---------|
| `wf_next` | The node(s) due now (agent task / one-shot LLM / user gate / fork), or `done`. Deterministic nodes are run for you and never appear. |
| `wf_done(node)` | Mark a non-gate node complete and advance. |
| `wf_verdict(gate, verdict, feedback?)` | Route a gate by one of the `verdicts` its node view lists; reloop is iteration-capped. A `user` gate is routed on the answer the runtime gets from a person, not on the argument. |
| `wf_edit(node, skip\|unskip)` | Hot-remove / restore a node (steering). |
| `wf_expand(director, entries)` | Hot-plug a director's N child agents from its plan. |
| `wf_state` | Full live state (cursor, completed, skipped, verdicts, log). |

The loop the agent follows: `wf_next` → do exactly that node (spawn a subagent for an `AgentNode`) → `wf_done` / `wf_verdict` / `wf_expand`. The spine refuses to hand over the next node until the current one is complete, so the structure cannot drift.

With `executeCommands: true`, `wf_next` crosses a sole due static `ForkNode` or
`JoinNode` automatically. Branch prompts are returned only after their delivery
contexts have been established; delegate those returned `AgentNode`/`LLMNode`
prompts in parallel. Target ids in a state view are not dispatch instructions.
A user pause on a fork or join still stops before that boundary. Structural
bookkeeping does not consume the interactive one-command budget, and a join
that closes a data pass is graded before the runtime reports completion.

With automatic execution disabled, or a control node alongside other concurrent
nodes, that control is still returned explicitly: acknowledge the due fork/join
with `wf_done`, then call `wf_next` before dispatching its branches. Complete each
branch only after its worker finishes. These rules apply on every repair round;
preexisting review files do not replace the current delivery attempt.


-----

<a id="config"></a>
## Config

```yaml
- id: workflow-spine
  name: '@deepseek-ai/dsh-workflow-spine'
  config:
    graph: { ... }          # a refactory Workflow.to_dict() object (or a graph.json)
    toolPrefix: wf          # optional
    maxIterations: 3        # optional reloop cap
    executeCommands: false  # optional: run deterministic nodes in-process
    workdir: /runs/abc      # optional: working directory for node commands
    commandEnv: {}          # optional: extra environment for node commands
    commandTimeoutMs: 600000
    dataCommand: ''         # optional: the item loader a DataNode iterates through
```

`graph` field names match refactory (`start_node`, `nodes`, `edges`, `prompt_template`, `evaluator_type`, `gate_prompt`, `writes`, `reads`, `expand`), so a `graph.json` file loads as-is. `validateGraph` rejects a graph whose structure the walk cannot follow — an unresolvable `start_node`, a dangling edge, a node with two unconditional edges, two edges carrying one gate verdict, an unknown node type, a `DataNode` whose subgraph cannot be iterated, or a node the walk can never reach — so a bad graph fails at load rather than stalling a run.

An edge `condition` is a label, not an enum. `proceed` exits along the edge, `reloop` rewinds, `halt` stops, and any other label is a forward outcome the gate itself names (`draft`, `merge`, …), matched case-insensitively. `maxIterations` caps a reloop only when the gate declares no `max_iterations` of its own.

-----

<a id="upstream-vocabulary"></a>
## Upstream vocabulary

`@deepseek-ai/dsh-workflow-spine` is one consumer of refactory's `Workflow` IR; the [named-gate-outcomes Agent Note](../../../.agents/notes/implemented/architecture/2026-09-09-workflow-spine-named-gate-outcomes.md) owns the vocabulary decision and its verified coverage.

-----

<a id="deterministic-nodes"></a>
## Deterministic nodes

With `executeCommands: true` and a `shell` executor mounted, the plugin runs a node itself when the graph gives it a command: an `FnNode`/`Study` with a `command`, or a `GateNode` whose `evaluator_type` is `fn`. `wf_next` runs those, routes the gate by the **last non-empty stdout line** (lowercased, matching how edge conditions compare), and reports only the nodes a model must do. Without a shell, command nodes are handed to the driver as before while sole static fork/join boundaries still advance. With execution off, all nodes remain manually driven.

A node command is generic — it knows nothing about the graph that invoked it — so the plugin hands it its own declaration as JSON under `DSH_WORKFLOW_NODE`: `{ id, type, command, reads, writes }`. A command that exits nonzero, times out, is aborted, or (for a gate) prints no verdict **fails the node**: `wf_next` returns `{ status: 'failed', node, exit_code, error, stdout, stderr }` and the cursor does not move, because a step that did not happen must not look like one that did.

A `DataNode` is iterated the same way. Its items live outside the graph, so the composition names the loader (`dataCommand`); the plugin runs `<dataCommand> list --node <id>`, one `begin --index <n>` before a pass and one `end --index <n>` after it, and walks the subgraph from `subgraph_entry` to `subgraph_exit` between them with the ordinary cursor. The item is on disk as the node's `current_item.json` and in the `wf_next` payload's `item` field, so a subagent is handed the item it is working on. An empty selection completes the node and continues; a loader that exits nonzero or prints no JSON fails the node like any other command. A graph with a `DataNode` and no loader is refused at load, because a model cannot enter a subgraph the cursor never enters.

A `user` gate is neither run nor routed by the model. `wf_verdict` asks the composed `user-questions` answerer with the gate's own verdict labels as options and routes the answer, so a composition without an answerer refuses the gate with a message naming it instead of inventing a verdict. The plugin records `{ status: done | ready | failed | refused }` in `<workdir>/.refactory/spine-status.json` when `workdir` is configured, so a reader can tell a run that finished from one that stopped at a gate.

-----

<a id="node-dsh-mapping"></a>
## Node → DSH mapping

| refactory node | spine behavior |
|----------------|----------------|
| `AgentNode` | `wf_next` returns it; the agent runs a subagent, then `wf_done`. |
| `LLMNode` | `wf_next` returns it with its `system_prompt` as `prompt_template`; the agent makes the one-shot call, then `wf_done`. |
| `AgentNode` + `expand` (director) | `wf_expand` hot-plugs N children and fans out. |
| `FnNode` / `Study` | `wf_next` returns it; the agent runs the deterministic op, then `wf_done`. |
| `GateNode` fn/user/agent | `wf_next` returns it; the agent calls `wf_verdict`. |
| `DataNode` | The plugin selects its items and iterates its subgraph once per item, reporting the item with each pass and closing it with the loader's `end`. |
| `ForkNode` / `JoinNode` | Automatic execution crosses sole due controls; manual execution uses `wf_done`. Each branch advances on its own, and the barrier is the `afters` stack. |
| Named gate condition | `wf_verdict(gate, "draft")` advances along the matching edge. |
| RELOOP edge | `wf_verdict(reloop, feedback)` rewinds, capped by the gate's `max_iterations` or `maxIterations`. |

-----

<a id="model-experience"></a>
## Model Experience

### The `wf_*` tools and the CEO prompt section

#### What the model sees

The plugin registers one tool per verb — `wf_next`, `wf_done`, `wf_verdict`, `wf_edit`, `wf_expand`, and `wf_state` — each with a description and a parameter schema. `wf_next` returns the node view of the node due now, carrying that node's `prompt_template`, `reads`, and `writes`, and, for a gate, the `verdicts` the gate accepts. A registered prompt section (first-party order `TOOL_WORKFLOW`) tells the CEO that the `wf_*` tools are the only way to advance, and the `ceoDeny` guard removes the coding and external tools from the CEO's own scope at `agent/created`, so the CEO sees the `wf_*` tools, delegation, and read-only tools while the subagents it spawns keep the full set. A `user` gate is answered by the runtime, not by the model: when `wf_verdict` reaches one, the plugin asks the composed `user-questions` answerer with the gate's own verdict labels as options and routes the answer the person gave, ignoring the model's `verdict` argument; a composition with no answerer — a headless run — refuses the gate with a message naming it.

#### Token effect

Constant per request when the graph and tools are fixed: six tool definitions plus one prompt section, independent of graph size. Node content reaches the model only through the tool results the CEO reads, so a node's `prompt_template` and a command's stdout cost tokens once, when that node is due.

#### KV Cache effect

The tool block and the prompt section are fixed for the life of a run and sit in the cached request prefix. A node view that names a different node changes only the message suffix after that prefix, so provider cache reuse for the prefix is preserved when the tool set and configuration do not change.

## Known Limitations and Deferred Work

<a id="known-limitations-and-deferred-work"></a>

These limits define the current spine. They are current package constraints, not a graph-authoring backlog.

- `SubgraphForkNode` / `SelectionNode` are advanced through with an `unsupported` marker; both need an isolated-worktree sub-executor no current graph declares.
- A `DataNode`'s passes run one after another. The node's `parallelism` is not honoured: the subgraph is walked on the single cursor, so a subgraph with concurrent branches would need a second cursor per item.
- The item loader is a command, not a service. `dataCommand` is a shell command line, so a composition that wants a different loader changes it rather than implementing a seam.
- `LLMNode` is executed as a one-shot agent node: the driving agent makes the call. Nothing enforces the declared `model`, `temperature`, or tool set.
- A reloop collapses the cursor onto its target, discarding any other active position, so a reloop gate reached with a fork in flight drops the other branches. Graph authors must place reloop gates where the cursor is single.
- The `afters` barrier is one stack, so a fork nested inside a fork branch makes the inner barrier fire on the outer join.
- The graph is inlined via `config.graph`; loading a `graphPath` at apply time (via the `fs` seam) is not yet implemented.
- The "force the loop" guidance is a registered prompt section; the structural enforcement (the spine only advances on a reported completion) is the drift-proof guarantee, and the section only asks the agent to cooperate.

<a id="dev-note"></a>
### Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

None.

</details>

**Runtime invariant:** No runtime invariant companion is published because the spine's cursor, gate verdicts, and iteration counters are read only through the `wf_*` tools the plugin registers on the session's fiber.

## Live intent and progress

`wf_open` accepts an optional `intent`. Refactory records that purpose before
lowering the graph; without prose, it records the requested mode identifier as
`Run <package>/<mode>.` Existing ledgers are retained.

`wf_steer {}` reads the ledger. `wf_steer {"note": "...", "kind": "constraint"}`
appends the user's words and returns the updated ledger. The kind is `info`
(the default), `correction`, or `constraint`; blank notes, a kind without a note,
and unsupported fields are rejected. The configured steering command must accept
`<workdir> --note=<text> --kind=<kind>` for writes. Recording a constraint does not
prove later output complies with it.

The shipped graph and web compositions exclude `wf_next` from the upstream
argument-only repeat reminder: identical arguments legitimately advance the
cursor or a data pass. Other tools remain covered. This does not claim to detect
lack of workflow progress; cursor, gate, and iteration controls remain responsible
for workflow validity.

### Redo and current Task grades

`wf_redo` invalidates the requested node and all reachable descendants, including
nested data bodies. Refactory archives the prior `facts.json` and cursor state in
`.refactory/redo/<id>/` before clearing the facts those nodes declare they produce.
Only the resulting current facts are scored; archived grades remain inspectable.
A full dataset restart archives affected node-owned outputs there too, retaining
only the restart node's declared read inputs. This resets state such as per-item
attempt counters. A current-item correction keeps its artifacts. Affected pending
agent deliveries are retired and new attempts must deliver fresh outputs.
Unsafe output declarations (runtime-owned paths, escaping paths, or symlinks) are
refused instead of moved.

Redo within an active data item keeps that item's setup and earlier item grades.
Redo of a completed body node after its dataset has finished restarts the owning
dataset from item zero; the tool reports this explicitly as `start` and
`restartData`. Redo upstream of an active dataset discards its pass and cached
setup before restarting. Concurrent forks must finish before redo, and a node
shared by multiple data bodies is refused as ambiguous. Prompt edits persist.

The existing `dataCommand` protocol also supplies `reset`, accepting the redo
plan on JSON stdin and returning a receipt. A custom loader must implement that
bookkeeping verb; a failed reset leaves the live cursor unchanged. Graphs without
a loader retain cursor-only redo and have no runtime-owned Task facts to reset.

### Pause before a node

Arm an explicit boundary with `wf_edit {node: "items", action: "pause"}` before
calling `wf_next` that could reach it. The runtime stops before that node, including
before a data node lists or sets up its first item, and reports `status: "paused"`.
Repeated `wf_next` calls remain stopped, and completion/verdict calls cannot cross
the boundary. The startup driving request reads the intent ledger before the
first advance, so the CEO can arm a requested boundary before deterministic work.
It yields for a follow-up instead of draining through the dataset. Use `wf_state` to inspect node ids without executing work.

After the person asks to continue, `wf_edit {node: "items", action: "unpause"}`
releases that boundary. Other armed boundaries and terminal halts remain intact;
redo does not erase pauses. Arming a data node after its pass started does not
interrupt that pass's grading/closure; it stops a later entry to the data node.
No pause is installed for ordinary runs. This is an
explicit workflow control; interpreting a free-form request and arming the correct
node remains the CEO's responsibility. `wf_open` still performs its documented
initial deterministic step, so an initial-node pause in terminal use should be
armed on the configured graph before its first `wf_next`.


A skip bypasses the selected due or future node through its unconditional
continuation; it never marks that work complete or discards another concurrent
branch. Skipping an unopened fork or dataset bypasses its body, and unskipping
it before arrival restores the body without clearing independent child skips.
A gate with only conditional successors must be routed explicitly instead of
guessing an outcome. Already-entered containers cannot be skipped retroactively.
Skipping a data body's last step still closes and grades that item; even a fully
skipped body receives its Task setup before verification. Skipping the unopened
DataNode skips the whole dataset without starting any item. Armed pause boundaries
also protect skipped container bodies.
Skip edits persist across normal data items until explicitly unskipped; an explicit
redo retains its existing reset behavior. If a worker was already dispatched for
a skipped step, the coordinator must stop that owned worker separately: a graph
edit does not cancel a running agent.

An admitted skip retires pending delivery attempts for the skipped step/container
body before moving the cursor. A later unskip/reloop creates a new output baseline
and requires a fresh write; repeated `wf_next` calls within an active attempt still
reuse its baseline. Invalid skip requests leave delivery attempts intact. This
retirement preserves the prior receipt and artifacts and does not stop a worker.
