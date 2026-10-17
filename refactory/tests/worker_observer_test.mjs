import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import assert from 'node:assert/strict'
import { apply } from '../refactory_dsh/worker_observer.mjs'

const root = mkdtempSync(join(tmpdir(), 'refactory-observer-'))
try {
  const directory = join(root, '.refactory', 'workers')
  mkdirSync(directory, { recursive: true })
  const path = join(directory, 'one.json')
  writeFileSync(path, JSON.stringify({ requested_route: { worker_backend: 'dsh' }, worker_id: 'one', run_id: root,
    usage: { status: 'unavailable', tokens: null } }))
  process.env.REFACTORY_WORKER_RECEIPT = path
  const events = new Map()
  let providers = ['spawn', 'fork']
  apply({ on: (name, callback) => events.set(name, callback), subagents: { list: () => providers } })
  const agent = { session: { id: 'dsh-parent' } }
  const request = events.get('agent/request')
  const stream = events.get('agent/assistant-stream')
  await request({ agent }, async () => ({ provider: 'dsh-p', model: 'dsh-model' }))
  stream({ agent, frame: { type: 'start' } })
  stream({ agent, frame: { type: 'chunk', chunk: { type: 'usage', usage: { totalTokens: 11 } } } })
  stream({ agent, frame: { type: 'end' } })
  events.get('refactory/worker-metadata')('dsh-parent', { actual_route: { model: 'external-model' }, usage: { tokens: 90, status: 'measured' } })
  events.get('subagent/start')({ id: 'external', provider: 'codex', local: false })
  stream({ agent, frame: { type: 'start' } })
  stream({ agent, frame: { type: 'chunk', chunk: { type: 'usage', usage: { totalTokens: 13 } } } })
  stream({ agent, frame: { type: 'end' } })
  const receipt = JSON.parse(readFileSync(path))
  assert.equal(receipt.actual_route.model, 'dsh-model')
  assert.equal(receipt.usage.status, 'unavailable')
  assert.equal(receipt.usage.tokens, null)
  assert.equal(receipt.usage.known_tokens, 24)
  assert.equal(receipt.native_observations[0].actual_route.model, 'external-model')

  const ledger = join(root, 'budget.json')
  writeFileSync(ledger, JSON.stringify({ limits: { max_tokens: 1000 } }))
  process.env.REFACTORY_BUDGET_LEDGER = ledger
  providers = ['spawn', 'fork', 'external']
  let called = false
  await assert.rejects(request({ agent }, async () => { called = true }), /unmetered providers/)
  assert.equal(called, false)
  providers = ['spawn', 'fork']
  await request({ agent }, async () => ({ provider: 'dsh-p', model: 'dsh-model' }))
  // A miscomposed external worker must fail before entering DSH transport.
  writeFileSync(path, JSON.stringify({ requested_route: { worker_backend: 'codex' }, worker_id: 'external', run_id: root,
    usage: { status: 'unavailable', tokens: null } }))
  apply({ on: (name, callback) => events.set(name, callback), subagents: { list: () => [] } })
  let fallback = false
  await assert.rejects(events.get('agent/request')({ agent }, async () => { fallback = true }), /cannot dispatch a DSH model request/)
  assert.equal(fallback, false)
  assert.equal(JSON.parse(readFileSync(path)).runtime.backend, null)
  assert.equal(JSON.parse(readFileSync(path)).runtime.version, null)
  console.log('worker observer accounting and capability boundaries passed')

  // --- A2: tokens_delta on usage events ---
  {
    const deltaRoot = mkdtempSync(join(tmpdir(), 'refactory-delta-'))
    try {
      const deltaDir = join(deltaRoot, '.refactory', 'workers')
      mkdirSync(deltaDir, { recursive: true })
      const deltaPath = join(deltaDir, 'delta.json')
      writeFileSync(deltaPath, JSON.stringify({
        requested_route: { worker_backend: 'dsh' },
        worker_id: 'delta', run_id: deltaRoot, node_id: 'test_node',
        usage: { status: 'unavailable', tokens: null }
      }))
      const savedReceipt = process.env.REFACTORY_WORKER_RECEIPT
      process.env.REFACTORY_WORKER_RECEIPT = deltaPath
      const deltaEvents = new Map()
      apply({
        on: (name, callback) => deltaEvents.set(name, callback),
        subagents: { list: () => ['spawn', 'fork'] }
      })
      const deltaAgent = { session: { id: 'delta-session' } }
      const deltaRequest = deltaEvents.get('agent/request')
      const deltaStream = deltaEvents.get('agent/assistant-stream')

      // First completion: 11 tokens
      await deltaRequest({ agent: deltaAgent }, async () => ({ provider: 'p', model: 'm' }))
      deltaStream({ agent: deltaAgent, frame: { type: 'start' } })
      deltaStream({ agent: deltaAgent, frame: { type: 'chunk', chunk: { type: 'usage', usage: { totalTokens: 11 } } } })
      deltaStream({ agent: deltaAgent, frame: { type: 'end' } })

      // Second completion: 13 tokens
      await deltaRequest({ agent: deltaAgent }, async () => ({ provider: 'p', model: 'm' }))
      deltaStream({ agent: deltaAgent, frame: { type: 'start' } })
      deltaStream({ agent: deltaAgent, frame: { type: 'chunk', chunk: { type: 'usage', usage: { totalTokens: 13 } } } })
      deltaStream({ agent: deltaAgent, frame: { type: 'end' } })

      // Parse events.jsonl for usage events
      const eventsContent = readFileSync(join(deltaRoot, '.refactory', 'events.jsonl'), 'utf8')
      const usageEvents = eventsContent.trim().split('\n')
        .map(line => JSON.parse(line))
        .filter(e => e.kind === 'worker' && e.state === 'usage')

      assert.equal(usageEvents.length, 2, 'should have 2 usage events')
      // First event: tokens_delta=11, tokens=11
      assert.equal(usageEvents[0].usage.tokens_delta, 11)
      assert.equal(usageEvents[0].usage.tokens, 11)
      // Second event: tokens_delta=13, tokens=24
      assert.equal(usageEvents[1].usage.tokens_delta, 13)
      assert.equal(usageEvents[1].usage.tokens, 24)
      // Sum of deltas equals final tokens
      const deltaSum = usageEvents.reduce((sum, e) => sum + e.usage.tokens_delta, 0)
      assert.equal(deltaSum, usageEvents[usageEvents.length - 1].usage.tokens)

      process.env.REFACTORY_WORKER_RECEIPT = savedReceipt
      console.log('tokens_delta accounting passed')
    } finally {
      rmSync(deltaRoot, { recursive: true, force: true })
    }
  }
} finally {
  rmSync(root, { recursive: true, force: true })
}
