---
description: "Refactory application identity, original two-gear artwork and blue theme through published DSH extension APIs."
kind: "package-reference"
---

# @deepseek-ai/dsh-client-ui-brand-refactory

Refactory's presentation plugin keeps the original blue `re`, two interlocking gears and `factory` lettering. Transparent alpha layers extracted from the original artwork replace the opaque navy rectangle. The gear icon scales independently in the sidebar and favicon; the full original lockup appears once in empty chat. Its secondary ink follows the theme.

## Integration

Mount the host and browser halves through the existing web-profile composition, then run the normal `pnpm install --frozen-lockfile` and `pnpm build`. No installed dependency files need manual editing. The published DSH dependency and existing compatibility patches are unchanged by this work; see [upstream compatibility](../../../refactory/docs/upstream-compatibility.md).

| Surface | Extension |
|---|---|
| Initial HTML title, favicon and app manifest | Host `webServer.tapIndex` and two static `webServer.register` routes |
| Sidebar gear icon and name | `sidebar.brand.mark` and `sidebar.brand.name` slots |
| Empty-chat lockup | `conversation.hero.brand.mark` slot |
| Blue accent, cool surfaces, accessible status colors | Disposable `theme.overrideTokens` layer |
| Welcome copy in English and Chinese | Existing `welcome-notice` leaf in `settings.onboarding`, retaining its component and controller props |
| Session-aware document title | Narrow, disposable observer replacing only the exact terminal `DeepSeek Harness` suffix |
| Stock chat headline and Preview badge | CSS scoped to the public hero `data-slot` wrapper, independent of CSS module hashes |

`src/identity.ts` owns product metadata; `src/artwork.ts` contains the original artwork's alpha masks. The display name is **Refactory**. The original lowercase lockup is its visual wordmark. Brand blue is `#2b8cff`; controls use `#176bcc` in light mode and `#76b6ff` in dark mode for contrast. `src/client/presentation.ts` isolates the two compatibility bridges for shell surfaces that currently have no dedicated upstream branding input.

## Lifecycle and boundaries

Slot declarations may arrive before or after this plugin. Contributions, CSS, title observer and theme overrides dispose with the owning fiber. Host metadata applies before browser scripts run. The upstream welcome component retains acknowledgement, focus handling, errors and untranslated business strings. Provider names, authentication endpoints, package identifiers, license attribution and runtime receipts stay truthful. This plugin registers no model tools or prompt content.

## Verification and limitations

`pnpm test:gui` covers declaration lifetimes, disposal, theme tokens, host metadata, title updates, both welcome locales and hiding the full stock headline through the real slot wrapper shape. [Native acceptance](../../../refactory/docs/panel-validation-20260927.md) includes fresh launch, gear favicon, both themes, resize, saved runs and controls.

The document-title and hero-wrapper bridges are specific to the published DSH contract currently pinned by the repository; recheck them on upgrades. They do not modify upstream bytes or inspect generated CSS hashes. The original artwork is raster-derived and is intended for these small application surfaces. A future print-scale asset should use the original vector source if available. Some pre-existing advanced panel labels remain English. The underlying launcher still identifies itself as `dsh web` in its terminal URL line.
