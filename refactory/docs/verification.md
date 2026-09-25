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

## The web surface and the pins it needs

`refactory ui` runs the pinned harness's `web` profile. Three pnpm patches in `patches/`
add what the fork used to add by copying upstream packages, and `pnpm-workspace.yaml`
declares them under `patchedDependencies`:

- `@deepseek-ai/dsh-web-app` gains rows for `refactory-console`, `ui-refactory` and
  `ui-brand-refactory`, disables `ui-brand-official`, and defaults the agent preset to
  `ceo`. Its manifest also declares our three packages, so the loader resolves them.
- `@deepseek-ai/dsh-agent-presets` ships the `ceo` preset directory, which mounts
  `tool-refactory`, the re:factory persona, and the **interactive workflow spine**. The
  spine is mounted with `interactiveOpen`, so the chat's CEO can open a mode with `wf_open`
  and drive it with `wf_next`; it runs one deterministic node per call, refuses a caller
  that is not the session that opened the run, and answers a `user` gate through
  `userQuestions`. Mounting it inside the preset is what makes the spine's own guard
  deny the CEO's coding tools, so the CEO cannot implement a node by hand.
- `@deepseek-ai/dsh-headless` accepts `--provider` and `--model` and makes a named
  selection the model-selection middleware's current one, so
  `refactory_dsh/model.py`'s completion client uses the model a caller named.

The gateway discovers the console at runtime from the `@Remote` markers on the mounted
service and binds arguments by the method parameter names, so the console answers without
the generated `./typert` and `./remote` artifacts. The picker's client face sends those
same names through the Connection's generic RPC,
`connection.rpc.call('/api', 'refactoryConsole/<method>', { args })`, because the pinned
Client Remote assembly is a prebuilt bundle that mounts a namespace service only for the
harness's own packages; `ui-refactory` therefore injects `connection`, not
`remote.refactoryConsole`. `refactory-console/src/types.ts` states the client face, and
`ui-refactory/tests/remote.client.spec.ts` pins each endpoint and argument name. The
published Typert generator emits the artifacts only when its own protocol package is a
workspace project, which a pinned install does not have.

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

Change a patch: `pnpm patch <name>@<version>`, edit the printed directory, then
`pnpm patch-commit <directory>`; the patch file and `pnpm-workspace.yaml` change together.
A patch is applied by `pnpm install`, so verify it in a clean clone, not only here.

## How a document's claim is checked

`test_ship_ready.py` reads the `bash` blocks a shipped document shows and refuses a module,
a flag, or a path that does not exist. `check_recipes` executes
`refactory_dsh/tools/recipes.py`, the one list the HTML tour also renders, so a command the
documentation shows is a command that was run. A claim about a run's output is checked the
same way the documentation's reader checks it: by running the command and reading the file.

## What this does not cover

The web picker has no automated browser test. Its layers are checked by hand against a
running server: the boot manifest lists the client rows, the page reports no
`Failed to load plugins` entry (a client plugin parked on a service nothing provides is how
one shipped), `refactoryConsole/modes` over `/api` returns the catalogue, `session/create`
with no preset returns `agentPreset: ceo`, and expanding the right sidebar and pressing
**Modes** renders the picker. `ui-refactory/tests/remote.client.spec.ts` covers the call
mapping, so a wrong endpoint or argument name fails a test; a future change to the patches
or to the harness's client assembly can still break the page without a red test.

The in-session drive is covered by `workflow-spine/tests/open.spec.ts` (open claims the
run, one node per `wf_next`, the refusals, the post-drain open view, a data pass crossing
calls, and the configured-graph path unchanged) and by hand in a browser: the CEO calls
`wf_open` then one `wf_next` per node, no `bash` call appears in the transcript, the run
reaches `done` with the mode's facts, and a second `wf_open` refuses with `a run is already
open in this session`. One in-session run per session is the accepted limit; a second
session is refused, and the detached search (`refactory start`) is untouched.

The Typert Remote faces are kept in step by hand. The generated artifacts would make that
impossible to get wrong, and issue #85 carries why they are not generated.
