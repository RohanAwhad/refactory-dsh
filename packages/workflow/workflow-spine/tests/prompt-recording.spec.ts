/**
 * CEO-dispatched prompt recording and validation (#347, C2): the spine records
 * each prompt the CEO sends through subagent/subagent_fork/send_message and
 * validates that review/audit nodes received the pending delivery's prompt.txt.
 */
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime, { defineTool } from '@deepseek-ai/dsh-tools'
import { afterEach, describe, expect, it } from 'vitest'
import * as spinePlugin from '../src/index.ts'
import type { Config } from '../src/index.ts'
import type { WorkflowGraph } from '../src/types.ts'

const dirs: string[] = []

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true })
})

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-spine-prompt-'))
  dirs.push(dir)
  return dir
}

/** Graph with a reviewer node (health_checker) followed by a non-reviewer (b). */
const REVIEWER_GRAPH: WorkflowGraph = {
  name: 't',
  start_node: 'health_checker',
  nodes: {
    health_checker: {
      _type: 'AgentNode', id: 'health_checker', role: 'reviewer',
      prompt_template: 'Review the code', writes: [], reads: [],
    },
    b: {
      _type: 'AgentNode', id: 'b', role: 'builder',
      prompt_template: 'Build something', writes: [], reads: [],
    },
  },
  edges: [
    { source: 'health_checker', target: 'b' },
  ],
}

/** Non-reviewer graph. */
const PLAIN_GRAPH: WorkflowGraph = {
  name: 't',
  start_node: 'emit',
  nodes: {
    emit: {
      _type: 'AgentNode', id: 'emit', role: 'builder',
      prompt_template: 'Write emit.txt', writes: ['emit.txt'], reads: [],
    },
  },
  edges: [],
}

function configuredConfig(overrides: Partial<Config> = {}): Config {
  return {
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

function registerStubs(root: Context): void {
  root.tools.register(defineTool({
    name: 'subagent',
    description: 'stub subagent',
    parameters: {
      prompt: { type: 'string', required: true, description: 'prompt' },
    },
    output: { schema: { type: 'json' }, render: () => [] },
    execute: () => Promise.resolve({ result: 'ok' }),
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

/** An executable stub for the agent command bridge. */
function agentStub(_workdir: string): string {
  // The stub bridge returns a minimal record for begin with a prompt field,
  // an empty object for finish, and a reviewers list for the reviewers action.
  const scriptDir = scratch()
  const script = join(scriptDir, 'agent-bridge')
  writeFileSync(script, `#!/usr/bin/env python3
import sys, json
req = json.load(sys.stdin)
action = req.get("action", "")
if action == "begin":
    print(json.dumps({"prompt": req.get("prompt", ""), "id": "test", "node": req.get("node", "")}))
elif action == "finish":
    print(json.dumps({"status": "delivered"}))
elif action == "reviewers":
    print(json.dumps({"reviewers": ["health_checker", "code_reviewer", "qa_director", "overwatch", "focused_qa"]}))
elif action == "invalidate":
    print(json.dumps({"requested": []}))
else:
    print(json.dumps({"error": "unknown action"}), file=sys.stderr)
    sys.exit(1)
`)
  chmodSync(script, 0o755)
  return script
}

describe('CEO-dispatched prompt recording and validation (#347, C2)', () => {
  it('wf_done refuses for review node when no recorded prompt matches prompt.txt', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: REVIEWER_GRAPH, workdir, agentCommand: stub,
    }))

    // Advance so the node is due and delivery context is established
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Do NOT record any CEO prompt — go directly to wf_done
    const result = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).toContain('wf_done refused')
  })

  it('wf_done succeeds for review node when recorded prompt contains prompt.txt', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: REVIEWER_GRAPH, workdir, agentCommand: stub,
    }))

    // Advance so the node is due
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Record a CEO prompt that includes the delivery prompt text
    await executeTool(root, 'subagent', { prompt: 'Review the code with full context' })

    const result = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(result.isError).toBe(false)
  })

  it('wf_done succeeds for non-review node without prompt validation', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: PLAIN_GRAPH, workdir, agentCommand: stub,
    }))

    // Advance so the node is due
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // No prompt recorded, but emit is not a reviewer node — should succeed
    const result = await executeTool(root, 'wf_done', { node: 'emit' })
    expect(result.isError).toBe(false)
  })
})
