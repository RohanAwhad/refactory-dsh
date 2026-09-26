# Concepts

Refactory makes an agentic workflow explicit, interactive and searchable. A mode can be small or elaborate: research and QA are graph choices, not requirements imposed on every harness.

![The mode and artifact workflows](assets/refactory-flow.svg)

## Packages, modes and Tasks

| Term | Meaning |
|---|---|
| **Package** | A named collection of Python modes and their supporting operations |
| **Mode** | An explicit graph, declared knobs and input/output contracts; a scored mode also defines the objective used by optimization |
| **Task** | The source of work items, per-item preparation and verification used by a mode's data node |
| **CEO** | The conversational coordinator that drives the graph and delegates its agent nodes |
| **Outer loop** | A search that proposes, validates and measures permitted changes to a mode |

Shipped packages live in `refactory/refactory_packages/`. Created, imported and adopted packages live in the project's `.factory/packages/`. Both use the same registry. A package may be disabled without deleting it.

Create produces a reusable package; Edit produces a revised package and records where it came from. Design produces an artifact in `.factory/artifacts/`. Create and Design are deliberately unscored authoring modes: successful completion is governed by their graph's checks, not a numeric search objective. Standalone Design artifacts are not optimizer candidates.

## What a graph contains

| Node | Work |
|---|---|
| `op` | Invoke a Python operation |
| `agent` | Delegate a tool-using agent task; it can involve multiple model turns |
| `gate` | Route on a deterministic, agent or user verdict |
| `fork` / `join` | Dispatch independent branches and wait for their results |
| `data` | Run a subgraph over a Task's items |

Nodes declare inputs, outputs and facts. Runtime validation catches structural violations; the mode's own checks establish its application requirements. Neither a file's existence nor an agent's statement proves its content correct.

A user gate is an explicit control point. The runtime asks the person or uses their deliberately configured authorization; the coordinator cannot invent approval.

## Graph execution and interactivity

The CLI and GUI present the same underlying workflows. The CEO uses workflow tools to open a graph, see the due node, dispatch workers and record completion. The runtime owns the cursor and the gates. Its guard disables the CEO's direct coding and skill tools during graph execution.

Workers may use skills as supporting instructions. The graph's structure and durable run state remain the execution mechanism. A graph does not depend on an agent remembering a checklist in a skill document.

Steering notes are recorded and supplied to subsequent work. Explicit prompt edits and reruns change a selected node's execution. Notes do not automatically rewrite deterministic operations, and delivery does not prove semantic compliance. See [Terminal and GUI](interfaces.md) for the current timing and UI boundaries.

## State, knobs and protected requirements

A Python mode declares its initial graph with `build()`. After that, `state_dict()` contains the graph and knobs as serializable data. The optimizer edits this state; it does not rewrite `build()` or arbitrary source files.

Knobs are declared inputs and parameters with types, bounds and mutability. Use them to pass actual values into deterministic operations. An intent such as “count to twenty” is context; `target=20` supplies the input.

A scored mode's `score()` and `invariants()` are methods outside the editable state. The validator enforces those declared boundaries before evaluating proposals. Required artifacts that are not part of a numeric score need meaningful invariants and acceptance checks; a higher score alone does not establish that every user requirement survived.

## The configurable outer loop

Three choices are independent:

| Choice | Responsibility |
|---|---|
| **Strategy / outer loop** | Organize selection, proposal rounds and stopping |
| **Optimizer** | Propose candidate edits |
| **Evaluator** | Execute a candidate and measure its result |

The default walker evaluates the graph in Python and supplies a model client to agent nodes. The DSH evaluator lowers the graph and runs it through the shipping DSH runtime. Their current model-selection paths differ; see [Models](models.md#model-selection-across-execution-paths).

Permitted edits can include knobs, prompts, nodes and edges, subject to the selected preset and invariants. A proposal is validated before it can spend candidate evaluation budget. Execution failures are recorded but cannot win.

For Task-bound modes, candidates use the training partition and the winner is audited on held-out items. A mode without a data split reports holdout as not applicable. The compared score can include a graph-size penalty; the raw score is retained separately.

Adoption writes the exact eligible winner into a new package with provenance. It does not alter the source package or guarantee a future stochastic run will reproduce the same outputs. No-improvement searches are valid outcomes.

## Models, agents and runtimes

Cordis supplies plugin composition and service lifecycle. DSH supplies the current agent runtime, tools, sessions and model-provider adapters. Refactory supplies modes, graph execution/steering and the meta outer loop.

The model provider and coding-agent runtime are different choices. Today, choosing an OpenAI model still runs the DSH coding agent. Optional Codex and Claude Code workers are tracked in [#237](https://github.com/akashgit/refactory-dsh/issues/237); they are not enabled by a model-name change.

## What evidence means

Keep three questions separate:

1. Did the framework execute the declared graph and handle gates, steering, failure and cleanup correctly?
2. Did the artifact meet its declared acceptance checks?
3. How good was the model's output across the workload, and at what cost?

A checker can be too narrow, a valid workflow can generate a poor answer, and a model timeout can leave a correctly reported failed run. Preserve the exact result and use the appropriate measurements for each question.

Next: [get started](getting-started.md), [write a mode](tutorials/01-write-a-mode.md), [Tasks](datasets.md), or [architecture](architecture.md).
