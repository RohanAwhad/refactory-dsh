/**
 * The spine plugin's composition: graph rejection at load, the cursor status it
 * leaves on disk, executing deterministic nodes, the tool renders, and the guard
 * that takes the CEO's coding tools away.
 *
 * These are the parts a run depends on but a single tool call does not reach.
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import { createScope } from '@deepseek-ai/dsh-scope'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime, { defineTool } from '@deepseek-ai/dsh-tools'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { afterEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
  vi.restoreAllMocks()
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-composition-'))
  dirs.push(dir)
  return dir
}

/** One agent node, so the cursor has something a model must do. */
const AGENT_GRAPH: WorkflowGraph = {
  name: 't',
  start_node: 'a',
  nodes: { a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] } },
  edges: [],
}

function config(overrides: Partial<Config> = {}): Config {
  return {
    graph: AGENT_GRAPH,
    toolPrefix: 'wf',
    maxIterations: 3,
    interactiveOpen: false,
    openCommand: '',
    openArgs: [],
    executeCommands: false,
    commandEnv: {},
    commandTimeoutMs: 10_000,
    commandSandboxMode: 'danger-full-access',
    dataCommand: '',
    steerCommand: '',
    steerArgs: [],
    ceoDeny: [],
    ...overrides,
  }
}

/** A context with the two services the plugin injects, and the plugin mounted. */
async function mount(overrides: Partial<Config> = {}): Promise<{ ctx: Context; tools: ToolRuntime }> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  await root.plugin(spinePlugin, config(overrides))
  return { ctx: root, tools: root.tools }
}

/** The shell request a node command is resolved from, as the plugin builds it. */
interface ShellRequest {
  command: string
  workdir?: string
  timeoutMs?: number
  env?: Record<string, string>
  sandboxPolicy?: unknown
}

/** A shell that reports whatever the caller asks for. */
function shell(behaviour: (command: string) => { exitCode?: number; stdout?: string; stderr?: string } = () => ({ exitCode: 0 })): object {
  return {
    resolve: (request: ShellRequest) => ({
      command: request.command,
      workdir: request.workdir ?? process.cwd(),
      timeoutMs: request.timeoutMs ?? 10_000,
      onExpiry: 'kill',
      stdoutMaxBytes: 1_000_000,
      env: request.env,
      sandboxPolicy: request.sandboxPolicy,
    }),
    execute: async (spec: { command: string }) => ({
      result: async () => {
        const result = behaviour(spec.command)
        return {
          exitCode: result.exitCode ?? 0, signal: null, timedOut: false, aborted: false, timeoutMs: 10_000,
          stdout: { text: result.stdout ?? '', truncated: false },
          stderr: { text: result.stderr ?? '', truncated: false },
        }
      },
    }),
  }
}

async function call(ctx: Context, name: string, args: unknown = {}): Promise<unknown> {
  const definition = ctx.tools.get(name)
  if (definition === undefined) throw new Error(`tool ${name} not registered`)
  return definition.execute(args, {} as never)
}

describe('mounting a graph', () => {
  it('refuses an invalid graph at load, naming the problem', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await expect(root.plugin(spinePlugin, config({
      graph: { name: 't', start_node: 'missing', nodes: {}, edges: [] },
    }))).rejects.toThrow(/invalid workflow graph:/)
  })

  it('names the node an issue belongs to', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await expect(root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'a',
        nodes: {
          a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
          ghost: { _type: 'AgentNode', id: 'ghost', role: 'builder', prompt_template: 'G', writes: [], reads: [] },
        },
        edges: [],
      },
    }))).rejects.toThrow(/ghost: unreachable from start_node/)
  })
})

