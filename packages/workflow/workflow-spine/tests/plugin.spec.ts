import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import * as workflowSpine from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const graph: WorkflowGraph = {
  name: 't',
  start_node: 'a',
  nodes: {
    a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
    gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', gate_prompt: 'ok?', reads: [] },
    b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', writes: [], reads: [] },
  },
  edges: [
    { source: 'a', target: 'gate' },
    { source: 'gate', target: 'b', condition: 'proceed' },
    { source: 'gate', target: 'a', condition: 'reloop' },
  ],
}

const graphWithReads: WorkflowGraph = {
  name: 't',
  start_node: 'a',
  nodes: {
    a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
    gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', gate_prompt: 'ok?', reads: ['design.md'] },
    b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', writes: [], reads: [] },
  },
  edges: [
    { source: 'a', target: 'gate' },
    { source: 'gate', target: 'b', condition: 'proceed' },
    { source: 'gate', target: 'a', condition: 'reloop' },
  ],
}

async function setup(): Promise<Context> {
  const ctx = new Context()
  await ctx.plugin(SystemPrompt)
  await ctx.plugin(ToolRuntime)
  await ctx.plugin(UserQuestionService)
  ctx.on('user-questions/request', request => Promise.resolve({
    answers: request.questions.map(question => ({ id: question.id, selected: ['proceed'] })),
  }))
  await ctx.plugin(workflowSpine, { graph, toolPrefix: 'wf', maxIterations: 3 })
  return ctx
}

async function run(ctx: Context, name: string, args: unknown): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, {} as never)
}

