# Terminal and GUI

Both interfaces use the same mode catalogue and run artifacts. The terminal is useful for repeatable commands; GUI chat is useful for conversation and node-by-node control; the Modes panel exposes full search profiles, worker routing and shared resource settings.

## Pick the right surface

| Action | Terminal | GUI |
|---|---|---|
| Create, Edit, Design | `refactory ceo` with the authoring mode | CEO chat for stepwise control; select a full/fast mode and **Run once** for execution |
| Run an existing mode interactively | `refactory ceo package/mode` | CEO chat for stepwise control; **Run once** for execution |
| Configure and start optimization | `refactory optimize` | CEO chat can launch a saved profile through Refactory tools; Modes → **Optimize**: saved profile or explicit controls → **Preview launch contract** → **Start optimization** |
| Inspect an existing run | `status --follow`, `artifacts`, `comparison`, `candidate` | **Runs and pending questions** or **Existing run directory → Read this run**; live activity and Steering |
| Add instructions or answer gates | Conversation and `refactory steer` | Chat and the attached run's Steering controls |
| Keep/reject candidates, pause/resume, adopt | `refactory steer`, `refactory adopt` | Optimizer controls in Steering |

**Optimize → Start optimization** searches scored modes. **Run once** also runs enabled authoring modes, including `create_fast`, `edit_fast` and `design_fast`; supply their required knobs first. Each CEO session owns one open workflow at a time. Use a separate session and distinct work directory for another concurrent workflow; opening a viewer never claims ownership of the run.

For chat-driven optimization, name the scored mode and saved profile/version, ask
the CEO to launch it, inspect the completed search, adopt the exact winner and
smoke the adopted mode. Chat and panel have separate
[actual-use evidence](release-validation-20260926.md#gui-chat-optimization-follow-up);
selecting a model or optimizer alone does not prove that it ran.

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

## Configure a launch

CLI and GUI share the same resolved configuration: strategy, optimizer, evaluator, starting/frozen knobs, permitted edits, epochs, batch size, candidate cap, retained parents, proposal attempts, seed, holdout and parsimony. The panel also exposes bounded repeats, resource limits/reserves, backend/provider/model/effort and role/node routing. Capability-aware controls disable unsupported external-backend options. See [profiles](optimization-profiles.md), [resources and fast modes](budgets-fast-modes.md) and [worker routing](models.md).

New workers select **Skip permission checks (default)**. This grants the tools OS-account access, including full DSH/Codex native sandbox bypass. Choose **Native permission policy** or CLI `--permission-mode default` to retain backend checks. Saved profiles and runs retain their effective policies.

A named profile version is reusable. A historical run's `resolved-profile.json` records the exact settings and contract it used. Save objective or required-artifact changes as a new version; preview again after changing launch settings. Task definitions, scoring and invariants remain owned by the mode.

Candidate counts do not substitute for time or token budgets. Repeated measurements and final validation consume the shared allocation. Candidate comparison keeps failed/unmeasured outcomes visible and reports unavailable cost/usage without inventing zero. Adoption keeps the exact eligible winner and its audit.

## Follow activity and questions

`refactory status --run RUN_DIR --follow` and the attached native panel read durable state and activity. The panel shows current/completed nodes, worker status and observed routes, usage, wait reasons, instruction-delivery receipts and output/check evidence. **Runs and pending questions** helps discover a gate waiting in another saved run. No estimated completion percentage is inferred.

Select a run to read its pending question and allowed verdicts. An answer is scoped to that run and invocation; a queued note or control is not proof that a worker consumed it. Reopening the view reconnects observation without starting work. See [live control and recovery](live-control.md) for timing, correction and ownership rules.

## Recovery and output

Pause/resume/stop controls apply at the owning graph or optimizer boundary; an in-flight worker may need to settle first. To correct a completed graph node, preview its affected results, then explicitly confirm the recorded revision. Prior evidence is archived and dependent results invalidated before rerun.

For a stopped graph owner, attach its saved run and press **resume**, or use `refactory graph-control resume --run RUN_DIR`. Both public paths have passed bounded acceptance: completed work, run identity and resource consumption are retained. Recovery is explicit and limited to a verified saved boundary. An unknown worker outcome, changed inputs/outputs or changed prepared configuration prevents automatic continuation. Reopening a mode can create a new invocation and archive outputs; it is not generic recovery. Native worker sessions are not resumed by this bridge. [Recovery details](live-control.md#recover-a-known-boundary) · [Work-directory lifecycle](tutorials/06-run-it-with-a-person.md#reusing-a-work-directory).

Created/adopted packages live in `.factory/packages/`, Design artifacts in `.factory/artifacts/`, and execution receipts in the selected run directory. A report's exact paths are authoritative. Task item results, aggregate score, execution completion and artifact quality are separate facts. Fast authoring, profiles, live control/recovery and worker routing have completed bounded CLI/GUI acceptance; see [validation status](../../README.md#release-capabilities-and-validation).


## Panel presentation

The Modes panel uses Refactory’s original two-gear identity and blue theme in both color schemes. **Configure a run**, **Activity & results**, **Graph controls** and **Steering** separate setup, observation and actions. Run and Optimize have separate launch actions while sharing inputs and the watched run. Help buttons explain fields, and clicking brief opens a large draft editor with Apply/Cancel. Advanced settings and package management expand without discarding inputs. A pending-question banner links to the available answers. Narrow and split panels stack the same controls; expanded panels place configuration beside activity.

The [panel guide](tutorials/07-pick-and-watch-from-the-panel.md#find-the-controls) describes navigation. [Presentation acceptance](panel-validation-20260927.md) and the [UX follow-up](panel-ux-validation-20260927.md) record native screenshots and bounded checks. [Upstream compatibility](upstream-compatibility.md) documents the published DSH dependency and pre-existing patch boundary.
