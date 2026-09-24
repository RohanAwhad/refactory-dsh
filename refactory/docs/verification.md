# Verification

The repository stopped copying the DeepSeek Harness, and the gates that came with the
copy — `pnpm run test:docs` and its fifteen checks, the snapshot suite, the bilingual
pairing gate, `benchmarks/`, `website/` and `.agents/` — went with it. This document
decides what verifies the six packages and the glue instead, and what a contributor runs
before pushing. It is the answer to issue #84, written before the deletion.

## The commands

Run everything from the repository root.

```bash
uv run python -m pytest refactory/tests -q                    # the glue's suite
uv run python -m pytest refactory/tests -q --run-integration  # ... plus the real-DSH runs
uv run python -m pytest refactory/tests -q --toolchain-gate   # which real tools ran, and which did not
uv run python -m refactory_dsh.tools.gen_api_reference --check # docs/api/reference.md matches the code
uv run python -m refactory_dsh.tools.gen_tutorial --check      # the tutorial matches the code
uv run python -m refactory_dsh.tools.check_recipes             # every documented command, executed
```

```bash
pnpm run typecheck             # tsc over the six packages, host face then client face
pnpm run build                 # typecheck, then the host and client bundle passes
pnpm run test                  # the six packages' vitest suites
pnpm run test:gui              # the client packages' suites under jsdom
pnpm run lint                  # oxlint
```

## What replaced each deleted gate

| the deleted gate | its replacement | what the replacement proves |
|---|---|---|
| `pnpm run test:docs`, fifteen harness documentation checks | `refactory/tests/test_ship_ready.py`, `test_docs_api.py`, `test_docs_tutorial.py`, the two generator `--check` commands, and `check_recipes` | every command a shipped document shows runs as written, every path it names exists, and the generated API reference and tutorial are current |
| the bilingual pairing gate | `refactory/tests/test_docs_english_only.py` | no document under `refactory/` or the six packages has a `.zh.md` side or a `README.i18n.yaml` that nothing keeps aligned |
| `pnpm run test:gui` over the harness client suites | `pnpm run test` and `pnpm run test:gui` | the six packages' own vitest suites pass, including the two client packages under jsdom |
| the recorded-session snapshot suite | the real-DSH runs: `pytest --run-integration` and `check_recipes` | a mode runs end to end on the pinned harness and leaves facts on disk |
| `benchmarks/`, `website/`, `.agents/` | none | removed with the harness; this repository publishes no benchmark gate, no documentation site, and no agent-note corpus |

The harness's per-file 100% coverage gate is also gone. `pnpm run test:coverage` runs the
six packages' suites with coverage, and no threshold is enforced, so coverage is a number
a contributor reads rather than a gate that fails.

## What a contributor runs before pushing

Change the glue or a document: `uv run python -m pytest refactory/tests -q`, then the two
generator `--check` commands and `check_recipes` when a document, a CLI verb, or the API
reference changed.

Change one of the six packages: `pnpm run build`, `pnpm run test`, and `pnpm run lint`.
`pnpm run build` is what puts `lib/` on disk, and the graph profile loads `lib/`, so a
change that is only typechecked has not reached a run.

Change anything that reaches DSH: `uv run python -m pytest refactory/tests -q
--run-integration`. That is the only check that drives the pinned harness rather than the
in-process walker. `refactory package smoke` does not touch DSH; it is the walker.

## How a document's claim is checked

`test_ship_ready.py` reads the `bash` blocks a shipped document shows and refuses a module,
a flag, or a path that does not exist. `check_recipes` executes
`refactory_dsh/tools/recipes.py`, the one list the HTML tour also renders, so a command the
documentation shows is a command that was run. A claim about a run's output is checked the
same way the documentation's reader checks it: by running the command and reading the file.

## What this does not cover

The web picker is not mounted by the pinned harness's `web` profile. `refactory ui` starts
the pinned web application and serves it, and `ui-refactory` and `ui-brand-refactory` build
and pass their vitest suites, but the composition that adds the Modes tab lived in the
deleted `packages/bundle/web-app` patch. Issue #85 carries the bundle patch, the console's
generated Remote face, and the pinned headless model selection.

`refactory optimize --model` and `--provider` name a model for the completion client, which
runs the pinned `headless` profile. The pin's headless does not read those two flags, so a
named model reaches the agent and mode runs (`refactory run`, `refactory ceo`, the graph
profile) but not the outer loop's completion client. Issue #85 carries that too.
