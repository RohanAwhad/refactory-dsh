/**
 * The graph app's command-line provider over a real Context: argv in, the run's
 * `graphStartup` service out. The spine and the runner both read that service, so
 * what it publishes is what a run is.
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { internals, provideCmdline } from '@deepseek-ai/dsh-cmdline'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apply, GRAPH_STARTUP_SERVICE, type GraphStartupValues } from '../src/startup.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
  internals.stdout = process.stdout
  internals.stderr = process.stderr
})

/** A graphs directory holding one `<mode>.graph.json`. */
function graphs(mode: string, body: unknown = { name: mode, nodes: {}, edges: [] }): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-graph-startup-'))
  dirs.push(dir)
  writeFileSync(join(dir, `${mode}.graph.json`), JSON.stringify(body))
  return dir
}

/** Boot the provider over a Context and collect what the invocation produced. */
function boot(
  args: string[],
  applyFn: typeof apply = apply,
): { values: GraphStartupValues | undefined; exits: number[]; out: string } {
  const exits: number[] = []
  let out = ''
  internals.stdout = { write: (chunk: string) => { out += chunk; return true } }
  internals.stderr = internals.stdout
  const ctx = new Context()
  provideCmdline(ctx, { args, exit: code => void exits.push(code) })
  applyFn(ctx)
  return { values: ctx.get(GRAPH_STARTUP_SERVICE) as GraphStartupValues | undefined, exits, out }
}

function required(dir: string, mode = 'probe'): string[] {
  const workdir = mkdtempSync(join(tmpdir(), 'dsh-graph-work-'))
  dirs.push(workdir)
  return [
    '--mode', mode, '--task', 'circle_packing', '--budget', '7',
    '--graphs', dir, '--workdir', workdir, '--python-bin', '/venv/bin',
  ]
}

describe('graph command-line provider', () => {
  it('publishes the graph, the work directory and the node environment', () => {
    const dir = graphs('probe')
    const { values, exits } = boot(required(dir))
    expect(exits).toEqual([])
    expect(values?.mode).toBe('probe')
    expect(values?.taskName).toBe('circle_packing')
    expect(values?.budget).toBe(7)
    expect(values?.pythonBin).toBe('/venv/bin')
    expect(values?.graph).toEqual({ name: 'probe', nodes: {}, edges: [] })
    expect(values?.commandEnv.SRF_TASK).toBe('circle_packing')
    expect(values?.commandEnv.SRF_BUDGET).toBe('7')
    expect(values?.commandEnv.REFACTORY_INTENT).toBe('circle_packing')
    expect(values?.commandEnv.PATH?.startsWith('/venv/bin:')).toBe(true)
    expect(values?.commandEnv.REFACTORY_PYTHON_BIN).toBe('/venv/bin/python')
  })

  it('passes the knob overrides through the environment, already JSON', () => {
    const dir = graphs('probe')
    const { values } = boot([...required(dir), '--knobs', '{"grid":24}'])
    expect(JSON.parse(values?.commandEnv.SRF_KNOBS ?? '{}')).toEqual({ grid: 24 })
  })

  it('names the path and the generator when the graph is missing', () => {
    const dir = graphs('probe')
    expect(() => boot(required(dir, 'absent'))).toThrow(/no graph at .*absent\.graph\.json.*srf graphs --out/)
  })

  it('prints its own help and publishes nothing', () => {
    const dir = graphs('probe')
    const { values, exits, out } = boot([...required(dir), '--help'])
    expect(out).toContain('dsh --profile graph')
    expect(values).toBeUndefined()
    expect(exits).toEqual([0])
  })

  it('defaults the work directory when none is named', () => {
    const dir = graphs('probe')
    const args = required(dir)
    args.splice(args.indexOf('--workdir'), 2)
    const { values } = boot(args)
    expect(values?.workdir).toContain('dsh-run-probe-')
  })

  it('carries a provider and model override through when named', () => {
    const dir = graphs('probe')
    const { values } = boot([...required(dir), '--provider', 'deepseek', '--model', 'flash'])
    expect(values?.provider).toBe('deepseek')
    expect(values?.model).toBe('flash')
  })

  it('is a one-shot run unless --chat asks for a conversation', () => {
    // The flag is how `ceo` asks for the live session; its absence is the run
    // scripts and CI have always had, so the default must say so rather than
    // reading as "chat was not mentioned".
    const dir = graphs('probe')
    expect(boot(required(dir)).values?.chat).toBe(false)
    expect(boot([...required(dir), '--chat']).values?.chat).toBe(true)
  })

  it('names only the interpreter when the environment has no PATH', () => {
    const dir = graphs('probe')
    const saved = process.env.PATH
    delete process.env.PATH
    try {
      const { values } = boot(required(dir))
      expect(values?.commandEnv.PATH).toBe('/venv/bin')
    } finally {
      process.env.PATH = saved
    }
  })

  it('resolves its graph and interpreter defaults from the environment first', async () => {
    // The defaults are computed at module load, so the branches resolveDep takes
    // are only observable by importing the module under a different environment
    // and letting it load a graph from the default location.
    const saved = {
      SRF_GRAPHS: process.env.SRF_GRAPHS,
      VENV_BIN: process.env.VENV_BIN,
      REFACTORY_DSH_HOME: process.env.REFACTORY_DSH_HOME,
    }
    const scratch = mkdtempSync(join(tmpdir(), 'dsh-graph-env-'))
    dirs.push(scratch)
    try {
      const explicit = join(scratch, 'graphs')
      mkdirSync(explicit, { recursive: true })
      writeFileSync(join(explicit, 'probe.graph.json'), JSON.stringify({ name: 'from-env' }))
      process.env.SRF_GRAPHS = explicit
      process.env.VENV_BIN = '/python'
      vi.resetModules()
      const overridden = await import('../src/startup.ts')
      const args = ['--mode', 'probe', '--task', 't', '--workdir', join(scratch, 'work')]
      const first = boot(args, overridden.apply)
      expect(first.values?.graph).toEqual({ name: 'from-env' })
      expect(first.values?.commandEnv.PATH?.startsWith('/python:')).toBe(true)
      expect(first.values?.commandEnv.REFACTORY_PYTHON_BIN).toBe('/python/python')

      delete process.env.SRF_GRAPHS
      delete process.env.VENV_BIN
      const home = join(scratch, 'home')
      mkdirSync(join(home, 'refactory', 'vendor', 'srf', 'graphs'), { recursive: true })
      writeFileSync(
        join(home, 'refactory', 'vendor', 'srf', 'graphs', 'probe.graph.json'),
        JSON.stringify({ name: 'from-home' }),
      )
      process.env.REFACTORY_DSH_HOME = home
      vi.resetModules()
      const rooted = await import('../src/startup.ts')
      const second = boot(args, rooted.apply)
      expect(second.values?.graph).toEqual({ name: 'from-home' })
      expect(second.values?.commandEnv.PATH?.startsWith(`${home}/.venv/bin:`)).toBe(true)
      expect(second.values?.commandEnv.REFACTORY_PYTHON_BIN).toBe(`${home}/.venv/bin/python`)
    } finally {
      for (const [key, value] of Object.entries(saved)) {
        if (value === undefined) Reflect.deleteProperty(process.env, key)
        else process.env[key] = value
      }
      vi.resetModules()
    }
  })
})