describe('the cursor status a run leaves behind', () => {
  it('records ready while work is due and done once it is not', async () => {
    const workdir = scratch()
    const { ctx } = await mount({ workdir })
    await call(ctx, 'wf_next')
    expect(JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')))
      .toMatchObject({ status: 'ready', nodes: ['a'], completed: [] })

    await call(ctx, 'wf_done', { node: 'a' })
    await call(ctx, 'wf_next')
    expect(JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')))
      .toMatchObject({ status: 'done', completed: ['a'], nodes: [] })
  })

  it('records a refusal, with the reason, when no answerer can be asked', async () => {
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate', condition: 'proceed' }],
      },
    })
    await expect(call(ctx, 'wf_verdict', { gate: 'gate' }))
      .rejects.toThrow(/mounts no user-questions channel/)
    const recorded = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')) as { status: string; error: string }
    expect(recorded.status).toBe('refused')
    expect(recorded.error).toContain('gate gate')
  })

  it('records the failed node\'s own stderr, not only its exit code', async () => {
    // "exit code 4" sends a reader back to re-run the node to find out why; the
    // stderr is the reason, and the run directory is where a reader looks.
    // dm-95: a node failure writes status ready with last_failure, not failed.
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'run-it', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell(() => ({ exitCode: 4, stderr: 'broken on purpose\n' })))
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'failed', exit_code: 4 })
    const recorded = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')) as { status: string; last_failure?: { node: string; error: string } }
    expect(recorded.status).toBe('ready')
    expect(recorded.last_failure).toMatchObject({ node: 'run', error: expect.stringContaining('broken on purpose') })
  })

  it('failure payload has node id, command field, and descriptive error (E7)', async () => {
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'run-it', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell(() => ({ exitCode: 4, stderr: 'broken on purpose\n' })))
    const result = await call(ctx, 'wf_next') as { status: string; node: string; command: string; error: string }
    expect(result.status).toBe('failed')
    expect(result.node).toBe('run')
    expect(result.command).toBe('run-it')
    expect(result.error).toContain('run exited with code 4')
    expect(result.error).toContain('broken on purpose')
  })

  it('facts summary replaces inline facts with keys, sizes and small inlines (E10)', async () => {
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'run-it', reads: [], writes: [] } },
        edges: [],
      },
    })
    // Write a facts.json with one small and one large value.
    writeFileSync(join(workdir, 'facts.json'), JSON.stringify({
      small: 'hello',
      big: 'x'.repeat(600),
    }))
    ctx.provide('shell', shell(() => ({ exitCode: 0 })))
    const result = await call(ctx, 'wf_next') as { ran: { facts: { _summary: boolean; keys: string[]; sizes: Record<string, number>; inline: Record<string, unknown>; path: string } } }
    expect(result.ran.facts._summary).toBe(true)
    expect(result.ran.facts.path).toBe('facts.json')
    expect(result.ran.facts.keys).toEqual(expect.arrayContaining(['small', 'big']))
    expect(result.ran.facts.sizes.small).toBeLessThanOrEqual(512)
    expect(result.ran.facts.sizes.big).toBeGreaterThan(512)
    expect(result.ran.facts.inline.small).toBe('hello')
    expect(result.ran.facts.inline).not.toHaveProperty('big')
  })

  it('records the exit code alone when a failed node said nothing', async () => {
    // dm-95: a node failure writes status ready with last_failure, not failed.
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'run-it', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell(() => ({ exitCode: 4 })))
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'failed' })
    const recorded = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')) as { status: string; last_failure?: { node: string; error: string } }
    expect(recorded.status).toBe('ready')
    expect(recorded.last_failure).toMatchObject({ node: 'run', error: 'run exited with code 4' })
  })

  it('a node failure writes ready with last_failure and an ISO timestamp, not run-level failed', async () => {
    // dm-95: the combination status=ready AND last_failure present is produced by
    // real spine code, and both spine-status.json and spine-checkpoint.json agree.
    const workdir = scratch()
    const { ctx } = await mount({
      workdir,
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'run-it', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell(() => ({ exitCode: 1, stderr: 'something broke' })))
    const result = await call(ctx, 'wf_next') as { status: string }
    expect(result.status).toBe('failed')
    const statusRecord = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8'))
    expect(statusRecord.status).toBe('ready')
    expect(statusRecord.last_failure).toBeDefined()
    expect(statusRecord.last_failure.node).toBe('run')
    expect(statusRecord.last_failure.error).toContain('something broke')
    expect(statusRecord.last_failure.at).toMatch(/^\d{4}-\d{2}-\d{2}T/)
    const checkpoint = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-checkpoint.json'), 'utf8'))
    expect(checkpoint.status).toBe('ready')
  })

  it('refuses dispatch when the recovery checkpoint cannot be written', async () => {
    const blocked = join(scratch(), 'not-a-directory')
    writeFileSync(blocked, 'x')
    const { ctx } = await mount({ workdir: join(blocked, 'child'), executeCommands: true,
      graph: { name: 'blocked', start_node: 'a', nodes: { a: { id: 'a', _type: 'FnNode', command: 'never' } }, edges: [] } })
    const executed = vi.fn(() => ({ exitCode: 0 }))
    ctx.provide('shell', shell(executed))
    await expect(call(ctx, 'wf_next')).rejects.toThrow(/Cannot persist graph recovery checkpoint/)
    expect(executed).not.toHaveBeenCalled()
  })
})

