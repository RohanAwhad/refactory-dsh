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
| `wf_next` | The node(s) due now (agent task / fn / gate / fork), or `done`. |
| `wf_done(node)` | Mark a non-gate node complete and advance. |
| `wf_verdict(gate, proceed\|reloop\|halt, feedback?)` | Route a gate; reloop is iteration-capped. |
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
```

`graph` field names match refactory (`start_node`, `nodes`, `edges`,
`prompt_template`, `evaluator_type`, `gate_prompt`, `writes`, `reads`,
`expand`), so a `graph.json` file loads as-is.

## Node → DSH mapping

| refactory node | spine behavior |
|----------------|----------------|
| `AgentNode` | `wf_next` returns it; the agent runs a subagent, then `wf_done`. |
| `AgentNode` + `expand` (director) | `wf_expand` hot-plugs N children and fans out. |
| `FnNode` / `Study` | `wf_next` returns it; the agent runs the deterministic op, then `wf_done`. |
| `GateNode` fn/user/agent | `wf_next` returns it; the agent calls `wf_verdict`. |
| `ForkNode` / `JoinNode` | `wf_done(fork)` fans out; the barrier is the `afters` stack. |
| RELOOP edge | `wf_verdict(reloop, feedback)` rewinds, capped by `maxIterations`. |

## Known limitations and deferred work

- The spine is pure logic; the agent drives it via the six tools. A prompt
  section that instructs the agent to only advance through the tools (the
  "force the loop" guidance) is not yet registered.
- `SubgraphForkNode` / `SelectionNode` / `LLMNode` are advanced through with an
  `unsupported` marker (they need worktrees / a sub-executor).
- The graph is inlined via `config.graph`; loading a `graphPath` at apply time
  (via the `fs` seam) is not yet implemented.
- Only the pure spine is unit-tested; the tool registration path has a smoke
  test but not the repo-wide real-composition gate.
