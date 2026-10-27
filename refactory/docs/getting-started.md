# Get started

Start with a small offline search, then connect a model for authoring and interactive work. Run commands from the repository root.

## 1. Install

You need Git, [Node.js](https://nodejs.org/) `^22.19` or `>=24`, and [uv](https://docs.astral.sh/uv/). The installer sets up the pinned Node dependencies, Python environment, UI bundles and DSH graph profile.

```bash
git clone https://github.com/boston-harness-group/refactory-dsh.git
cd refactory-dsh
./install.sh
uv run refactory list
```

The repository must be accessible to your GitHub account. `uv` manages the Python environment. Run `./install.sh` again after every pull, then restart any running GUI; see [Updating](../../README.md#updating).

> **Multiple checkouts.** The DSH graph profile links to whichever checkout
> last ran `install.sh`. A run from a different checkout (a second clone, a
> worktree, a contributor's branch) will refuse and name the command that
> relinks the profile. Run `install.sh` from the checkout you want to use, or
> run the named `dsh plugin --profile graph add` command it prints.
>
> **Pinned runs.** When a run is resumed after the driver moved to a new
> commit, a pin is created: a `graph-pin-<commit12>` profile in your own
> `$DSH_HOME` (`~/.dsh` unless you set it), pointing to a prepared worktree of
> the original commit. Your credentials stay in `$DSH_HOME`, which is unchanged.
> `refactory pin list` shows active pins; `refactory pin prune` removes unused ones.

### Builds and restarts

A workspace-wide build stamp covers all TypeScript packages the runtime loads.
At launch, `require_build()` compares the stamp against the current source. When
the build is stale:

- **CLI** (`refactory ceo`, `refactory optimize`): the launcher automatically runs
  `pnpm run build` before starting the run, unless a live run uses the same
  checkout — in that case it warns and continues on the existing build.  When the
  rebuild itself fails (e.g. a syntax error in a TS source), the launch continues
  with a warning naming the stale packages and the command to rebuild manually.
- **GUI** (`refactory ui`): a stale build logs a warning naming the stale packages
  and the command to rebuild (`pnpm run build`).  The GUI does not rebuild `lib/`
  underneath itself.

After every `git pull`, run `./install.sh` and restart any running GUI.

## 2. Try a search without a model

The shipped circle-packing example is deterministic. These options keep proposal generation and evaluation offline:

```bash
uv run refactory optimize circle_packing/adaptive \
  --optimizer random --preset knob_only --evaluator walker \
  --epochs 2 --batch-size 4 --max-evaluations 8 --seed 5 \
  --runs-root .factory/loop/first-search
uv run refactory leaderboard --run .factory/loop/first-search
uv run refactory best --run .factory/loop/first-search
```

Read the candidate changes, raw score and adjusted score. The latter includes the graph-size penalty. This mode has no dataset split, so its holdout is explicitly not applicable. Use a fresh run directory for a separate experiment.

Keep the winner as a new package:

```bash
uv run refactory adopt --run .factory/loop/first-search --as first_tuned
uv run refactory package verify first_tuned
uv run refactory package smoke first_tuned/adaptive
```

Use another package name if `first_tuned` already exists. Adoption preserves the original package.

## 3. Open the GUI and configure a model

```bash
uv run refactory ui --port 3081 --no-open
```

Open the exact URL printed by the server, including its token (`http://127.0.0.1:3081/?token=…`); `--no-open` only stops it from opening a browser itself. Choose this checkout as the workspace, then create a new session. A blank session can open the right sidebar's **Modes** tab; no introductory model message is needed.

In **Settings → Models**, configure a provider and save its API key with **Edit → API key → Apply**. Use the managed credential store for nested authoring and validation. An exported key or a repository `.env` alone does not survive nested execution's secret scrub. See [model setup](models.md#credentials-for-nested-execution).

```bash
uv run refactory doctor
uv run refactory runtime
```

Doctor checks the chosen provider's local configuration, not authentication or quota. Choosing a model in **Settings → Models** (or with `uv run refactory model set --provider <provider> --model <model>`) is the one choice every terminal run uses, and a GUI session starts on it; a terminal run with none uses the only provider with a key and says so, asks at a terminal when several have keys, or is refused ([First run](models.md#first-run)). CLI runs choose their model with `--provider` and `--model` for one run instead; `uv run refactory runtime` shows the route a run would use and where it came from.

New coding workers default to **Skip permission checks**, including full native sandbox bypass for DSH/Codex. Their tools can access what your OS account can access. To retain backend checks, choose **Native permission policy** in the Modes panel or pass `--permission-mode default` in the CLI. Existing saved profiles and runs preserve their policy. [Worker permissions](models.md#model-provider-versus-coding-agent)

## Targets, uncommitted changes and worktrees

A Design run works on the repository its `target` knob names (or a new project when empty). Pass `--target /path/to/repo` in the CLI, or pick one from the GUI's target selector, which lists the same projects as `refactory projects`.

To start a run from your uncommitted changes, set `--knob include_uncommitted=true`. The run copies your modified, staged and untracked files into its worktree and commits them as its own base. Your checkout is never changed.

**Which modes get a worktree:** Design and design_fast use a git worktree. Create, create_fast, edit and edit_fast stage their package in a per-run directory (not a git worktree, because `.factory/` is gitignored). Loop and optimize write only into their run directory and need no isolation.

## 4. Choose the output you want

| Your goal | Use | Result |
|---|---|---|
| Build a reusable workflow | Create | A registered package of modes |
| Revise an existing workflow | Edit | A new package with provenance and comparison status |
| Build software or another artifact | Design | An artifact checked against the supplied command and brief |
| Use a workflow you already have | Run its mode | Its declared outputs and execution evidence |
| Improve a scored workflow | Optimize | Candidate comparisons, audit and a winner eligible for adoption |

Start an interactive terminal run:

```bash
uv run refactory ceo counting_pack/fixed_step \
  --knob target=8 --knob step=2 --intent "Count to eight and explain the recorded result."
```

The knobs supply the operation's inputs; prose intent does not change deterministic inputs automatically. The conversational CEO uses a model even when the underlying mode is deterministic.

To author a small artifact:

```bash
uv run refactory ceo \
  "Build wordcount.py, which prints a file's line, word and character counts as wc -l -w -m counts them, as three numbers separated by spaces, and a plain-ASCII sample.txt." \
  --mode design --name wordcount \
  --check 'test "$(python wordcount.py sample.txt)" = "$(wc -l -w -m < sample.txt | xargs)"'
```

Review the proposed design when asked. The check compares the script's counts with `wc`, so it fails when they are wrong, not only when the script crashes; a check that only runs the script would pass on wrong counts. Still read the check evidence and review findings.

Once the reviews pass, the run opens a pull request, which needs `gh` logged in (`gh auth login`). Without a login the files stay on the run's branch, which the run names (a new project's `main` stays empty), and `delivery.json` in the run directory lists the commands that publish them. Authoring can take substantial time because the current default modes include research and review stages.

To author a reusable mode, follow [Create and Edit](tutorials/03-author-with-a-model.md). Choose a [Task](datasets.md) for repeated items, or `--single` explicitly for a one-off harness.

## 5. Stay involved

In GUI chat, ask: “Open counting_pack/fixed_step with target=8 and step=2. Walk it one node at a time.” Ask to pause before a node, correct an instruction, or answer a gate. In the Modes panel, choose a mode and set its knobs: **Run once** executes it, including full and fast authoring modes; **Start** optimizes a scored mode. Chat supports stepwise authoring and user gates. Fast authoring uses smaller graphs with focused QA and bounded repair; see its [tested scope and resource choices](budgets-fast-modes.md#acceptance-status).

Attach the run's exact directory with **Existing run directory → Read this run** to follow persisted live activity, worker receipts and pending questions. Reading a run does not execute it. Use **resume** explicitly for a supported saved boundary after its owner stops; completed work and consumed resources are retained. [Interface guide](interfaces.md) explains what is interactive, what updates automatically, and when controls take effect.

Next: [command reference](cli.md) · [optimization tutorial](tutorials/02-optimize-it.md) · [write your own mode](tutorials/01-write-a-mode.md).
