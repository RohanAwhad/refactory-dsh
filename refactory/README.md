# The Refactory Python project

This directory contains the mode language, graph execution bridge, package registry, authoring modes and configurable outer loop. The [repository README](../README.md) is the user entry point.

- [Get started](docs/getting-started.md): installation, an offline search, model setup and first interactive run.
- [Concepts](docs/concepts.md): modes, Tasks, graphs, workers and optimization.
- [Command reference](docs/cli.md): terminal operations and controls.
- [Terminal and GUI](docs/interfaces.md): current capabilities and their boundaries.
- [Write a mode](docs/tutorials/01-write-a-mode.md): the Python authoring interface.
- [Architecture](docs/architecture.md) and [API](docs/api/index.md): implementation details.

## Source map

| Directory / module | Owns |
|---|---|
| `refactory_dsh/nn/` | Graphs, knobs, state, execution and validation |
| `refactory_dsh/create.py`, `edit.py`, `design.py` | Authoring graphs and entry points |
| `refactory_dsh/optim/` | Candidate proposers |
| `refactory_dsh/strategies.py` | Outer-loop strategies |
| `refactory_dsh/evaluators.py` | Walker and DSH evaluation |
| `refactory_dsh/loop.py` | Search, measurements and history |
| `refactory_dsh/console.py`, `cli.py` | Shared operations and command-line interface |
| `refactory_packages/` | Shipped packages, including the default authoring modes |
| `docs/` | Guides, reference and generated HTML tour |

A mode's graph and knobs are editable state. Its scoring function and invariants remain methods outside that state. Create/Edit produce packages; Design produces artifacts. See [contributing](docs/contributing.md) before changing these contracts.
