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
| Stock chat headline and Preview badge | CSS scoped to the public hero `data-slot` wrapper, independent of CSS module hashes |
| CEO and Minimal choices | Public preset slots reuse native components and controllers with filtered UI snapshots |

`src/identity.ts` owns product metadata; `src/artwork.ts` contains the original artwork's alpha masks. The display name is **Refactory**. The original lowercase lockup is its visual wordmark. Brand blue is `#2b8cff`; controls use `#176bcc` in light mode and `#76b6ff` in dark mode for contrast. `src/client/presentation.ts` isolates the compatibility bridges for shell surfaces that currently have no dedicated upstream branding input. The session-aware document title is [`ui-refactory`](../ui-refactory/README.md)'s, because that package is on the additive page too and this one is not.

## Lifecycle and boundaries

Slot declarations may arrive before or after this plugin. Slot contributions and theme overrides dispose with the owning fiber. Hero CSS is mounted with the logo component so a reload cannot leave the logo without its headline suppression. Host metadata applies before browser scripts run. The upstream welcome component retains acknowledgement, focus handling, errors and untranslated business strings. Provider names, authentication endpoints, package identifiers, license attribution and runtime receipts stay truthful. This plugin registers no model tools or prompt content.

The new-chat picker and Agent presets settings offer only **CEO** and **Minimal**. CEO's English and Chinese descriptions explain its Refactory workflow role. Extra-preset authoring is disabled in this UI; native View and Set as default actions remain. If an older default names another preset, opening the new-chat picker stages CEO (or Minimal when CEO is unavailable) through the native controller. This does not rewrite the saved default. Existing sessions still display their real preset, and the host's complete preset roster stays intact for worker delegation and CLI use.

## Verification and limitations

`pnpm test:gui` covers declaration lifetimes, disposal, theme tokens, host metadata, both welcome locales and hiding the full stock headline through the real slot wrapper shape. [Native acceptance](../../../refactory/docs/panel-validation-20260927.md) includes fresh launch, gear favicon, both themes, resize, saved runs and controls.

The hero-wrapper bridge is specific to the published DSH contract currently pinned by the repository; recheck it on upgrades. It does not modify upstream bytes or inspect generated CSS hashes. The original artwork is raster-derived and is intended for these small application surfaces. A future print-scale asset should use the original vector source if available. Some pre-existing advanced panel labels remain English. The underlying launcher still identifies itself as `dsh web` in its terminal URL line.

The pinned settings shell builds navigation from raw slot registrations rather than the winning contribution, so shadowing a section produces duplicate navigation. A reversible observer scoped to the public `sidebar.settings` wrapper and localized preset label hides the duplicate button. It preserves the native settings shell and controller; inspect this bridge along with the typed preset faces on upstream upgrades. [Preset acceptance](../../../refactory/docs/preset-ui-validation-20260927.md) records the checks.
