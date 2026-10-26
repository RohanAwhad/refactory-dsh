/** One node delegated through the installed DSH named-provider registry. */
import { randomUUID } from 'node:crypto'

export const name = 'refactory-worker-bridge'
export const inject = ['agents', 'subagents', 'agentDefaultModel', 'headlessStartup']
export function apply(ctx) {
  const controller = new AbortController()
  let owned
  ctx.on('dispose', async () => {
    controller.abort()
    await owned?.dispose()
  })
  void (async () => {
    await ctx.get('loader')?.await()
    const startup = ctx.get('headlessStartup')
    const selection = ctx.get('agentDefaultModel').currentSelection()
    const provider = startup.provider || selection.provider
    const model = startup.model || selection.model
    if (!provider && !model) {
      process.stderr.write('dsh: No model chosen: pick one in Settings → Models or with `refactory model set --provider <provider> --model <model>`.\n')
      ctx.get('appExit')(1)
      return
    }
    const handle = await ctx.get('agents').create({
      sessionId: `refactory-parent-${randomUUID()}`,
      meta: { cwd: process.cwd() },
      agentOptions: { provider, model },
    })
    try {
      owned = await ctx.get('subagents').start('refactory-worker', {
        parent: handle.agent,
        prompt: [{ type: 'text', text: ctx.get('headlessStartup').task }],
        signal: controller.signal,
      })
      const result = await owned.result
      process.stdout.write(result.output.filter(block => block.type === 'text').map(block => block.text).join('') + '\n')
      if (result.stopReason !== 'completed') {
        process.stderr.write(`dsh: worker ${result.stopReason}: ${result.diagnostic ?? 'no diagnostic'}\n`)
      }
      await owned.dispose()
      owned = undefined
      await handle.dispose()
      ctx.get('appExit')(result.stopReason === 'completed' ? 0 : 1)
    } catch (error) {
      await owned?.dispose()
      owned = undefined
      await handle.dispose()
      throw error
    }
  })().catch(error => {
    // Provider diagnostics already exclude raw protocol/credential material.
    process.stderr.write(`dsh: worker failed: ${error instanceof Error ? error.message : String(error)}\n`)
    ctx.get('appExit')(1)
  })
}
