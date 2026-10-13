# Native Create variants and adoption

The pinned source is remote-factory `3ab6f8f7ca792c874f2654574d7f7fd6dd18b896`.
Its builder distinguishes new portable workflows, updates to existing
definitions and standalone plugin packages. The native candidate preserves the
conditional duties through the following explicit mappings. Controlled tests
prove native mechanics; they do not establish live variant authoring fidelity.

| Source variant | Native candidate | Evidence and boundary |
|---|---|---|
| New portable workflow | Run-local `PACKAGE` and reusable `nn.Mode` classes | Real validation, discovery, skill export, native execution and user contract tests |
| Update existing definitions/registrations | `variant=update-existing`, qualified `revision_of`, complete original workspace package copied into a separate authoring run | Same package/mode identity; exact source file baseline; all modes validated; unrelated declarations compared; real original/revised regression tests still required |
| Plugin entry point plus editable install/list/validate/uninstall | Standalone native `Package`; real `registry add/import`, catalogue/resolve, native validation and `registry remove` in an isolated consumer | `plugin-check` records structural lifecycle and post-removal failure to resolve in a fresh process; actual installed mode execution is a separate required test |

The native registry has no `factory.plugins`/`register_plugin` or Python entry
point scanner. A successful native import is not evidence for foreign pip
packaging. Source `pyproject.toml` registration conventions map to native
`Package.version`, `requires`, modes and ops rather than an invented loader.
Source registration checklist references map to actual package ownership,
discovery, qualified resolution, parameters, graph calls/invariants, CLI,
README and exported skill. Framework files and shipped packages remain
read-only. External placement uses explicit registry import of an accepted
standalone artifact; Create still authors under its run-local package workspace.
Existing module objects in another running Python process are not revoked by a
CLI removal; verification uses fresh consumer processes.

## Existing-mode revision

```sh
refactory ceo "Revise the existing mode according to the full supplied request" \
  --mode create_fidelity --name existing_package \
  --workdir /absolute/fresh-authoring-run \
  --knob variant=update-existing \
  --knob revision_of=existing_package/run \
  --knob project_path=/absolute/original-project
```

Init copies the existing `.factory/packages/existing_package` without importing
the original code, validates the isolated copy in a contained child, and binds
`.factory/create-baseline.json` to the immutable Create request. The baseline
contains complete original file bytes and mode states. Researchers, planners,
builder and reviewers receive its full content through the native API context.
Reinitialization preserves candidate edits; source drift or a different request
is refused. `update-check` verifies the original source remains unchanged and
every non-target mode declaration survives. It explicitly does not infer
unchanged behavior from unchanged declarations. Required input checks apply to
the revised mode while graph validation still covers every package mode.

Rollback before explicit adoption is to discard the isolated candidate: the
original project has not been overwritten. The full baseline remains available
for review and byte restoration. Updating an installed package in place or
silently resolving a same-name installed/workspace clash is unsupported by the
actual registry, which refuses such clashes. Candidate imports compile current
source in the contained child rather than accepting timestamp-based stale
bytecode; installed framework/dependency loaders are unchanged.

## Native plugin

```sh
refactory ceo "Build a standalone native plugin with the full supplied contract" \
  --mode create_fidelity --name my_plugin --workdir /absolute/fresh-plugin-run \
  --knob variant=plugin
python -m refactory_dsh.create_fidelity_ops plugin-check my_plugin \
  --project /absolute/fresh-plugin-run
python -m refactory_dsh.registry add \
  /absolute/fresh-plugin-run/.factory/packages/my_plugin --project /absolute/consumer
python -m refactory_dsh.registry list --project /absolute/consumer --json
python -m refactory_dsh.registry show my_plugin/run --project /absolute/consumer
# Execute actual contract cases through create_fidelity_ops run with real knobs.
python -m refactory_dsh.registry remove my_plugin --project /absolute/consumer
```

The standalone source stays intact. Native checks retain raw command output and
status and prove cleanup even when validation reports an invalid graph. QA must
also execute the installed plugin's requested behavior with independently
derived cases; `plugin-check` is structural evidence. Admission/export must
finish before a claim of unchanged-package reuse freezes all relevant files,
including origin/admission metadata and generated SKILL. The authored digest
deliberately excludes those generated files and is insufficient for that claim.

After the builder's admission/export and freeze, QA, code/health reviewers,
Overwatch and documentation checks read the delivered package without modifying
it. Existing skill/admission receipts are evidence to inspect; admitting or
exporting again rewrites metadata and is not a read-only test. Mutation and
registration/import/remove lifecycle tests use disposable complete file copies
and separate consumers, with no links back to the delivered source. Compare
every original source file before/after each operation, including generated and
cache-named files. Restoring an earlier file after a mutation does not erase the
drift seen by concurrent peers; preserve and report it as a failure. Native
plugin verification uses actual `PACKAGE`/registry copy/import and consumer
execution, not foreign pip entry points or editable-install claims.

## Public adoption and saved runs

