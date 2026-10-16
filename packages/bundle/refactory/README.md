---
description: "The refactory agent-preset bundle: declares the `ceo` preset inline as its own `@deepseek-ai/dsh-agent-preset` row and makes it the profile's default."
kind: "package-bundle"
---

# @deepseek-ai/dsh-refactory

## Summary

This bundle is two patch layers and no code. `presets/ceo.patch.yml` declares the `ceo` preset as an inline `@deepseek-ai/dsh-agent-preset` row, the way the harness declares its own shipped presets; `cordis.patch.yml` sets that preset as the deployment default on the `agent-preset-registry` row. Nothing here edits any harness package, and nothing here walks a preset directory: at this pin a preset is its own declaration, not a file the roster discovers.

The layer is **surface-independent**: install it into any profile whose sessions should resolve `CEO` from this checkout, browser or TUI. The browser console is a separate layer, `@deepseek-ai/dsh-refactory-web`.

## Use this package

### Install into a profile

```text
# the re:factory-owned GUI profile (harness web surface + this layer)
dsh plugin --profile refactory add <checkout>/packages/bundle/refactory
# or attach the surface-independent layer to a profile you already have (e.g. Martty)
dsh plugin --profile martty add <checkout>/packages/bundle/refactory
dsh plugin --profile refactory remove @deepseek-ai/dsh-refactory
```

`@deepseek-ai/dsh-refactory` is not published to the npm registry, so `add` takes a path into a checkout, and the profile links it. The manifest declares `dsh.bundle.patch`, so the reconcile step appends `@deepseek-ai/dsh-refactory` to `dsh.profile.bundles` and both layers activate on the next boot.

### Layer-ordering precondition

The `agent-preset-registry` row both layers patch is inserted by the `dsh-web-app` layer, and a patch only reaches a row that an earlier layer created. **This bundle must therefore be applied after `@deepseek-ai/dsh-web-app` in `dsh.profile.bundles`.** `dsh plugin add` appends a newly installed bundle after the profile's template bundles, so an ordinary profile install satisfies that as long as the adds run in layer order, which `dsh_profile`'s plan does. It is not satisfied by declaring a dependency on `@deepseek-ai/dsh-web-app`: `pnpm add` writes a profile manifest's `dependencies` keys in alphabetical order, the harness appends new bundles by iterating those keys, and `dsh-refactory` sorts before `dsh-web-app`. Only a hand-edited bundle list needs to keep the order by hand.

### What you get

- The `ceo` preset, declared with the display name `CEO` and roster `order: 1`.
- `ceo` as the registry default, so a session created without a named preset composes from it.
- The interactive workflow spine that the preset mounts (`wf_*` tools, `interactiveOpen`), with its `ceoDeny` list leaving the CEO on the workflow tools rather than the coding tools.
- Subagents without a shell: the preset's `subagent` and `subagent_fork` rows set a `toolFilter` allow-list (`read`, `write`, `edit`, `glob`, `grep`, `read_image`, `web_search`, `web_fetch`, `skill`, `todo_write`, `send_message`), so a subagent started through either gets no `bash`, no `wf_*` tools, and nothing that starts a run. It still sees `subagent` (and `list_subagent_models` when subagent model selection is on), which the `subagent` row registers on each agent's own scope; the host's default depth limit (1) refuses the call. Its `write` and `edit` are not yet confined to the run directory.

### Verify it, without trusting this README

```bash
pnpm run test:acceptance     # packages/bundle/refactory/tests/verify-ceo-root.mjs
```

