// @vitest-environment jsdom
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RefactoryProfile, RefactoryStartRequest } from '@deepseek-ai/dsh-refactory-console/types'
import { CandidateComparison, ProfileControls, RuntimeControls } from '../src/client/ProfileControls.tsx'
import { runtimeSettings } from '../src/client/face.ts'
import type { RefactorySettings } from '../src/client/store.ts'

afterEach(cleanup)

describe('profile and worker controls', () => {
  it('selects an immutable version, saves an explicit objective change and previews the same launch settings', async () => {
    const profile: RefactoryProfile = { schema_version: 1, name: 'small', version: 1,
      settings: { optimizer: 'random', strategy: 'default', evaluator: 'walker', repeats: 2, epochs: 1, batch_size: 2, max_evaluations: 2 },
      contract: { objective: 'original', required_artifacts: [] } }
    const save = vi.fn(async (_name, spec) => ({ ok: true as const, value: { ...profile, ...spec, version: 2 } }))
    const preview = vi.fn(async (_request: RefactoryStartRequest) => ({ ok: true as const, value: { contract: 'resolved' } }))
    const api = { profiles: vi.fn(async () => ({ ok: true as const, value: [profile] })), profileSave: save, profileResolve: preview }
    const signal = new AbortController().signal
    function Harness() {
      const [settings, setSettings] = useState<RefactorySettings>({ strategy: '', optimizer: '', evaluator: '' })
      return <ProfileControls api={api} mode="circle_packing/adaptive" settings={settings} knobs={[]} signal={signal}
        onField={(name, value) => { setSettings(current => ({ ...current, [name]: value })) }} onKnob={() => {}} />
    }
    render(<Harness />)
    fireEvent.click(screen.getByText('Saved profiles and advanced search'))
    await screen.findByRole('option', { name: 'small · version 1' })
    fireEvent.change(screen.getByLabelText('Saved profile version'), { target: { value: 'small:1' } })
    expect((screen.getByLabelText('Trials per candidate and winner audit (1–10)') as HTMLInputElement).value).toBe('2')
    fireEvent.change(screen.getByLabelText('Search objective'), { target: { value: 'explicitly changed' } })
    fireEvent.click(screen.getByText('Save new version'))
    await waitFor(() => { expect(save).toHaveBeenCalledOnce() })
    expect(save.mock.calls[0]?.[1]).toMatchObject({ settings: { repeats: 2, max_evaluations: 2 }, contract: { objective: 'explicitly changed' } })
    await screen.findByRole('option', { name: 'small · version 2' })
    fireEvent.click(screen.getByText('Preview launch contract'))
    await waitFor(() => { expect(preview).toHaveBeenCalledOnce() })
    expect(preview.mock.calls[0]?.[0]).toMatchObject({ mode: 'circle_packing/adaptive', settings: { profile: 'small', profile_version: 2 } })
  })

  it('hides an old contract preview when the selected mode or external controls change', async () => {
    const preview = vi.fn(async (_request: RefactoryStartRequest) => ({ ok: true as const, value: { marker: 'Exact launch snapshot' } }))
    const signal = new AbortController().signal
    const fields = { strategy: '', optimizer: '', evaluator: '', max_evaluations: '2' }
    const props = { api: { profileResolve: preview }, knobs: [], signal, onField: () => {}, onKnob: () => {} }
    const view = render(<ProfileControls {...props} mode="pack/first" settings={fields} />)
    fireEvent.click(screen.getByText('Preview launch contract'))
    await screen.findByText(/Exact launch snapshot/)
    view.rerender(<ProfileControls {...props} mode="pack/first" settings={{ ...fields, max_evaluations: '3' }} />)
    expect(screen.queryByText(/Exact launch snapshot/)).toBeNull()
    fireEvent.click(screen.getByText('Preview launch contract'))
    await screen.findByText(/Exact launch snapshot/)
    view.rerender(<ProfileControls {...props} mode="pack/second" settings={{ ...fields, max_evaluations: '3' }} />)
    expect(screen.queryByText(/Exact launch snapshot/)).toBeNull()
  })

  it('names unmeasured proposals from their recorded candidate identity', async () => {
    const api = { comparison: vi.fn(async () => ({ ok: true as const, value: { candidates: [
      { candidate: 'unmeasured-id', status: 'unmeasured', parent: 'parent-id' },
    ] } })) }
    render(<CandidateComparison api={api} run="/saved/run" signal={new AbortController().signal} />)
    fireEvent.click(screen.getByText('Compare all candidates'))
    await screen.findByText(/unmeasured-id · unmeasured/)
    expect(screen.getByText('Parent: parent-id · No evaluation was recorded.')).toBeDefined()
    expect(screen.queryByText(/undefined/)).toBeNull()
  })

  it('switching external backend clears unsupported inherited routing', () => {
    const changed = vi.fn()
    render(<RuntimeControls configuration={JSON.stringify({ worker_backend: 'dsh', provider: 'openai', model: 'old', effort: 'high' })} onChange={changed} />)
    fireEvent.change(screen.getByLabelText('Coding worker backend'), { target: { value: 'codex' } })
    expect(JSON.parse(changed.mock.calls[0]?.[0] as string)).toMatchObject({ worker_backend: 'codex', provider: '', model: '', effort: '', permission_mode: 'skip' })
  })

  it('inherits project permissions while preserving an explicit native permission policy', () => {
    const changed = vi.fn()
    const view = render(<RuntimeControls configuration='{"worker_backend":"codex"}' onChange={changed} />)
    expect((screen.getByLabelText('Worker permission mode') as HTMLSelectElement).value).toBe('')
    view.rerender(<RuntimeControls configuration='{"worker_backend":"claude_code","permission_mode":"default"}' onChange={changed} />)
    expect((screen.getByLabelText('Worker permission mode') as HTMLSelectElement).value).toBe('default')
    fireEvent.change(screen.getByLabelText('Worker permission mode'), { target: { value: 'skip' } })
    expect(JSON.parse(changed.mock.calls[0]?.[0] as string).permission_mode).toBe('skip')
  })

  it.each([['default', 'default'], ['skip', 'skip'], ['acceptEdits', 'skip']])(
    'backend switches preserve shared policy %s and resolve to %s', (permission, expected) => {
      const changed = vi.fn()
      render(<RuntimeControls configuration={JSON.stringify({ worker_backend: 'claude_code', permission_mode: permission })} onChange={changed} />)
      fireEvent.change(screen.getByLabelText('Coding worker backend'), { target: { value: 'codex' } })
      expect(JSON.parse(changed.mock.calls[0]?.[0] as string).permission_mode).toBe(expected)
    })

  it('Run once carries route/resource settings and JSON overrides without search-only settings', () => {
    expect(runtimeSettings(JSON.stringify({ repeats: 3, optimizer: 'random', worker_backend: 'codex', model: 'selected', max_elapsed_s: 40, max_tokens: null, node_routes: { repair: { worker_backend: 'dsh' } } })))
      .toEqual({ worker_backend: 'codex', model: 'selected', max_elapsed_s: 40, node_routes: '{"repair":{"worker_backend":"dsh"}}' })
  })

  it('keeps untouched project routes inherited and allows explicit clearing and restoration', () => {
    const changed = vi.fn()
    const view = render(<RuntimeControls configuration="{}" onChange={changed} />)
    const field = screen.getByLabelText('Node route overrides (JSON object)')
    fireEvent.blur(field)
    expect(changed).not.toHaveBeenCalled()
    expect((screen.getByLabelText('Coding worker backend') as HTMLSelectElement).value).toBe('')
    fireEvent.change(field, { target: { value: '{}' } })
    fireEvent.blur(field)
    expect(JSON.parse(changed.mock.calls[0]![0])).toEqual({ node_routes: {} })
    view.rerender(<RuntimeControls configuration='{"node_routes":{}}' onChange={changed} />)
    fireEvent.change(screen.getByLabelText('Node route overrides (JSON object)'), { target: { value: '' } })
    fireEvent.blur(screen.getByLabelText('Node route overrides (JSON object)'))
    expect(JSON.parse(changed.mock.calls[1]![0])).toEqual({})
  })

})
