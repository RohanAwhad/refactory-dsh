---
description: "The refactory web bundle: the console, the right-sidebar picker and the re:factory brand occupants, layered over the pinned web app."
kind: "package-bundle"
---

# @deepseek-ai/dsh-refactory-web

## Summary

This bundle is one patch layer plus a startup plugin. The patch mounts the Web
surface's refactory occupants over the pinned `@deepseek-ai/dsh-web-app` layer: the
`refactory-console` Host service, the `ui-refactory` right-sidebar picker, and the
`ui-brand-refactory` brand occupants in place of the official whale. It also keeps
the `wf_next` exclusion on the repeat-tool-reminder row. Nothing here edits
`@deepseek-ai/dsh-web-app` or any other upstream package: the harness's
client-module system discovers a browser plugin by scanning already-mounted Loader
entries, so a package that declares `dsh.client` and is an active entry is picked
up without the upstream manifest listing it.

Install it into the re:factory-owned `refactory` profile, which is initialized
from the harness's `web` template, so the GUI shows the refactory console and
picker. It is a **browser** surface and is not mounted under a TUI profile such as
Martty; `@deepseek-ai/dsh-refactory`, the sibling bundle, ships the `ceo`
preset and is the surface-independent layer. The two are independent layers and a
full web install carries both.

## Use this package

### Install into a profile

```text
dsh plugin --profile refactory add <checkout>/packages/bundle/refactory-web
dsh plugin --profile refactory remove @deepseek-ai/dsh-refactory-web
```

`@deepseek-ai/dsh-refactory-web` is not published to the npm registry, so `add`
takes a path into a checkout, and the profile links it. The manifest declares
`dsh.bundle.patch`, so the reconcile step appends `@deepseek-ai/dsh-refactory-web`
to `dsh.profile.bundles` and the layer activates on the next boot.

### Additive web profile

```bash
./refactory-dsh --additive
uv run refactory ui --additive --port 3081
```

The launcher initializes the normal `web` template under the profile name
`refactory-web`, then installs the existing `@deepseek-ai/dsh-refactory` and
`@deepseek-ai/dsh-refactory-web` bundles through `dsh plugin add`. Its manifest's
`dsh.profile.bundles` lists base, web, Refactory presets and Refactory browser
bundles in that order. No profile directory is cloned and no plugin paths are
rewritten. The existing `web` profile remains unchanged.

A small saved profile patch disables the standalone Refactory brand and enables
the native brand, exposing the complete native preset picker plus CEO. The Modes
panel exposes the full Refactory mode catalogue; the Refactory preset bundle
sets CEO as the default. Later launches reuse the profile and preserve edits.
The `dsh.profile.refactoryAdditive` flag distinguishes this profile from the old
standalone profile with the same name, so ordinary launch and installation do
not retire it. The graph runtime profile is also provisioned.

This pinned harness stores profile declarations under
`$DSH_HOME/profiles/<name>/package.json`, not in the global `settings.yaml`.
Upstream boot-time settings migration still applies; see the
[home-isolation guide](../../../refactory/docs/rc2-home-isolation.md).

### Layer-ordering precondition

The rows this patch disables or appends sit in the `@deepseek-ai/dsh-web-app`
layer, and a patch only reaches a row an earlier layer created. **This bundle must
therefore be applied after `@deepseek-ai/dsh-web-app` in `dsh.profile.bundles`.**
`dsh plugin add` appends a newly installed bundle after the profile's template
bundles, so an ordinary profile install satisfies that automatically. It is not
satisfied by declaring a dependency on `@deepseek-ai/dsh-web-app`: `pnpm add`
writes a profile manifest's `dependencies` keys in alphabetical order, the harness
reconciles bundles by iterating those keys, and `dsh-refactory-web` sorts before
`dsh-web-app`. Only a hand-edited bundle list needs to keep the order by hand.

### What you get

- The `refactory-console` row, with `repoRoot` resolved to this checkout so
  `POST /api/refactoryConsole/modes` reads the real mode catalogue.
