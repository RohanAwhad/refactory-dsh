# Tutorials

Start with [Get started](../getting-started.md) for installation and a small offline search. [Concepts](../concepts.md) explains the vocabulary; [Terminal and GUI](../interfaces.md) maps the two interfaces.

| Tutorial | What you learn |
|---|---|
| [1. Write a mode](01-write-a-mode.md) | Define, validate and execute a Python graph |
| [2. Optimize it](02-optimize-it.md) | Configure a search and inspect candidates, scores and audit |
| [3. Author with a model](03-author-with-a-model.md) | Create/Edit a reusable mode or Design an artifact |
| [4. Ship it on DSH](04-ship-it-on-dsh.md) | Execute the lowered graph on the shipping runtime |
| [5. Drive it from GUI chat](05-drive-it-from-the-gui.md) | Open, inspect and steer a workflow conversationally |
| [6. Run it with a person](06-run-it-with-a-person.md) | Answer gates, preserve approval history and steer a search |
| [7. Pick and watch from the panel](07-pick-and-watch-from-the-panel.md) | Set knobs and search budgets, attach runs and use controls |

The shipped deterministic examples keep the first steps small. Model-backed authoring and agent nodes have provider costs and variable results. `--optimizer random` alone does not guarantee an offline run: use a deterministic target and an edit preset that cannot introduce agent nodes, as in the getting-started example.

[The generated HTML tour](../tutorial.html) provides a visual catalogue of shipped graphs and options. Its tables are generated from code; explanatory prose still needs review when behavior changes.

Use the [command reference](../cli.md) for options, [model setup](../models.md) for credentials and routing, and [Tasks](../datasets.md) for a harness that operates on a class of inputs.