The acceptance run is the one that proves the mechanism, because only it can see the layer order and the roster the harness actually builds. It runs `dsh_profile`'s provisioning plan into a throwaway `DSH_HOME`, composes the owned `refactory` profile from the harness's `web` template, checks that the harness's own `web` template keeps exactly its template bundles and mounts none of re:factory's rows, then boots `refactory` and asks its live services: the roster row (`ceo`, not broken, default), `session/create` without a preset, and the composed agent's own tool catalog (`wf_*` present, the preset's `ceoDeny` absent). A scripted model route (`tests/scripted-model.mjs`, added with `--patch`) then makes the CEO spawn a subagent through `subagent` and through `subagent_fork`, on this profile and on a fresh profile carrying the `graph` layer, and each child's own catalog must be its row's allow-list, with no shell and nothing that starts a run. It also reads the installed `dsh-base` and `dsh-web-app` layers and fails if either declares a `ceo` preset of its own — without that check a stale patched `node_modules` would satisfy every live assertion with this bundle's declaration doing nothing. CI runs it as the `Web profile acceptance` step.

## Understand the implementation

<details>
<summary>Implementation internals — click to expand</summary>

A preset is one `@deepseek-ai/dsh-agent-preset` row whose `config.plugins` **is** the agent-plane composition. The registry mounts it once under a standing scope; every session naming `ceo` joins by scope parentage, so the tools and prompt sections registered there cover each joined agent while a session's own state stays keyed per Session/Agent inside the plugins. The host composition keeps everything a preset must not own: the registries themselves, the sandbox and approval stack, persistence, and the model route.

`agent-preset-registry` replaces a row's whole `config` object rather than merging it, so `cordis.patch.yml` restates `default` — the schema requires it, and omitting it fails the mount. `selectedDefault` is deliberately not stated: it is the volatile field a person's Settings write owns, and a patch must not pin it.

That replacement rule makes the registry row's `config` **single-owner**. A layer applied after this bundle that patches the same `id` without repeating `default` drops this bundle's default entirely, and the only symptom is the harness's own default winning with no diagnostic. Which layer is last follows `dsh.profile.bundles` order, so a second patcher of `agent-preset-registry` is a conflict rather than a merge. The `dsh-web-app` layer owns the row's other keys and must not state `agent-preset-registry` at all once this bundle supplies it.

### Source map

| File | Role |
|---|---|
| [`cordis.patch.yml`](cordis.patch.yml) | Makes `ceo` the registry default |
| [`presets/ceo.patch.yml`](presets/ceo.patch.yml) | The `ceo` composition: the `@deepseek-ai/dsh-agent-preset` row and its `plugins` |

</details>

## Known Limitations and Deferred Work

- **The bundle carries the `ceo` composition only.** Any other refactory-owned preset added later is another `@deepseek-ai/dsh-agent-preset` declaration in this bundle's patch list.
- **The composition's plugin packages must resolve from the profile.** A preset row's package names resolve against the base the harness was loaded from, not against this package, so the profile's install must carry the packages `ceo` names.
- **The subagent allow-list has two gaps.** With `DSH_TOOLS_MODE` set to a PTC mode, every agent also gets `run_code`, which runs model-written code, is added after tool filters apply and cannot be named by one. A continuable subagent started before the filter keeps the tools it was started with when it is continued.
- **Layer order is a precondition, not an assertion.** A profile that lists this bundle *before* `@deepseek-ai/dsh-web-app` gets a `patch: entry "agent-preset-registry" not found` warning and no default is set at all — not a fallback to a shipped preset. The composition still names nothing, so a session created without a named preset composes the harness's own default instead. `dsh_profile`'s target table is what `./install.sh`, `./refactory-dsh` and `refactory doctor` use to notice a profile that lacks the layer entirely.

## Dev Note

<details>
<summary>Working context for maintainers — click to expand</summary>

`ceo` was previously delivered by patching `@deepseek-ai/dsh-agent-presets` to carry a `presets/ceo/` tree, then by pointing that roster's configured `roots` at this package through a startup plugin. Both mechanisms are gone at the `0.2.0-rc.2` pin: the package was renamed and redesigned, its roster takes no configured roots, and each preset is now its own inline declaration. If you change the preset, change `presets/ceo.patch.yml`.

</details>
