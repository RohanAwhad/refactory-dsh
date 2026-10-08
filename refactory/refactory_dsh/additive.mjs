// Derive an owned additive profile without changing the source profile.
// File URLs resolve checkout plugins, including browser modules.
import { readFile, mkdir, writeFile, rename, cp, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export async function additiveOverlay(anchor, profileName) {
  const root = createRequire(join(anchor, 'package.json'));
  const harness = createRequire(root.resolve('@deepseek-ai/dsh/package.json'));
  const boot = await import(pathToFileURL(harness.resolve('@deepseek-ai/dsh-app-boot')).href);
  const { resolveDshHome } = await import(pathToFileURL(harness.resolve('@deepseek-ai/dsh-home-paths')).href);
  const { parseDocument, visit, isScalar } = harness('yaml');
  const home = resolveDshHome();
  const installAnchor = root.resolve('@deepseek-ai/dsh/package.json');
  const directory = boot.resolveProfileDir(profileName, home);
  const profile = existsSync(join(directory, 'package.json'))
    ? boot.loadProfileDirectory('refactory additive', directory, installAnchor)
    : boot.loadProfile('refactory additive', profileName, installAnchor, home);
  if (profile.skippedBundles.length) {
    throw new Error(profile.skippedBundles.map(bundle => bundle.reason).join('; '));
  }
  const entries = boot.composeEntries([
    ...profile.layers.map(layer => layer.patches), profile.patches,
    boot.loadOptionalPatches('refactory additive', join(home, 'cordis.patch.yml')) ?? [],
  ]);
  const ids = new Set();
  const presets = new Set();
  let refactoryBrand;
  const collect = rows => {
    for (const row of rows) {
      ids.add(row.id);
      if (row.id === 'ui-brand-refactory') refactoryBrand = row;
      if (row.config?.id && Array.isArray(row.config.plugins)) presets.add(row.config.id);
      if (row.group && Array.isArray(row.config)) collect(row.config);
    }
  };
  collect(entries);
  if (!ids.has('agent-preset-registry') || !profile.layers.some(layer => layer.packageName === '@deepseek-ai/dsh-web-app')) {
    throw new Error(`profile ${JSON.stringify(profileName)} must contain the harness web surface`);
  }
  const modules = new Map();
  for (const directory of ['workflow/workflow-spine', 'refactory/tool-refactory',
    'refactory/refactory-console', 'client/ui-refactory']) {
    const base = join(anchor, 'packages', directory);
    const manifest = JSON.parse(await readFile(join(base, 'package.json'), 'utf8'));
    const file = join(base, manifest.main);
    if (!existsSync(file)) throw new Error(`${file} is missing; run ./install.sh first`);
    modules.set(manifest.name, pathToFileURL(file).href);
  }
  modules.set('@deepseek-ai/dsh-refactory-web/startup', pathToFileURL(join(anchor, 'packages/bundle/refactory-web/lib/startup.js')).href);
  const documents = [];
  for (const file of ['packages/bundle/refactory/presets/ceo.patch.yml', 'packages/bundle/refactory-web/additive.patch.yml']) {
    const doc = parseDocument(await readFile(join(anchor, file), 'utf8'));
    if (doc.errors.length) throw doc.errors[0];
    visit(doc, { Pair: (_, pair) => {
      if (isScalar(pair.key) && pair.key.value === 'name' && isScalar(pair.value) && modules.has(pair.value.value)) {
        pair.value.value = modules.get(pair.value.value);
      }
    } });
    doc.contents.items = doc.contents.items.filter(patch => {
      const insert = patch.get('insert', true);
      insert.items = insert.items.filter(row => !ids.has(row.get('id'))
        && !presets.has(row.get('config', true)?.get?.('id')));
      return insert.items.length > 0;
    });
    if (doc.contents.items.length) documents.push(doc.toString());
  }
  // A profile carrying the standalone brand must still expose its complete
  // native picker in the derived additive profile.
  if (refactoryBrand && refactoryBrand.disabled !== true) {
    documents.push('- id: ui-brand-refactory\n  disabled: true\n');
    if (ids.has('ui-brand-official')) documents.push('- id: ui-brand-official\n  disabled: false\n');
  }
  return documents.join('\n');
}

export async function ensureAdditiveProfile(anchor, sourceName) {
  const root = createRequire(join(anchor, 'package.json'));
  const harness = createRequire(root.resolve('@deepseek-ai/dsh/package.json'));
  const boot = await import(pathToFileURL(harness.resolve('@deepseek-ai/dsh-app-boot')).href);
  const { resolveDshHome } = await import(pathToFileURL(harness.resolve('@deepseek-ai/dsh-home-paths')).href);
  const home = resolveDshHome();
  const target = boot.resolveProfileDir('refactory-web', home);
  const manifestFile = join(target, 'package.json');
  if (existsSync(manifestFile)) {
    const manifest = JSON.parse(await readFile(manifestFile, 'utf8'));
    if (!manifest.refactoryAdditive) {
      throw new Error('refactory-web already exists and is not an additive profile; choose a different DSH_HOME or rename that profile');
    }
    if (sourceName && manifest.refactoryAdditive.source !== sourceName) {
      throw new Error(`refactory-web was derived from ${manifest.refactoryAdditive.source}, not ${sourceName}; use its original source or a different DSH_HOME`);
    }
    return 'refactory-web';
  }
  sourceName ||= 'web';
  if (sourceName === 'refactory-web') throw new Error('the additive source cannot be refactory-web');
  const overlay = await additiveOverlay(anchor, sourceName);
  const source = boot.resolveProfileDir(sourceName, home);
  const temporary = `${target}.${randomUUID()}`;
  await mkdir(join(home, 'profiles'), { recursive: true });
  try {
    // Copy configuration and installed dependency links into an independent
    // directory; never point the whole node_modules directory at the source.
    await cp(source, temporary, { recursive: true, verbatimSymlinks: true });
    const manifest = JSON.parse(await readFile(join(temporary, 'package.json'), 'utf8'));
    manifest.refactoryAdditive = { source: sourceName };
    await writeFile(join(temporary, 'package.json'), JSON.stringify(manifest, null, 2) + '\n');
    const patch = join(temporary, 'cordis.patch.yml');
    const original = existsSync(patch) ? await readFile(patch, 'utf8') : '';
    // YAML sequence concatenation preserves !!js expressions and source rows.
    const { parseDocument } = harness('yaml');
    const document = parseDocument(original || '[]');
    if (document.errors.length) throw document.errors[0];
    if (document.contents === null) document.contents = parseDocument('[]').contents;
    const additions = parseDocument(overlay || '[]');
    for (const row of additions.contents.items) document.contents.items.push(row);
    await writeFile(patch, document.toString(), { mode: 0o600 });
    await rename(temporary, target);
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
  return 'refactory-web';
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [anchor, profile] = process.argv.slice(2);
    process.stdout.write(await ensureAdditiveProfile(anchor, profile));
  } catch (error) {
    process.stderr.write(`refactory-dsh: ${error.message}\n`);
    process.exitCode = 1;
  }
}
