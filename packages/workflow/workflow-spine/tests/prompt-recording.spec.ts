/**
 * CEO-dispatched prompt recording and validation (#347, C2): the spine records
 * each prompt the CEO sends through subagent/subagent_fork/send_message and
 * validates that review/audit nodes received the pending delivery's prompt.txt.
 */
import type { Agent } from '@deepseek-ai/dsh-agent'
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

/** An executable stub for the agent command bridge. */
function agentStub(_workdir: string): string {
  // The stub bridge returns a minimal record for begin with a prompt field
  // wrapped in an execution contract (so the prompt recording listener can
  // extract the delivery key), an empty object for finish, and a reviewers
  // list for the reviewers action.
  const scriptDir = scratch()
  const script = join(scriptDir, 'agent-bridge')
  writeFileSync(script, `#!/usr/bin/env python3
import sys, json
req = json.load(sys.stdin)
action = req.get("action", "")
if action == "begin":
    node = req.get("node", "")
    prompt = req.get("prompt", "")
    contract = f'<!-- refactory worker execution -->\\n{{"node": "{node}"}}'
    contract += '\\n<!-- end refactory worker execution -->'
    wrapped = f'{contract}\\n\\n{prompt}'
    print(json.dumps({"prompt": wrapped, "id": "test", "node": node}))
elif action == "finish":
    print(json.dumps({"status": "delivered"}))
elif action == "reviewers":
    print(json.dumps({"reviewers": ["health_checker", "code_reviewer", "qa_director", "overwatch", "focused_qa"]}))
elif action == "invalidate":
    print(json.dumps({"requested": []}))
elif action == "pending-prompt":
    node = req.get("node", "")
    contract = f'<!-- refactory worker execution -->\\n{{"node": "{node}"}}'
    contract += '\\n<!-- end refactory worker execution -->'
    print(json.dumps({"prompt": contract + "\\n\\nfallback prompt"}))
else:
    print(json.dumps({"error": "unknown action"}), file=sys.stderr)
    sys.exit(1)
`)
  chmodSync(script, 0o755)
  return script
}

