# refactory-dsh

Modes are Python classes. Their architecture and parameters are a serializable state dict. Optimizers propose typed updates to that state, and every proposal is validated before it can cost an inner run.

A mode is an **agentic harness for a class of work**: a graph of typed nodes over a `Task`, which lists the items, prepares each one, says what to ask for and judges each pass — so the mode's `score()` is a claim about the class rather than about the one example that was tried. `create` produces that harness, naming the Task is what makes the per-item binding real, and `--single` is the explicit special case. [docs/datasets.md](docs/datasets.md) is the whole story.

The design follows PyTorch deliberately, because the analogy is the load-bearing part: a mode is an `nn.Module`, its `state_dict()` carries the parameters *and* the architecture, a knob is an `nn.Parameter`, and an optimizer is a `torch.optim.Optimizer` that may only write what the mode declares writable. `score()` and the mode's invariants are methods, so no proposal can reach them.

- **DSH** is the runtime and the model client (this repo is a DSH fork).
- **`refactory/refactory_dsh/nn/`** is the library: modes, graphs, knobs, the edit algebra, the validator, the executor.
- **`refactory/refactory_dsh/optim/`** is the optimizer layer.
- **`refactory/refactory_packages/`** holds packages of modes. The library knows no package by name; it discovers, lists and imports them.

## Layout

```
apps/                       DSH apps (CLI)
packages/                   DSH packages (workflow-spine, science, brand, …)
refactory/                  the glue
  refactory_dsh/nn/           the mode library (torch.nn analogue)
  refactory_dsh/optim/        the optimizers (torch.optim analogue)
  refactory_dsh/loop.py       init → forward → loss → backward → optimizer → step
                              → scheduler → report
  refactory_dsh/registry.py   package discovery, enable/disable, catalogue
  refactory_dsh/create.py     create-mode: a package from a brief
  refactory_packages/<pkg>/   the packages of modes (torchvision analogue)
  docs/architecture.md        the system as built, and why it looks like that
  docs/tutorials/             seven walkthroughs, run against this checkout
  docs/packing-tools.md       packing a tool that is not Python, such as Lean
  docs/plans/pythonic-modes.md  the design and its acceptance gates
install.sh                  one-command install
refactory-dsh               the launcher (opens DSH web)
```

## Install

```bash
git clone https://github.com/akashgit/refactory-dsh.git
cd refactory-dsh
./install.sh
```

Follow the [repository quickstart](../README.md#quickstart) for prerequisites and
the offline smoke check. Before model-backed work, save a provider key through
**Settings → Models**; see [model setup](docs/models.md#credentials-for-nested-execution).
`uv run refactory doctor` checks local configuration without making a model call.

## Run

List the packages and the modes they own:

```bash
uv run refactory package list
uv run refactory package show circle_packing/adaptive
```

Try a small offline search first. Restricting this shipped example to knob edits
keeps it deterministic and avoids model calls:

```bash
uv run refactory optimize circle_packing/adaptive \
    --epochs 2 --batch-size 4 --optimizer random --preset knob_only --seed 5

# what came out of it
cat .factory/loop/report.md
```

For model-proposed edits, use `--optimizer autoresearch` after model setup.
Both optimizers validate proposals before evaluating them. Read the raw score and
held-out audit separately from the score's graph-size penalty.

Create a package of modes from a brief. The authoring workflow researches, designs,
emits, reviews, validates and smoke-runs the package. A class-of-work harness uses
`--task <module:Class>`; this example uses the explicit one-off `--single` form.
The terminal CEO asks you to review the strategy before emission:

```bash
uv run refactory ceo "what the package should do" \
    --mode create --name my_pkg --single
```

Run an existing mode with `uv run refactory ceo <package>/<mode>`. The live CEO
accepts your steering between turns. See the [command guide](../README.md#the-command-line)
for design, edit, optimization, adoption and unattended gate approvals.

## GUI

```bash
uv run refactory ui --port 3081
```

Select this checkout as the workspace and start a chat. Ask the CEO to open a mode,
for example, `Open counting_pack/fixed_step and drive it one node at a time`.
The chat drives the workflow through its public tools and presents user gates.

Open the conversation's right sidebar and choose **Modes** to select, run and
optimize modes, inspect progress, or attach an existing run. The panel displays the
project its console uses; changing the chat workspace does not retarget that console.
The install applies the GUI patches that provide these surfaces.
See the [chat tutorial](docs/tutorials/05-drive-it-from-the-gui.md) and
[panel tutorial](docs/tutorials/07-pick-and-watch-from-the-panel.md).

## Test

```bash
uv run python -m pytest refactory/tests -q
```

`tests/test_registry.py::test_every_installed_package_mode_validates` asserts the strongest statement the library can make about a package it did not write: every mode of every installed package builds a graph that validates with all of its calls resolved.

`tests/test_ir_parity.py` holds `refactory_dsh/ir.py` against the reference graph language over every shipped mode, serializing both byte for byte. The reference is a test-time oracle, not a runtime dependency: run `uv sync --group reference` to install it, and without it those tests skip with a reason.