describe('a run that stops short of its end', () => {
  it('halts naming the nodes it never reached, and records that for refactory status', async () => {
    const workdir = scratch()
    const graph: WorkflowGraph = {
      name: 'incomplete',
      start_node: 'a',
      nodes: {
        a: { _type: 'AgentNode', id: 'a', role: 'builder', prompt_template: 'A', writes: [], reads: [] },
        b: { _type: 'AgentNode', id: 'b', role: 'builder', prompt_template: 'B', writes: [], reads: [] },
      },
      edges: [{ source: 'a', target: 'b' }],
    }
    const { ctx } = await mount({ workdir, graph })
    await call(ctx, 'wf_next')
    await call(ctx, 'wf_done', { node: 'a' })
    await call(ctx, 'wf_next')
    // A cursor that empties with `b` never run, restored the way a resume restores
    // a saved checkpoint.
    const saved = join(workdir, '.refactory', 'spine-checkpoint.json')
    const checkpoint = JSON.parse(readFileSync(saved, 'utf8'))
    checkpoint.state.cursor = []
    checkpoint.state.completed = ['a']
    writeFileSync(saved, JSON.stringify(checkpoint))
    const sha = createHash('sha256').update(readFileSync(saved)).digest('hex')
    writeFileSync(join(workdir, '.refactory', 'resume-request.json'), JSON.stringify({ checkpoint_sha256: sha }))
    const { ctx: resumed } = await mount({ workdir, graph })
    const result = await call(resumed, 'wf_next') as { status: string; halted?: { reason: string; unreached?: string[] } }
    expect(result).toMatchObject({ status: 'done', halted: { reason: 'incomplete', unreached: ['b'] } })
    const recorded = JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8'))
    expect(recorded).toMatchObject({ status: 'halted', error: 'never reached: b' })
  })
})

