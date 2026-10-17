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
uv run refactory ui --port 3081
```

Open the exact URL printed by the server, including its token. Choose this checkout as the workspace, then create a new session. A blank session can open the right sidebar's **Modes** tab; no introductory model message is needed.

In **Settings → Models**, configure a provider and save its API key with **Edit → API key → Apply**. Use the managed credential store for nested authoring and validation. An exported key or a repository `.env` alone does not survive nested execution's secret scrub. See [model setup](models.md#credentials-for-nested-execution).

```bash
uv run refactory doctor
uv run refactory runtime
```

Doctor checks local configuration, not authentication or quota. Runtime shows the nested defaults. Select a configured model in chat or use the CLI's `--provider` and `--model` options.

New coding workers default to **Skip permission checks**, including full native sandbox bypass for DSH/Codex. Their tools can access what your OS account can access. To retain backend checks, choose **Native permission policy** in the Modes panel or pass `--permission-mode default` in the CLI. Existing saved profiles and runs preserve their policy. [Worker permissions](models.md#model-provider-versus-coding-agent)

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
  --knob target=20 --knob step=2 --intent "Count to twenty and explain the recorded result."
```

The knobs supply the operation's inputs; prose intent does not change deterministic inputs automatically. The conversational CEO uses a model even when the underlying mode is deterministic.

To author a small artifact:

```bash
uv run refactory ceo \
  "Build wordcount.py to count lines, words and characters in a UTF-8 file. Include sample.txt and verify the counts." \
  --mode design --name wordcount --check "python wordcount.py sample.txt"
```

Review the proposed design when asked. A command that merely prints counts does not by itself prove those counts correct; read the check evidence and review findings. Authoring can take substantial time because the current default modes include research and review stages.

To author a reusable mode, follow [Create and Edit](tutorials/03-author-with-a-model.md). Choose a [Task](datasets.md) for repeated items, or `--single` explicitly for a one-off harness.

## 5. Stay involved

In GUI chat, ask: “Open counting_pack/fixed_step with target=20 and step=2. Walk it one node at a time.” Ask to pause before a node, correct an instruction, or answer a gate. In the Modes panel, choose a mode and set its knobs: **Run once** executes it, including full and fast authoring modes; **Start** optimizes a scored mode. Chat supports stepwise authoring and user gates. Fast authoring uses smaller graphs with focused QA and bounded repair; see its [tested scope and resource choices](budgets-fast-modes.md#acceptance-status).

Attach the run's exact directory with **Existing run directory → Read this run** to follow persisted live activity, worker receipts and pending questions. Reading a run does not execute it. Use **resume** explicitly for a supported saved boundary after its owner stops; completed work and consumed resources are retained. [Interface guide](interfaces.md) explains what is interactive, what updates automatically, and when controls take effect.

Next: [command reference](cli.md) · [optimization tutorial](tutorials/02-optimize-it.md) · [write your own mode](tutorials/01-write-a-mode.md).
