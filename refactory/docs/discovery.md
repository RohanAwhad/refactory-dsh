# Capability and documentation discovery

The CEO receives a compact [operating guide](ceo-guide.md) through its tool's
system prompt. `refactory help` returns that guide, current operation/mode IDs,
interface support and build/documentation identity. `--topic <ID>` reads actual
argument schemas and examples. Chat uses actions `help`, `docs`, `profiles`,
`profile-resolve` and `workers`; the native panel uses the same mode declarations.

```sh
refactory help
refactory help --topic counting_pack/fixed_step
refactory docs --query 'worker routing'
refactory docs --page refactory/docs/worker-routing.md --limit 6000
```

Docs return exact indexed page IDs, section IDs, resolved links and continuation
offsets. Reads are limited to 12,000 characters; search returns at most 12 bounded
excerpts. Only README and the public `refactory/docs` Markdown tree are bundled.
Runtime retrieval reads the installation's bundle, never project files, private
runs, credentials, swap files or arbitrary paths. Non-document links are identified
as repository assets. Shell/coding/web permissions of the CEO stay restricted.

Coverage targets shipped mode declarations and public console/CLI entry points.
Internal Python helpers are covered by the separate API reference generator,
not advertised as chat actions. Forwarding entry points such as `package` expose
their wrapper schema and supported terminal route; backend-native credential and
configuration flags remain owned by the native product and are deliberately not
chat configuration arguments.

The authoritative facts come from `console._parser()`, the CLI parser, profile
settings, worker capabilities, mode declarations and the installed package
registry. The generated chat schema and invocation mappings use those same
arguments. Interface declarations distinguish callable actions, `wf_open`,
terminal/panel routes and deliberately unavailable configuration controls. Worker
routing is a file/CLI/panel feature with read-only chat discovery.

`refactory_dsh/data/coverage.json` owns reviewed purpose, examples, documentation
links and interface routes. Its `reviewed_schema` binds that guidance to actual
public arguments, defaults and mode structure. Missing/stale metadata fails the
ordinary build with the capability ID, the behavior the CEO cannot reliably
choose/use, and repair instructions. Generation never updates semantic review
hashes automatically. Signatures are facts, not proof that an explanation works.

```sh
pnpm run docs:generate
pnpm run docs:check
pnpm run build
pnpm run package:python
```

Generation is deterministic and offline: no model, credentials, network service
or vector database. It reuses the API reference generator and writes the full
installation docs bundle, [capability reference](api/capabilities.md), and the
TypeScript tool contract. Check mode fails stale artifacts and invalid links.
The runtime JSON bundle and TypeScript tool contract are ignored build artifacts,
not tracked snapshots. Build, typecheck and TypeScript test commands generate them
before use; the Python test session does the same. `package:python` generates them before writing wheel and source
archives under `.artifacts/python/`. The Python package includes the JSON bundle,
so installed documentation retrieval does not need a source checkout. GitHub
Actions runs the same build and packaging tests without committing outputs back.
The readable API/capability Markdown references and reviewed coverage metadata
remain tracked. Generation still refuses missing or stale semantic review hashes;
`docs:check` verifies generated outputs after generation. Build identity hashes version, schemas, reviewed metadata and docs;
documentation identity hashes indexed pages. Live mode schemas may differ after
installing a package; the help response shows coverage warnings in that case.

## Worked extension: mode

Register a mode normally and declare a new knob with its type, default, bounds
or accepted values, and a useful description. For example, add a frozen input
`target` and an integer `step` to a counting mode. The live catalogue immediately
shows these facts and its nodes/roles/gates/ports. Add a `modes` entry in
`data/coverage.json` explaining when to choose it, a working example such as
`{"knobs":{"target":5,"step":1},"explanation":"Reach five exactly."}`, its
public documentation page and CLI/chat/panel routes. Run `docs:generate` to get
the expected schema hash, review the guidance, and set `reviewed_schema` to that
hash. Regenerate and check. A later knob or graph change produces a new review
diagnostic. Newly installed modes still appear at runtime even without this
metadata, with a visible warning instead of a false claim of complete coverage.

## Worked extension: operation

Add the actual console parser and dispatch implementation, then add its operation
coverage entry: purpose, `examples` with console argument names, `doc`, and all
three interface routes. If it is a CEO action, `chat.fields` maps tool argument
names to actual parser destinations. The generator derives types, flags and
positionals, and the tool uses those mappings to invoke the console. Missing
required mappings and invalid example arguments fail generation. If it is file
or terminal only, omit `chat` and explain the supported route and why chat cannot
mutate it. `workers` and [project worker configuration](worker-routing.md) are a
worked example of intentionally read-only conversational support.

Run focused schema, negative coverage, docs retrieval and invocation tests. For
an exposed model workflow, demonstrate a small actual CEO discovery/docs/launch
case; deterministic schema checks alone do not establish model usability.