describe('gate reason composition through applyControls', () => {
  it('rerun without text shows the gate reason; with text, reason then text; after proceed, no reason', async () => {
    const workdir = scratch()
    const graph: WorkflowGraph = {
      name: 'gate-reason',
      start_node: 'emit',
      nodes: {
        emit: { _type: 'AgentNode', id: 'emit', role: 'builder', prompt_template: 'E', writes: [], reads: [] },
        check: { _type: 'GateNode', id: 'check', evaluator_type: 'fn', evaluator_command: 'true', reads: [], max_iterations: 1 },
        done_node: { _type: 'FnNode', id: 'done_node', writes: [], command: '' },
      },
      edges: [
        { source: 'emit', target: 'check' },
        { source: 'check', target: 'emit', condition: 'reloop' },
        { source: 'check', target: 'done_node', condition: 'proceed' },
      ],
    }
    const { ctx } = await mount({ workdir, graph })
    // Drive to halt: emit → check reloop → emit → check reloop (halts at max_iterations)
    await call(ctx, 'wf_next')     // emit is due
    await call(ctx, 'wf_done', { node: 'emit' })
    await call(ctx, 'wf_next')     // check is due
    await call(ctx, 'wf_verdict', { gate: 'check', verdict: 'reloop', feedback: 'fix the formatting' })
    await call(ctx, 'wf_next')     // emit is due (reloop)
    await call(ctx, 'wf_done', { node: 'emit' })
    await call(ctx, 'wf_next')     // check is due
    await call(ctx, 'wf_verdict', { gate: 'check', verdict: 'reloop', feedback: 'still wrong' })
    // Now halted at max_iterations
    const halted = await call(ctx, 'wf_next') as { status: string; halted?: { reason: string } }
    expect(halted.status).toBe('done')
    expect(halted.halted?.reason).toBe('max_iterations')
    // Write a checkpoint for the revision
    const cpBytes = readFileSync(join(workdir, '.refactory', 'spine-checkpoint.json'))
    const revision = createHash('sha256').update(cpBytes).digest('hex')
    // Queue a rerun control without text
    const directory = join(workdir, '.refactory', 'graph-controls')
    mkdirSync(directory, { recursive: true })
    writeFileSync(join(workdir, '.refactory', 'invocation.json'), JSON.stringify({ id: null }))
    writeFileSync(join(directory, '1.json'), JSON.stringify({
      id: '1', action: 'rerun', target: 'emit', text: '',
      revision, run_id: resolve(workdir), invocation: null,
      status: 'queued',
    }))
    // Call wf_next — applyControls should apply the rerun and compose the gate reason
    const result = await call(ctx, 'wf_next') as { status: string; nodes: Array<{ id: string }>; feedback?: Record<string, string> }
    expect(result.status).toBe('ready')
    expect(result.nodes.some(n => n.id === 'emit')).toBe(true)
    // Without rerun text, the gate's reason alone is the feedback
    expect(result.feedback?.emit).toBe('still wrong')
    // Now test with rerun text: drive to halt again (iterations were reset by the
    // person's rerun, so two reloops are needed to exceed cap=1 again), then rerun
    // with text.
    await call(ctx, 'wf_done', { node: 'emit' })
    await call(ctx, 'wf_next')     // check is due
    await call(ctx, 'wf_verdict', { gate: 'check', verdict: 'reloop', feedback: 'third refusal' })
    // count=1, cap=1: 1 > 1 is false → loops back to emit
    await call(ctx, 'wf_done', { node: 'emit' })
    await call(ctx, 'wf_next')     // check is due again
    await call(ctx, 'wf_verdict', { gate: 'check', verdict: 'reloop', feedback: 'fourth refusal' })
    // count=2, cap=1: 2 > 1 → halts at max_iterations
    const cpBytes2 = readFileSync(join(workdir, '.refactory', 'spine-checkpoint.json'))
    const revision2 = createHash('sha256').update(cpBytes2).digest('hex')
    writeFileSync(join(directory, '2.json'), JSON.stringify({
      id: '2', action: 'rerun', target: 'emit', text: 'try a different approach',
      revision: revision2, run_id: resolve(workdir), invocation: null,
      status: 'queued',
    }))
    const result2 = await call(ctx, 'wf_next') as { status: string; feedback?: Record<string, string> }
    // With rerun text, gate reason first then rerun text
    const fb = result2.feedback?.emit ?? ''
    expect(fb).toContain('fourth refusal')
    expect(fb).toContain('try a different approach')
    expect(fb.indexOf('fourth refusal')).toBeLessThan(fb.indexOf('try a different approach'))

    // Now test after gate proceeded: complete emit, check proceeds, rerun should show no old reason
    await call(ctx, 'wf_done', { node: 'emit' })
    await call(ctx, 'wf_next')     // check is due
    await call(ctx, 'wf_verdict', { gate: 'check', verdict: 'proceed' })
    await call(ctx, 'wf_next')     // done_node
    await call(ctx, 'wf_done', { node: 'done_node' })
    // Now done. Redo emit after gate proceeded.
    const cpBytes3 = readFileSync(join(workdir, '.refactory', 'spine-checkpoint.json'))
    const revision3 = createHash('sha256').update(cpBytes3).digest('hex')
    writeFileSync(join(directory, '3.json'), JSON.stringify({
      id: '3', action: 'rerun', target: 'emit', text: '',
      revision: revision3, run_id: resolve(workdir), invocation: null,
      status: 'queued',
    }))
    const result3 = await call(ctx, 'wf_next') as { status: string; feedback?: Record<string, string> }
    // After gate proceeded, no old reason
    expect(result3.feedback?.emit).toBeUndefined()
  })
})

