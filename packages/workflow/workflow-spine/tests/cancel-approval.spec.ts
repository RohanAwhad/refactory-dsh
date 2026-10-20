/**
 * Cancelled approval rewrite (#347, C3): a `tools/post-execute` listener in the
 * workflow spine that rewrites `ask_user_question` cancellation errors to
 * "Not approved: stop and wait for the person."
 */
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime, { defineTool } from '@deepseek-ai/dsh-tools'
import { UserQuestionError } from '@deepseek-ai/dsh-user-questions'
import { afterEach, describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-cancel-'))
  dirs.push(dir)
  return dir
}

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

/** Register a stub ask_user_question that throws with a UserQuestionError cancellation code. */
function registerCancelStub(root: Context, code: string): void {
  root.tools.register(defineTool({
    name: 'ask_user_question',
    description: 'stub ask_user_question',
    parameters: {
      question: { type: 'string', required: true, description: 'q' },
    },
    output: { schema: { type: 'json' }, render: () => [] },
    execute: () => {
      throw new UserQuestionError('question was cancelled', code)
    },
  }))
}

/** Register a successful ask_user_question stub. */
function registerSuccessStub(root: Context): void {
  root.tools.register(defineTool({
    name: 'ask_user_question',
    description: 'stub ask_user_question',
    parameters: {
      question: { type: 'string', required: true, description: 'q' },
    },
    output: { schema: { type: 'json' }, render: () => [] },
    execute: () => Promise.resolve({ answer: 'yes' }),
  }))
}

/** Register a different tool that throws with a UserQuestionError. */
function registerOtherCancelStub(root: Context): void {
  root.tools.register(defineTool({
    name: 'some_other_tool',
    description: 'stub other',
    parameters: {},
    output: { schema: { type: 'json' }, render: () => [] },
    execute: () => {
      throw new UserQuestionError('something cancelled', 'ASK_CANCELLED')
    },
  }))
}

async function executeTool(
  ctx: Context,
  toolName: string,
  args: unknown,
): Promise<{ isError: boolean; content: Array<{ type: string; text: string }> }> {
  const controller = new AbortController()
  const result = await ctx.tools.execute({
    callId: `call-${Date.now()}-${Math.random()}` as never,
    name: toolName,
    arguments: args,
    signal: controller.signal,
  })
  return result as { isError: boolean; content: Array<{ type: string; text: string }> }
}

describe('cancelled approval rewrite (#347, C3)', () => {
  it('rewrites ask_user_question ASK_CANCELLED to "Not approved: stop and wait for the person."', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerCancelStub(root, 'ASK_CANCELLED')
    await root.plugin(spinePlugin, configuredConfig({ workdir: scratch() }))

    const result = await executeTool(root, 'ask_user_question', { question: 'may I?' })
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).toBe('Not approved: stop and wait for the person.')
  })

  it('rewrites ask_user_question ASK_ABORTED to the same message', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerCancelStub(root, 'ASK_ABORTED')
    await root.plugin(spinePlugin, configuredConfig({ workdir: scratch() }))

    const result = await executeTool(root, 'ask_user_question', { question: 'may I?' })
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).toBe('Not approved: stop and wait for the person.')
  })

  it('rewrites ask_user_question ASK_TIMED_OUT to the same message', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerCancelStub(root, 'ASK_TIMED_OUT')
    await root.plugin(spinePlugin, configuredConfig({ workdir: scratch() }))

    const result = await executeTool(root, 'ask_user_question', { question: 'may I?' })
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).toBe('Not approved: stop and wait for the person.')
  })

  it('does not rewrite a successful ask_user_question result', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerSuccessStub(root)
    await root.plugin(spinePlugin, configuredConfig({ workdir: scratch() }))

    const result = await executeTool(root, 'ask_user_question', { question: 'may I?' })
    expect(result.isError).toBe(false)
    const text = result.content.map(c => c.text).join('')
    expect(text).not.toContain('Not approved')
  })

  it('does not rewrite cancellations of other tools', async () => {
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerOtherCancelStub(root)
    await root.plugin(spinePlugin, configuredConfig({ workdir: scratch() }))

    const result = await executeTool(root, 'some_other_tool', {})
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).not.toBe('Not approved: stop and wait for the person.')
  })
})