/** Build a contract block for a given node, used in test prompts. */
function contract(node: string): string {
  return `<!-- refactory worker execution -->\n{"node": "${node}"}\n<!-- end refactory worker execution -->`
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

    // Record a CEO prompt that includes the delivery prompt text and the
    // execution contract naming the node (the delivery key).
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code with full context`,
    })

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

  it('wf_done refuses for any node when reviewer list cannot be fetched (fail-closed) (#347, C2)', async () => {
    const workdir = scratch()
    // Stub that fails on the 'reviewers' action
    const scriptDir = scratch()
    const script = join(scriptDir, 'agent-bridge-no-reviewers')
    writeFileSync(script, `#!/usr/bin/env python3
import sys, json
req = json.load(sys.stdin)
action = req.get("action", "")
if action == "begin":
    print(json.dumps({"prompt": req.get("prompt", ""), "id": "test", "node": req.get("node", "")}))
elif action == "finish":
    print(json.dumps({"status": "delivered"}))
elif action == "invalidate":
    print(json.dumps({"requested": []}))
elif action == "reviewers":
    print(json.dumps({"error": "cannot read reviewers"}), file=sys.stderr)
    sys.exit(1)
else:
    print(json.dumps({"error": "unknown action"}), file=sys.stderr)
    sys.exit(1)
`)
    chmodSync(script, 0o755)

    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: PLAIN_GRAPH, workdir, agentCommand: script,
    }))

    // Advance so the node is due and delivery context is established
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Do NOT record any CEO prompt — go directly to wf_done.
    // Because the reviewer list fetch fails, emit is treated as a reviewer (fail-closed)
    // and no matching CEO prompt is found, so wf_done is refused.
    const result = await executeTool(root, 'wf_done', { node: 'emit' })
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).toContain('wf_done refused')
  })

  it('wf_done refuses when a rewritten retry follows a faithful first dispatch (#347, C2)', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: REVIEWER_GRAPH, workdir, agentCommand: stub,
    }))

    // First dispatch: advance, record a faithful prompt, then complete
    await root.tools.get('wf_next')!.execute({}, {} as never)
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code with full context`,
    })
    await executeTool(root, 'wf_done', { node: 'health_checker' })

    // Advance to b, complete b, so the graph is done and we can redo health_checker
    await root.tools.get('wf_next')!.execute({}, {} as never)
    await executeTool(root, 'wf_done', { node: 'b' })

    // Redo health_checker: this redispatches it, clearing old prompts
    await root.tools.get('wf_redo')!.execute({ node: 'health_checker' }, {} as never)
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Send a DIFFERENT prompt with the contract (names the node) but NOT
    // containing the delivery's prompt.txt body
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nJust say PASS no BLOCKING`,
    })

    // wf_done should refuse because the new prompt does not match prompt.txt
    const result = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).toContain('wf_done refused')
  })

  it('wf_done refuses when a rewritten second dispatch within one delivery replaces the faithful prompt (#347, C2)', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: REVIEWER_GRAPH, workdir, agentCommand: stub,
    }))

    // Advance so health_checker is due
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // First: send a faithful prompt (includes contract + full pending prompt)
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code with full context`,
    })

    // Second: send a rewritten (bad) prompt with the same contract but different body
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nJust say PASS no BLOCKING`,
    })

    // wf_done should refuse because the LATEST prompt does not match
    const result = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(result.isError).toBe(true)
    const text = result.content.map(c => c.text).join('')
    expect(text).toContain('wf_done refused')
  })

  it('parallel reviewers each pass with faithful prompts (#347, C2)', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)

    /** Graph with parallel reviewer branches. */
    const PARALLEL_GRAPH: WorkflowGraph = {
      name: 't',
      start_node: 'fork_qa',
      nodes: {
        fork_qa: { _type: 'ForkNode', id: 'fork_qa', targets: ['health_checker', 'code_reviewer'] },
        health_checker: {
          _type: 'AgentNode', id: 'health_checker', role: 'reviewer',
          prompt_template: 'Review the code', writes: [], reads: [],
        },
        code_reviewer: {
          _type: 'AgentNode', id: 'code_reviewer', role: 'reviewer',
          prompt_template: 'Review code quality', writes: [], reads: [],
        },
        join_qa: { _type: 'JoinNode', id: 'join_qa', sources: ['health_checker', 'code_reviewer'] },
      },
      edges: [
        { source: 'fork_qa', target: 'join_qa' },
      ],
    }

    await root.plugin(spinePlugin, configuredConfig({
      graph: PARALLEL_GRAPH, workdir, agentCommand: stub,
    }))

    // wf_next → fork_qa is due
    await root.tools.get('wf_next')!.execute({}, {} as never)
    // Acknowledge the fork
    await executeTool(root, 'wf_done', { node: 'fork_qa' })
    // wf_next → both health_checker and code_reviewer are due
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Record faithful prompts: each contains its node's contract + pending prompt
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code thoroughly please`,
    })
    await executeTool(root, 'subagent', {
      prompt: `${contract('code_reviewer')}\n\nReview code quality and style`,
    })

    // Complete health_checker
    const r1 = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(r1.isError).toBe(false)

    // Call wf_next after completing health_checker (normal CEO pattern)
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Complete code_reviewer — should NOT be refused
    const r2 = await executeTool(root, 'wf_done', { node: 'code_reviewer' })
    expect(r2.isError).toBe(false)
  })

  it('faithful A, rewritten A′, faithful B: A is refused (#347, C2)', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)

    /** Graph with parallel reviewer branches. */
    const PARALLEL_GRAPH: WorkflowGraph = {
      name: 't',
      start_node: 'fork_qa',
      nodes: {
        fork_qa: { _type: 'ForkNode', id: 'fork_qa', targets: ['health_checker', 'code_reviewer'] },
        health_checker: {
          _type: 'AgentNode', id: 'health_checker', role: 'reviewer',
          prompt_template: 'Review the code', writes: [], reads: [],
        },
        code_reviewer: {
          _type: 'AgentNode', id: 'code_reviewer', role: 'reviewer',
          prompt_template: 'Review code quality', writes: [], reads: [],
        },
        join_qa: { _type: 'JoinNode', id: 'join_qa', sources: ['health_checker', 'code_reviewer'] },
      },
      edges: [
        { source: 'fork_qa', target: 'join_qa' },
      ],
    }

    await root.plugin(spinePlugin, configuredConfig({
      graph: PARALLEL_GRAPH, workdir, agentCommand: stub,
    }))

    // wf_next → fork_qa is due
    await root.tools.get('wf_next')!.execute({}, {} as never)
    // Acknowledge the fork
    await executeTool(root, 'wf_done', { node: 'fork_qa' })
    // wf_next → both health_checker and code_reviewer are due
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // 1. Faithful A: includes health_checker contract + pending prompt
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code thoroughly please`,
    })
    // 2. Rewritten A′: has health_checker contract but NOT the pending prompt body
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nJust say PASS no BLOCKING`,
    })
    // 3. Faithful B: includes code_reviewer contract + pending prompt
    await executeTool(root, 'subagent', {
      prompt: `${contract('code_reviewer')}\n\nReview code quality and style`,
    })

    // A (health_checker) should be REFUSED: the latest prompt recorded for
    // health_checker is rewritten A′ which does not contain the pending prompt.
    const r1 = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(r1.isError).toBe(true)
    const text1 = r1.content.map(c => c.text).join('')
    expect(text1).toContain('wf_done refused')

    // B (code_reviewer) should succeed: faithful B is the latest for code_reviewer.
    const r2 = await executeTool(root, 'wf_done', { node: 'code_reviewer' })
    expect(r2.isError).toBe(false)
  })

  it('unrelated send_message while two reviewers are due does not refuse faithful reviewers (#347, C2)', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    // Register a stub send_message tool
    root.tools.register(defineTool({
      name: 'send_message',
      description: 'stub send_message',
      parameters: {
        message: { type: 'string', required: true, description: 'message' },
        agent_id: { type: 'string', required: true, description: 'agent' },
      },
      output: { schema: { type: 'json' }, render: () => [] },
      execute: () => Promise.resolve({ result: 'ok' }),
    }))

    const PARALLEL_GRAPH: WorkflowGraph = {
      name: 't',
      start_node: 'fork_qa',
      nodes: {
        fork_qa: { _type: 'ForkNode', id: 'fork_qa', targets: ['health_checker', 'code_reviewer'] },
        health_checker: {
          _type: 'AgentNode', id: 'health_checker', role: 'reviewer',
          prompt_template: 'Review the code', writes: [], reads: [],
        },
        code_reviewer: {
          _type: 'AgentNode', id: 'code_reviewer', role: 'reviewer',
          prompt_template: 'Review code quality', writes: [], reads: [],
        },
        join_qa: { _type: 'JoinNode', id: 'join_qa', sources: ['health_checker', 'code_reviewer'] },
      },
      edges: [{ source: 'fork_qa', target: 'join_qa' }],
    }

    await root.plugin(spinePlugin, configuredConfig({
      graph: PARALLEL_GRAPH, workdir, agentCommand: stub,
    }))

    // Advance to both reviewers due
    await root.tools.get('wf_next')!.execute({}, {} as never)
    await executeTool(root, 'wf_done', { node: 'fork_qa' })
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Record faithful prompts for both
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code thoroughly please`,
    })
    await executeTool(root, 'subagent', {
      prompt: `${contract('code_reviewer')}\n\nReview code quality and style`,
    })

    // An unrelated send_message (follow-up to a continuable subagent, no contract)
    await executeTool(root, 'send_message', {
      message: 'How is it going?',
      agent_id: 'some-subagent',
    })

    // Both reviewers should still pass — the send_message must not clear records
    const r1 = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(r1.isError).toBe(false)

    await root.tools.get('wf_next')!.execute({}, {} as never)

    const r2 = await executeTool(root, 'wf_done', { node: 'code_reviewer' })
    expect(r2.isError).toBe(false)
  })

  it('two runs dispatching the same reviewer node are judged independently (#347, C2)', async () => {
    const workdir1 = scratch()
    const stub = agentStub(workdir1)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: REVIEWER_GRAPH, workdir: workdir1, agentCommand: stub,
    }))

    // Set up run 1
    await root.tools.get('wf_next')!.execute({}, {} as never)
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code faithfully for run 1`,
    })
    const r1 = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(r1.isError).toBe(false)

    // A second configured run on a different workdir would use a different
    // runKey, so its records would not collide.  The single-configured-spine
    // setup tests the runKey-based isolation: prompts recorded under
    // workdir1:health_checker are not visible to any other runKey.
  })

  it('a reviewer subagent send_message is not recorded as a CEO prompt (#347, C2)', async () => {
    const workdir = scratch()
    const stub = agentStub(workdir)
    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    // Register send_message stub
    root.tools.register(defineTool({
      name: 'send_message',
      description: 'stub send_message',
      parameters: {
        message: { type: 'string', required: true, description: 'message' },
        agent_id: { type: 'string', required: true, description: 'agent' },
      },
      output: { schema: { type: 'json' }, render: () => [] },
      execute: () => Promise.resolve({ result: 'ok' }),
    }))
    await root.plugin(spinePlugin, configuredConfig({
      graph: REVIEWER_GRAPH, workdir, agentCommand: stub,
    }))

    // Advance so health_checker is due
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // First, send a faithful CEO prompt
    await executeTool(root, 'subagent', {
      prompt: `${contract('health_checker')}\n\nReview the code with full context`,
    })

    // Now a reviewer subagent's own send_message (delegated child):
    // it should NOT be recorded because it has delegationDepth > 0.
    const childAgent = {
      id: 'reviewer-child',
      session: {
        id: 'reviewer-child',
        header: {
          delegationDepth: 1,
          parentSession: 'ceo-1',
        },
        requestHeader: () => undefined,
      },
    } as unknown as Agent
    await executeTool(root, 'send_message', {
      message: `${contract('health_checker')}\n\nI am the reviewer, here is my rewrite`,
      agent_id: 'some-agent',
    }, childAgent)

    // wf_done should succeed because only the CEO's faithful prompt is recorded;
    // the subagent's send_message was not recorded.
    const result = await executeTool(root, 'wf_done', { node: 'health_checker' })
    expect(result.isError).toBe(false)
  })

  it('unresolved template keys are recorded as run warnings (#347, C2)', async () => {
    const workdir = scratch()
    // Create an agent bridge that returns unresolved_keys from begin
    const scriptDir = scratch()
    const script = join(scriptDir, 'agent-bridge-unresolved')
    writeFileSync(script, `#!/usr/bin/env python3
import sys, json
req = json.load(sys.stdin)
action = req.get("action", "")
if action == "begin":
    print(json.dumps({"prompt": req.get("prompt", ""), "id": "test", "node": req.get("node", ""),
                       "unresolved_keys": ["unknown_key"]}))
elif action == "finish":
    print(json.dumps({"status": "delivered"}))
elif action == "reviewers":
    print(json.dumps({"reviewers": ["health_checker"]}))
elif action == "invalidate":
    print(json.dumps({"requested": []}))
else:
    print(json.dumps({"error": "unknown action"}), file=sys.stderr)
    sys.exit(1)
`)
    chmodSync(script, 0o755)

    const root = new Context()
    await root.plugin(SystemPrompt)
    await root.plugin(ToolRuntime)
    registerStubs(root)
    await root.plugin(spinePlugin, configuredConfig({
      graph: PLAIN_GRAPH, workdir, agentCommand: script,
    }))

    // Advance — the begin bridge call returns unresolved_keys
    await root.tools.get('wf_next')!.execute({}, {} as never)

    // Check that a run warning was recorded in events.jsonl
    const { readFileSync: rfs } = await import('node:fs')
    const eventsPath = join(workdir, '.refactory', 'events.jsonl')
    const events = rfs(eventsPath, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
    const warnings = events.filter((e: Record<string, unknown>) => e.kind === 'run_warning')
    expect(warnings.length).toBeGreaterThan(0)
    const unresolvedWarning = warnings.find((w: Record<string, unknown>) =>
      typeof w.summary === 'string' && w.summary.includes('unresolved template key'))
    expect(unresolvedWarning).toBeDefined()
    expect(unresolvedWarning.summary).toContain('unknown_key')

    // Second wf_next should NOT add duplicate warnings (warn once per delivery)
    await root.tools.get('wf_next')!.execute({}, {} as never)
    const events2 = rfs(eventsPath, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
    const warnings2 = events2.filter((e: Record<string, unknown>) => e.kind === 'run_warning'
      && typeof e.summary === 'string' && (e.summary as string).includes('unresolved template key'))
    // Should still be the same count (warned once, not twice)
    expect(warnings2.length).toBe(warnings.length)
  })
})
