/**
 * Subagent write confinement (#347, C0): a `tools/pre-execute` listener in the
 * workflow spine that denies `write` and `edit` calls whose resolved path falls
 * outside the open run's directory, when the calling session is a delegated
 * child of a session that has an open run.
 *
 * These tests mount the spine plugin with the services it injects, register a
 * stub `write` tool so `ToolRuntime.execute` fires the full pre-execute pipeline,
 * and verify that the listener denies outside-run writes while allowing inside-run
 * writes and non-write tools.
 */
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime, { defineTool } from '@deepseek-ai/dsh-tools'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { afterEach, describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-confine-'))
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

function configuredConfig(overrides: Partial<Config> = {}): Config {
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

function interactiveConfig(overrides: Partial<Config> = {}): Config {
  return {
    toolPrefix: 'wf',
    maxIterations: 3,
    interactiveOpen: true,
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

/** Register the stub write, edit and read tools the pre-execute pipeline needs. */
function registerStubs(root: Context): void {
  root.tools.register(defineTool({
    name: 'write',
    description: 'stub write',
    parameters: {
      file_path: { type: 'string', required: true, description: 'path' },
      content: { type: 'string', description: 'content' },
    },
    output: { schema: { type: 'json' }, render: () => [] },
    execute: () => Promise.resolve(null),
  }))
  root.tools.register(defineTool({
    name: 'edit',
    description: 'stub edit',
    parameters: {
      file_path: { type: 'string', required: true, description: 'path' },
      old_string: { type: 'string', description: 'old' },
      new_string: { type: 'string', description: 'new' },
    },
    output: { schema: { type: 'json' }, render: () => [] },
    execute: () => Promise.resolve(null),
  }))
  root.tools.register(defineTool({
    name: 'read',
    description: 'stub read',
    parameters: {
      file_path: { type: 'string', required: true, description: 'path' },
    },
    output: { schema: { type: 'json' }, render: () => [] },
    execute: () => Promise.resolve(null),
  }))
}

/**
 * Mount with a configured graph (CLI graph profile mode).
 */
async function mountConfigured(overrides: Partial<Config> = {}): Promise<Context> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  registerStubs(root)
  await root.plugin(spinePlugin, configuredConfig(overrides))
  return root
}

/**
 * Mount with interactiveOpen: true (GUI CEO preset mode).
 */
async function mountInteractive(overrides: Partial<Config> = {}): Promise<Context> {
  const root = new Context()
  await root.plugin(SystemPrompt)
  await root.plugin(ToolRuntime)
  registerStubs(root)
  await root.plugin(spinePlugin, interactiveConfig(overrides))
  return root
}

/**
 * Mock agent with the session fields the listener reads.
 */
function mockAgent(sessionId: string, opts: {
  cwd?: string
  delegationDepth?: number
  parentSession?: string
} = {}): Agent {
  // When the agent is a delegated child (has parentSession or delegationDepth > 0),
  // set origin to 'subagent' so the CEO deny guard (which checks header.origin)
  // skips it without accessing agent.ctx.
  const isChild = opts.parentSession !== undefined || (opts.delegationDepth !== undefined && opts.delegationDepth > 0)
  return {
    id: sessionId,
    session: {
      id: sessionId,
      header: {
        cwd: opts.cwd,
        delegationDepth: opts.delegationDepth,
        parentSession: opts.parentSession,
        ...(isChild ? { origin: 'subagent' } : {}),
      },
      requestHeader: () => undefined,
    },
  } as unknown as Agent
}

/**
 * Execute a tool through the full ToolRuntime pipeline (including pre-execute).
 */
async function executeTool(
  ctx: Context,
  toolName: string,
  args: unknown,
  agent?: Agent,
): Promise<{ isError: boolean; content: Array<{ type: string; text: string }> }> {
  const controller = new AbortController()
  const result = await ctx.tools.execute({
    callId: `call-${Date.now()}-${Math.random()}` as never,
    name: toolName,
    arguments: args,
    signal: controller.signal,
    ...(agent !== undefined ? { agent } : {}),
  })
  return result as { isError: boolean; content: Array<{ type: string; text: string }> }
}

/** An executable stub, so `spawnSync` runs it exactly as it would the console. */
function executable(body: string): string {
  const path = join(scratch(), 'open-stub')
  writeFileSync(path, `#!/bin/sh\n${body}\n`)
  chmodSync(path, 0o755)
  return path
}

/** A lowered graph on disk, as `interactive.prepare` would leave it. */
function lower(run: string, graph: WorkflowGraph = AGENT_GRAPH): string {
  const path = join(run, 'graphs', 't.graph.json')
  mkdirSync(join(run, 'graphs'), { recursive: true })
  writeFileSync(path, JSON.stringify(graph))
  return path
}

/** The open command's payload, as the console's `open` verb returns it. */
function payload(run: string, graph: string): string {
  return JSON.stringify({
    mode: 'pkg/t',
    run,
    name: 't',
    graph,
    declarations: join(run, '.refactory', 'declarations.json'),
  })
}

/** An executable that prints the payload `open` prints, for a run already lowered. */
function openStub(run: string, graph: string): string {
  return executable(`printf '%s' '${payload(run, graph)}'`)
}

// ────────────────────────────────────────────────────────────────────────
// Test cases
// ────────────────────────────────────────────────────────────────────────

describe('subagent write confinement', () => {
  describe('configured-graph mode (CLI graph profile)', () => {
    it('allows a subagent to write inside the run directory', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'write', {
        file_path: join(workdir, 'output.txt'),
        content: 'hello',
      }, child)
      expect(result.isError).toBe(false)
    })

    it('denies a subagent writing outside the run directory', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/elsewhere/escape.txt',
        content: 'bad',
      }, child)
      expect(result.isError).toBe(true)
      const text = result.content.map(c => c.text).join('')
      expect(text).toContain('this subagent may only write inside the run directory')
    })

    it('denies a subagent editing outside the run directory', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'edit', {
        file_path: '/tmp/elsewhere/escape.txt',
        old_string: 'a',
        new_string: 'b',
      }, child)
      expect(result.isError).toBe(true)
      const text = result.content.map(c => c.text).join('')
      expect(text).toContain('this subagent may only write inside the run directory')
    })

    it('denies path traversal via ../', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'write', {
        file_path: join(workdir, '..', 'escape', 'file.txt'),
        content: 'bad',
      }, child)
      expect(result.isError).toBe(true)
    })

    it('allows a non-subagent (CEO) to write outside', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      // No delegationDepth, no parentSession — this is the CEO
      const ceo = mockAgent('ceo-1', { cwd: workdir })

      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/anywhere/file.txt',
        content: 'allowed',
      }, ceo)
      // Not denied by our listener (the CEO deny guard is separate)
      expect(result.isError).toBe(false)
    })

    it('allows a non-write/edit tool from a subagent', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'read', {
        file_path: '/tmp/anywhere/file.txt',
      }, child)
      expect(result.isError).toBe(false)
    })
  })

  describe('path resolution with session cwd', () => {
    it('denies a relative path when session cwd is outside the run dir', async () => {
      const workdir = scratch()
      const outsideCwd = scratch() // a different directory
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: outsideCwd,
      })

      // Relative path resolves against outsideCwd, which is outside workdir
      const result = await executeTool(ctx, 'write', {
        file_path: 'file.txt',
        content: 'bad',
      }, child)
      expect(result.isError).toBe(true)
      const text = result.content.map(c => c.text).join('')
      expect(text).toContain('this subagent may only write inside the run directory')
    })

    it('allows a relative path when session cwd is inside the run dir', async () => {
      const workdir = scratch()
      const subDir = join(workdir, 'sub')
      mkdirSync(subDir, { recursive: true })
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: subDir,
      })

      // Relative path resolves against subDir, which is inside workdir
      const result = await executeTool(ctx, 'write', {
        file_path: 'file.txt',
        content: 'ok',
      }, child)
      expect(result.isError).toBe(false)
    })
  })

  describe('subagent_fork children', () => {
    it('denies a fork child writing outside the run directory', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      // A subagent_fork child: has parentSession and delegationDepth
      const forkChild = mockAgent('fork-child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/elsewhere/escape.txt',
        content: 'bad',
      }, forkChild)
      expect(result.isError).toBe(true)
    })
  })

  describe('nested subagent (grandchild)', () => {
    it('denies a grandchild writing outside the run directory', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      // Emit agent/created for the child so childToParent is populated
      const childAgent = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })
      ctx.emit('agent/created', { agent: childAgent, source: 'startup' })

      // Grandchild: its parentSession is child-1
      const grandchild = mockAgent('grandchild-1', {
        delegationDepth: 2,
        parentSession: 'child-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/elsewhere/escape.txt',
        content: 'bad',
      }, grandchild)
      expect(result.isError).toBe(true)
      const text = result.content.map(c => c.text).join('')
      expect(text).toContain('this subagent may only write inside the run directory')
    })
  })

  describe('parent has no open run', () => {
    it('allows freely when the parent has no open run (interactiveOpen)', async () => {
      const ctx = await mountInteractive()

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: '/tmp',
      })

      // No run is open, and no configuredSpine (interactiveOpen: true, no workdir)
      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/anywhere/file.txt',
        content: 'ok',
      }, child)
      expect(result.isError).toBe(false)
    })
  })

  describe('GUI CEO preset: wf_open integration', () => {
    it('denies a subagent writing outside the opened run directory', async () => {
      const run = scratch()
      const graph = lower(run)
      const ctx = await mountInteractive({
        openCommand: openStub(run, graph),
      })

      // Open a run as the CEO session
      const ceoSession = 'ceo-gui-1'
      const definition = ctx.tools.get('wf_open')!
      await definition.execute(
        { mode: 'pkg/t', workdir: run },
        { agent: { session: { id: ceoSession, requestHeader: () => undefined } } } as never,
      )

      // Emit agent/created for the child so childToParent is populated
      const childAgent = mockAgent('gui-child-1', {
        delegationDepth: 1,
        parentSession: ceoSession,
        cwd: run,
      })
      ctx.emit('agent/created', { agent: childAgent, source: 'startup' })

      // The child tries to write outside the opened run's workdir
      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/elsewhere/escape.txt',
        content: 'bad',
      }, childAgent)
      expect(result.isError).toBe(true)
      const text = result.content.map(c => c.text).join('')
      expect(text).toContain('this subagent may only write inside the run directory')

      // But writing inside the run directory is allowed
      const okResult = await executeTool(ctx, 'write', {
        file_path: join(run, 'output.txt'),
        content: 'ok',
      }, childAgent)
      expect(okResult.isError).toBe(false)
    })
  })

  describe('deny message', () => {
    it('contains the run directory path', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      const child = mockAgent('child-1', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/elsewhere/escape.txt',
        content: 'bad',
      }, child)
      expect(result.isError).toBe(true)
      // The deny message must name the run directory where the subagent may write
      const text = result.content.map(c => c.text).join('')
      // canonicalWorkdir resolves symlinks and realpath, so use resolve on the
      // workdir to get what canonicalWorkdir returns for an existing directory
      expect(text).toContain(resolve(workdir))
    })
  })

  describe('direct child without agent/created event', () => {
    it('confines a direct child even without agent/created by using header.parentSession', async () => {
      const workdir = scratch()
      const ctx = await mountConfigured({ workdir })

      // Do NOT emit agent/created — the child's header.parentSession is still
      // used as the first step up the chain.
      const child = mockAgent('unseen-child', {
        delegationDepth: 1,
        parentSession: 'ceo-1',
        cwd: workdir,
      })

      // In configured-graph mode, config.workdir is the fallback boundary even
      // if ceo-1 is not in openedBySession, because configuredSpine exists.
      const result = await executeTool(ctx, 'write', {
        file_path: '/tmp/elsewhere/escape.txt',
        content: 'bad',
      }, child)
      expect(result.isError).toBe(true)
    })
  })
})
