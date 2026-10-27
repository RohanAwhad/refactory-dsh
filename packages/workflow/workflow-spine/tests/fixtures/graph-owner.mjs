/**
 * The graph profile's spine, mounted headless with a scripted person: the owner a
 * `refactory ceo` run gets, without the CEO model.
 *
 *   node graph-owner.mjs --script FILE -- <graph-startup arguments>
 *
 * The real `graph-startup` row parses the arguments `refactory ceo` passes to
 * `dsh --profile graph run` and publishes the run's graph, work directory, command
 * environment and bridges; the spine row is then mounted with the config
 * `packages/bundle/graph/cordis.patch.yml` gives it. So every node runs as on a real
 * run: commands and agent workers (`worker_node`) dispatched by the spine, a user gate
 * put to the person through the user-questions channel and recorded by the gate bridge.
 * What stands in for the CEO is the loop at the end, which only advances the cursor and
 * routes user gates, as a CEO following the spine's driving contract does.
 *
 * FILE holds `answers`: what the person answers at user gates, in the order the run
 * asks them, across every owner of the run. Each is a verdict, or `{ selected, custom }`
 * for a verdict with a note. A gate asked with no answer left leaves the run waiting for
 * the person, and the owner exits. Each question is appended to `questions.log` beside
 * FILE.
 */
import { spawnSync } from 'node:child_process'
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import * as spinePlugin from '../../src/index.ts'
import { apply as graphStartup, GRAPH_STARTUP_SERVICE } from '../../../../bundle/graph/src/startup.ts'

const argv = process.argv.slice(2)
const at = argv.indexOf('--script')
const split = argv.indexOf('--')
if (at < 0 || argv[at + 1] === undefined || split < 0) {
  throw new Error('usage: graph-owner.mjs --script FILE -- <graph-startup arguments>')
}
const scriptFile = resolve(argv[at + 1])
const answers = JSON.parse(readFileSync(scriptFile, 'utf8')).answers ?? []
const questions = join(dirname(scriptFile), 'questions.log')

const ctx = new Context()
// What the launcher provides before the tree mounts (`provideCmdline`).
const args = Object.freeze(argv.slice(split + 1))
ctx.provide('cmdlineArgs', { get: () => args })
ctx.provide('appExit', code => { throw new Error(`graph-startup refused its arguments (exit ${code})`) })
await ctx.plugin(SystemPrompt)
await ctx.plugin(ToolRuntime)
await ctx.plugin(UserQuestionService)
await ctx.plugin({ name: 'graph-startup', inject: ['cmdlineArgs'], apply: graphStartup })
const startup = ctx.get(GRAPH_STARTUP_SERVICE)
if (startup === undefined) throw new Error('graph-startup published no run')
const workdir = startup.workdir

ctx.on('user-questions/request', request => {
  // The n-th question the run asks gets the n-th answer, whichever owner asks it.
  const asked = existsSync(questions) ? readFileSync(questions, 'utf8').split('\n').filter(Boolean).length : 0
  for (const question of request.questions) appendFileSync(questions, `${question.id}\n`)
  const answer = answers[asked]
  if (answer === undefined) {
    // Nobody answers here: the spine recorded `waiting_user` before asking.
    setImmediate(() => process.exit(0))
    return new Promise(() => {})
  }
  const { selected, custom } = typeof answer === 'string' ? { selected: answer, custom: '' } : answer
  return Promise.resolve({
    answers: request.questions.map(question => ({ id: question.id, selected: [selected], custom: custom ?? '' })),
  })
})
ctx.provide('shell', {
  resolve: spec => ({
    command: spec.command, workdir: spec.workdir ?? workdir, timeoutMs: spec.timeoutMs ?? 600_000,
    onExpiry: 'kill', stdoutMaxBytes: 10_000_000, env: spec.env, sandboxPolicy: spec.sandboxPolicy,
  }),
  execute: async spec => ({
    result: async () => {
      // No startup files: a shell that read the person's profile would rewrite PATH.
      const done = spawnSync('bash', ['--norc', '--noprofile', '-c', spec.command], {
        encoding: 'utf8', cwd: spec.workdir, timeout: spec.timeoutMs, env: { ...process.env, ...spec.env },
        maxBuffer: 64 * 1024 * 1024,
      })
      return {
        exitCode: done.status ?? 1, signal: done.signal, timedOut: done.error?.code === 'ETIMEDOUT', aborted: false,
        timeoutMs: spec.timeoutMs, stdout: { text: done.stdout ?? '', truncated: false },
        stderr: { text: done.stderr ?? '', truncated: false },
      }
    },
  }),
})
// The spine row as the graph profile's patch configures it.
await ctx.plugin(spinePlugin, {
  graph: startup.graph, toolPrefix: 'wf', maxIterations: 500, executeCommands: true,
  workdir: startup.workdir, commandEnv: startup.commandEnv, dataCommand: startup.dataCommand,
  agentCommand: startup.agentCommand, personAttached: startup.chat, gateCommand: startup.gateCommand,
  steerCommand: startup.steerCommand ?? '', steerArgs: startup.steerArgs ?? [], commandTimeoutMs: 3_600_000,
  ceoDeny: ['bash', 'pwsh', 'write', 'edit', 'str_replace_editor', 'web_search', 'web_fetch', 'skill',
    'ralph', 'workflow', 'read_image'],
})

async function call(name, input = {}) {
  const tool = ctx.tools.get(name)
  if (tool === undefined) throw new Error(`tool ${name} not registered`)
  return tool.execute(input, {})
}

for (;;) {
  const next = await call('wf_next')
  if (next.status !== 'ready') break
  const gate = (next.nodes ?? []).find(node => node._type === 'GateNode' && node.evaluator_type === 'user')
  if (gate === undefined) {
    throw new Error(`graph owner has no worker for ${(next.nodes ?? []).map(node => node.id).join(', ')}: ${JSON.stringify(next).slice(0, 2000)}`)
  }
  await call('wf_verdict', { gate: gate.id })
}
process.exit(0)
