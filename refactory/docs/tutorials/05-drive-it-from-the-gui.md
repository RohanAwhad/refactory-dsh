# 5. Drive it from the GUI

The loop stays a Python process. The Web GUI drives it through one tool, and
renders what came back. No terminal is involved once it is running.

## Start the GUI

```bash
./refactory-dsh --port 3081 --no-open
```

It prints an authenticated URL; open it. The default preset is **ceo**, which
mounts the `refactory` tool.

> The GUI serves the client bundle from `packages/*/lib/client.js`, so a checkout
> that has not been built serves the bundled version of each client module. Build
> first if you have changed one: `pnpm run build:lib:client`.

## What the tool does

One action per console verb, so the tool and a shell are the same surface:

| action | needs | what it returns |
|---|---|---|
| `modes` | — | every mode the checkout can start, with its parameters and the **action that runs it** (`start` for a scored mode, its own verb for an authoring one) |
| `strategies` | — | the named outer loops, optimizers and evaluators, each with what it is |
| `runs` | — | every run under the run root, newest first |
| `start` | `mode`, `settings` | the run id, root, and pid |
| `status` | `run`, `after` | the state, counters, best score, and new events |
| `artifacts` | `run`, `contents` | what the run left, optionally with contents |
| `leaderboard` | `run` | the candidates by score, each with its parent diff |
| `candidate` | `run`, `candidate` | one candidate and what it changed |
| `steer` | `run`, and `intent`/`note`/`kind` | the intent ledger, and any problems with it |
| `best` | `run` | the winner, and with `state` its whole state dict |
| `adopt` | `run`, `as` | the new package the winner was written to |
| `registry` | `packages`, `target`, `args` | the registry's own JSON for `list`, `show`, `verify`, `smoke`, `add`, `import`, `remove`, `enable`, `disable` |
| `create` | `name`, `brief`, and optionally `extends`, `dataNode`, `task` | the package it produced, and the design review it wrote; with `dataNode` the mode iterates a dataset, with `task` it is bound to a Task that supplies and judges the items |
| `edit` | `mode`, `name`, `brief` | the revision, its before/after scores, and whether it regressed |
| `design` | `name`, `brief`, `check` | the artifact, and whether its check command passed |
| `stop` | `run` | whether the run was asked to stop |

Read the `action` a mode's catalogue entry carries before running it: a scored mode
is run by `start`, and an authoring mode — `create`, `edit`, `design` — by its own
verb, which is also what `start` will tell you if you try it anyway.

In the conversation, ask for it directly:

> Use the refactory tool with action `modes`.

It answers with the catalogue — `annealing_pack/anneal`,
`circle_packing/adaptive`, `loop_mode/loop` — and the call renders as a **run
card** rather than a generic tool row.

## Start a run

> Start a run of `circle_packing/adaptive` with settings
> `{"epochs": 2, "batch_size": 4, "optimizer": "random", "seed": 5}` and
> runs-root `/tmp/gui-tour`.

The call returns immediately. **The run is a separate process**, so it continues
after the call returns and after the session ends — a run that died with the window
would be a run nobody could start and walk away from.

## Watch it while it runs

> Poll `status` until the run is finished, then read the `leaderboard` and the
> `artifacts` with contents.

`status` takes an `after` offset, so a poll asks for what is new rather than
re-reading the whole run. While the run is going the card shows the state, the
best score so far, and the progress tail — one line per candidate evaluated, which
is why it moves within an epoch rather than only between them.

## Author, manage and steer

The same tool reaches the rest of the system, so none of it needs a terminal.

Authoring is model-driven and slow (minutes):

> Ask the CEO: *"what does the plan look like?"* It shows the strategy the run would
> build. Approve it, then:

> Use the refactory tool with action `create`, name `gui_made`, brief "count up to a
> target in deterministic steps and score the count reached", approve
> `["gate_strategy=proceed"]`.

The approval has to be part of the call: the tool runs the authoring mode for minutes
inside that call and cannot pause to ask you, and a headless run refuses a gate it was
not told the answer to. While it runs, the run directory reports its own progress:

> Use the refactory tool with action `status`, run `.factory/create/gui_made`.

The answer names the nodes done, the nodes still due, and what the run has validated so
far. It returns the package it wrote **and the design review** — which approaches it
considered, which it chose, and what the review refused — so the choice is visible
rather than only its result. `edit` revises a mode and returns the before/after
scores, refusing a regression unless you pass `allowRegression`; `design` takes a
`check` command and reports whether the artifact passed it.

A mode you have just created can be used from the same conversation, which `start`
cannot do: `start` is the optimizer.

> Use the refactory tool with action `run`, mode `gui_made/counter`, intent "count
> to five and show me the result".

It runs the mode once on DSH and reports the run directory, how the run ended and
what it recorded, so the answer is the run rather than a promise about one.

Managing packages is the registry's own verbs, passed through:

> Use the refactory tool with action `registry`, packages `disable`, target `annealing_pack`.
> Then action `registry`, packages `list`.

A refusal comes back as the console's own error, so removing a package another one
requires fails naming the dependent rather than breaking it.

Steering is what the person says while *using* a mode, and it never changes the
graph:

> Use the refactory tool with action `steer`, run `…`, intent "make it better, no new
> deps", note "no numpy", kind "constraint".

The card shows the ledger and any problem with it. The ledger is folded into every
agent node, and the instruction names the live file, so a note added while the run
goes reaches the nodes that run afterwards.

And the winner becomes a mode of its own:

> Use the refactory tool with action `best`, run `…`. Then action `adopt`, run `…`,
> as `adopted_demo`.

The adopted package scores what the winner scored and the source package is byte
identical afterwards.

## What renders

The tool projects the console's payload through `output.presentationMeta`, so the
card renders the **run**, not a sentence about the run:

- **state and best score**, while collapsed;
- expanded: the counters, the progress tail, the leaderboard with each candidate's
  diff, and the artifact list.

Nothing about the card re-derives the loop's files. It renders the JSON the console
produced, which is the same JSON `python -m refactory_dsh.console` prints — so a
reader in the browser and a reader in a shell are reading one answer.

## The same thing from a shell

If you would rather not use the GUI, the console is the same surface:

```bash
uv run refactory modes
uv run refactory start circle_packing/adaptive \
  --run-id gui-tour --epochs 2 --batch-size 4 --optimizer random --seed 5 \
  --runs-root /tmp/gui-tour
uv run refactory status --run /tmp/gui-tour/gui-tour --after 0
```

## Changing what the card shows

The card is a **client plugin** claiming the tool's wire name through the
`tool.call.toolview` slot, and its model is a pure function of the logged metadata:

- `packages/client/ui-tool/src/client/tool/models/refactory-card-model.ts` — the
  derivation, and the test surface;
- `packages/client/ui-tool/src/client/tool/toolviews/refactory-row.tsx` — the row.

Because it is a keyed slot, registering under a tool name **replaces** that tool's
row and leaves every other tool alone. A new action needs no card change unless it
needs a new *shape*; the model switches on the action and falls back to the
model-facing summary for anything it does not know.

## What to take away

- The GUI is a **view of the run directory**, not a second implementation of the
  loop. Everything the card shows came from `console.py`.
- A run outlives the call, the tab, and the session.
- The tool's payload is the contract: the card, a shell, and any future reader all
  read the same JSON.

Back to the [tutorial index](index.md).
