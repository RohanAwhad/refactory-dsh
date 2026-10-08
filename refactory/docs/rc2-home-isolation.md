# Upgrading to 0.2.0-rc.2: isolate the harness home

This checkout pins `@deepseek-ai/dsh@0.2.0-rc.2` and runs it through `node_modules/.bin/dsh`. That harness moves the global settings document aside on its first boot and imports it into the profile it booted. The move is a rename, so the global document stops existing at its old path. Run it against a home another harness generation already uses and the two generations contend for one `settings.yaml`.

Two harness generations can share this machine. They do not share a settings layout, and they must not share a home.

## What changes

| | the `dsh` binary on `PATH` | this checkout's `node_modules/.bin/dsh` |
|---|---|---|
| package | the operator's own DSH install (`dsh-tauri`, version `0.1.6-alpha.1`) | `@deepseek-ai/dsh@0.2.0-rc.2` |
| settings layout | one global document | profile-owned configuration |
| migration on boot | none | `importLegacyDocument()` |

The migration is `importLegacyDocument()`, a method of the `SettingsForms` service in the package `@deepseek-ai/dsh-settings@0.2.0-rc.2`, at `lib/index.js` lines 346-363. The service schedules it from its constructor once the Loader settles: `ctx.root.loader.await().then(() => this.importLegacyDocument())` (line 339).

The method reads the document at `join(profile.home, "settings.yaml")` — the global file under the resolved harness home, not a per-profile path. It then does three things:

1. `const imported = \`${path}.imported\`` then `await rename(path, imported)` — the document is renamed **before the first write**, so a partial import never repeats.
2. Parses the renamed file and calls `this.update(section, values)` for each top-level section. Sections are written into the booted profile's `cordis.patch.yml`; a section the running composition rejects is logged and left only in the renamed file.
3. Logs `settings: imported %s into profile %s`.

This is intentional upstream behaviour. The harness deliberately migrates away from a single global document, and the rename-first ordering is what makes the import idempotent — the second boot finds no `settings.yaml` and returns at line 349. Do not patch it out, and do not edit `node_modules` by hand; the documented patch set in [Upstream compatibility](upstream-compatibility.md) covers five unrelated compatibility changes.

Section names are remapped through a small table before the write: `ui-developer-tools` and `ui-onboarding` both go to `ui-settings`, and `shell` goes to the platform's shell-sandbox entry.

## What the operator risks

Two failures, and the second is the serious one.

**The global document stops being the live file.** After the first rc.2 boot, edits made through the fork's GUI no longer reach the harness, and edits made through rc.2 no longer reach the global file. A section the running rc.2 composition rejects is reported with a `warn` line and is never written anywhere except the renamed file.

**The rename can be the only copy.** `rename(path, imported)` overwrites its target. There is no timestamp and no collision check. A home that is booted, restored by hand, and booted again loses the intermediate state: the second boot renames the restored file onto the same `settings.yaml.imported` name and replaces whatever the first boot left there. Verified against a throwaway home — two successive boots each produced a byte-identical copy of that boot's input at `settings.yaml.imported`, with no second file created.

A backup taken before the first boot is the only durable protection. Take it outside the home.

### The files that appear

Read from the live home on this machine:

```
~/.dsh/settings.yaml                            2516 bytes   the restored live document
~/.dsh/settings.yaml.imported                   2516 bytes   byte-identical to the above
~/.dsh/settings.yaml.imported-20261001-1813     2540 bytes   carries a timestamp
~/.dsh/settings.yaml.bak                         274 bytes
~/.dsh/settings.yaml.lost-20261001-145440         99 bytes   carries a timestamp
```

Only `settings.yaml.imported` — the plain suffix, no timestamp — is produced by `importLegacyDocument()`. Confirmed by grep: the string `.imported` occurs exactly once in that file, at line 350, and the interpolated name has no date component anywhere in the package.

The two timestamped names are **not** attributable to verified code. `settings.yaml.imported-20261001-1813` and `settings.yaml.lost-20261001-145440` exist on this machine, but neither this checkout, nor the fork's source, nor the pinned rc.2 packages contain the strings that would write them. Treat them as operator backups of unknown provenance. Their contents differ from every other variant, so they are not interchangeable with `settings.yaml.imported`, and they are unavailable after the fact to prove which file a given boot consumed.

Because `settings.yaml.imported` is overwritten in place, its modification time equals the live document's to the second on this machine — both `2026-10-01 20:23:06` — which is consistent with a rename out of a restore, but does not by itself date the import.

## The isolation remedy

Give each harness generation its own `DSH_HOME`. The environment variable is read by `@deepseek-ai/dsh-home-paths`'s `resolveDshHome()`; precedence is an explicit configured path, then `$DSH_HOME`, then `~/.dsh`. An empty or whitespace-only `DSH_HOME` is treated as unset, so the fallback is never the current directory.

