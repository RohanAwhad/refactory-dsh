/**
 * A scripted model for the acceptance run: the CEO it answers spawns one subagent
 * through each delegation tool, and each subagent's tool catalog is recorded.
 *
 * It is one plugin row the acceptance run adds with `--patch`, registering the
 * `scripted` provider route, so the rest of the profile is the one that ships: the
 * subagent is created by the real delegation row, joins the real preset and gets the
 * real `toolFilter`. The tools a model request carries are the catalog the child
 * itself was given, which is the fact under test, so the record is that list.
 *
 * Config: `log` is the JSON-lines file that gets a `{ via, tools }` record per
 * subagent, a `{ via, nested }` record for the answer to a subagent's own
 * `subagent` call, and a `{ result }` record per tool result the CEO reads, so a
 * delegation that failed says why; `tools` are the delegation tools the CEO calls,
 * once each, in order; `marker`, when set, is text the conversation must contain
 * before the CEO delegates.
 */
import { appendFileSync } from 'node:fs'

export const name = 'acceptance-scripted-model'
export const inject = ['llm']

/** The fixed statement DSH adds to every in-process child's runtime context. */
const DELEGATED = 'You are a delegated subagent'

/** The delegation tool a child was spawned through, named in its own prompt. */
const SPAWNED_BY = /spawned by (\w+)/gu

/** One assistant reply: a text answer, or one tool call. */
function* reply(block) {
  yield { type: 'block-start', index: 0, blockType: block.type }
  if (block.type === 'text') yield { type: 'text-delta', index: 0, text: block.text }
  else yield { type: 'tool-call-delta', index: 0, id: block.id, name: block.name, argumentsDelta: block.arguments }
  yield { type: 'block-end', index: 0, block }
  yield { type: 'finish', reason: { kind: block.type === 'text' ? 'stop' : 'tool-calls' } }
}

class ScriptedModel {
  constructor(config) {
    this.config = config
  }

  providerInfo(provider) {
    return { id: provider, name: provider }
  }

  providerRetryPolicy() {}

  imageRequestPricing() {}

  listModels(provider) {
    return Promise.resolve([{ provider, id: 'scripted', name: 'scripted' }])
  }

  resolveModel(provider, model) {
    return Promise.resolve({ provider, id: model, name: model })
  }

  async prepareCall(provider, model, signal) {
    return { model: await this.resolveModel(provider, model, signal), stream: options => this.stream(options) }
  }

  async *stream(options) {
    const text = JSON.stringify(options.messages)
    const tools = (options.tools ?? []).map(tool => tool.name)
    const last = options.messages.at(-1)
    const result = last?.role === 'tool'
      ? (last.content ?? []).filter(block => block.type === 'text').map(block => block.text).join('')
      : undefined
    if (text.includes(DELEGATED)) {
      // A forked child inherits its parent's history, so the last name is its own.
      const via = [...text.matchAll(SPAWNED_BY)].at(-1)?.[1]
      if (result !== undefined) {
        appendFileSync(this.config.log, `${JSON.stringify({ via, nested: result })}\n`)
        yield* reply({ type: 'text', text: 'done' })
        return
      }
      appendFileSync(this.config.log, `${JSON.stringify({ via, tools })}\n`)
      // A child that still sees `subagent` tries it once, so the run can show what
      // the host's depth limit does with the call.
      if (tools.includes('subagent')) {
        const args = JSON.stringify({ description: 'nested', prompt: 'Reply done.' })
        yield* reply({ type: 'tool-call', id: `nested-${String(via)}`, name: 'subagent', arguments: args })
        return
      }
      yield* reply({ type: 'text', text: 'done' })
      return
    }
    if (result !== undefined) appendFileSync(this.config.log, `${JSON.stringify({ result })}\n`)
    const called = options.messages
      .filter(message => message.role === 'assistant')
      .flatMap(message => message.content ?? [])
      .filter(block => block.type === 'tool-call')
      .map(block => block.name)
    const next = this.config.tools.find(tool => tools.includes(tool) && !called.includes(tool))
    const asked = this.config.marker === undefined || text.includes(this.config.marker)
    if (next === undefined || !asked) {
      yield* reply({ type: 'text', text: 'ready' })
      return
    }
    const args = JSON.stringify({ description: `acceptance ${next}`, prompt: `You were spawned by ${next}. Reply done.` })
    yield* reply({ type: 'tool-call', id: `acceptance-${next}`, name: next, arguments: args })
  }
}

/**
 * Register the scripted route.
 * @param ctx - the host context that owns the `llm` registry.
 * @param config - the record file, the delegation tools, and the optional marker.
 */
export function apply(ctx, config) {
  ctx.llm.registerAdapter(['scripted'], new ScriptedModel(config))
}
