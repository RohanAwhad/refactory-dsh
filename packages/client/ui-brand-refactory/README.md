---
description: "re:factory brand occupants for the Web client's sidebar and hero brand slots, plus the rose primary accent, for users and maintainers of the re:factory Web surface."
kind: "package-reference"
---

# @deepseek-ai/dsh-client-ui-brand-refactory

English | [中文](README.zh.md)

## Summary

This package gives the Web client the re:factory identity: it replaces the DeepSeek whale mark and the harness wordmark in the sidebar with the re:factory logo and name, fills the blank-session hero mark with the same logo, and tints the primary accent rose. Choose it in a deployment that ships the re:factory product; the occupants are pure presentation, so nothing here reaches a model request. The package declares no slot of its own — it occupies three slots that `ui-sidebar` and `ui-conversation` declare — so leaving it out restores the fallbacks those declarers render. The hero occupant replaces the animated whale fallback rather than decorating it, and one raster logo serves both color schemes.

## Table of Contents

- [Use this package](#use-this-package)
- [Understand the implementation](#understand-the-implementation)
- [Further Exploration](#further-exploration)
- [Model Experience](#model-experience)
- [Known Limitations and Deferred Work](#known-limitations-and-deferred-work)
- [Dev Note](#dev-note)

-----

<a id="use-this-package"></a>
## Use this package

Mount the browser plugin in the client roster; the sidebar mark, the sidebar name, the hero mark, and the rose accent then appear on the next page load.

### What you get

- `sidebar.brand.mark` → the re:factory logo (inlined raster).
- `sidebar.brand.name` → `refactory`.
- `conversation.hero.brand.mark` → the re:factory logo (replaces the animated whale fallback).
- `--dsw-alias-brand-primary` → rose (`#e11d48` light / `#fb7185` dark) via a theme token override layer.

-----

<a id="understand-the-implementation"></a>
## Understand the implementation

<details>
<summary>Implementation internals — click to expand</summary>

The browser half is [`src/client/index.ts`](src/client/index.ts); the node half is an empty Loader seat, as in the other brand occupants. The sidebar mark and name install as one declaration-aware registration set — nested `ctx.slots.inject()` calls wait on the sidebar declaration — so the pair works whether this row activates before or after the declarer, and it withdraws both occupants together when the declaration collapses. The hero mark is a single slot registration with its own default height of 34 px. The rose accent is a theme override token layer registered through `ctx.effect()`, so it disposes with the fiber. The logo is a base64 raster constant in [`src/client/logo.ts`](src/client/logo.ts).

</details>

-----

<a id="further-exploration"></a>
## Further Exploration

Read these pages when the brand surface is not enough. They move from the slots this package occupies to the shell that renders them.

- [ui-sidebar](../ui-sidebar/README.md) — declares `sidebar.brand.mark` and `sidebar.brand.name` and renders their fallbacks.
- [ui-conversation](../ui-conversation/README.md) — declares `conversation.hero.brand.mark` in the hero.
- [ui-brand-official](../ui-brand-official/README.md) — the upstream occupier of the same sidebar slots, gated to official builds.
- [Web client architecture](../../../.agents/notes/implemented/architecture/2026-07-19-gui-web-client-architecture.md) — how browser plugin rows load and register slots.

-----

<a id="model-experience"></a>
## Model Experience

### Presentation-only brand occupants

#### What the model sees

Nothing: the package occupies the `sidebar.brand.mark`, `sidebar.brand.name`, and `conversation.hero.brand.mark` slots and registers one theme token override, and it registers no tool, prompt section, or session event, so no byte of a model request changes.

#### Token effect

None: the occupants render browser chrome only and never enter request assembly.

#### KV Cache effect

None: the package adds no prompt content, so the cached request prefix is identical with or without it.

## Known Limitations and Deferred Work

<a id="known-limitations-and-deferred-work"></a>

These limits define the re:factory brand surface. They are current package constraints, not a brand-design comparison or a task backlog.

- **The logo does not follow the color scheme** — the package never swaps between the upstream `logo-dark`/`logo-light` variants, so one raster serves both schemes.
- **The logo is an inlined downscale** of `remote-factory/docs/assets/refactory_logo.png` — refresh the base64 in `src/client/logo.ts` if the upstream asset changes.
- **The wordmark is text, not the raster** — `sidebar.brand.name` renders `refactory` as styled text so that it inherits the theme's label color and scales cleanly.

<a id="dev-note"></a>
### Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

None.

</details>

**Runtime invariant:** No runtime invariant companion is published because the package retains no mutable state; its three slot occupants and the theme-token override are registry-disposed with the browser fiber.