describe('workflow-spine real composition', () => {
  it('registers the six wf_* tools and walks a graph through them', async () => {
    const ctx = await setup()
    for (const name of ['wf_next', 'wf_done', 'wf_verdict', 'wf_edit', 'wf_expand', 'wf_state']) {
      expect(ctx.tools.get(name)).toBeDefined()
    }

    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'a' }] })
    await run(ctx, 'wf_done', { node: 'a' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'gate' }] })
    await run(ctx, 'wf_verdict', { gate: 'gate' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'b' }] })
    await run(ctx, 'wf_done', { node: 'b' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'done' })
  })

  it('refuses a verdict for a user gate instead of recording one the person never gave', async () => {
    const ctx = await setup()
    await run(ctx, 'wf_done', { node: 'a' })
    await expect(run(ctx, 'wf_verdict', { gate: 'gate', verdict: 'proceed' }))
      .rejects.toThrow(/user gate.*never gave/s)
    // The honest call — the gate alone — is the one that routes, and it routes the
    // person's answer, not the model's.
    expect(await run(ctx, 'wf_verdict', { gate: 'gate' }))
      .toMatchObject({ status: 'routed', gate: 'gate', verdict: 'proceed', answered_by: 'user' })
  })

  it('refuses to route a user gate when the composition can ask nobody', async () => {
    // The defect this holds: a headless run used to route a `user` gate with whatever
    // verdict the model passed, which is indistinguishable from one a person gave.
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(workflowSpine, { graph, toolPrefix: 'wf', maxIterations: 3 })
    await run(ctx, 'wf_done', { node: 'a' })
    await expect(run(ctx, 'wf_verdict', { gate: 'gate' }))
      .rejects.toThrow(/gate gate is answered by a person/)
  })

  it('refuses to route a user gate when no answerer accepts the request', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(UserQuestionService)
    await ctx.plugin(workflowSpine, { graph, toolPrefix: 'wf', maxIterations: 3 })
    await run(ctx, 'wf_done', { node: 'a' })
    await expect(run(ctx, 'wf_verdict', { gate: 'gate' }))
      .rejects.toThrow(/gate gate is answered by a person and could not be asked/)
  })

  it('includes gate reads files in the question detail', async () => {
    let capturedRequest: { questions: Array<{ id: string; detail?: string }> } | undefined
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(UserQuestionService)
    ctx.on('user-questions/request', request => {
      capturedRequest = request
      return Promise.resolve({
        answers: request.questions.map(question => ({ id: question.id, selected: ['proceed'] })),
      })
    })
    await ctx.plugin(workflowSpine, { graph: graphWithReads, toolPrefix: 'wf', maxIterations: 3 })

    // Walk to the gate
    await run(ctx, 'wf_done', { node: 'a' })
    // Route the user gate (triggers userQuestions.ask)
    await run(ctx, 'wf_verdict', { gate: 'gate' })

    expect(capturedRequest).toBeDefined()
    const firstQuestion = capturedRequest!.questions[0]!
    expect(firstQuestion.detail).toBeDefined()
    expect(firstQuestion.detail).toContain('design.md')
  })

  type Asked = { id: string; detail?: string; options?: Array<{ label: string }>; intent?: unknown }

  /** A composition whose answerer records the question and replies with `reply`. */
  async function gateRun(
    gateGraph: WorkflowGraph, workdir: string, reply: (question: Asked) => Promise<unknown>,
  ): Promise<{ ctx: Context; asked: Asked[] }> {
    const asked: Asked[] = []
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(UserQuestionService)
    ctx.on('user-questions/request', request => {
      asked.push(...request.questions)
      return reply(request.questions[0]!) as never
    })
    await ctx.plugin(workflowSpine, { graph: gateGraph, toolPrefix: 'wf', maxIterations: 3, workdir })
    await run(ctx, 'wf_done', { node: 'a' })
    return { ctx, asked }
  }

  const proceed = (question: Asked) => Promise.resolve({ answers: [{ id: question.id, selected: ['proceed'] }] })

  it('names the gate reads by full path, with every outcome offered', async () => {
    // The question stays host-neutral and short: no presentation intent and no inlined
    // file content, so the gate's own outcomes (and free text) stay in view.
    const tmp = mkdtempSync(join(tmpdir(), 'spine-gate-reads-'))
    try {
      writeFileSync(join(tmp, 'design.md'), '# My Design\n\nThe plan is to do X.')
      const { ctx, asked } = await gateRun(graphWithReads, tmp, proceed)
      expect(await run(ctx, 'wf_verdict', { gate: 'gate' }))
        .toMatchObject({ status: 'routed', verdict: 'proceed', answered_by: 'user' })
      expect(asked[0]!.detail).toBe(`Gate reads files: ${join(tmp, 'design.md')}`)
      expect(asked[0]!.intent).toBeUndefined()
      expect(asked[0]!.options?.map(option => option.label)).toEqual(['proceed', 'reloop', 'halt'])
    } finally { rmSync(tmp, { recursive: true, force: true }) }
  })

  it('lets the person stop the run at a user gate that has no halt edge', async () => {
    const tmp = mkdtempSync(join(tmpdir(), 'spine-gate-halt-'))
    try {
      const { ctx } = await gateRun(graphWithReads, tmp,
        question => Promise.resolve({ answers: [{ id: question.id, selected: [], custom: 'halt' }] }))
      expect(await run(ctx, 'wf_verdict', { gate: 'gate' }))
        .toMatchObject({ status: 'routed', verdict: 'halt', answered_by: 'user', halted: true })
      expect(await run(ctx, 'wf_next', {})).toMatchObject({ halted: { gate: 'gate', reason: 'verdict' } })
      expect(await run(ctx, 'wf_state', {})).toMatchObject({ gate_verdicts: { gate: 'halt' }, cursor: [] })
    } finally { rmSync(tmp, { recursive: true, force: true }) }
  })

  it('tells the CEO to reply and wait, not re-ask, when the answer is no outcome', async () => {
    const tmp = mkdtempSync(join(tmpdir(), 'spine-gate-replied-'))
    try {
      const { ctx } = await gateRun(graphWithReads, tmp,
        question => Promise.resolve({ answers: [{ id: question.id, selected: [], custom: 'stop' }] }))
      const replied = await run(ctx, 'wf_verdict', { gate: 'gate' }) as { status: string; note: string }
      expect(replied.status).toBe('replied')
      expect(replied.note).toMatch(/asked correctly/)
      expect(replied.note).toMatch(/Do not ask the gate's question yourself or recommend an outcome/)
    } finally { rmSync(tmp, { recursive: true, force: true }) }
  })

  it('CEO prompt instructs to present gate reads files before routing a user gate', async () => {
    const ctx = await setup()
    const assembly = await ctx.systemPrompt.assemble()
    const spineSection = assembly.sections.find(s => s.name === 'tool:wf-spine')
    expect(spineSection).toBeDefined()
    expect(spineSection!.text).toMatch(/user gate/)
    expect(spineSection!.text).toMatch(/reads/)
    expect(spineSection!.text).toMatch(/summarise|summarize/)
  })
})

/** A graph whose first two nodes are commands, so the plugin can run them. */
const commandGraph: WorkflowGraph = {
  name: 'commands',
  start_node: 'prepare',
  nodes: {
    prepare: {
      _type: 'FnNode', id: 'prepare', command: 'printf prepared',
      reads: [], writes: ['note.txt'],
    },
    stop: {
      _type: 'GateNode', id: 'stop', evaluator_type: 'fn', evaluator_command: 'echo proceed',
      reads: [], writes: [],
    },
    work: {
      _type: 'AgentNode', id: 'work', role: 'builder', prompt_template: 'W', reads: [], writes: [],
    },
  },
  edges: [
    { source: 'prepare', target: 'stop' },
    { source: 'stop', target: 'work', condition: 'proceed' },
    { source: 'stop', target: 'prepare', condition: 'reloop' },
  ],
}