describe('graph control warnings', () => {
  it('journals a failed control as a run_warning event', async () => {
    const workdir = scratch()
    const { ctx } = await mount({ workdir, executeCommands: true, graph: {
      name: 'control-fail', start_node: 'a', nodes: {
        a: { id: 'a', _type: 'AgentNode', prompt_template: 'A', writes: [], reads: [] },
      }, edges: [],
    } })
    // Queue a rerun control with a stale revision — it will fail validation
    const directory = join(workdir, '.refactory/graph-controls')
    mkdirSync(directory, { recursive: true })
    writeFileSync(join(workdir, '.refactory/invocation.json'), JSON.stringify({ id: null }))
    writeFileSync(join(directory, '1.json'), JSON.stringify({
      action: 'rerun', target: 'a', revision: 'stale-revision',
      run_id: workdir, invocation: null,
    }))
    await call(ctx, 'wf_next')
    // The receipt should say failed
    const receipt = JSON.parse(readFileSync(join(workdir, '.refactory/graph-control-receipt.json'), 'utf8'))
    expect(receipt.status).toBe('failed')
    // events.jsonl should have a run_warning entry
    const eventsPath = join(workdir, '.refactory/events.jsonl')
    expect(existsSync(eventsPath)).toBe(true)
    const events = readFileSync(eventsPath, 'utf8')
      .split('\n').filter(Boolean).map(line => JSON.parse(line))
    const warnings = events.filter(e => e.kind === 'run_warning')
    expect(warnings.length).toBeGreaterThanOrEqual(1)
    expect(warnings[0].summary).toContain('graph control rerun failed')
  })
})

describe('deterministic nodes', () => {
  it.each(['pause', 'stop'])('applies queued %s before dispatching the next node in the same drain', async (action) => {
    const workdir = scratch()
    const { ctx } = await mount({ workdir, executeCommands: true, graph: {
      name: 'boundaries', start_node: 'a', nodes: {
        a: { id: 'a', _type: 'FnNode', command: 'first' },
        b: { id: 'b', _type: 'FnNode', command: 'second' },
      }, edges: [{ source: 'a', target: 'b' }],
    } })
    const commands: string[] = []
    ctx.provide('shell', shell(command => {
      commands.push(command)
      const directory = join(workdir, '.refactory/graph-controls')
      mkdirSync(directory, { recursive: true })
      writeFileSync(join(directory, '1.json'), JSON.stringify({ action, run_id: workdir, invocation: null }))
      return { exitCode: 0 }
    }))
    await call(ctx, 'wf_next')
    expect(commands).toEqual(['first'])
    const saved = JSON.parse(readFileSync(join(workdir, '.refactory/spine-checkpoint.json'), 'utf8'))
    expect(saved.state.completed).toEqual(['a'])
    expect(saved.status).toBe(action === 'pause' ? 'paused' : 'halted')
  })

  it('hands the node over when no shell is mounted', async () => {
    const { ctx } = await mount({
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'true', reads: [], writes: [] } },
        edges: [],
      },
    })
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'ready', nodes: [{ id: 'run' }] })
  })

  it('runs the last deterministic node and reports done', async () => {
    const { ctx } = await mount({
      workdir: scratch(),
      executeCommands: true,
      graph: {
        name: 't',
        start_node: 'run',
        nodes: { run: { _type: 'FnNode', id: 'run', command: 'true', reads: [], writes: [] } },
        edges: [],
      },
    })
    ctx.provide('shell', shell())
    await expect(call(ctx, 'wf_next')).resolves.toMatchObject({ status: 'done' })
  })
})

