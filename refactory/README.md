# refactory-dsh

Modes are Python classes. Their architecture and parameters are a serializable
state dict. Optimizers propose typed updates to that state, and every proposal is
validated before it can cost an inner run.

The design follows PyTorch deliberately, because the analogy is the load-bearing
part: a mode is an `nn.Module`, its `state_dict()` carries the parameters *and*
the architecture, a knob is an `nn.Parameter`, and an optimizer is a
`torch.optim.Optimizer` that may only write what the mode declares writable.
`score()` and the mode's invariants are methods, so no proposal can reach them.

- **DSH** is the runtime and the model client (this repo is a DSH fork).
- **`refactory/refactory_dsh/nn/`** is the library: modes, graphs, knobs, the
  edit algebra, the validator, the executor.
- **`refactory/refactory_dsh/optim/`** is the optimizer layer.
- **`refactory/refactory_packages/`** holds packages of modes. The library knows
  no package by name; it discovers, lists and imports them.

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
  docs/plans/pythonic-modes.md  the design and its acceptance gates
  vendor/remote-factory/      refactory (git submodule)
  vendor/srf/                 SRF (git submodule)
install.sh                  one-command install
refactory-dsh               the launcher (opens DSH web)
```

## Install

```bash
git clone <repo> --recurse-submodules
./install.sh
```

## Run

List the packages and the modes they own:

```bash
.venv/bin/python -m refactory_dsh.registry list
.venv/bin/python -m refactory_dsh.registry show circle_packing/adaptive
```

Optimize a mode. `autoresearch` reads the traces and asks a model for the next
edit; `random` samples the edit catalogue. Both run the same validating loop, so
a proposal that cannot run costs tokens and not budget.

```bash
.venv/bin/python -m refactory_dsh.loop circle_packing/adaptive \
    --epochs 2 --batch-size 4 --optimizer autoresearch

# what came out of it
cat .factory/loop/report.md
```

Create a package of modes from a brief. Design and emit are model steps; the
gate is deterministic — the package is imported, its graph is built, every call
it names is resolved, and it is run once before it is registered.

```bash
.venv/bin/python -m refactory_dsh.create \
    --name my_pkg \
    --brief "what the package should do" \
    --extends circle_packing/adaptive
```

## Test

```bash
.venv/bin/python -m pytest refactory/tests -q
```

`tests/test_registry.py::test_every_installed_package_mode_validates` asserts the
strongest statement the library can make about a package it did not write: every
mode of every installed package builds a graph that validates with all of its
calls resolved.

## Not yet wired

The DSH agent presets that used to drive the JSON graphs are not regenerated any
more, because creating and optimizing are now Python entry points. Wiring them
back onto `refactory_dsh.create` and `refactory_dsh.loop` for the GUI is the next
piece of work; see `docs/plans/pythonic-modes.md`.
