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
| `modes` | — | every mode the checkout can start, with its parameters |
| `runs` | — | every run under the run root, newest first |
| `start` | `mode`, `settings` | the run id, root, and pid |
| `status` | `run`, `after` | the state, counters, best score, and new events |
| `artifacts` | `run`, `contents` | what the run left, optionally with contents |
| `leaderboard` | `run` | the candidates by score, each with its parent diff |
| `candidate` | `run`, `candidate` | one candidate and what it changed |
| `stop` | `run` | whether the run was asked to stop |

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
.venv/bin/python -m refactory_dsh.console modes
.venv/bin/python -m refactory_dsh.console start circle_packing/adaptive \
  --run-id gui-tour --epochs 2 --batch-size 4 --optimizer random --seed 5 \
  --runs-root /tmp/gui-tour
.venv/bin/python -m refactory_dsh.console status --run /tmp/gui-tour/gui-tour --after 0
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

Back to the [tutorial index](README.md).