describe('tool presentation', () => {
  it('renders every tool\'s value as compact JSON text', async () => {
    const { ctx } = await mount()
    for (const name of ['wf_next', 'wf_done', 'wf_verdict', 'wf_edit', 'wf_expand', 'wf_state']) {
      const definition = ctx.tools.get(name)
      expect(definition).toBeDefined()
      const rendered = definition?.output.render({}, { status: 'ready' })
      expect(rendered?.[0]).toMatchObject({ type: 'text' })
    }
  })

  it('unskips a node through the edit tool', async () => {
    const { ctx } = await mount()
    await call(ctx, 'wf_edit', { node: 'a', action: 'skip' })
    expect((await call(ctx, 'wf_state')) as { skipped: string[] }).toMatchObject({ skipped: ['a'] })
    await call(ctx, 'wf_edit', { node: 'a', action: 'unskip' })
    expect((await call(ctx, 'wf_state')) as { skipped: string[] }).toMatchObject({ skipped: [] })
  })
})

describe('asking a person', () => {
  it.each(['pause', 'stop'])('applies %s while a gate question is pending without recording a verdict', async (action) => {
    const workdir = scratch()
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    let questionSignal: AbortSignal | undefined
    const asked = Promise.withResolvers<void>()
    root.on('user-questions/request', (request: { signal?: AbortSignal }) => {
      questionSignal = request.signal
      asked.resolve()
      return new Promise<never>((_resolve, reject) => request.signal?.addEventListener('abort',
        () => reject(new Error('pending native question released')), { once: true }))
    })
    await root.plugin(spinePlugin, config({ workdir, graph: {
      name: 'pending-control', start_node: 'gate', nodes: {
        gate: { id: 'gate', _type: 'GateNode', evaluator_type: 'user', reads: [] },
        after: { id: 'after', _type: 'AgentNode', prompt_template: 'must remain undispatched' },
      }, edges: [{ source: 'gate', target: 'after', condition: 'proceed' }],
    } }))
    const pending = call(root, 'wf_verdict', { gate: 'gate' })
    await asked.promise
    expect(questionSignal?.aborted).toBe(false)
    const directory = join(workdir, '.refactory/graph-controls')
    mkdirSync(directory, { recursive: true })
    writeFileSync(join(directory, '1.json'), JSON.stringify({ action, run_id: workdir, invocation: null }))
    const result = await pending
    expect(result).toMatchObject({ status: action === 'pause' ? 'paused' : 'halted', interrupted_by: 'graph_control' })
    expect(result).not.toHaveProperty('verdict')
    expect(questionSignal?.aborted).toBe(true)
    const state = await call(root, 'wf_state')
    expect(state).toMatchObject({ cursor: ['gate'], completed: [], gate_verdicts: {} })
    expect(JSON.parse(readFileSync(join(workdir, '.refactory/spine-status.json'), 'utf8')))
      .toMatchObject({ status: action === 'pause' ? 'paused' : 'halted' })
    expect(JSON.parse(readFileSync(join(workdir, '.refactory/graph-control-receipt.json'), 'utf8')))
      .toMatchObject({ action, status: 'applied' })
  })

  it('refuses an answer that carries no verdict', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    root.on('user-questions/request', (request: { questions: Array<{ id: string }> }) =>
      Promise.resolve({ answers: request.questions.map(question => ({ id: question.id, selected: [] })) }))
    await root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate', condition: 'proceed' }],
      },
    }))
    await expect(call(root, 'wf_verdict', { gate: 'gate' }))
      .rejects.toThrow(/answered without a verdict/)
  })

  it('forwards the calling agent and its abort signal to the answerer', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    let seen: { agent?: unknown; signal?: unknown } = {}
    root.on('user-questions/request', (request: { questions: Array<{ id: string }>; agent?: unknown; signal?: unknown }) => {
      seen = { agent: request.agent, signal: request.signal }
      return Promise.resolve({ answers: request.questions.map(question => ({ id: question.id, selected: ['proceed'] })) })
    })
    await root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate', condition: 'proceed' }],
      },
    }))
    const controller = new AbortController()
    const agent = { id: 'agent-1' } as unknown as Agent
    // The question service accepts only the registry's exact live root agent.
    root.provide('agents', { get: (id: string) => (id === 'agent-1' ? agent : undefined), roots: () => [agent] })
    const definition = root.tools.get('wf_verdict')
    await definition?.execute({ gate: 'gate' }, { agent, signal: controller.signal } as never)
    expect(seen.agent).toBe(agent)
    expect(seen.signal).toBeInstanceOf(AbortSignal)
    expect((seen.signal as AbortSignal).aborted).toBe(true) // the resolved question no longer owns a polling loop
  })

  it('asks a gate that lists no outcomes as a free-text question', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    let asked: { options?: unknown } = {}
    root.on('user-questions/request', (request: { questions: Array<{ id: string; options?: unknown }> }) => {
      asked = request.questions[0] ?? {}
      return Promise.resolve({ answers: [{ id: 'gate', selected: [], custom: 'proceed' }] })
    })
    await root.plugin(spinePlugin, config({
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate' }],
      },
    }))
    await expect(call(root, 'wf_verdict', { gate: 'gate' }))
      .resolves.toMatchObject({ status: 'routed', verdict: 'proceed', answered_by: 'user' })
    expect(asked.options).toBeUndefined()
  })

  it('records a refusal when the answerer itself fails, whatever it throws', async () => {
    const workdir = scratch()
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(UserQuestionService)
    // A listener may reject with anything, so the plugin must report it rather than
    // crash; a rejection that is not an Error is the case under test.
    const notAnError = { toString: () => 'no answerer here' }
    root.on('user-questions/request', () => Promise.reject(notAnError))
    await root.plugin(spinePlugin, config({
      workdir,
      graph: {
        name: 't',
        start_node: 'gate',
        nodes: { gate: { _type: 'GateNode', id: 'gate', evaluator_type: 'user', reads: [] } },
        edges: [{ source: 'gate', target: 'gate' }],
      },
    }))
    await expect(call(root, 'wf_verdict', { gate: 'gate' }))
      .rejects.toThrow(/could not be asked: no answerer here/)
    expect(JSON.parse(readFileSync(join(workdir, '.refactory', 'spine-status.json'), 'utf8')))
      .toMatchObject({ status: 'refused' })
  })
})

