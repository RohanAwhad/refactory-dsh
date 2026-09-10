# `@deepseek-ai/dsh-workflow-spine`

The interactive, drift-free workflow runner. It turns a refactory workflow
graph (the JSON from `Workflow.to_dict()`) into a live, steerable run the
driving agent walks step by step — without the agent ever choosing what to do
next.

## Why

The headless `workflow` tool runs a script top-to-bottom and returns. It cannot
pause for the user or change the graph mid-run. The interactive runner fills
that gap: a per-session `Spine` owns the position ("what is next"), gate
routing, iteration caps, fork/join barriers, director expansion, and graph
edits. The agent only fills content at each node, so it cannot skip, reorder,
or bypass a checkpoint.

## Tools (default prefix `wf`)

| Tool | Purpose |
|------|---------|
| `wf_next` | The node(s) due now (agent task / one-shot LLM / user gate / fork), or `done`. Deterministic nodes are run for you and never appear. |
| `wf_done(node)` | Mark a non-gate node complete and advance. |
| `wf_verdict(gate, verdict, feedback?)` | Route a gate by one of the `verdicts` its node view lists; reloop is iteration-capped. |
| `wf_edit(node, skip\|unskip)` | Hot-remove / restore a node (steering). |
| `wf_expand(director, entries)` | Hot-plug a director's N child agents from its plan. |
| `wf_state` | Full live state (cursor, completed, skipped, verdicts, log). |

The loop the agent follows: `wf_next` → do exactly that node (spawn a subagent
for an `AgentNode`) → `wf_done` / `wf_verdict` / `wf_expand`. The spine refuses
to hand over the next node until the current one is complete, so the structure
cannot drift.

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
```

`graph` field names match refactory (`start_node`, `nodes`, `edges`,
`prompt_template`, `evaluator_type`, `gate_prompt`, `writes`, `reads`,
`expand`), so a `graph.json` file loads as-is. `validateGraph` rejects a graph
whose structure the walk cannot follow — an unresolvable `start_node`, a dangling
edge, a node with two unconditional edges, two edges carrying one gate verdict,
an unknown node type, or a node the walk can never reach — so a bad graph fails
at load rather than stalling a run.

An edge `condition` is a label, not an enum. `proceed` exits along the edge,
`reloop` rewinds, `halt` stops, and any other label is a forward outcome the gate
itself names (`draft`, `merge`, …), matched case-insensitively. `maxIterations`
caps a reloop only when the gate declares no `max_iterations` of its own.

## Upstream vocabulary

`@deepseek-ai/dsh-workflow-spine` is one consumer of refactory's `Workflow` IR;
the [named-gate-outcomes Agent Note](../../../.agents/notes/implemented/architecture/2026-09-09-workflow-spine-named-gate-outcomes.md)
owns the vocabulary decision and its verified coverage.

## Deterministic nodes

With `executeCommands: true` and a `shell` executor mounted, the plugin runs a
node itself when the graph gives it a command: an `FnNode`/`Study` with a
`command`, or a `GateNode` whose `evaluator_type` is `fn`. `wf_next` runs those,
routes the gate by the **last non-empty stdout line** (lowercased, matching how
edge conditions compare), and reports only the nodes a model must do. With no
shell mounted, or with execution off, every node is handed to the driver as
before.

A node command is generic — it knows nothing about the graph that invoked it — so
the plugin hands it its own declaration as JSON under `DSH_WORKFLOW_NODE`:
`{ id, type, command, reads, writes }`. A command that exits nonzero, times out,
is aborted, or (for a gate) prints no verdict **fails the node**: `wf_next`
returns `{ status: 'failed', node, exit_code, error, stdout, stderr }` and the
cursor does not move, because a step that did not happen must not look like one
that did.

## Node → DSH mapping

| refactory node | spine behavior |
|----------------|----------------|
| `AgentNode` | `wf_next` returns it; the agent runs a subagent, then `wf_done`. |
| `LLMNode` | `wf_next` returns it with its `system_prompt` as `prompt_template`; the agent makes the one-shot call, then `wf_done`. |
| `AgentNode` + `expand` (director) | `wf_expand` hot-plugs N children and fans out. |
| `FnNode` / `Study` | `wf_next` returns it; the agent runs the deterministic op, then `wf_done`. |
| `GateNode` fn/user/agent | `wf_next` returns it; the agent calls `wf_verdict`. |
| `ForkNode` / `JoinNode` | `wf_done(fork)` fans out; each branch advances on its own, and the barrier is the `afters` stack. |
| Named gate condition | `wf_verdict(gate, "draft")` advances along the matching edge. |
| RELOOP edge | `wf_verdict(reloop, feedback)` rewinds, capped by the gate's `max_iterations` or `maxIterations`. |

## Known Limitations and Deferred Work

- `SubgraphForkNode` / `SelectionNode` are advanced through with an `unsupported`
  marker; both need an isolated-worktree sub-executor no current graph declares.
- `LLMNode` is executed as a one-shot agent node: the driving agent makes the
  call. Nothing enforces the declared `model`, `temperature`, or tool set.
- A reloop collapses the cursor onto its target, discarding any other active
  position, so a reloop gate reached with a fork in flight drops the other
  branches. Graph authors must place reloop gates where the cursor is single.
- The `afters` barrier is one stack, so a fork nested inside a fork branch makes
  the inner barrier fire on the outer join.
- The graph is inlined via `config.graph`; loading a `graphPath` at apply time
  (via the `fs` seam) is not yet implemented.
- The "force the loop" guidance is a registered prompt section; the structural
  enforcement (the spine only advances on a reported completion) is the
  drift-proof guarantee, and the section only asks the agent to cooperate.
