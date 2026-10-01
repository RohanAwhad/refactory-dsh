# Upstream compatibility

Refactory composes the published `@deepseek-ai/dsh` dependency with local plugins and Python tools. It does not vendor or maintain a fork of the DSH source tree. Keep that boundary: panel graphics, layout, theme and product identity belong in Refactory's local browser/host plugins, through published slots, theme tokens, routes and index hooks.

The current install is **not completely patch-free**. `pnpm-workspace.yaml` declares five compatibility patches for the pinned `0.2.0-rc.2` packages: directory picker, headless, goal-round driver, Codex subagent and Claude Code subagent. They cover existing execution and interface compatibility. The web surface and the `ceo` preset are **not** patches: `packages/bundle/refactory-web` and `packages/bundle/refactory-presets` contribute those rows as ordinary bundle layers over the pinned app, so the loader picks them up without rewriting an upstream package. Normal installation applies the patches reproducibly; do not edit `node_modules` by hand.

## Upgrade discipline

1. Keep DSH resolved from its published package. Do not copy the upstream application or execution engine into this repository to implement presentation changes.
2. Use the local `ui-brand-refactory` and `ui-refactory` plugins for branding and panel work. The Remote console remains the execution boundary. The live map adds only optional, read-only checkpoint topology to activity responses; old saved runs and older hosts without it remain readable.
3. Review each existing patch when advancing the pin; remove a patch only when its behavior is supported upstream and the corresponding acceptance still passes. Do not silently expand the patch set as part of visual work.
4. Perform a clean frozen install, host/client build and GUI regression suite. Inspect a fresh native launch, both themes and the supported locales. Recheck the isolated title-suffix and hero-wrapper bridges because those surfaces do not yet have dedicated upstream configuration.
5. Preserve the execution contracts and bounded evidence in [release validation](release-validation-20260926.md). A presentation-only upgrade does not justify repeating model-backed campaigns.

The branding host changes only the returned index HTML and registers Refactory-owned icon/manifest routes. It leaves installed upstream assets and authentication handling intact. Provider/model names and required license attribution are not application branding and must remain accurate.