Both harness generations honour `DSH_HOME`, so one variable separates them completely. This checkout's Python reads it in `refactory_dsh/dsh_profile.py:176-179` and `refactory_dsh/model.py:204`.

The examples below use `~/.dsh-rc2` for the pinned rc.2 harness and leave `~/.dsh` to the fork. Any two distinct absolute paths work; keep the rc.2 home outside `~/.dsh` so the rename can never reach the fork's document.

### (a) `./install.sh` — provisions profiles into the rc.2 home

```bash
cd /path/to/refactory-dsh
DSH_HOME="$HOME/.dsh-rc2" ./install.sh
```

`install.sh` exports `DSH="$HERE/node_modules/.bin/dsh"` and runs the plan from `dsh_profile.render_plan()` through it, so `DSH_HOME` selects where the pinned harness writes. Verified: with `DSH_HOME` set to a `/tmp` path, the script created `profiles/graph` and `profiles/refactory` under it and left `~/.dsh` untouched. `REFACTORY_SKIP_DSH_PROFILE=1` skips profile provisioning entirely.

The plan for a fresh home is four rows, in this order:

| row | profile | effect |
|---|---|---|
| `add` | `graph` | `@deepseek-ai/dsh-graph`, installed from `packages/bundle/graph` by path |
| `init` | `refactory` | copies the shipped `web` template's bundle list |
| `add` | `refactory` | `@deepseek-ai/dsh-refactory` — the `ceo` preset and roster |
| `add` | `refactory` | `@deepseek-ai/dsh-refactory-web` — console, picker, brand rows |

After it, the two homes hold:

- **`$HOME/.dsh-rc2`** — `profiles/graph` and `profiles/refactory`, plus everything the pinned harness writes later (`sessions/`, `storages/`, `.credentials.yaml`, `.anonymous-user-id`). It starts with no `settings.yaml`, which is what makes the first boot safe.
- **`$HOME/.dsh`** — the fork's `settings.yaml` and `settings.yaml.*` files, and the fork's `profiles/`. On this machine those are `graph`, `headless`, `martty`, `probe`, `refactory`, `refactory-web` and `web`, with `refactory` carrying `['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', '@deepseek-ai/dsh-refactory', '@deepseek-ai/dsh-refactory-web']`. No rc.2 profile is created there.

The project-local sharing between the two homes is unavoidable and harmless: `profiles/<name>` holds absolute symlinks, and both homes link to the same `packages/bundle/*` directories in this checkout. Each home keeps its own `profiles/<name>/package.json`, so writes stay separated.

### (b) `./refactory-dsh` — boots the GUI from the rc.2 home

```bash
cd /path/to/refactory-dsh
DSH_HOME="$HOME/.dsh-rc2" ./refactory-dsh --port 3081 --no-open
```

The launcher heals a missing layer into the selected home before booting, so it reads the same `DSH_HOME`. Verified against a `/tmp` home: it provisioned `graph` and `refactory` there. It ends in `exec "$DSH" --profile refactory "$@"`, so the whole session runs on the pinned harness from `$HOME/.dsh-rc2`.

### (c) the rc.2 GUI

`uv run refactory ui` is the same launcher by another route. `cmd_ui` resolves `_launcher()` to `REPO_ROOT / "refactory-dsh"` and calls it with `--port` and `--no-open`:

```bash
cd /path/to/refactory-dsh
DSH_HOME="$HOME/.dsh-rc2" uv run refactory ui --port 3081 --no-open
```

