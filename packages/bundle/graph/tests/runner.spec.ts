/**
 * The graph runner: one CEO agent, driven to quiescence, reported as JSON.
 *
 * The runner is the only thing that turns a finished session into the object
 * `refactory_dsh.dsh_runtime` reads, so what it reports — the mode, the task, the
 * budget, the work directory, the best score and the CEO's last words — is the run
 * as far as the Python side is concerned.
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { Readable } from 'node:stream'
import { Context } from '@deepseek-ai/cordis'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apply as applyRunner, type Io } from '../src/index.ts'
import { GRAPH_STARTUP_SERVICE, type GraphStartupValues } from '../src/startup.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
  vi.restoreAllMocks()
})

/** A work directory with the given files in it. */
function workdir(files: Record<string, string> = {}): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-graph-run-'))
  dirs.push(dir)
  for (const [name, body] of Object.entries(files)) {
    mkdirSync(dirname(join(dir, name)), { recursive: true })
    writeFileSync(join(dir, name), body)
  }
  return dir
}

interface Harness {
  exits: number[]
  out: () => string
  err: () => string
  createOptions: Array<{ provider?: string; model?: string }>
  setupCalls: number
  followups: string[]
}

/** The text of one user message, for asserting what reached the agent. */
function textOf(message: unknown): string {
  const content = (message as { content?: Array<{ type?: string; text?: string }> }).content ?? []
  return content.filter(block => block.type === 'text').map(block => block.text ?? '').join('')
}

/** Mount the runner over fake core services and collect what it reported. */
async function bench(options: {
  workdir?: string
  mode?: string
  provider?: string
  model?: string
  omit?: 'loader' | 'agents' | 'sessions' | 'agentDefaultModel' | 'graphStartup'
  createRejects?: boolean | string
  followupThrows?: unknown
  events?: Array<unknown>
  chat?: boolean
  /** The assistant text of each turn, in order (turn 1 is the driving task). */
  chatTurns?: string[]
  stdin?: NodeJS.ReadableStream
} = {}): Promise<Harness> {
  let out = ''
  let err = ''
  vi.spyOn(process.stdout, 'write').mockImplementation((chunk: unknown) => { out += String(chunk); return true })
  vi.spyOn(process.stderr, 'write').mockImplementation((chunk: unknown) => { err += String(chunk); return true })

  const exits: number[] = []
  const state = { seq: 0 }
  // The turn's own assistant event is written as the turn is sent, which is what
  // `finalText` reads back: one scripted text per follow-up.
  const events = [...(options.events ?? [])]
  let turn = 0
  const followups: string[] = []
  const session = {
    id: 'session-probe',
    get seq() { return state.seq },
    eventAt: (seq: number) => events[seq],
  }
  let idleCalls = 0
  const agent = {
    session,
    whenIdle: async () => {
      idleCalls += 1
      // The turn is the second quiescence; the first is the agent coming up.
      if (idleCalls === 2 && options.followupThrows !== undefined) throw options.followupThrows
    },
    followup: (message: unknown) => {
      followups.push(textOf(message))
      const scripted = options.chatTurns?.[turn]
      turn += 1
      if (scripted !== undefined) {
        events[state.seq] = { type: 'assistant/message', data: { message: { content: [{ type: 'text', text: scripted }] } } }
      }
      state.seq = Math.max(state.seq + 1, events.length)
    },
  }
  const createOptions: Harness['createOptions'] = []
  let setupCalls = 0
  const ctx = new Context()
  if (options.omit !== 'loader') ctx.provide('loader', { await: async () => {} })
  if (options.omit !== 'agentDefaultModel') {
    ctx.provide('agentDefaultModel', { currentSelection: () => ({ provider: 'deepseek', model: 'flash' }) })
  }
  if (options.omit !== 'agents') {
    ctx.provide('agents', {
      create: async (request: { setup?: (ctx: unknown) => void; agentOptions?: { provider?: string; model?: string } }) => {
        if (options.createRejects === true) throw new Error('no such preset')
        if (typeof options.createRejects === 'string') throw options.createRejects
        createOptions.push(request.agentOptions ?? {})
        if (request.setup !== undefined) { setupCalls += 1; request.setup({ on: () => () => {} }) }
        return { agent }
      },
    })
  }
  if (options.omit !== 'sessions') ctx.provide('sessions', { flush: async () => {} })
  if (options.omit !== 'graphStartup') {
    ctx.provide(GRAPH_STARTUP_SERVICE, {
      mode: options.mode ?? 'probe', taskName: 'circle_packing', budget: 7,
      workdir: options.workdir ?? workdir(), graph: {}, commandEnv: {},
      task: 'drive it', provider: options.provider, model: options.model, pythonBin: '/venv/bin',
      chat: options.chat ?? false,
    } as GraphStartupValues)
  }
  const io: Io = {
    stdout: process.stdout,
    stderr: process.stderr,
    stdin: options.stdin ?? process.stdin,
    exit: (code: number) => { exits.push(code) },
  }
  ctx.provide('appExit', (code: number) => { exits.push(code) })
  applyRunner(ctx, io)
  // `apply` runs the runner without awaiting it; let the microtask queue drain.
  for (let tick = 0; tick < 30; tick += 1) await new Promise(resolve => setImmediate(resolve))
  return { exits, out: () => out, err: () => err, createOptions, setupCalls, followups }
}