describe('the CEO deny guard', () => {
  function agentWith(ctx: Context | undefined, origin = 'root'): Agent {
    return { id: 'agent-1', session: { header: { origin } }, ctx } as unknown as Agent
  }

  /**
   * An agent context inside the composition. The agent runtime mints its context
   * from the agent's own composition, so `tools` resolves there; a bare scope
   * context does not carry that injection, and the proxy supplies the service the
   * guard reads from the agent's side.
   */
  function agentCtxIn(root: Context, parent?: object): Context {
    const scoped = createScope(root, {}, parent === undefined ? {} : { parent }).ctx
    return new Proxy(scoped, {
      get: (target, prop, receiver): unknown => (prop === 'tools' ? root.tools : Reflect.get(target, prop, receiver)),
    })
  }

  /** A root with the injected services, a scoped composition, and the plugin mounted. */
  async function composed(deny: string[]): Promise<{
    root: Context
    composition: Context
    compositionKey: object
    restrict: MockInstance<typeof ToolRuntime.prototype.restrict>
  }> {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    const compositionKey = {}
    const composition = createScope(root, compositionKey).ctx
    await composition.plugin(spinePlugin, config({ ceoDeny: deny }))
    // The tool registry is one instance; a scoped context resolves the same one.
    const restrict = vi.spyOn(root.tools, 'restrict')
    return { root, composition, compositionKey, restrict }
  }

  /** A root with the injected services and the plugin mounted at host level, as the CLI profile mounts it. */
  async function hostLevel(deny: string[]): Promise<{
    root: Context
    restrict: MockInstance<typeof ToolRuntime.prototype.restrict>
  }> {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    await root.plugin(spinePlugin, config({ ceoDeny: deny }))
    const restrict = vi.spyOn(root.tools, 'restrict')
    return { root, restrict }
  }

  it('denies a named tool the CEO can actually reach, and disposes with the agent', async () => {
    const { root, composition, compositionKey, restrict } = await composed(['wf_next', 'ghost-tool'])
    const disposed: number[] = []
    restrict.mockImplementation(() => () => { disposed.push(1) })
    const agent = agentWith(agentCtxIn(root, compositionKey))

    composition.emit('agent/created', { agent, source: 'startup' })
    expect(restrict).toHaveBeenCalledWith({ deny: ['wf_next'] })

    composition.emit('agent/disposed', { agent })
    expect(disposed).toHaveLength(1)
  })

  it('leaves a subagent alone', async () => {
    const { root, composition, compositionKey, restrict } = await composed(['wf_next'])
    const agentCtx = createScope(root, {}, { parent: compositionKey }).ctx
    composition.emit('agent/created', { agent: agentWith(agentCtx, 'subagent'), source: 'startup' })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('leaves an agent outside the composition alone', async () => {
    const { root, composition, restrict } = await composed(['wf_next', 'host-tool'])
    // A host tool the outside agent does see, so only the composition check spares it.
    root.tools.register(defineTool({
      name: 'host-tool',
      description: 'A tool every agent on the host sees.',
      parameters: {},
      output: { schema: { type: 'json' }, render: () => [] },
      execute: () => Promise.resolve(null),
    }))
    composition.emit('agent/created', { agent: agentWith(agentCtxIn(root)), source: 'startup' })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('denies a named tool the CEO can actually reach when the spine is mounted at host level, and disposes with the agent', async () => {
    const { root, restrict } = await hostLevel(['wf_next', 'ghost-tool'])
    const disposed: number[] = []
    restrict.mockImplementation(() => () => { disposed.push(1) })
    const agent = agentWith(agentCtxIn(root))

    root.emit('agent/created', { agent, source: 'startup' })
    expect(restrict).toHaveBeenCalledWith({ deny: ['wf_next'] })

    root.emit('agent/disposed', { agent })
    expect(disposed).toHaveLength(1)
  })

  it('leaves a subagent alone when the spine is mounted at host level', async () => {
    const { root, restrict } = await hostLevel(['wf_next'])
    root.emit('agent/created', { agent: agentWith(agentCtxIn(root), 'subagent'), source: 'startup' })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('leaves an unscoped agent alone when the spine is mounted at host level', async () => {
    const { root, restrict } = await hostLevel(['wf_next'])
    root.emit('agent/created', { agent: agentWith(root), source: 'startup' })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('leaves an unscoped agent alone', async () => {
    const { root, composition, restrict } = await composed(['wf_next'])
    composition.emit('agent/created', { agent: agentWith(root), source: 'startup' })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('denies nothing when the list names tools the CEO cannot reach', async () => {
    const { root, composition, compositionKey, restrict } = await composed(['not-a-tool', 'pwsh'])
    const agentCtx = createScope(root, {}, { parent: compositionKey }).ctx
    composition.emit('agent/created', { agent: agentWith(agentCtx), source: 'startup' })
    expect(restrict).not.toHaveBeenCalled()
  })

  it('disposes an agent the guard never restricted without failing', async () => {
    const { root, composition } = await composed(['wf_next'])
    expect(() => { composition.emit('agent/disposed', { agent: agentWith(root) }) }).not.toThrow()
  })
})
