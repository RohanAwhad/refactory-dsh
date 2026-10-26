/**
 * A scripted graph owner: the real spine, mounted headless on a small graph of shell
 * commands, so the Python `refactory graph-control` tests drive the controls against an
 * owner that restores, drains and dispatches exactly as a run's owner does.
 *
 *   node scripted-owner.mjs --workdir DIR [--headless] [--resume]
 *
 * It is what a test run's `owner_argv` starts, by hand or through recovery's
 * supervisor. It reads DIR/owner-script.json:
 *   - `graph`: the workflow graph. Its FnNodes, fn gates and command-carrying agents are
 *     commands, as lowered graphs make them; a test's commands append to a log.
 *   - `answers`: verdicts a person gives at user gates, in the order the run asks them,
 *     across every owner of the run. A gate asked with no answer left leaves the run
 *     waiting for the person, and the owner exits.
 *   - `hold_while_paused`: stay alive while paused, polling for controls the way a
 *     live owner picks them up, for at most `hold_seconds` (default 30).
 * It appends `start <pid>` to DIR/owners.log, and each user-gate question to
 * DIR/questions.log. The spine writes every status and checkpoint itself.
 */
import { spawnSync } from 'node:child_process'
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { setTimeout as wait } from 'node:timers/promises'
import { Context } from '@deepseek-ai/cordis'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import UserQuestionService from '@deepseek-ai/dsh-user-questions'
import * as spinePlugin from '../../src/index.ts'

const argv = process.argv.slice(2)
const at = argv.indexOf('--workdir')
if (at < 0 || argv[at + 1] === undefined) throw new Error('usage: scripted-owner.mjs --workdir DIR')
const workdir = resolve(argv[at + 1])
const script = JSON.parse(readFileSync(join(workdir, 'owner-script.json'), 'utf8'))
const answers = script.answers ?? []
const questions = join(workdir, 'questions.log')
appendFileSync(join(workdir, 'owners.log'), `start ${process.pid}\n`)

const ctx = new Context()
await ctx.plugin(SystemPrompt)
await ctx.plugin(ToolRuntime)
await ctx.plugin(UserQuestionService)
ctx.on('user-questions/request', request => {
  // The n-th question the run asks gets the n-th answer, whichever owner asks it.
  const asked = existsSync(questions) ? readFileSync(questions, 'utf8').split('\n').filter(Boolean).length : 0
  for (const question of request.questions) appendFileSync(questions, `${question.id}\n`)
  const answer = answers[asked]
  if (answer === undefined) {
    // Nobody answers here: the spine recorded `waiting_user` before asking, so the
    // run waits for the person, as a run whose owner exited at a gate does.
    setImmediate(() => process.exit(0))
    return new Promise(() => {})
  }
  return Promise.resolve({ answers: request.questions.map(question => ({ id: question.id, selected: [answer], custom: '' })) })
})
ctx.provide('shell', {
  resolve: spec => ({
    command: spec.command, workdir: spec.workdir ?? workdir, timeoutMs: spec.timeoutMs ?? 60_000,
    onExpiry: 'kill', stdoutMaxBytes: 1_000_000, env: spec.env, sandboxPolicy: spec.sandboxPolicy,
  }),
  execute: async spec => ({
    result: async () => {
      const done = spawnSync('bash', ['-c', spec.command], {
        encoding: 'utf8', cwd: spec.workdir, timeout: spec.timeoutMs, env: { ...process.env, ...spec.env },
      })
      return {
        exitCode: done.status ?? 1, signal: done.signal, timedOut: done.error?.code === 'ETIMEDOUT', aborted: false,
        timeoutMs: spec.timeoutMs, stdout: { text: done.stdout ?? '', truncated: false },
        stderr: { text: done.stderr ?? '', truncated: false },
      }
    },
  }),
})
await ctx.plugin(spinePlugin, {
  graph: script.graph, workdir, toolPrefix: 'wf', maxIterations: 3, interactiveOpen: false,
  openCommand: '', openArgs: [], executeCommands: true, commandEnv: {}, commandTimeoutMs: 60_000,
  commandSandboxMode: 'danger-full-access', dataCommand: '', steerCommand: '', steerArgs: [], ceoDeny: [],
})

async function call(name, args = {}) {
  const tool = ctx.tools.get(name)
  if (tool === undefined) throw new Error(`tool ${name} not registered`)
  return tool.execute(args, {})
}

const deadline = Date.now() + (script.hold_seconds ?? 30) * 1000
for (;;) {
  const next = await call('wf_next')
  if (next.status === 'ready') {
    const gate = (next.nodes ?? []).find(node => node._type === 'GateNode' && node.evaluator_type === 'user')
    if (gate === undefined) throw new Error(`scripted owner has no worker for ${(next.nodes ?? []).map(node => node.id).join(', ')}`)
    await call('wf_verdict', { gate: gate.id })
    continue
  }
  if (next.status === 'paused' && script.hold_while_paused && Date.now() < deadline) {
    await wait(100)
    continue
  }
  break
}
process.exit(0)
