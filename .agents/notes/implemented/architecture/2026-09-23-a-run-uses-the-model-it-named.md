# Agent Note: A run's model is the one the invocation named

Status: implemented

## Problem

Choosing a model did not work, in three places at once, and each hid the others.

**`--model` on a run was inert.** Both command-line bundles install the model-selection middleware on the agent they create, and both built it from `agentDefaultModel.currentSelection()` — the user's settings default. That middleware rewrites every request from its own `current` after delegating, so the provider and model in `agentOptions` were discarded a moment later. The graph profile had parsed `--provider`/`--model` all along; a run that asked for one model answered from another and said nothing.

**The one-shot profile had no such flag at all.** `dsh --profile headless --model X` was `error: unknown option '--model'`, which is why every `autoresearch` proposal failed: `DshModelClient` has passed that flag since it was written. The run reported a search that found nothing, which is also what a search that found nothing looks like.

**A route that did not exist was not an error either.** With an unknown provider, the request came back answered by the default model. A person who believed they were on one provider was on another, and the only symptom was that the answers looked the same.

The question that found this was "how do I configure models?" — and the honest answer at the time was "change the global default; per-run choice is not wired", which is not what the README's `optimize --model` row said.

## Decision

**The middleware's `current` is the effective selection.** `installModelSelection` is the thing that decides a request's route, so both CLI bundles now build it from the invocation: `config.provider || selection.provider`, `config.model || selection.model`, and an inherited reasoning effort only when the model is unchanged, because an effort belongs to the model it came from. The compose-time selection is the fallback, not the authority.

**The one-shot profile takes `--provider`/`--model` like the graph profile does.** Parsed in the startup provider, published on the startup service, wired through the row's config to the runner, and used for the agent it creates. Nothing is written back to settings, so choosing a model for one run cannot change the next one.

**A route that does not exist fails loudly.** It now ends the run with `NO_ADAPTER: no adapter registered for provider "…"` and exit 1. Quietly answering from the default is the worse failure of the two: it produces a result attributed to the wrong model.

**Refactory exposes the two flags where a person meets them.** `refactory run`, `refactory ceo` and `refactory optimize` take `--model` and `--provider`; the authoring and loop module entry points take `--provider` beside the `--model` they already had. The settings default remains `agent-default-model` in `~/.dsh/settings.yaml` — written by the Web GUI's Models page or by hand — and the flags are per invocation.

## Consequences

A model can be chosen per run, from the terminal or from the GUI's composer, and the catalogue stays what it always was: advisory. `deepseek-v4-flash`, `deepseek-v4-pro` and `deepseek-v4-flash-vision-exp` are what the pickers offer, an unlisted id on the DeepSeek route still goes to the API, and adding a provider or a model is an edit to `settings.yaml` (with the key in the credential store or the environment), not a code change.

Verified by running it: an `openai-completions` route pointed at a local server through `--patch` answered a headless run with that server's text, and the request the server logged named the model the run had chosen. Reverting the middleware line makes the new spec assert the settings default and fail. An unknown provider now exits 1 naming the route.

The adapter's own catalogue, the `Models` page, `settings.yaml` and the credential store are documented together in the README's Models section, because "which models can I use" was answerable only by reading three packages.

## Alternatives considered

**Write the chosen model into `agent-default-model` for the run's duration.** Rejected: it makes a per-run choice a global setting, and a crash between the write and the restore leaves the user's default changed without their having asked.

**Pass the model through an environment variable the adapter reads.** Rejected: the selection is a request-level fact with a documented per-invocation flag on the other profile, and an environment variable would be a second mechanism for one thing, invisible in `--help`.

**Keep `agentOptions` as the only carrier and teach the agent loop to prefer it.** Rejected: the middleware exists so a *later* pick takes effect on the next step, and the web UI depends on that — the model picker updates `selection.current` mid-session. The bug was which selection the CLI bundles seeded it with, not the middleware.

**Leave the profile's catalogue as the only way to add a model.** Rejected for the DeepSeek route: its catalogue is advisory by design and requests are unrestricted, so refusing an unlisted id here would be a new restriction rather than a fix.
