// Rewrite only obsolete re:factory selections under the harness's writer lock.
// The request names the pinned install, profile directory and obsolete names.
// Reports go to stdout; the caller reports the retained backup to the operator.
import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

/** Resolve through the selected harness and plugin manager, so another version
 * in pnpm's store cannot supply a different lock protocol. */
export async function loadAtomicWrite(anchor) {
  const root = createRequire(join(anchor, "package.json"));
  const harness = createRequire(root.resolve("@deepseek-ai/dsh/package.json"));
  const manager = createRequire(harness.resolve("@deepseek-ai/dsh-plugin-manager"));
  return import(pathToFileURL(manager.resolve("@deepseek-ai/dsh-atomic-write")).href);
}

/** Read, decide, back up and replace while holding the same lock as dsh plugin. */
export async function cleanProfile(request) {
  const manifestPath = join(request.profileDir, "package.json");
  const atomic = await loadAtomicWrite(request.anchor);
  return atomic.withFileLock(manifestPath, async () => {
    const before = await readFile(manifestPath, "utf8");
    const manifest = JSON.parse(before);
    const declared = manifest?.dsh?.profile?.bundles;
    if (!Array.isArray(declared)) return { status: "no-bundle-list" };

    const obsolete = new Set(request.obsolete);
    const kept = declared.filter((name) => !obsolete.has(name));
    const removed = declared.filter((name) => obsolete.has(name));
    if (removed.length === 0) return { status: "clean", removed: [] };

    const backupPath = `${manifestPath}.refactory-backup-${randomUUID()}`;
    // Exclusive creation retains every pre-image across retries. If this fails,
    // the live manifest is untouched. The backup survives success and failure.
    await writeFile(backupPath, before, { mode: 0o600, flag: "wx" });
    manifest.dsh.profile.bundles = kept;
    await atomic.writeFileAtomic(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 });
    return { status: "rewritten", removed, kept, backupPath };
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const request = JSON.parse(await readFile(process.argv[2], "utf8"));
  process.stdout.write(JSON.stringify(await cleanProfile(request)));
}
