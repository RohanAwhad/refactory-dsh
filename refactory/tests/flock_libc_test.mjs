/** The pinned flock loads and locks without generating a diagnostic report. */
import { createRequire } from 'node:module'
import { closeSync, mkdtempSync, openSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const [root] = process.argv.slice(2)
const project = createRequire(join(root, 'package.json'))
const installed = createRequire(project.resolve('@deepseek-ai/dsh/package.json'))
const persistence = createRequire(installed.resolve('@deepseek-ai/dsh-session-persistence-jsonl/package.json'))
const flock = await import(pathToFileURL(persistence.resolve('@deepseek-ai/node-addon-system/flock')).href)

// A report walks every libuv handle and aborts the process when a socket is closing.
process.report.getReport = () => { throw new Error('flock generated a diagnostic report') }
const fd = openSync(join(mkdtempSync(join(tmpdir(), 'flock-')), 'lock'), 'w')
try {
  await flock.tryLockExclusive(fd)
} finally {
  closeSync(fd)
}
