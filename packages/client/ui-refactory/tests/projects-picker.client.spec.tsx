// @vitest-environment jsdom
/**
 * The target picker: the registered projects appear in the GUI, and picking one
 * sets the `target` knob in the start request (#351).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, waitFor } from '@testing-library/react'
import {
  AUTHORING, MODE, NO_STRATEGIES, PACKAGE, PROJECT, STRATEGIES, ok,
} from './fake-console.client.ts'
import { TAB, mountBody } from './mount.client.tsx'

const CHOICES = {
  project: '/repo',
  modes: [MODE, AUTHORING],
  strategies: STRATEGIES,
  packages: [PACKAGE],
  projects: [PROJECT],
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function at(view: { container: HTMLElement }, attribute: string, value?: string): HTMLElement {
  const selector = value === undefined ? `[${attribute}]` : `[${attribute}="${value}"]`
  const found = view.container.querySelector(selector)
  if (found === null) throw new Error(`no element for ${selector}`)
  return found as HTMLElement
}

describe('the target picker', () => {
  it('lists the registered project in the target select', () => {
    const { view } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      chosen: MODE.id,
    })
    const select = at(view, 'data-refactory-target') as HTMLSelectElement
    expect(select).toBeDefined()
    // The project option is present.
    const options = Array.from(select.options).map(o => o.textContent)
    expect(options.some(text => text!.includes('weather-cli'))).toBe(true)
  })

  it('picking a project sets the target knob in the store', () => {
    const { view, instance } = mountBody({
      choices: { kind: 'ready', value: CHOICES },
      chosen: MODE.id,
    })
    const select = at(view, 'data-refactory-target') as HTMLSelectElement
    fireEvent.change(select, { target: { value: '/projects/weather-cli' } })
    const state = instance.getSnapshot().byTab[TAB]
    expect(state?.knobs?.target).toBe('/projects/weather-cli')
  })
})
