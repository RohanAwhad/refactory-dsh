# Models

A run asks one model. Which model answers is your settings document, `~/.dsh/settings.yaml`, and the `--model` and `--provider` flags override it for a single run without writing anything back.

## Where the list comes from

The adapter's catalogue is **advisory**: it is what pickers offer, not what the route accepts. The DeepSeek route ships `deepseek-v4-flash`, `deepseek-v4-pro` and `deepseek-v4-flash-vision-exp`, and it will send any id you name — so a model that is not listed still works, it just is not offered. The settings document is hot-reloaded: nothing needs a restart.

## In the GUI

**Settings → Models.** Add a provider, paste its API key, optionally a base URL, and list the models — or press **Fetch available models** and pick from what the provider reports. **Add a custom provider** takes an id, a display name, one of three wire protocols (`openai-completions`, `openai-responses`, `anthropic-messages`) and a base URL, which is how a local server (Ollama, LM Studio, vLLM) is reached. The page writes the settings document and the credential store for you; the composer's model picker then offers the new models.

## In the terminal

The same file, by hand:

```yaml
# ~/.dsh/settings.yaml
agent-default-model:
  provider: deepseek-official
  model: deepseek-v4-pro          # the default for every run that names none

llm-pi-ai:
  providers:
    openai:
      apiKeyEnv: OPENAI_API_KEY   # the *name* of the variable, never the key
    anthropic:
      apiKeyEnv: ANTHROPIC_API_KEY
      models:
        - id: claude-sonnet-4-5
          contextWindow: 200000
    ollama:                       # a local OpenAI-compatible server
      displayName: Ollama
      api: openai-completions
      baseURL: http://127.0.0.1:11434/v1
      models:
        - id: qwen3-coder
          contextWindow: 262144
```

Keys are read from the environment (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, …, including a `.env` at the checkout root) or from the managed store at `~/.dsh/.credentials.yaml` that the Models page writes. A `llm-deepseek:` section overrides the direct DeepSeek route the same way: `models:`, `baseURL:`, `apiKeyEnv:`, `thinking:`, `reasoningEffort:`.

A local OpenAI-compatible server works through the same route, and still needs *a* key — pi-ai's OpenAI protocol asks for one, so name a dummy variable and export any value (`apiKeyEnv: LOCAL_TEST_KEY`, then `LOCAL_TEST_KEY=dummy`), or set an `Authorization` header.

## One run, one model

```bash
uv run refactory ceo counting_pack/fixed_step --model deepseek-v4-pro
uv run refactory ceo "an idea" --mode create --single --model deepseek-v4-pro
uv run refactory optimize circle_packing/adaptive --provider deepseek-official --model deepseek-v4-pro
```

`--model` names the id and `--provider` the route; both fall back to `agent-default-model` in your settings, and neither writes anything back, so choosing a model for one run does not change the next. A route that does not exist is a loud failure (`NO_ADAPTER: no adapter registered for provider …`), not a quiet return to the default. The same two flags exist one level down, on `dsh --profile headless|graph`, which is what `refactory` calls.

## Credentials for nested execution

Create/edit smoke and panel optimization launch further DSH processes. Their environment
is deliberately scrubbed, and the smoke process has a workspace-local `HOME`. Save the
provider key once through **Settings → Models** in the DSH home used by the run:
open the provider row with **Edit**, enter its **API key**, and choose **Apply**. The
product passes that nonsecret home location to nested model processes; keys remain in
DSH's managed store and are resolved by its credential service. Do not copy `.env`
files into generated packages or smoke directories. If an exported key shadows the
reference, the UI reports “Provided by the launch environment (read-only)”; launch
without that override before saving. DSH resolves keys in this order: inherited launch
environment, managed store, invocation-directory `.env`, then DSH-home `.env`. A stored
key overrides both `.env` layers, but not an inherited environment override. The launch
environment is a startup snapshot; changing the launching shell afterward does not
change the running GUI's credential source.

`refactory doctor --provider deepseek-official --model deepseek-flash` checks local
credential configuration through the same scrubbed subprocess boundary as smoke. Create
and edit perform this check before research. The check makes no model request: a
configured key is not proof of valid authentication, available quota, or network access.
The later real model smoke remains required. Provider-native authentication without an
API-key reference is reported as `native-auth-unverified`, not as a verified credential.

The local diagnostic uses the installed headless profile's supported file-backed
credential/settings services and adapter schemas. A replaced service or dynamic `!!js`
configuration, an external `DSH_CLI`, or a source-checkout launcher is reported as
`diagnostic-unavailable`: the doctor prints **WARN** and authoring continues with an
explicit unverified-readiness warning. The actual selected runtime performs its normal
checks. Confirmed missing references and malformed known credential/settings configuration
still stop authoring before research. The diagnostic never guesses a credential location.
DSH's normal profile loader may initialize a missing shipped headless profile; the check
makes no model request and does not write credential values. The local managed store is owner-only but does not
isolate credentials from generated code running as the same OS user; stricter isolation
requires a separate model-service boundary.

For a DSH source checkout, set `DSH_ROOT` to its root after installing its dependencies.
The source launcher resolves `tsx` and the checkout's TypeScript configuration there;
agent processes still run in their item directories. `DSH_CLI` continues to select an
external executable directly. A custom/source runtime must support the flags supplied
to it: upstream headless profiles without this fork's `--provider`/`--model` extension
can select their route through the profile's `agent-default-model` configuration.
