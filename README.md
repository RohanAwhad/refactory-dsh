# re:factory

**Describe a class of work; a graph of typed nodes builds a harness for it, then searches the harness.**

```bash
git clone <this-repo> && cd <this-repo>
./install.sh
uv run refactory ceo "a mode that drafts a haiku and checks the syllable count" --mode create --single
```

That is the whole product. [tutorials/](refactory/docs/tutorials/index.md) is the same ground as a walkthrough, and [architecture.md](refactory/docs/architecture.md) says which layer owns a behaviour. Everything below is what it is made of.

| layer | what it owns |
|---|---|
| **the mode** | an agentic harness for a class of work: a Python class holding a graph of typed nodes over a `Task`, declared knobs, and a `score()` over the items. The unit you create, run and search. |
| **the outer loop** | the search over a harness: propose typed edits → validate → evaluate → keep what improves, and report what it refused. It is graded on part of the items and audited on the part it was not shown, and a candidate pays a small cost for its size. |
| **the CEO** | one agent that drives a mode's graph node by node, spawning a subagent per model call, and stops at the gates a person owns. |
| **the panel** | the same choices in the browser: pick a mode, press Start, read the run. Its tab is not mounted on the pinned harness yet — see [Known limitations](#known-limitations). |

**A harness is for a class of work.** "Write the build script for any Java source tree" is the shape of everything here — not "write the script for this tree". A `Task` names the class: it lists the items, prepares each one, says what to ask for, and judges each pass, so a harness's score is the share of items that came out right rather than a claim about the one example that was tried. A mode that runs once is the special case, and `create` makes you say which of the two you are building.

**What a harness means does not depend on what runs it.** `{graph, knobs, ports, memory}`, lowered to one IR — `Workflow.to_dict()` — is the whole meaning, and that JSON is the only thing a runtime sees. Which model answers, which tools an agent node may use, which sandbox the work happens in: that is the runtime's business, and the same harness means the same thing on either one. The **walker** runs a graph in-process, which is what a search uses; **DSH** is what ships; running one mode on both and comparing the facts keeps them honest.

---

<a id="run-from-source"></a>
## Install

**Prerequisites:** [uv](https://docs.astral.sh/uv), Node `^22.19` or `>=24` (npm comes with it — the script finds pnpm itself), git, and a DeepSeek API key in `.env` for anything that calls a model.

```bash
git clone <this-repo>
cd <this-repo>
./install.sh
```

`install.sh` does three things, in order:

1. `pnpm install --frozen-lockfile && pnpm run build` — the pnpm workspace: the pinned `@deepseek-ai/dsh` harness plus the six TypeScript packages this repository owns, then `lib/` for those six. This is the step that needs pnpm, and if the machine has none the script puts the pinned version on PATH first: `corepack` when it is there, otherwise `npm install -g pnpm@<pin>`, so Node and npm are enough (opt out with `REFACTORY_SKIP_GLOBAL_PNPM=1`).
2. `uv sync` — **one** Python project at the root: it creates `.venv`, installs the local `refactory-dsh` package in editable mode and puts the `refactory` command on `.venv/bin`, pinned by `uv.lock`.
3. Creates the DSH profile a mode runs on (`dsh plugin --profile graph add …`). A profile is machine state, so this is a once-per-machine step; skip it with `REFACTORY_SKIP_DSH_PROFILE=1`.

Then check the machine, not the docs:

```bash
uv run refactory doctor
```

```
  ok    python >= 3.12        3.13.2
  ok    uv                    /Users/you/.local/bin/uv
  ok    node >= 22.19         /Users/you/.nvm/versions/node/v22.23.2/bin/node
  ok    import refactory_dsh  this package
  ok    DSH profile 'graph'   /Users/you/.dsh/profiles/graph
  ok    packages of modes     16 package(s) under /Users/you/re-factory

refactory doctor: ready — try `refactory list`, or `refactory ceo "an idea" --mode design`
```

The package count is the one line that is yours rather than the tool's: it counts what the checkout ships plus whatever you have created under `.factory/packages`, so a fresh clone reports eleven.

---

<a id="run"></a>
## Use

Four steps, four commands.

###
1. Create a mode

```bash
uv run refactory ceo "one agent drafts a product blurb; one op checks its length and cliches" \
  --mode create --name blurb --single
```

`create` is remote-factory's **create-v2**: a research phase, a strategy phase **you approve**, a build, three reviews in parallel, an audit of their evidence, then registration and a smoke run. It writes a Python package under `.factory/packages/<name>` and registers it.

- `--mode design` (the default) designs a checked **artifact** instead of a package. It needs `--check "<command that proves it works>"`.
- `--mode edit` revises an existing mode and refuses a regression; it needs the mode to revise: `--mode edit --revise <package>/<mode>`.
- A name is optional: without `--name`, your words become the name.
- Ask for a model call in what you say ("one agent drafts …") and the created mode has an `agent` node. Otherwise it is deterministic — which is a feature: an offline mode is one a search can evaluate hundreds of times.
- Naming a `Task` (`--task my_pack.tasks:MyTask`) is the whole directive for a dataset mode: it implies the `data` node, and the validate gate *checks* the package really has one bound to that Task. `--single` says you are building the one-off special case instead; `--data-node` on its own is a requirement on top, not a directive.

###
2. Use it

```bash
uv run refactory run blurb/writer --intent "a blurb about teapots"
```

Runs the mode on **DSH** with your terminal attached, so a gate only a person can answer reaches you. The run's directory holds `facts.json`, `trace.json`, `state.json`, `result.json`, the intent ledger and the lowered graph.

`uv run refactory package smoke <package>/<mode>` is the fast check, and it runs **without** a model on purpose — a mode with an `agent` node cannot be smoked, and says so instead of failing obscurely.

###
3. Optimize it

```bash
uv run refactory optimize blurb/writer --epochs 4 --batch-size 6 --optimizer random --seed 5
```

The **outer loop**: it proposes typed edits to the mode — `set_knob`, `duplicate`, `insert_after`, … — validates every candidate before it may cost a run, evaluates what survives, and keeps what improves. It reports the leaderboard *and* every rejection, so a run that found nothing says why.

`uv run refactory list` shows what it can search. The loop's own options, the ten strategies, seven optimizers, two evaluators and four edit presets are in [the HTML tour](refactory/docs/tutorial.html#loop).

###
4. Use it again

```bash
uv run refactory adopt --run <the run id the last command printed> --as blurb_tuned
uv run refactory run blurb_tuned/writer --intent "another blurb"
```

`adopt` writes the winning state as a new package and leaves its input untouched, so the tuned mode is a mode like any other.

### Read a run

```bash
uv run refactory runs                                       # every run, newest first
uv run refactory status --run <id>                          # where it is
uv run refactory leaderboard --run <id>                     # candidates, with diffs against their parents
uv run refactory candidate <candidate> --run <id>           # one candidate, exactly what it changed
uv run refactory best --run <id>                            # the winner, with the state that scored it
uv run refactory artifacts --run <id> --contents            # what it left on disk
uv run refactory steer --run <id> --note "keep it small"    # what the run is for, while it runs
uv run refactory stop --run <id>
```

### Manage packages

```bash
uv run refactory list                                       # every package and mode
uv run refactory package verify my_pack                     # does it import and validate
uv run refactory package smoke my_pack/<mode> --json        # run it once, offline
uv run refactory package add|import|remove|enable|disable …
```

---

## The command line

Every command is `uv run refactory <verb>`. This is the reference; [QUICKSTART.md](QUICKSTART.md) is the same thing as a walkthrough, and `uv run refactory <verb> --help` is always the current truth for one verb.

<a id="cli-create"></a>
### Create and build

| command | what it does |
|---|---|
| `refactory ceo "<what you want>" --mode create --task <module:Class>` | build a harness for a **class of work**: the Task lists the items and judges each pass, and the produced package binds a `data` node to it (research, a strategy you approve, a build, three parallel reviews, an audit, then register and smoke; 20–30 minutes) |
| `refactory ceo "<what you want>" --mode create --single` | the special case: a harness that runs once, for one input, with no Task binding |
| `refactory ceo "<what you want>" --mode design --check "<command>"` | design a checked artifact instead of a package; `--check` is the command that decides whether it works (this is the default mode) |
| `refactory ceo "<what you want>" --mode edit --revise <package>/<mode>` | revise an existing mode and refuse a regression; `--revise` names what it started from |
| `refactory ceo <package>/<mode>` | run that mode instead of authoring anything |

| option | what it does |
|---|---|
| `--name <name>` | the package or artifact name; without it your words become the name |
| `--task <module:Class>` | create only: the class of work. The produced harness binds a `data` node to that Task and runs its subgraph once per item the Task lists, so it is used later on any item of the class rather than on the one you tried |
| `--single` | create only: build a harness for one run of one thing. One of `--task` or `--single` is required, because a harness built for one hardcoded example is the special case and has to be asked for as one |
| `--check "<command>"` | design only: run inside the artifact directory; the gate reloops until it exits 0 |
| `--revise <package>/<mode>` | edit only: the mode being revised |
| `--model <id>` | the model this run uses, e.g. `deepseek-v4-pro` (default: your settings default) |
| `--provider <route>` | the provider route that model is on, e.g. `deepseek-official` or `openai` |
| `--knob name=value` | set one of the authoring mode's knobs for this run, e.g. `allow_regression=1` to accept a revision that scores lower, or `requires_input=package` to require that the produced mode accepts a package |
| `--approve <gate>=<value>` | state a gate's answer up front, e.g. `gate_strategy=proceed`, for an unattended run |
| `--workdir <dir>` | where the run's evidence lands (default `.factory/interactive/<package>-<mode>`) |

<a id="cli-manage"></a>
### Manage packages of modes

| command | what it does |
|---|---|
| `refactory list` | every package and mode this checkout can run |
| `refactory list modes --json` | the same catalogue as JSON, with each mode's `action`, parameters and guidance |
| `refactory package verify <package>` | import every mode and validate its graph; an absent tool is not a finding |
| `refactory package show <package>/<mode>` | one mode, described |
| `refactory package tools <package>` | the tools it declares, and whether this machine has them |
| `refactory package add <dir>` | install a package from a directory (`--as` to rename) |
| `refactory package import <dir-or-git-url>` | import one, recording where it came from (`--ref`, `--as`) |
| `refactory package remove <package>` | remove it |
| `refactory package enable/disable <package>` | include it, or leave it out of the catalogue |

<a id="cli-run"></a>
### Run and use a mode

| command | what it does |
|---|---|
| `refactory run <package>/<mode> --intent "<what it is for>"` | run the mode on DSH with your terminal attached, so a gate reaches you |
| `refactory run <package>/<mode> --knob name=value` | set one of the mode's knobs for this run |
| `refactory run <package>/<mode> --source <dir>` | read the items from here instead of the source the mode declares, so a harness built for a class of work is pointed at items it has never seen; `--source-format directory\|jsonl\|csv` says how to read it |
| `refactory run <package>/<mode> --workdir <dir>` | where the run's evidence lands |
| `refactory run <package>/<mode> --timeout <seconds>` | how long to wait at a gate; `0` waits for you |
| `refactory run <package>/<mode> --model <id> [--provider <route>]` | the model this run uses |
| `refactory run <package>/<mode> --approve <gate>=<value>` | answer a gate before the run instead of at it |
| `refactory package smoke <package>/<mode>` | run it once, in a sandbox, without a model (free) |
| `refactory package smoke <package>/<mode> --model` | score a mode whose graph has an `agent` node, by supplying a model |

<a id="cli-optimize"></a>
### Optimize it

`refactory optimize <package>/<mode>` searches a mode's architecture and knobs: propose typed edits, validate, evaluate, keep what improves.

| option | what it does | default |
|---|---|---|
| `--epochs <n>` | rounds of propose → validate → evaluate → select | 2 |
| `--batch-size <n>` | candidates proposed per epoch | 4 |
| `--optimizer <name>` | how candidates are proposed: `random`, `hill_climb`, `evolutionary`, `archive`, `islands`, `map_elites`, `autoresearch` | `autoresearch` |
| `--strategy <name>` | which outer loop drives the search: `default`, `best_of_n`, `reflective`, `reflective_sampler`, `tree_search`, `staged_curriculum`, `staged_v1`, `adaptive_meta`, `autoscientists`, `research_then_optimize` | the harness's own (`default`) |
| `--evaluator walker\|dsh` | which runtime scores a candidate: fast in-process, or the one that ships | `walker` |
| `--preset <name>` | which edits are allowed: `broad`, `structure`, `conservative`, `knob_only` | `broad` |
| `--seed <n>` | reproduce a search exactly | 0 |
| `--elite <n>` | how many parents each round proposes against | 3 |
| `--max-evaluations <n>` | a hard ceiling on evaluated candidates | 64 |
| `--holdout <fraction>` | the share of a Task's items kept out of the search and used to audit the winner afterwards; one item cannot be split, which the audit records | 0.3 |
| `--parsimony <cost>` | what each node of a candidate's graph costs its score: the search compares `raw_score − parsimony × nodes` | 0.01 |
| `--attempts <n>` | retries per batch after a refusal | 3 |
| `--runs-root <dir>` | the run directory itself; read it back with `--run <this path>` | `.factory/loop` |
| `--intent "<words>"` | what the run is for; seeds the ledger the agents read | empty |
| `--model <name>` | the model the reasoning optimizer asks (not the mode's own agents) | the configured route |
| `--provider <route>` | the provider route that model is on | your settings default |

**Every candidate is searched on the train part of the items, and the winner is then re-evaluated on the held-out part.** The part travels in the run directory (`subset.json` beside each candidate), not in a knob, so a search cannot choose its own exam; the audit lands in `<run root>/holdout.json` and in the report as `{"train", "holdout", "gap", "overfit", "threshold", "instances"}`, with `overfit` true when `train − holdout` exceeds 0.15. The score the search compares has the size cost taken off it, and the unpenalised number is recorded beside it as `raw_score`, so a harness that won on merit can be told from one that won on size. `refactory best` reports the audit, and `refactory adopt` refuses an overfit winner unless you pass `--allow-overfit`.

<a id="cli-read"></a>
### Read the run, and steer it

| command | what it does |
|---|---|
| `refactory runs` | every run under the root, newest first |
| `refactory status --run <id>` | where it is, and the events since an offset |
| `refactory leaderboard --run <id>` | candidates by score, each with its diff against its parent |
| `refactory candidate <id> --run <id>` | one candidate, exactly what it changed |
| `refactory best --run <id>` | the winner, with the state that scored it and the held-out audit of whether it works beyond its training items |
| `refactory artifacts --run <id> [--contents]` | what it left on disk |
| `refactory adopt --run <id> --as <package>` | write the winner as a new package, input untouched; refused when the audit says the winner is overfit |
| `refactory adopt --run <id> --as <package> --allow-overfit` | adopt it anyway, with the audit recorded beside the package |
| `refactory steer --run <id> --note "<words>" [--kind info\|correction\|constraint]` | add what you said to the run's intent ledger |
| `refactory steer --run <id> --verdict <gate>=<value>` | record a gate's answer, so a resume replays it |
| `refactory stop --run <id>` | ask a run to stop |

**Steering is context, not structure.** A note goes into the run's `intent-ledger.md`, and every agent node's prompt names that file and tells the node to read it before acting — so `--kind constraint` ("must be respected"), `info` and `correction` reach the nodes that run *after* the note, in a `run` and in a search alike. The mode's graph never changes; searching the graph is `optimize`'s job. A `user` gate is the other half: the runtime asks you, records your answer as a `verdict` entry, and a resumed run replays it rather than asking twice.

<a id="cli-other"></a>
### Everything else

| command | what it does |
|---|---|
| `refactory doctor` | whether this machine can run a mode, and what to install if not |
| `refactory ui [--port <n>] [--no-open]` | the Web GUI (below) |

## The GUI: run modes from the browser

<a id="gui-launch"></a>
### Launch it

```bash
uv run refactory ui --port 3081 --no-open
```

It prints one line — open that URL, token and all:

```
dsh web: http://127.0.0.1:3081/?token=<per-process token>
```

The token is per-process; the bare `http://127.0.0.1:3081/` answers `401`. Leave off `--no-open` and your browser opens for you.

<a id="gui-first-run"></a>
### First run

1. **Pick the workspace.** Choose this checkout. The app remembers it next time.
2. **Send one message.** Any message will do — "hello". A session has to exist before the Sidebar's expand control appears in the header, which is the step that confuses people.
3. **Open the panel:** press the expand control in the conversation header (its tooltip is `Open the sidebar`), then the **Modes** tab.

**The Modes tab is not mounted on the pinned harness.** This repository no longer carries the web bundle patch that added the tab's rows to the `web` profile, so the tab described below is absent and the conversation surface at [Running and authoring from the conversation](#gui-ceo) is what a run uses. Issue #85 carries the bundle patch, the console's generated Remote face, and the pinned headless model selection. Every console verb the tab calls is reachable from `uv run refactory`.

<a id="gui-panel"></a>
### Running a mode from the panel

| control | what it does |
|---|---|
| **Mode** | every mode the catalogue lists, each with the verb that runs it |
| **Outer loop**, **Optimizer**, **Evaluator** | the three level-two choices, each with the console's own description; `Use the default` leaves the console's default in place |
| **The mode's knobs** | one field per knob the chosen mode declares, shaped by the kind it declares — a knob with declared values is a choice, a number is bounded by its declared range, only an open text knob is typed. What the fields show is what the run sends, including a knob left at its default, because a mode's inputs *are* its knobs |
| **Start** | starts the chosen mode in its own process (the search), and the panel names the run it began |
| **Run once** | uses the mode once with those knob values and reports what happened: the run's own status word, the nodes still due, and the facts it left. A run stopped at a gate a person owns says so — the panel does not answer gates |
| **Run status** | state, best score and evaluations; **Refresh status** reads the next frame, **Stop the run** asks it to stop |
| **Packages of modes** | per package: **Enable**, **Disable**, **Remove**, and **Import a package** from a directory or a git URL |

The panel reads the same console the CLI and the model do, so a run started here is the run `refactory status --run <id>` reads back, with the same artifacts.

<a id="gui-ceo"></a>
### Running and authoring from the conversation

Ask in words; the CEO calls the `refactory` tool, which is the same console. Its coding tools are disabled on purpose: it can choose, start, read, steer and author, and cannot wander off and implement something itself.

| you say | what happens |
|---|---|
| *"What modes can I run?"* | the catalogue, with each mode's parameters |
| *"Start a run of counting_pack/fixed_step with the random optimizer."* | a run starts; the CEO answers with the run id |
| *"Where is that run? Show me the best score."* | `status` then `best`, answered in words |
| *"Show me the leaderboard and what the winner changed."* | `leaderboard`, then `candidate` for the winning row |
| *"Keep the winner as a package called my_tuned."* | `adopt`, input untouched |
| *"This run is for making the count land exactly; keep the step small."* | `steer`: the note reaches the run's intent ledger |
| *"Create a mode that drafts a blurb with an agent and checks it."* | `create`, the same authoring mode as `ceo --mode create`. It needs to know which of the two things it is building: a `task` names the class of work, `single: true` is the one-off special case, and neither is refused |
| *"Where is that creation up to?"* | `status` on the run directory — `.factory/interactive/<package>-<mode>` for an authoring run started through `ceo`, `.factory/create/<name>` for the `create` entry point — which reports the run's own node-by-node progress |
| *"Run that mode once and show me."* | `run`, the CLI's `refactory run`: it uses the mode on DSH and reports the run directory and what it recorded |
| *"That creation died on a network blip — pick it up."* | `resume` with the run directory: it continues at the node that failed, keeping every node that finished |
| *"Show me the packages, then disable annealing_pack."* | `registry` with `list`, then `disable` |

<a id="gui-gates"></a>
### A gate a person owns is still a person's

An authoring call takes `inputs` (what the brief says the mode operates on), `sample` (knob values such as the real input, so the smoke exercises that path instead of the built-in fixture) and `resume` (a run directory to continue). The run is refused when the artifact it produced is not the format it claims: a `.whl` without `METADATA` and `WHEEL` beside its `RECORD`, or an archive that cannot be opened.

If a mode reaches a `user` gate (create-v2's plan approval is one), the runtime asks **you**: the terminal in `refactory run`, a dialog in the GUI. The CEO cannot answer it, and neither can the model. To run unattended, state the answer up front: `--approve gate_strategy=proceed` — and note that an authoring call from the conversation runs for minutes *inside* the tool call, so the approval has to be given before it starts rather than while it waits.

[The GUI tutorial](refactory/docs/tutorials/05-drive-it-from-the-gui.md) walks the conversation surface, and [the panel tutorial](refactory/docs/tutorials/07-pick-and-watch-from-the-panel.md) covers the widget.

## Models

<a id="models-list"></a>
### Where the list comes from

The adapter's catalogue is **advisory**: it is what pickers offer, not what the route accepts. The DeepSeek route ships `deepseek-v4-flash`, `deepseek-v4-pro` and `deepseek-v4-flash-vision-exp`, and it will send any id you name — so a model that is not listed still works, it just is not offered. Which routes exist and which models each offers is your **settings document**, `~/.dsh/settings.yaml`, which is hot-reloaded: nothing needs a restart.

<a id="models-gui"></a>
### In the GUI

**Settings → Models.** Add a provider, paste its API key, optionally a base URL, and list the models — or press **Fetch available models** and pick from what the provider reports. **Add a custom provider** takes an id, a display name, one of three wire protocols (`openai-completions`, `openai-responses`, `anthropic-messages`) and a base URL, which is how a local server (Ollama, LM Studio, vLLM) is reached. The page writes the settings document and the credential store for you; the composer's model picker then offers the new models.

<a id="models-terminal"></a>
### In the terminal

The same file, by hand:

```yaml
# ~/.dsh/settings.yaml
agent-default-model:
  provider: deepseek-official
  model: deepseek-v4-pro          # the default for every run that names none

llm-pi-ai:
  providers:
    openai:
      apiKeyEnv: OPENAI_API_KEY   # the *name* of the variable, never the key
    anthropic:
      apiKeyEnv: ANTHROPIC_API_KEY
      models:
        - id: claude-sonnet-4-5
          contextWindow: 200000
    ollama:                       # a local OpenAI-compatible server
      displayName: Ollama
      api: openai-completions
      baseURL: http://127.0.0.1:11434/v1
      models:
        - id: qwen3-coder
          contextWindow: 262144
```

Keys are read from the environment (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, …, including a `.env` at the checkout root) or from the managed store at `~/.dsh/.credentials.yaml` that the Models page writes. A `llm-deepseek:` section overrides the direct DeepSeek route the same way: `models:`, `baseURL:`, `apiKeyEnv:`, `thinking:`, `reasoningEffort:`.

A local OpenAI-compatible server works through the same route, and still needs *a* key — pi-ai's OpenAI protocol asks for one, so name a dummy variable and export any value (`apiKeyEnv: LOCAL_TEST_KEY`, then `LOCAL_TEST_KEY=dummy`), or set an `Authorization` header. This was verified by running it: an `openai-completions` route pointed at a local server answered a headless run, and the request the server logged named the model the run had chosen.

<a id="models-one-run"></a>
### One run, one model

```bash
uv run refactory run counting_pack/fixed_step --model deepseek-v4-pro
uv run refactory ceo "an idea" --mode create --single --model deepseek-v4-pro
uv run refactory optimize circle_packing/adaptive --provider deepseek-official --model deepseek-v4-pro
```

`--model` names the id and `--provider` the route; both fall back to `agent-default-model` in your settings, and neither writes anything back, so choosing a model for one run does not change the next. A route that does not exist is a loud failure (`NO_ADAPTER: no adapter registered for provider …`), not a quiet return to the default. The same two flags exist one level down, on `dsh --profile headless|graph`, which is what `refactory` calls.

## Documentation

| document | what it answers |
|---|---|
| **[`refactory/docs/tutorial.html`](refactory/docs/tutorial.html)** | every mode, both v2 pipelines, the outer loop and all its options, the GUI — as one generated page |
| [tutorials/](refactory/docs/tutorials/index.md) | how do I do the next thing, in order |
| [architecture.md](refactory/docs/architecture.md) | what the layers are, and which one owns a behaviour |
| [datasets.md](refactory/docs/datasets.md) | a mode that iterates items, and the Task that supplies and judges them |
| [api/](refactory/docs/api/index.md) | what every public name is for |
| [hardening.md](refactory/docs/hardening.md) | which guarantees are enforced, and by what |
| [packing-tools.md](refactory/docs/packing-tools.md) | how a mode declares a tool it needs installed |
| [contributing.md](refactory/docs/contributing.md) | how to change this repo |

The HTML tour, the API reference and the config catalog are **generated from the code**, and two gates keep the commands honest without trusting the prose: the tour's commands are recipes that `uv run python -m refactory_dsh.tools.check_recipes` executes, and every flag in this README's option tables is checked against the real parser (`refactory/tests/test_cli.py`), so a documented flag is one the command accepts and a demonstrated command is one that ran. Neither gate proves a command does what the sentence around it says; that is what the tutorials' recorded output is for.

---

## What ships

**The modes.** `default_pack` is the authoring package: `create` (create-v2), `design` (design-v2) and `edit`, each with a plan-approval gate a person answers. The rest are fixtures — small, deterministic and offline on purpose, so the whole system can be tested and searched without a provider: `circle_packing`, `counting_pack`, `sympy_pack`, `lean_pack`, `review_pack`, `annealing_pack`, `loop_mode`, and `dataset_probe` (a Task-bound data node).

**The loop.** Ten strategies (ports of SRF's own loops: `default`, `best_of_n`, `reflective`, `tree_search`, `staged_curriculum`, …), seven optimizers (`random`, `hill_climb`, `evolutionary`, `archive`, `islands`, `map_elites`, `autoresearch`) and two evaluators (`walker`, `dsh`).

**The panel.** `packages/refactory/refactory-console` is the Host service over the same console the tool reads, and `packages/client/ui-refactory` is the right-Sidebar tab over it. The tab's Remote namespace is stated in the console's `types.ts`; the harness's typert generator emits `./typert` and `./remote` only when its own protocol package is a project in the same workspace, which a pinned install does not have.

---

## Layout

```
packages/                    the six TypeScript packages this repository owns
  bundle/graph/                the dsh bundle a mode runs on
  workflow/workflow-spine/     the per-session Spine and its wf_* tools
  refactory/tool-refactory/    the model-facing refactory tool
  refactory/refactory-console/ the Web picker's Host service
  client/ui-refactory/         the right-Sidebar picker tab
  client/ui-brand-refactory/   the re:factory brand occupants
refactory/                   the glue — the modes, the loop, the console
  refactory_dsh/               cli.py is the entry point; nn/ is the language; tools/ generates docs
  refactory_packages/          the packages of modes
  docs/                        the HTML tour, tutorials, architecture, API reference
types/                       the client build-environment ambient declaration
package.json                 the workspace root: the pinned harness, the six packages, the scripts
pnpm-workspace.yaml          which directories are workspace packages, and the cordis pin
tsconfig.base.json           shared compiler settings and the six packages' source paths
tsconfig.host.json           the Host typecheck aggregate (the typert generator finds the root here)
tsconfig.client.json         the Client typecheck aggregate
tsdown.config.ts             the host and client bundle passes
tsdown.client.ts             the UI plugin client-bundle preset
vitest.config.ts             the six packages' test run
pyproject.toml               the one Python project (`uv sync`, `uv run refactory …`)
uv.lock                      the pinned environment
install.sh                   one-command install
refactory-dsh                the GUI launcher (what `refactory ui` runs)
```

**The harness is a dependency, not a copy.** `package.json` declares `@deepseek-ai/dsh` at one exact version, `pnpm-workspace.yaml` lists the six directories under `packages/`, and every other harness name resolves through `node_modules` to that pin. The `pnpm.overrides` table pins cordis and schemastery to the versions the pinned tree carries, so the install resolves to one copy of each.

A mode is a Python class in `refactory_packages/<package>/`, not a `graph.json`; the decision record is [pythonic-modes.md](refactory/docs/plans/pythonic-modes.md).

---

## Development

```bash
uv run python -m pytest refactory/tests -q          # the glue: 800+ tests, offline
pnpm run build                                       # typecheck, then the host and client bundle passes
pnpm run test                                        # the six packages' vitest suites
pnpm run test:gui                                    # the two client packages only
pnpm run typecheck && pnpm run lint                  # TypeScript
uv run python -m refactory_dsh.tools.check_recipes   # the documented commands that need no model
```

The integration tier makes real model calls:

```bash
uv run python -m pytest refactory/tests -q --run-integration   # one mode on both runtimes
uv run python -m refactory_dsh.tools.check_recipes --tier model
```

### Rebuilding the graph profile after a harness bump

A profile is machine state, so a new harness version needs the profile rebuilt rather than migrated: `pnpm exec dsh plugin --profile graph add packages/bundle/graph`. `install.sh` does this when the profile does not already carry the bundle.

---

## Known limitations

- **A mode that calls a model costs money to run.** Nothing else does: the fixtures and the walker are offline, and the loop's `walker` evaluator never calls a model unless the mode itself has `agent` nodes.
- **`trimul` (GPU task) needs `numpy`** — not in the project's dependencies; the math tasks do not need it.
- **The Web GUI does not carry the Modes tab.** `refactory ui` starts the pinned harness's own `web` profile, which composes the harness's client packages; it does not compose `@deepseek-ai/dsh-client-ui-refactory` or the brand package, so the right-Sidebar tab is absent. Both packages build `lib/client.js`, and a bundle that adds them to the web composition is what puts the tab back. Every console verb the tab calls is reachable from `uv run refactory`.
- **Ships as a repo + `install.sh`**, not a single binary: bundling Node and Python into one per-platform executable was considered and deferred.