- The `ui-refactory` tab in the right Sidebar, and `ui-brand-refactory` in the
  generic brand slots, with `ui-brand-official` disabled.
- The `wf_next` exclusion on the `repeat-tool-reminder` row, so driving a workflow
  with identical arguments is not reported as repetition.

### Verify it, without trusting this README

```bash
pnpm test packages/bundle/refactory-web        # the startup provider and the layer
dsh plugin --profile refactory add <checkout>/packages/bundle/refactory-web
dsh plugin --profile refactory --dump-config   # the rows above are present, no skipped patch
```

`tests/startup.spec.ts` checks that the provider publishes the checkout holding
`refactory/` rather than the caller's working directory, and that the layer still
carries every row the deleted `@deepseek-ai__dsh-web-app` pnpm patch supplied. The
remaining acceptance checks are browser-facing and run by hand against a live
server: the boot manifest lists `refactory-console`, `ui-refactory` and
`ui-brand-refactory`; the page reports no `Failed to load plugins`; the Modes
button renders the picker; and `session/create` with no preset returns
`agentPreset: ceo` when the sibling preset bundle is also installed.

## Understand the implementation

<details>
<summary>Implementation internals — click to expand</summary>

The layer restates the `repeat-tool-reminder` config (the patch dialect replaces a
row's `config` wholesale), disables `ui-brand-official`, and appends four rows: the
startup provider and the three refactory occupants. The `refactory-console` row
injects `refactoryWebStartup` and reads `ctx.refactoryWebStartup.repoRoot`, which is
what orders the provider before its reader.

A patch row is evaluated as `new Function('ctx', 'expr', ...)`, so a `!!js`
expression has no `import.meta` and cannot compute its own package directory. This
bundle therefore splits the work: the `refactory-web-startup` row mounts a small
plugin that resolves the checkout above its own module and publishes it as the
`refactoryWebStartup` service. The deleted pnpm patch used `process.cwd()`, which
for a profile-installed bundle names the user's working directory, not this
checkout.

The `agent-preset-registry` row is deliberately absent. Its `config` is replaced
wholesale, and `@deepseek-ai/dsh-refactory` is its single owner: that bundle states
`default: ceo`. A second `config` here would drop the default, decided by bundle
order.

The dependencies on the three occupant packages keep the harness install able to
resolve them the way the deleted pnpm patch did when it added them to the upstream
manifest. `@deepseek-ai/dsh-workflow-spine` is carried for the same reason: the
`ceo` preset's composition mounts it, and the roster resolves a preset row's
package names against the installation the profile was loaded from.

### Source map

| File | Role |
|---|---|
| [`cordis.patch.yml`](cordis.patch.yml) | The layer: the reminder and brand overrides, then the four inserted rows |
| [`src/startup.ts`](src/startup.ts) | The `refactory-web-startup` provider: resolves and publishes the checkout root |

</details>

## Known Limitations and Deferred Work

- **The picker has no automated browser test.** `tests/startup.spec.ts` covers the
  provider and the layer bytes; the rendered picker and the failure banner are
  checked by hand against a running server.
- **`repoRoot` assumes a by-path install.** The provider resolves the checkout from
  this package's own location, which is correct when the profile links this
  directory. A package published to and installed from the registry would resolve
  inside the profile's `node_modules` instead.
- **Layer order is a precondition, not an assertion.** A profile that lists this
  bundle before `@deepseek-ai/dsh-web-app` gets a skipped-patch warning and none of
  the overrides apply.

## Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

This package replaces `patches/@deepseek-ai__dsh-web-app@0.1.5-rc.3.patch`, which
edited the upstream package's own `cordis.patch.yml` and `package.json`. The patch
forced the repository onto one patched `0.1.5-rc.3` release and could not be
installed into any other DSH version. Its `agent-presets` hunk moved to
`packages/bundle/refactory` in #262; everything else is here.

</details>
