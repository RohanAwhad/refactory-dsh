# Terminal and GUI

Both interfaces use the same mode catalogue and run artifacts. The terminal is useful for repeatable commands; GUI chat is useful for conversation and node-by-node control; the Modes panel exposes common run and search settings.

## Pick the right surface

| Action | Terminal | GUI |
|---|---|---|
| Create, Edit, Design | `refactory ceo` with the authoring mode | Ask the CEO in chat to open and drive the authoring workflow |
| Run an existing mode interactively | `refactory ceo package/mode` | CEO chat for stepwise control; **Run once** for execution |
| Configure and start optimization | `refactory optimize` | Modes: mode, knobs, outer loop, optimizer, evaluator and search budgets → **Start** |
| Inspect an existing run | `status`, `artifacts`, `leaderboard`, `candidate` | **Existing run directory → Read this run**, status and Steering |
| Add instructions or answer gates | Conversation and `refactory steer` | Chat and the attached run's Steering controls |
| Keep/reject candidates, pause/resume, adopt | `refactory steer`, `refactory adopt` | Optimizer controls in Steering |

Authoring entries do not use the panel's ordinary **Start/Run once** buttons. Follow their authoring guidance in chat. Each CEO session owns one open workflow at a time; use a separate session and distinct work directory for another concurrent workflow.

## What runs the graph

The CEO calls `wf_open` and advances with `wf_next`. The runtime executes deterministic operations and function gates, tracks the cursor and routes branches. Agent nodes are delegated to coding/research workers. A user-owned gate asks the user.

While driving a graph, the CEO's direct coding and skill tools are disabled. A correction can change a node's prompt with `wf_edit`, then rerun it with `wf_redo`; the execution records the change and invalidation. Workers may use skills as supporting instructions, but skills are not the mode execution mechanism.

The same mode can run through the Python walker for evaluation. Agent nodes there use the configured model client; an agent completion can include multiple tool calls and model turns.

## Steering is precise about timing

- A note is persisted context. Delivery to a node is not proof of semantic compliance by its output.
- A deterministic operation consumes its declared inputs and knobs; a prose note does not rewrite it.
- The interactive CEO can honor a requested node boundary and apply explicit prompt changes. Changes do not retroactively alter completed work.
- Optimizer pause/resume/stop and population changes apply at generation boundaries. They may wait for an active model call or evaluation.
- A finished search has no next boundary. Its live controls are disabled/refused; explicit adoption can still apply immediately when the winner has the required audit.

Use the exact directory returned by the run, including for runs opened in chat. Do not infer an id from a mode name.

## What you can configure today

The Modes panel exposes starting knobs, strategy, optimizer, evaluator, epochs, candidates per epoch and maximum candidate evaluations. The CLI/API additionally expose edit presets, elite count, attempts, random seed, holdout fraction, parsimony and model options. See [the command reference](cli.md#optimization).

Evaluation counts are not token or elapsed-time limits. Final held-out checks are additional. Global resource budgets and complete GUI profile controls are planned in [#234](https://github.com/akashgit/refactory-dsh/issues/234) and [#236](https://github.com/akashgit/refactory-dsh/issues/236).

## What is live today

Terminal CEO turns and optimization events stream as work happens. **Run once** has its own launch/run identity and execution stream. The panel's attached status and Steering snapshots are also readable during work, but some require manual refresh; they are not yet a unified continuously updated graph/worker dashboard. That is the scope of [#235](https://github.com/akashgit/refactory-dsh/issues/235).

Check actual status and receipts before interpreting a quiet chat or an older snapshot. A gate waiting for input, a failed node, a requested cancellation and a completed run are different states.

## Recovery and output

Use documented resume APIs/commands for continuation. Opening a mode again can create a fresh invocation and archive previous declared outputs; it is not a generic resume operation. [The interactive tutorial](tutorials/06-run-it-with-a-person.md#reusing-a-work-directory) describes these boundaries.

Modes and artifacts have separate outputs: created/adopted packages live in `.factory/packages/`, Design artifacts in `.factory/artifacts/`, and execution receipts in the selected run directory. A report's exact paths are the authority. A Task's per-item results and a mode's aggregate score are distinct; the GUI does not invent a score when the run produced none.