describe('graph runner', () => {
  it('refuses to call an unfinished graph finished', async () => {
    // The CEO can reach quiescence with nodes still due — it backgrounded a subagent
    // and ended its turn, or a gate could not be answered. Reporting a score for that
    // is the failure this guards: the run must fail, and name what never ran.
    const harness = await bench({
      workdir: workdir({
        '.refactory/spine-status.json': JSON.stringify({
          status: 'ready', nodes: ['join_qa', 'overwatch'], completed: ['prepare', 'survey'],
        }),
      }),
      events: [{ type: 'assistant/message', data: { message: { content: [{ type: 'text', text: 'done' }] } } }],
    })
    expect(harness.exits).toEqual([1])
    expect(harness.err()).toContain('the graph did not finish')
    expect(harness.err()).toContain('join_qa, overwatch still due')
  })

  it('reports a graph the spine marked refused, with its reason', async () => {
    const harness = await bench({
      workdir: workdir({
        '.refactory/spine-status.json': JSON.stringify({
          status: 'refused', error: 'gate gate_strategy is answered by a person and nobody could be asked',
        }),
      }),
    })
    expect(harness.exits).toEqual([1])
    expect(harness.err()).toContain('nobody could be asked')
  })

  it('drives the agent, reports the run, and exits zero', async () => {
    const harness = await bench({
      workdir: workdir({ 'probe_state.json': JSON.stringify({ best_score: 1.25 }) }),
      events: [{ type: 'assistant/message', data: { message: { content: [{ type: 'text', text: 'complete' }] } } }],
    })
    expect(harness.exits).toEqual([0])
    const report = JSON.parse(harness.out()) as Record<string, unknown>
    expect(report).toMatchObject({
      mode: 'probe', task: 'circle_packing', budget: 7, best_score: 1.25,
      session_id: 'session-probe', final: 'complete',
    })
    expect(harness.setupCalls).toBe(1)
  })

  it('chats when asked: each turn prints and a typed line reaches the agent', async () => {
    // The live session's whole contract: the CEO's turn appears while the graph is
    // still running, and what the person types between turns is a follow-up the CEO
    // can act on rather than a line nobody reads.
    const harness = await bench({
      chat: true,
      chatTurns: ['the first node is done', 'redoing that step'],
      stdin: Readable.from(['make it count from two and redo that step\n/quit\n']),
    })
    expect(harness.out()).toContain('the first node is done')
    expect(harness.out()).toContain('redoing that step')
    expect(harness.followups).toEqual([
      'drive it',
      'make it count from two and redo that step',
    ])
    expect(harness.exits).toEqual([0])
  })

  it('does not read the terminal when chat is off', async () => {
    // The one-shot run is a promise to scripts: no prompt, no read, no waiting on a
    // person who is not there. A stdin that never ends would hang the run if it did.
    const harness = await bench({ stdin: new Readable({ read: () => {} }) })
    expect(harness.exits).toEqual([0])
    expect(harness.followups).toEqual(['drive it'])
  })

  it('takes the mode\'s own state file first, then any other score it finds', async () => {
    // `gepa` names `gepa_state.json`; the file is absent, so the sweep falls back.
    const harness = await bench({
      mode: 'gepa',
      workdir: workdir({ 'other.json': JSON.stringify({ best_score: 2 }), 'notes.txt': 'not a score' }),
    })
    expect((JSON.parse(harness.out()) as { best_score: number }).best_score).toBe(2)
  })

  it('reports no score rather than zero when nothing recorded one', async () => {
    // `gepa` names `gepa_state.json`; the file is absent and the sweep finds no other
    // score. An authoring mode has no `score()` at all, so zero would say "scored zero"
    // where the truth is "not measured".
    const harness = await bench({ mode: 'gepa', workdir: workdir({ 'broken.json': 'not json' }) })
    expect((JSON.parse(harness.out()) as { best_score: number | null }).best_score).toBeNull()
  })

  it('reports empty final text when the turn produced none', async () => {
    const harness = await bench({
      events: [{ type: 'user/message' }, { type: 'assistant/message', data: { message: { content: [{ type: 'tool_use' }] } } }],
    })
    expect((JSON.parse(harness.out()) as { final: string }).final).toBe('')
  })

  it('routes the agent at the provider and model the run named', async () => {
    const harness = await bench({ provider: 'deepseek', model: 'flash' })
    expect(harness.createOptions).toEqual([{ provider: 'deepseek', model: 'flash' }])
  })

  it('reports an agent turn that failed, and still reports the run', async () => {
    const harness = await bench({ followupThrows: new Error('the preset is broken') })
    expect(harness.err()).toContain('the preset is broken')
    expect(harness.exits).toEqual([0])
  })

  it('reports a non-Error turn failure as its own text', async () => {
    const harness = await bench({ followupThrows: 'plain failure' })
    expect(harness.err()).toContain('plain failure')
  })

  for (const omit of ['agents', 'sessions', 'agentDefaultModel', 'graphStartup'] as const) {
    it(`does nothing when the ${omit} service is absent`, async () => {
      const harness = await bench({ omit })
      expect(harness.exits).toEqual([])
      expect(harness.out()).toBe('')
    })
  }

  it('runs without a loader to await', async () => {
    const harness = await bench({ omit: 'loader' })
    expect(harness.exits).toEqual([0])
    expect(harness.out()).toContain('session-probe')
  })

  it('refuses to mount without the launcher exit request', () => {
    const ctx = new Context()
    expect(() => { applyRunner(ctx) }).toThrow(/must provide ctx\.appExit/)
  })

  it('reports a failure to the launcher and exits one', async () => {
    const harness = await bench({ createRejects: true })
    expect(harness.exits).toEqual([1])
    expect(harness.err()).toContain('no such preset')
  })

  it('reports a non-Error mount failure as its own text', async () => {
    const harness = await bench({ createRejects: 'string failure' })
    expect(harness.exits).toEqual([1])
    expect(harness.err()).toContain('string failure')
  })
})
