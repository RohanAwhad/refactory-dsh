/**
 * The connection-backed console face: every gesture reaches the API channel as
 * the `refactoryConsole/<method>` endpoint, with the arguments named the way the
 * gateway's SRC binding reads them.
 *
 * The pinned Client Remote assembly is a prebuilt bundle carrying the harness's
 * own namespaces, so it mounts no `remote.refactoryConsole` service for this
 * fork's console. Binding the face to `connection.rpc.call` is what the picker
 * does instead, and a wrong argument name would not fail until a person pressed a
 * control in a browser, which is how the pending-plugin boot error shipped once.
 */
import { describe, expect, it, vi } from 'vitest'
import type { ClientConnectionRpc } from '@deepseek-ai/dsh-client-connection/client'
import type { ConnectionRpcResult } from '@deepseek-ai/dsh-client-connection/client'
import { refactoryConsoleOverConnection } from '../src/client/face.ts'

interface Call {
  readonly channel: string
  readonly endpoint: string
  readonly payload: unknown
  readonly signal: AbortSignal | undefined
}

/** A caller that answers every endpoint with the same value and records the calls. */
function recorder(): { rpc: ClientConnectionRpc; calls: Call[] } {
  const calls: Call[] = []
  const rpc = {
    call: vi.fn(async (channel: string, endpoint: string, payload: unknown, signal?: AbortSignal) => {
      calls.push({ channel, endpoint, payload, signal })
      return { ok: true, value: undefined } as ConnectionRpcResult<unknown>
    }),
  }
  return { rpc: rpc as unknown as ClientConnectionRpc, calls }
}

describe('refactoryConsoleOverConnection', () => {
  it('sends each read to its own endpoint with an empty argument object', async () => {
    const { rpc, calls } = recorder()
    const console = refactoryConsoleOverConnection(rpc).refactoryConsole
    await console.modes(undefined)
    await console.strategies(undefined)
    await console.packages(undefined)
    expect(calls.map((call) => call.endpoint)).toEqual([
      'refactoryConsole/modes',
      'refactoryConsole/strategies',
      'refactoryConsole/packages',
    ])
    for (const call of calls) {
      expect(call.channel).toBe('/api')
      expect(call.payload).toEqual({ args: {} })
    }
  })

  it('names every parameter the gateway binds and carries the signal on the transport', async () => {
    const { rpc, calls } = recorder()
    const console = refactoryConsoleOverConnection(rpc).refactoryConsole
    const controller = new AbortController()
    await console.status('run-1', 3, controller.signal)
    await console.stop('run-1', controller.signal)
    await console.start({ mode: 'counting_pack/fixed_step' }, controller.signal)
    await console.run({ mode: 'counting_pack/fixed_step' }, controller.signal)
    await console.setPackageEnabled('counting_pack', true, controller.signal)
    await console.removePackage('counting_pack', controller.signal)
    await console.importPackage('/somewhere', 'as_name', controller.signal)
    expect(calls.map((call) => [call.endpoint, call.payload, call.signal === controller.signal])).toEqual([
      ['refactoryConsole/status', { args: { run: 'run-1', after: 3 } }, true],
      ['refactoryConsole/stop', { args: { run: 'run-1' } }, true],
      ['refactoryConsole/start', { args: { request: { mode: 'counting_pack/fixed_step' } } }, true],
      ['refactoryConsole/run', { args: { request: { mode: 'counting_pack/fixed_step' } } }, true],
      ['refactoryConsole/setPackageEnabled', { args: { name: 'counting_pack', enabled: true } }, true],
      ['refactoryConsole/removePackage', { args: { name: 'counting_pack' } }, true],
      ['refactoryConsole/importPackage', { args: { source: '/somewhere', name: 'as_name' } }, true],
    ])
  })

  it('returns the failure the endpoint answered with, rather than rejecting', async () => {
    const failure: ConnectionRpcResult<unknown> = {
      ok: false,
      error: { code: 'gateway/internal', message: 'boom', details: {} },
    }
    const rpc = { call: vi.fn(async () => failure) } as unknown as ClientConnectionRpc
    const answer = await refactoryConsoleOverConnection(rpc).refactoryConsole.modes(undefined)
    expect(answer).toEqual(failure)
  })
})
