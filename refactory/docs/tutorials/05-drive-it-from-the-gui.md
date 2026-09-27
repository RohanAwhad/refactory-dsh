# 5. Drive a workflow from GUI chat

GUI chat is the conversational interface to the same explicit mode graph used by the terminal. The adjacent [Modes panel](07-pick-and-watch-from-the-panel.md) offers direct controls for existing modes and searches.

## Open a session

```bash
uv run refactory ui --port 3081
```

Open the full URL the server prints. Select the checkout as the workspace and create a new session. Configure a provider and managed key under **Settings → Models** before model-backed work; see [Models](../models.md).

## Walk a small mode

Ask the CEO:

> Open counting_pack/fixed_step with target=9 and step=1. Drive it one node at a time. Tell me which node is due before doing its work.

The CEO uses `wf_open`, then `wf_next`. The runtime runs deterministic operations; the CEO delegates agent nodes and waits for their results. User gates ask you in chat. Its direct coding and skill tools are disabled while it drives the graph.

Knobs set actual operation inputs. Prose intent describes what you want; it does not silently replace `target` or `step`.

## Author a mode or artifact

Be explicit about the output:

> Use Create to build a reusable single-run harness for drafting and checking a project README from a brief. Call the package readme_pack. Open its workflow and let me review the strategy before building it.

For a class of work, supply the importable Task reference instead of the single-run choice. [The authoring guide](03-author-with-a-model.md) explains both.

> Use Design to build wordcount.py and sample.txt, with a check command of `python wordcount.py sample.txt`. Show me the design before implementing it, and verify the reported counts.

Design produces an artifact rather than a registered mode. Its supplied command and reviews determine acceptance. The current full authoring modes include substantial research and review; [fast modes with focused QA](../budgets-fast-modes.md) use smaller graphs and completed bounded CLI/native GUI acceptance. Required checks and repair limits still apply.

## Steer and correct

You can say:

> Pause before the implementation node so I can review the plan.

> Preserve the existing command-line interface. Add that as a constraint for the remaining work.

> That node used the wrong assumption. Change its prompt and rerun it; show me which dependent results become stale.

The CEO uses workflow state, steering and edit/redo tools. A delivery receipt shows which instruction reached work; inspect the outputs to judge compliance. An already running worker may not receive a change immediately. See [the interface guide](../interfaces.md#steering-is-precise-about-timing).

A session owns one open workflow. Use another session with a distinct run directory for independent work. Reopening a mode can start a fresh invocation; it is not equivalent to resuming an interrupted one.

## Follow a run in the panel

The original two-gear Refactory lockup identifies empty chat. Open **Modes** for the blue configuration and activity cards. Advanced routing, profiles, search controls and packages expand independently; required questions remain prominent above the controls. See the [panel guide](07-pick-and-watch-from-the-panel.md#find-the-controls).

Copy the exact run directory reported by chat into **Existing run directory → Read this run**. The attached status and Steering sections refer to that directory. The attached view streams persisted graph/worker activity, wait reasons, completed nodes and pending questions. Reading or reconnecting does not execute the run; [supported recovery](../live-control.md#recover-a-known-boundary) requires explicit resume.

For optimization, select **Optimize** in Modes, choose a scored mode, set its starting inputs and search limits, and press **Start optimization**. Search methods and saved profiles are expandable. Inspect candidates and use the optimizer-specific steering/adoption controls. Use **Run once** for an authoring mode after supplying its required knobs, or chat for stepwise control and approval gates.

The same run can be inspected from a second terminal:

```bash
uv run refactory status --run <exact-run-directory>
uv run refactory steer --run <exact-run-directory>
```

Replace the placeholder with the actual directory. [The command reference](../cli.md) lists note, gate and search controls.
