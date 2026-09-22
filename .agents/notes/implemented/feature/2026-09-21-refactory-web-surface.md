# Agent Note: The `refactory` tool is the Web view's whole surface

Status: implemented

English | [中文](2026-09-21-refactory-web-surface.zh.md)

## Problem

The Web view could run the outer loop and read a run, and could do nothing else. Authoring a mode, managing a package, steering a run and reading its winner were Python entry points with no tool action, so the two things the product exists for — creating a mode and searching one — could not be reached from the page at all. The tool exposed eight of the console's thirteen verbs.

Separately, selecting a workspace in the Web view did nothing. The picker was fine; **session creation failed**. The `ceo` preset mounts `tool-refactory`, and `packages/bundle/web-app` never depended on `@deepseek-ai/dsh-tool-refactory`, so the profile's healed `node_modules` mirror carried no such package and every session stopped with *preset "ceo" failed to mount: row "tool-refactory" names a plugin that cannot be resolved*. The client caught the rejection and set the pending workspace back, so the click looked inert and the browser console stayed clean.

## Decision

The `refactory` tool exposes one action per console verb, and the console grew the verbs the rest of the system needed. `strategies` returns the three level-2 seams by name with the one-line description from each class docstring, read from the registries, so the optimizer, strategy and evaluator are chosen from a described catalogue. `registry`, `create`, `edit` and `design` forward to the entry points that already own that work and return their JSON, with a non-JSON line preserved as text rather than dropped; `create` additionally carries the design review it wrote, so the chosen approach and what the review refused are visible. `steer`, `best` and `adopt` complete the console's read surface. The client card renders every action from the console's JSON, so the conversation is a view of the run rather than of a sentence about it.

`packages/bundle/web-app` declares `@deepseek-ai/dsh-tool-refactory`, and `packages/preset/agent-presets/tests/preset-plugin-dependencies.spec.ts` asserts that every plugin a shipped preset names is reachable from the transitive dependency closure of the CLI package the profile mirror heals from — the check that would have caught the workspace defect.

The blank-session hero is text-only by decision. The only artwork in the repository is a 5.26:1 lockup on an opaque background, the hero's next mark column is 34px, and the column beside it is already the wordmark; the mark slot stays claimed and empty so the harness's whale does not appear in its place.

## Alternatives considered

**A client panel with its own host RPC seam.** The surface this change shipped was the tool, because the panel needs a Service Definition, a provider, a Typert remote and a client registration — larger than the surface it would replace — and because the tool is model-facing, so the client cannot call it. [The Web picker reads the console through one Remote service](../architecture/2026-09-24-the-web-picker-reads-the-console-over-remote.md) builds that seam; a picker with the strategy descriptions beside it is that note's subject.

**A second copy of the catalogue in the client.** Rejected: the console's JSON is the contract both surfaces read and `tool-refactory/src/console.ts` is the one boundary, so a parallel store would drift from the tool the moment either changed.

**Bespoke cards per artifact (report.md, the gradient, the rejection histogram).** Rejected for now: `artifacts` returns each with its media type and contents and the generic card renders them, so a markdown card is a later view over the same JSON rather than new data.

**Un-claiming the hero slot so the harness's own whale renders.** Rejected: the page would carry a second brand.

## Consequences

- Every action shells out to the same `python -m refactory_dsh.console` a person runs, so a run started from the page is the run a shell would start, with the same artifacts and the same report.
- The named choice is a described catalogue the model reads and [the picker](../../../../packages/client/ui-refactory/README.md) offers as dropdowns; both read the console's own descriptions rather than a second list.
- The preset-dependency gate fails naming `ceo: @deepseek-ai/dsh-tool-refactory` when the dependency is removed, so the workspace defect cannot return silently.
- The tool's action enum and its model-visible description grow with the console; the client card's action list is a second list that must move with them, and a card test covers each new action.
- `create`, `edit` and `design` are model-driven and slow; a call blocks for minutes, which is why the tool's timeout is ten minutes and why the run they start is not a background job.