`refactory ui` accepts `--port <int>` (default: the launcher's own) and `--no-open`. Anything beyond those two flags has to go through `./refactory-dsh` directly.

### (d) the Python glue's model calls

The glue resolves its model home once, before containment rewrites `HOME`:

1. An explicit `DshModelClient(dsh_home=...)` path.
2. Nonblank `REFACTORY_MODEL_DSH_HOME`.
3. Nonblank `DSH_HOME`.
4. The original user home's `.dsh` directory.

Paths are trimmed, expanded and resolved to absolute paths. Whitespace-only
values are treated as unset. The model subprocess and graph runtime receive
both `DSH_HOME` and `REFACTORY_MODEL_DSH_HOME` set to that resolved path. Runtime
preflight inspects the graph profile in the same home, including an explicit
client override. A missing profile's repair command includes `DSH_HOME` so it
provisions the home the run will actually use.

```bash
cd /path/to/refactory-dsh
REFACTORY_MODEL_DSH_HOME="$HOME/.dsh-rc2" uv run refactory ceo "an idea" --mode design
```

Verified by probe: with `DSH_HOME=/tmp/probe-dsh` and `REFACTORY_MODEL_DSH_HOME=/tmp/probe-refactory`, `DshModelClient()._dsh_home` resolved to `/tmp/probe-refactory` and both child variables carried that value. With `REFACTORY_MODEL_DSH_HOME` unset, `DSH_HOME=/tmp/probe-dsh` was honoured. With neither set, it resolved to `/home/kai/.dsh`.

`model.py:184`, `:201` and `:202` read `REFACTORY_MODEL_EFFORT`, `REFACTORY_MODEL_ID` and `REFACTORY_MODEL_PROVIDER` — the route, not the home. They apply only when the worker backend is `dsh` and no routes were resolved, and they do not affect which home is used.

### Installation/session and model diagnostics

Installation and GUI provisioning use `DSH_HOME`, falling back to `~/.dsh`.
`REFACTORY_MODEL_DSH_HOME` selects model workers independently; it does not
redirect `install.sh` or `./refactory-dsh`. With `DSH_HOME=A` and
`REFACTORY_MODEL_DSH_HOME=B`, installation and the GUI target A, while model
workers and graph runtime preflight target B. The console tool explicitly forwards
its session `DSH_HOME`; the harness subprocess provider preserves the ordinary
`REFACTORY_MODEL_DSH_HOME` variable, so console model workers follow the same
precedence as the Python CLI. Contained workers carry the resolved client home
in their serialized context before `HOME` is rewritten.

`refactory doctor` reports both resolved homes. It checks the installation's
owned profiles and any attached Martty profile, and additionally checks the
model home's graph profile when the homes differ. It only reads and reports.
To provision a separate model home, run installation explicitly against it:

```bash
DSH_HOME="$HOME/.dsh-model" ./install.sh
DSH_HOME="$HOME/.dsh-rc2" REFACTORY_MODEL_DSH_HOME="$HOME/.dsh-model" uv run refactory doctor
```

### Stranded bundle selections: upstream follow-up (#286)

At the pinned `0.2.0-rc.2`, plugin-manager already has a deselect primitive:
`selectBundle(name, false)` removes a selected name without requiring a
corresponding dependency. The remote method `setBundleEnabled(name, false)`
wraps it. However, `dsh plugin --profile NAME remove PACKAGE` forwards to pnpm;
a name absent from `dependencies` makes removal fail, and `reconcile()` preserves
that selection. The upstream request is to expose deselection through the CLI,
or tolerate dangling selections in CLI removal.

The offline installer cannot simply call `./remote`: an import probe finds only
`TYPERT_REMOTE` and `default` protocol metadata, with no executable manager.
Constructing `PluginManager` without a host context fails while reading
`ctx.reflect`. The implementation also uses `ctx.profileContext`, a profile
writer lock, the owning context and loader/HMR services. A host RPC invocation
requires a running profile, whose startup can already fail on the stranded
selection. Calling an internal prototype with a fabricated host would introduce
another unsupported adapter. Keep the owned-manifest cleanup using the pinned
writer lock and atomic writer until a supported offline operation exists.

Registry and tarball checks on 2026-10-06 found `latest` and `next` still at
`0.2.0-rc.2`, with `alpha` at `0.2.1-alpha.1`. The alpha plugin-manager's
`lib/types/operations.js` is byte-identical to the pinned file; upgrading to that
alpha does not fix CLI reconciliation.

## Safe first run

Set `H` once per shell and use it throughout. Substitute your own rc.2 home path.

```bash
H="$HOME/.dsh-rc2"
```

1. **Back up the live document before anything else, outside the home.** `importLegacyDocument()` renames the file inside the home, so a backup left inside `~/.dsh` is itself a file the next boot can collide with.

   ```bash
   cp -p "$HOME/.dsh/settings.yaml" "$HOME/.dsh/settings.yaml.pre-rc2.$(date +%Y%m%d-%H%M%S)"
   sha256sum "$HOME/.dsh/settings.yaml" "$HOME"/.dsh/settings.yaml.pre-rc2.*
   ```

   Record the sha256. It is the reference for step 4, and it is the only comparison that distinguishes a `rename` from a `write`. On this machine the expected value is `1958a76fa29edd3382b29d6a52f3c0f1be1eebc93e1337d16b4bfd3f527664e7`. Copy it to a second location if it matters — a backup that shares a filesystem with the original is not a backup.

2. **Confirm the two homes are distinct and that the rc.2 home starts clean.**

   ```bash
   ls -d "$HOME/.dsh" "$H"
   ls "$H/settings.yaml" 2>&1   # expected: No such file or directory
   ```

   `No such file or directory` is the safe state. A file present here is a document this home's first boot would rename.

3. **Provision and boot rc.2 in the isolated home.** The first boot is the one that migrates.

   ```bash
   cd /path/to/refactory-dsh
   DSH_HOME="$H" ./install.sh
   DSH_HOME="$H" uv run refactory doctor
   DSH_HOME="$H" ./refactory-dsh --port 3081 --no-open
   ```

   Provisioning reads and writes only under `$H`. Verified against a `/tmp` home.

4. **Confirm the original is untouched.** While the GUI is running is the best moment to check, because the migration has already fired.

   ```bash
   sha256sum "$HOME/.dsh/settings.yaml"
   ls -la "$HOME/.dsh"/settings.yaml*
   ```

   The sha256 must still match step 1, and the `settings.yaml*` listing must be exactly the set step 1 saw. Any new `.imported` file, or a changed digest, means the rc.2 boot reached the fork's home: stop, and restore with the section below.

5. **Confirm the migration landed in the rc.2 home**, where it is expected and harmless.

   ```bash
   ls -la "$H"/settings.yaml* 2>&1
   ```

   At this point `$H` has no `settings.yaml` to import, so nothing should appear here either. If you deliberately placed a settings document in `$H`, seeing `settings.yaml.imported` there is the migration working as designed.

## Detecting that it already happened, and restoring

### Detect

The signature is a `settings.yaml`/`settings.yaml.imported` pair in one harness home, with the global document no longer live.

```bash
ls -la "$HOME/.dsh"/settings.yaml*
```

Two conditions confirm it:

- **`settings.yaml.imported` exists.** The rc.2 harness renamed the document rather than copying it. This file is a real document, not a stub, and is byte-identical to what the global file held at the moment of the rename when the rename was the last writer.
- **`settings.yaml` is absent, or its content is a restore.** If a file is present, its digest settles which: matching a backup you took means you already restored it, and does not mean the migration did not happen.

On this machine both files are present and byte-identical — sha256 `1958a76fa29edd3382b29d6a52f3c0f1be1eebc93e1337d16b4bfd3f527664e7` for each — which is the "migrated, then restored by hand" state. The rc.2 profile directories under `~/.dsh/profiles/` are the other marker: this checkout's rc.2 harness wrote them, and the fork never creates `graph` or a re:factory-owned `refactory` with those four bundles.

Compare every variant before choosing a file to restore from. They are not equivalents:

```bash
sha256sum "$HOME/.dsh"/settings.yaml* && ls -la "$HOME/.dsh"/settings.yaml*
```

The largest file is not necessarily the right one. `settings.yaml.imported-20261001-1813` on this machine is 24 bytes larger than `settings.yaml.imported` but hashes differently, and neither timestamped name is attributable to verified code.

### Restore

1. **Stop every harness process** on the affected home. A running rc.2 boot can rename what you restore, which is how a restore becomes the next loss.
2. **Diff before you copy.** Neither candidate is guaranteed to be the newer document.

   ```bash
   diff -u "$HOME/.dsh/settings.yaml" "$HOME/.dsh/settings.yaml.imported"
   ```

   An empty diff means the two agree and the choice does not matter. A non-empty diff means deciding which document you want, and that is a reading task, not a command.
3. **Restore the chosen file to `settings.yaml`**, leaving the source in place:

   ```bash
   cp -p "$HOME/.dsh/settings.yaml.imported" "$HOME/.dsh/settings.yaml"
   ```

   Do not `mv` the only copy back. `rename` destroyed the previous occupant of the destination once already.
4. **Record the new digest and keep a copy outside the home**, then re-run the detection command above. Expect `settings.yaml` present with the digest you just recorded, and the `.imported` file unchanged.
5. **Re-check the profile-owned configuration the import wrote.** Sections that were imported were written into the rc.2 profile's `cordis.patch.yml`, under `$H/profiles/<name>/`. A restore of the global document does not remove them, and a section present in both places is applied twice:

   ```bash
   grep -rn "agent-default-model\|llm-pi-ai" "$HOME/.dsh-rc2"/profiles/*/cordis.patch.yml
   ```

6. **Prevent the recurrence** by running step 3 of the safe first-run procedure above — every rc.2 invocation with an explicit `DSH_HOME`.

## What this document could not verify

- **The origin of the timestamped names.** `settings.yaml.imported-<stamp>` and `settings.yaml.lost-<stamp>` are present on this machine, but no verified source in this checkout, in the fork, or in the pinned rc.2 packages contains the code that writes them. They are described as observed files of unknown provenance, not as harness behaviour.
- **The exact date of the destructive import.** The `.imported` file's mtime equals the live document's, so it dates the rename that produced it but not the import that preceded it.
- **Word-for-word configuration of the rc.2 GUI under an isolated home.** Provisioning and the profile plan were executed against `/tmp` homes and verified. A full interactive GUI session on a fresh isolated home was not run, because that requires booting the pinned harness and making model requests. The launcher path, the `DSH_HOME` plumbing and the resulting profile set were verified.