Public adoption remains pending full acceptance. Do not change
`refactory_dsh.create.CreateMode` or its graph: existing Python checkpoints must
continue to call that class. After acceptance, a separate faithful subclass can
use public name `create`, while a `create_legacy` registry/CLI alias retains the
old class and graph. Design and Fast keep their current classes, graphs and
fingerprints.

Before moving a public registry/CLI alias, freeze and test both classes, their
exact saved state dictionaries and graph/instruction equality. Python callers
can continue through the unchanged legacy class. Interactive checkpoints are a
stricter boundary: the saved mode identifier must equal the requested identifier,
and recovery binds graph name plus full source/configuration fingerprints,
including default_pack and declared ops. Simply using `--mode create_legacy`
cannot resume a prior `create` checkpoint. Preserve its pinned source/runtime
recovery path unless a narrowly tested explicit alias identity adapter retains
the original graph name and identical source fingerprints. Do not rewrite saved
identities or migrate them automatically. `Mode.resume` also intentionally
refuses a different graph or instruction set; opt-in checkpoints made before
these new instructions must use their pinned source archive. A fresh revised
candidate is not a migration of v7.

The adoption change must separately map existing `--single` and smoke knobs,
Task/data directives, names and revision targets. Reject unsupported contracts
before model dispatch; do not accept knobs that have no effect. Once both full
authoring and delivered capability evidence pass, root can review a small alias
change plus explicit rollback to the former alias. Keep legacy class and
checkpoint tests during that rollout; no acceptance or alias change is performed
by this variant implementation.

## Separate full-Create cases

Versioned neutral cases are defined at
[`variants/v1/cases.json`](../tests/fixtures/create_fidelity/variants/v1/cases.json).
`native-update-offset-v1` supplies an original +1 integer transform and unrelated
identity mode, requesting a +7 revision; that original package is intended input,
not a hand-finished output. `native-plugin-reverse-v1` starts with no package and
requests a Unicode-code-point reversal plugin. Neither case copies a booking
artifact or claims the booking checker's 56 cases.

Use a trusted frozen candidate lane configuration as a template for source,
route and interpreter settings. Preparation creates a new named trial with
private runtime homes, fresh policy, explicit frozen authoring knobs and only
the intended original update input. It never starts a model:

```sh
python -m benchmarks.create_fidelity.variant_cases prepare /absolute/new-variant-trial \
  --template /absolute/frozen-candidate-lane/config.json --case native-update-offset-v1
python -m benchmarks.create_fidelity.variant_cases preflight /absolute/new-variant-trial \
  --probe /absolute/completed-served-route-probe
# Launch the ordinary full authoring lane after that preflight passes:
python -m benchmarks.create_fidelity.harness live /absolute/new-variant-trial \
  --lanes dsh-candidate
python -m benchmarks.create_fidelity.variant_cases check /absolute/new-variant-trial
```

The separate single-lane trial retains normal current-digest human decisions and
owner-pipe lifecycle limits. Its copied independent checker module is denied
to the authoring lane, with an explicit negative control in preflight. After
delivery the operator launches the frozen checker under kernel network denial
with generated imports guarded against model use and all relevant source and
artifact bytes read-only. Eight independent cases verify actual native results
or honest invalid-input failures, plus original/unrelated behavior for updates
or installation/removal for plugins and unchanged delivered bytes. Malformed
JSON output produces a failed row and remaining cases continue. Missing OS
boundary evidence is unavailable. Capability results deliberately keep
`case_accepted=false`: final acceptance still needs independent complete Create
role/output/approval/lifecycle review. No live variant result is claimed here.

Each prepared variant allocates its own short identity-bound native temporary
root and records it in `operator/native-temp-creation.json` for controller
cleanup after ownership settles. Its policy permits that root and denies
template/peer projects and temporary roots; remote-only ADC, environment and
CLI mappings are removed from the DSH configuration. Preflight includes actual
own-root read/write and peer/global-temp denial controls. A template's passed
preflight does not establish a new variant's isolation.

Variant capability evaluator `create-fidelity-native-variant-capability/v2`
checks exact JSON integer tokens before value equality: booleans and decimal
floats cannot satisfy integer results. It keeps the original eight cases and
adds positive, negative and carry probes beyond 4300 decimal digits for the
update contract. The operator parses those tokens lexically without changing
the generated native child process's Python digit limit. All regular package
files are frozen through `create-fidelity-artifact-inventory/v1`, including
cache-named directories, generated metadata and authored data; symlinks and
special files are rejected. This is stricter than the generic source inventory
used by historical evaluator v1.

Use the pinned checker for a frozen historical trial. Running the current
`check` against a v1 trial is refused. A separate `supplemental-check` creates
`operator/evaluators/native-variant-v2`, pins the evaluator, inventory helper,
supervisor and copied dependencies, and uses a distinct supervisor directory.
It preserves the original trial/configuration, frozen checker, lifecycle and
capability result. Supplemental evidence names its version and provenance; it
does not claim v1 performed those stronger checks and cannot establish full
authoring acceptance.
