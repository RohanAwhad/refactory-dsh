// Preload the selected source runtime without changing the agent's cwd or argv.
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const entry = resolve(process.argv[1])
const runtimeRoot = resolve(dirname(entry), '../../..')
const require = createRequire(pathToFileURL(entry))
const { register } = await import(pathToFileURL(require.resolve('tsx/esm/api')).href)
const tsconfig = resolve(runtimeRoot, 'tsconfig.json')
register({ tsconfig: existsSync(tsconfig) ? tsconfig : false })
