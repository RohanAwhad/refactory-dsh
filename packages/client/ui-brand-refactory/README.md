# @deepseek-ai/dsh-client-ui-brand-refactory

re:factory brand occupants for the Web client's generic brand slots. This package
replaces the DeepSeek whale and wordmark with the re:factory logo and name, and
tints the primary accent rose.

## Behavior

- `sidebar.brand.mark` → the re:factory logo (inlined raster).
- `sidebar.brand.name` → `refactory`.
- `conversation.hero.brand.mark` → the re:factory logo (replaces the animated whale fallback).
- `--dsw-alias-brand-primary` → rose (`#e11d48` light / `#fb7185` dark) via a theme token override layer.

## Model Experience

No model, token, or KV-cache effects — this package renders browser chrome only.

## Known Limitations and Deferred Work

The logo is an inlined downscale of `remote-factory/docs/assets/refactory_logo.png`;
refresh the base64 in `src/client/logo.ts` if the upstream asset changes. The
logo does not yet swap between the upstream `logo-dark`/`logo-light` variants by
color scheme.
