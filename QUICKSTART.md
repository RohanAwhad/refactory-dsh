# Quick start

Six commands, in the order you use them. Everything runs from the repository root.

```bash
git clone --recurse-submodules <repo-url> re-factory && cd re-factory
printf 'DEEPSEEK_API_KEY=%s\n' "$YOUR_KEY" > .env
./install.sh
uv run refactory doctor
```

`doctor` is the one check that does not depend on trusting this file: it reports
python, uv, node, the vendored language, the DSH profile and the packages it found.
If anything is `MISS`, it says what to install.

---

## 1. See what you have

```bash
uv run refactory list                    # every package and mode, with one-line descriptions
uv run refactory list modes --json       # the same catalogue as JSON, with each mode's action
```

Two kinds of entry: `action: start` is a mode you run or search; `action: create`,
`edit` or `design` is an authoring mode, run through `ceo` below.

## 2. Create a harness

```bash
uv run refactory ceo "one agent node writes a two-line haiku about the topic in the task; one op counts syllables and checks the count" \
  --mode create --name haiku_pack --single
```

- **`--single`, or `--task`.** A harness is for a *class* of work, so `create` makes you say which you are building before it spends anything. `--single` is this one-off: a harness that runs once, for one input. `--task <module:Class>` names the class — the produced package binds a `data` node to that Task, runs its subgraph once per item the Task lists, and is judged per item, so it is used later on any item of the class. Naming neither is refused, in milliseconds, with both options named. [refactory/docs/datasets.md](refactory/docs/datasets.md) is the whole story.
- **~20–25 minutes**, real model calls. Keep the terminal open.
- It stops at `gate_strategy` and **asks you** to approve the plan — that gate is a
  person's, so the model cannot answer it. Type `proceed` (or pass
  `--approve gate_strategy=proceed` to answer up front, for an unattended run).
- Ask for "an agent node" in the brief and the mode calls a model. Leave it out and
  the mode is deterministic — cheaper to search.
- Evidence lands in `.factory/create_haiku/` (`survey.json`, `strategy.json`,
  `design.md`, `reviews/`, `smoke.json`); the package lands in
  `.factory/packages/haiku_pack/`.

Variants:

```bash
# Design an artifact instead of a package (the default mode), proved by your command
uv run refactory ceo "a python module that converts roman numerals both ways, with tests" \
  --mode design --name roman --check "python -m pytest -q"

# Revise an existing mode; it must name what it revises, and a regression is refused
uv run refactory ceo "ask for fewer steps" --mode edit \
  --revise counting_pack/fixed_step --name counting_revised --approve gate_strategy=proceed
```

## 3. Use it

```bash
uv run refactory run haiku_pack/haiku --intent "a haiku about the ocean"
```

Runs the mode on DSH with your terminal attached, so any gate reaches you. The run
directory holds the work and the facts:

```bash
cat .factory/interactive/haiku_pack-haiku/haiku.md
cat .factory/interactive/haiku_pack-haiku/facts.json
```

The fast check, without a full run — most modes call a model, so say so:

```bash
uv run refactory package smoke haiku_pack/haiku --model --json
```

## 4. Optimize it

```bash
uv run refactory optimize counting_pack/fixed_step \
  --epochs 2 --batch-size 6 --optimizer random --seed 5 --runs-root .factory/my-run
```

It proposes typed edits (`set_knob`, `duplicate`, `insert_after`, …), validates each
before it may cost a run, evaluates what survives, and keeps what improves. The
report has the leaderboard **and** every rejection, so a run that found nothing says
why. `--optimizer random` is free; `autoresearch` asks a model to read the traces.

## 5. Read the run

```bash
uv run refactory status --run .factory/my-run          # where it is
uv run refactory leaderboard --run .factory/my-run     # candidates, each with its diff against its parent
uv run refactory best --run .factory/my-run            # the winner, with the state that scored it
uv run refactory candidate <id> --run .factory/my-run  # one candidate, exactly what it changed
uv run refactory artifacts --run .factory/my-run --contents
```

## 6. Keep the winner, and use it

```bash
uv run refactory adopt --run .factory/my-run --as my_tuned
uv run refactory package smoke my_tuned/fixed_step --json
uv run refactory run my_tuned/fixed_step --intent "count to seven"
```

Steering, at any point while a run is going:

```bash
uv run refactory steer --run .factory/my-run --note "keep the step small" --kind constraint
uv run refactory stop  --run .factory/my-run
```

---

## The GUI

```bash
uv run refactory ui --port 3081 --no-open
```

Open the printed `http://127.0.0.1:3081/?token=…` (the token is per-process; the bare
URL answers `401`). Then: pick the workspace, send one message so a session exists,
press **Open the sidebar** in the conversation header, then the **Modes** tab. Or
just ask in the conversation:

- *"What modes can I run?"*
- *"Start a run of counting_pack/fixed_step with the random optimizer and tell me the best score."*
- *"Show me the leaderboard."*
- *"Keep the winner as a package called my_tuned."*
- *"This run is for making the count land exactly."* (steering)

---

## What costs what

| command | model calls | time |
|---|---|---|
| `list`, `status`, `leaderboard`, `best`, `candidate`, `artifacts` | none | instant |
| `package smoke <mode>` | none (add `--model` for a mode with an agent node) | seconds |
| `package smoke <mode> --model` | the mode's own agent nodes | seconds–minutes |
| `optimize` | only with `--optimizer autoresearch`, or if the mode has agent nodes | seconds–minutes per epoch |
| `run` | the mode's agent nodes, plus the CEO driving the graph | minutes |
| `ceo … --mode create/design/edit` | many | 20–30 minutes |

---

## When something goes wrong

| message | what it means |
|---|---|
| `refactory: … calls a model … add --model to score it` | `package smoke` supplies no model unless asked; that is deliberate, not a failure |
| `"score": null` in `smoke.json` | the mode calls a model and none was reachable — not a zero, an absence |
| `dsh: the graph did not finish (ready: <nodes> still due)` | the CEO ended its turn with nodes left; the run fails rather than reporting a score |
| `refactory: --mode edit needs --revise <package>/<mode>` | an edit must name the mode it revises, or it cannot report what changed |
| `gate <name> is answered by a person and nobody could be asked` | pass `--approve <gate>=<value>`, or run where a person can answer |
| `error: node … is too old` | Node must be `>=22.19` or `>=24`; `nvm install 22 && nvm use 22` |
| `MISS` lines in `doctor` | exactly what is missing, and what to install |

`uv run python -m refactory_dsh.tools.check_recipes` runs every command this
repository documents and reports which ones no longer do what the docs say — the
first thing to run after a pull.