/** A shell executor that really runs the command, for the composition test. */
function realShell(): object {
  return {
    resolve: (request: { command: string; workdir?: string; timeoutMs?: number; env?: Record<string, string> }) => ({
      command: request.command,
      workdir: request.workdir ?? process.cwd(),
      timeoutMs: request.timeoutMs ?? 10_000,
      onExpiry: 'kill',
      stdoutMaxBytes: 100_000,
      env: request.env,
      sandboxPolicy: undefined,
    }),
    execute: async (spec: { command: string; workdir: string; timeoutMs: number; env?: Record<string, string> }) => ({
      result: async () => {
        const started = spawnSync('/bin/sh', ['-c', spec.command], {
          cwd: spec.workdir, encoding: 'utf8', timeout: spec.timeoutMs,
          env: { ...process.env, ...spec.env },
        })
        return {
          exitCode: started.status, signal: started.signal, timedOut: false, aborted: false,
          timeoutMs: spec.timeoutMs,
          stdout: { text: started.stdout ?? '', truncated: false },
          stderr: { text: started.stderr ?? '', truncated: false },
        }
      },
    }),
  }
}

describe('deterministic nodes run without the driver', () => {
  async function commandSetup(options: { executeCommands: boolean }): Promise<Context> {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    ctx.provide('shell', realShell() as never)
    await ctx.plugin(workflowSpine, {
      graph: commandGraph, toolPrefix: 'wf', maxIterations: 3, ...options,
    })
    return ctx
  }

  it('runs the FnNode and the fn gate, then hands over only the agent node', async () => {
    const ctx = await commandSetup({ executeCommands: true })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({
      status: 'ready', nodes: [{ id: 'work', _type: 'AgentNode' }],
    })
    const state = (await run(ctx, 'wf_state', {})) as { completed: string[]; gate_verdicts: Record<string, string> }
    expect(state.completed).toContain('prepare')
    expect(state.completed).toContain('stop')
    expect(state.gate_verdicts).toEqual({ stop: 'proceed' })
  })

  it('hands the deterministic nodes over when execution is off', async () => {
    const ctx = await commandSetup({ executeCommands: false })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'prepare' }] })
  })

  it('requires a verdict for a gate with a command', async () => {
    const ctx = await commandSetup({ executeCommands: false })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'prepare' }] })
    await run(ctx, 'wf_done', { node: 'prepare' })
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'ready', nodes: [{ id: 'stop' }] })
    await expect(run(ctx, 'wf_verdict', { gate: 'stop' }))
      .rejects.toThrow(/routes on a verdict/)
    expect(await run(ctx, 'wf_verdict', { gate: 'stop', verdict: 'proceed' }))
      .toMatchObject({ status: 'routed', gate: 'stop', verdict: 'proceed' })
  })

  it('reports a failing command and does not advance the cursor', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    ctx.provide('shell', realShell() as never)
    await ctx.plugin(workflowSpine, {
      graph: {
        ...commandGraph,
        nodes: {
          ...commandGraph.nodes,
          prepare: { _type: 'FnNode', id: 'prepare', command: 'echo broken >&2; exit 4', reads: [], writes: [] },
        },
      },
      toolPrefix: 'wf',
      executeCommands: true,
    })

    const result = (await run(ctx, 'wf_next', {})) as { status: string; exit_code: number; stderr: string }
    expect(result.status).toBe('failed')
    expect(result.exit_code).toBe(4)
    expect(result.stderr).toBe('broken')
    expect(await run(ctx, 'wf_next', {})).toMatchObject({ status: 'failed' })
  })
})


describe('recovery checkpoint ownership', () => {
  it('refuses dispatch when the execution checkpoint cannot be replaced', async () => {
    const root = mkdtempSync(join(tmpdir(), 'spine-checkpoint-'))
    try {
      mkdirSync(join(root, '.refactory', 'spine-checkpoint.json'), { recursive: true })
      const ctx = new Context()
      await ctx.plugin(SystemPrompt)
      await ctx.plugin(ToolRuntime)
      let calls = 0
      ctx.provide('shell', { ...realShell(), execute: () => { calls++; throw new Error('must not dispatch') } } as never)
      await ctx.plugin(workflowSpine, { graph: commandGraph, executeCommands: true, workdir: root })
      await expect(run(ctx, 'wf_next', {})).rejects.toThrow(/recovery checkpoint/)
      expect(calls).toBe(0)
    } finally { rmSync(root, { recursive: true, force: true }) }
  })
})
