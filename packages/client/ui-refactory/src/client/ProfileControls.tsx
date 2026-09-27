import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { RefactoryConsoleRemote, RefactoryProfile } from '@deepseek-ai/dsh-refactory-console/types'
import type { RefactoryKnobSetting, RefactorySettings, RefactorySettingName } from './store.ts'
import { searchBudget } from './store.ts'
import { FieldLabel } from './FieldHelp.tsx'
import css from './RefactoryBody.module.css'

const controlHelp: Record<string, string> = {
  "profile-select": "Load a saved, immutable version of search settings and its objective. Your edits become a new version only when you save.",
  "elite": "How many leading candidates remain available as parents for the next generation.",
  "attempts": "Maximum proposal attempts when a proposed candidate cannot be accepted.",
  "seed": "Seed for reproducible random choices, where the selected optimizer supports it.",
  "holdout": "Fraction of task data reserved for final winner validation rather than candidate ranking.",
  "parsimony": "Penalty per graph node, subtracted from validated score to prefer smaller solutions when scores are otherwise comparable.",
  "repeats": "How many trials measure each candidate and the winner. Repetition reports variability; it does not guarantee improvement.",
  "preset": "Choose the permitted families of candidate edits. Task definitions, scoring and validation remain fixed.",
  "frozen": "Comma-separated input names the search must keep unchanged. You can still set their initial values above.",
  "allowed_ops": "Comma-separated edit operations to allow. Leave empty to use the selected edit preset.",
  "name": "Name under which to save this search recipe. Saving appends an immutable version.",
  "objective": "Human-readable goal recorded in the search contract. Save a version to apply an objective change; it does not replace the mode’s score.",
  "artifacts": "Additional output files required by the contract, one relative path per line. Save a version to apply changes.",
  "backend": "Runtime used for coding workers. DSH is the default; optional backends require installation and their own authentication.",
  "provider": "Model provider override for DSH workers. Leave blank to inherit the runtime selection.",
  "model": "Model identifier used by workers. Leave blank to inherit the selected backend’s model.",
  "effort": "Reasoning effort override for DSH workers, such as a value supported by the selected model. Blank inherits the runtime selection.",
  "permission": "Permission policy for worker tools. Skip uses the existing approval/sandbox bypass defaults; a native policy retains the backend’s checks.",
  "max_elapsed_s": "Optional total elapsed-time budget for the run, in seconds. Blank means no added limit.",
  "max_tokens": "Optional shared token budget for DSH workers. Blank means no added limit. External backends do not support this control.",
  "validation_reserve_s": "Time kept for final validation within the total elapsed-time budget, in seconds. Zero adds no reserve.",
  "validation_reserve_tokens": "Tokens kept for final validation within the DSH token budget. Zero adds no reserve.",
  "role_routes": "JSON object mapping worker roles to routing overrides. More specific node overrides take precedence. An empty object inherits the run-wide route.",
  "node_routes": "JSON object mapping node IDs to routing overrides. These take precedence over role and run-wide routes. An empty object adds no overrides."
}

type ProfileApi = Pick<RefactoryConsoleRemote, 'profiles' | 'profileSave' | 'profileResolve' | 'comparison'>

/** One draft, used for saving and previewing the same settings the start gesture sends. */
export function searchSettings(settings: RefactorySettings, knobs: readonly RefactoryKnobSetting[]): Record<string, unknown> {
  const extra = JSON.parse(settings.configuration || '{}') as Record<string, unknown>
  return { ...extra, ...searchBudget(settings),
    ...settings.strategy ? { strategy: settings.strategy } : {},
    ...settings.optimizer ? { optimizer: settings.optimizer } : {},
    ...settings.evaluator ? { evaluator: settings.evaluator } : {},
    knobs: Object.fromEntries(knobs.map(knob => [knob.name, knob.value])),
  }
}

const NUMBERS = [
  ['elite', 'Retained parents', 3], ['attempts', 'Proposal attempts', 3],
  ['seed', 'Random seed', 0], ['holdout', 'Holdout fraction', 0.3],
  ['parsimony', 'Size penalty per node', 0.01], ['repeats', 'Trials per candidate and winner audit (1–10)', 1],
] as const

/** Saved recipes and the remaining search controls; scoring remains owned by the mode. */
export function ProfileControls({ api, mode, settings, knobs, knobDefaults = [], onField, onKnob, signal }: {
  readonly api: ProfileApi
  readonly mode: string
  readonly settings: RefactorySettings
  readonly knobs: readonly RefactoryKnobSetting[]
  readonly knobDefaults?: readonly RefactoryKnobSetting[]
  readonly onField: (name: RefactorySettingName, value: string) => void
  readonly onKnob: (name: string, value: string) => void
  readonly signal: AbortSignal
}): ReactNode {
  const [profiles, setProfiles] = useState<readonly RefactoryProfile[]>([])
  const [name, setName] = useState('my-search')
  const [objective, setObjective] = useState("Maximize the mode's immutable score")
  const [required, setRequired] = useState('')
  const [message, setMessage] = useState('')
  const [resolved, setResolved] = useState<{ request: string; value: Record<string, unknown> } | null>(null)
  const [busy, setBusy] = useState(false)
  const additional = JSON.parse(settings.configuration || '{}') as Record<string, unknown>
  const previewRequest = { mode, settings: { settings_json: JSON.stringify(searchSettings(settings, knobs)),
    ...settings.profile ? { profile: settings.profile } : {}, ...settings.profile_version ? { profile_version: Number(settings.profile_version) } : {} } }
  const previewIdentity = JSON.stringify(previewRequest)
  useEffect(() => {
    if (!api.profiles) return
    void api.profiles(signal).then(answer => {
      if (signal.aborted) return
      if (answer.ok) setProfiles(answer.value)
      else setMessage(answer.error.message)
    })
  }, [api, signal])
  const change = (key: string, value: unknown): void => {
    onField('configuration', JSON.stringify({ ...additional, [key]: value }))
    setResolved(null)
  }
  const apply = (profile: RefactoryProfile): void => {
    setName(profile.name)
    setObjective(profile.contract.objective)
    setRequired(profile.contract.required_artifacts.join('\n'))
    onField('profile', profile.name)
    onField('profile_version', String(profile.version))
    onField('configuration', JSON.stringify(profile.settings))
    for (const key of ['strategy', 'optimizer', 'evaluator', 'epochs', 'batch_size', 'max_evaluations'] as const) {
      onField(key, String(profile.settings[key] ?? ''))
    }
    const starting = profile.settings.knobs
    for (const knob of knobDefaults) onKnob(knob.name, knob.value)
    if (starting && typeof starting === 'object') {
      for (const [key, value] of Object.entries(starting)) onKnob(key, String(value))
    }
    setResolved(null)
  }
  return <details className={css.disclosure} data-refactory-profiles>
    <summary className={css.heading}>Saved profiles and advanced search</summary>
    <FieldLabel htmlFor="refactory-profile-select" help={controlHelp['profile-select']!}>Saved profile version</FieldLabel>
    <select id="refactory-profile-select" className={css.control} value={`${settings.profile || ''}:${settings.profile_version || ''}`}
      onChange={event => {
        const profile = profiles.find(p => `${p.name}:${p.version}` === event.target.value)
        if (profile) apply(profile)
        else { onField('profile', ''); onField('profile_version', ''); setResolved(null) }
      }}>
      <option value=":">Unsaved settings</option>
      {profiles.map(profile => <option key={`${profile.name}:${profile.version}`} value={`${profile.name}:${profile.version}`}>{profile.name} · version {profile.version}</option>)}
    </select>
    {NUMBERS.map(([key, label, fallback]) => <div className={css.field} key={key}>
      <FieldLabel htmlFor={`refactory-profile-${key}`} help={controlHelp[key]!}>{label}</FieldLabel>
      <input className={css.input} id={`refactory-profile-${key}`} type="number" step="any"
        value={String(additional[key] ?? fallback ?? '')} placeholder={fallback === null ? 'Unlimited' : undefined}
        onChange={event => { change(key, event.target.value === '' ? null : Number(event.target.value)) }} />
    </div>)}
    <FieldLabel htmlFor="refactory-preset" help={controlHelp['preset']!}>Permitted edit preset</FieldLabel>
    <select className={css.control} id="refactory-preset" value={String(additional.edit_preset ?? 'broad')} onChange={event => { change('edit_preset', event.target.value) }}>
      {['broad', 'conservative', 'knob_only', 'structure'].map(value => <option key={value}>{value}</option>)}
    </select>
    {(['frozen', 'allowed_ops'] as const).map(key => <div className={css.field} key={key}>
      <FieldLabel htmlFor={`refactory-profile-${key}`} help={controlHelp[key]!}>{key === 'frozen' ? 'Frozen knobs (comma separated)' : 'Allowed operations (comma separated; empty uses preset)'}</FieldLabel>
      <input className={css.input} id={`refactory-profile-${key}`} value={Array.isArray(additional[key]) ? additional[key].join(', ') : ''}
        onChange={event => { change(key, event.target.value.split(',').map(s => s.trim()).filter(Boolean)) }} />
    </div>)}
    <p className={css.note}>Task definitions, scoring and invariants stay fixed. Ranking uses mean validated score minus size penalty. Cost and latency are reported; missing measurements confer no advantage. Repeated trials report observed variability, not proof of improvement.</p>
    <FieldLabel htmlFor="refactory-profile-name" help={controlHelp['name']!}>Profile name</FieldLabel>
    <input className={css.input} id="refactory-profile-name" value={name} onChange={event => { setName(event.target.value) }} />
    <FieldLabel htmlFor="refactory-profile-objective" help={controlHelp['objective']!}>Search objective</FieldLabel>
    <textarea className={css.input} id="refactory-profile-objective" value={objective} onChange={event => { setObjective(event.target.value) }} />
    <FieldLabel htmlFor="refactory-profile-artifacts" help={controlHelp['artifacts']!}>Additional required artifacts (one relative path per line)</FieldLabel>
    <textarea className={css.input} id="refactory-profile-artifacts" value={required} onChange={event => { setRequired(event.target.value) }} />
    <p className={css.note}>Save a version to apply changes to the objective or required artifacts. Every launch records a separate exact snapshot.</p>
    <div className={css.gestures}>
      <button className={css.action} disabled={busy || !api.profileSave} onClick={() => {
        if (!api.profileSave) return
        setBusy(true)
        void api.profileSave(name, { settings: searchSettings(settings, knobs), contract: { objective, required_artifacts: required.split('\n').map(s => s.trim()).filter(Boolean) } }, signal)
          .then(answer => { if (signal.aborted) return; if (answer.ok) { setProfiles([...profiles, answer.value]); apply(answer.value); setMessage(`Saved ${answer.value.name} version ${answer.value.version}`) } else setMessage(answer.error.message) })
          .finally(() => { setBusy(false) })
      }}>Save new version</button>
      <button className={css.action} disabled={busy || !api.profileResolve} onClick={() => {
        if (!api.profileResolve) return
        setBusy(true)
        void api.profileResolve(previewRequest, signal)
          .then(answer => { if (signal.aborted) return; if (answer.ok) { setResolved({ request: previewIdentity, value: answer.value }); setMessage('Effective settings and contract resolved') } else setMessage(answer.error.message) })
          .finally(() => { setBusy(false) })
      }}>Preview launch contract</button>
    </div>
    {message && <p role="status" className={css.note}>{message === 'Effective settings and contract resolved' && resolved?.request !== previewIdentity ? 'Settings changed; preview the current launch contract.' : message}</p>}
    {resolved?.request === previewIdentity && <pre data-refactory-resolved-profile>{JSON.stringify(resolved.value, null, 2)}</pre>}
  </details>
}

/** All outcomes are inspectable, including states that cannot win. */
export function CandidateComparison({ api, run, signal }: { readonly api: ProfileApi; readonly run: string; readonly signal: AbortSignal }): ReactNode {
  const [answer, setAnswer] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')
  return <details data-refactory-comparison>
    <summary className={css.heading}>Candidate changes, measurements and validation</summary>
    <button className={css.action} onClick={() => {
      if (!api.comparison) return
      void api.comparison(run, signal).then(result => {
        if (signal.aborted) return
        if (result.ok) { setAnswer(result.value); setError('') } else setError(result.error.message)
      })
    }}>Compare all candidates</button>
    {error && <p role="alert">{error}</p>}
    {answer && <>
      <p className={css.note}>{String(answer.interpretation || '')}</p>
      {Array.isArray(answer.candidates) && answer.candidates.map((candidate: Record<string, unknown>, index) => <details key={String(candidate.id ?? candidate.candidate ?? `proposal-${index + 1}`)}>
        <summary>{String(candidate.id ?? candidate.candidate ?? `Proposal ${index + 1}`)} · {String(candidate.status)} · raw {String(candidate.raw_score ?? 'unmeasured')} · adjusted {String(candidate.score ?? 'unmeasured')} · {String(candidate.duration_s ?? 'unavailable')} s · tokens {String(candidate.tokens ?? 'unavailable')} · cost {String(candidate.cost ?? 'unavailable')}</summary>
        <p>Parent: {String(candidate.parent ?? 'seed')}{candidate.status === 'unmeasured' ? ' · No evaluation was recorded.' : ''}</p>
        <pre>{JSON.stringify(candidate, null, 2)}</pre>
      </details>)}
      <p className={css.label}>Held-out winner validation</p><pre>{JSON.stringify(answer.holdout, null, 2)}</pre>
      <details><summary>Exact run configuration and contract</summary><pre>{JSON.stringify(answer.resolved_profile, null, 2)}</pre></details>
    </>}
  </details>
}

/** Capability-aware routing and resources shared by search and Run once. */
export function RuntimeControls({ configuration, onChange }: { readonly configuration: string; readonly onChange: (value: string) => void }): ReactNode {
  const value = JSON.parse(configuration) as Record<string, unknown>
  const backend = String(value.worker_backend || 'dsh')
  const [routeError, setRouteError] = useState('')
  const change = (key: string, next: unknown): void => { onChange(JSON.stringify({ ...value, [key]: next })) }
  const permissions = backend === 'codex' ? ['skip', 'default', 'never', 'approve-for-me', 'dangerously-bypass-approvals-and-sandbox'] : backend === 'claude_code' ? ['skip', 'default', 'dontAsk', 'acceptEdits', 'auto', 'plan', 'bypassPermissions'] : ['skip', 'default']
  return <details className={css.disclosure} data-refactory-runtime-controls>
    <summary className={css.heading}>Worker routing and shared resources</summary>
    <FieldLabel htmlFor="refactory-worker-backend" help={controlHelp['backend']!}>Coding worker backend</FieldLabel>
    <select className={css.control} id="refactory-worker-backend" value={backend} onChange={event => { onChange(JSON.stringify({ ...value, worker_backend: event.target.value, provider: '', model: '', effort: '', permission_mode: value.permission_mode === 'default' ? 'default' : 'skip', max_tokens: null, validation_reserve_tokens: 0 })) }}>
      <option value="dsh">DSH (default)</option><option value="codex">Codex (opt in)</option><option value="claude_code">Claude Code (opt in)</option>
    </select>
    {(['provider', 'model', 'effort'] as const).map(key => <div className={css.field} key={key}>
      <FieldLabel htmlFor={`refactory-profile-${key}`} help={controlHelp[key]!}>{key}</FieldLabel>
      <input className={css.input} id={`refactory-profile-${key}`} value={String(value[key] ?? '')} disabled={backend !== 'dsh' && key !== 'model'} placeholder="Inherit runtime selection"
        onChange={event => { change(key, event.target.value) }} />
    </div>)}
    <FieldLabel htmlFor="refactory-permission-mode" help={controlHelp['permission']!}>Worker permission mode</FieldLabel>
    <select className={css.control} id="refactory-permission-mode" value={String(value.permission_mode || 'skip')} onChange={event => { change('permission_mode', event.target.value) }}>
      {permissions.map(permission => <option key={permission} value={permission}>{permission === 'skip' ? 'Skip permission checks (default)' : permission === 'default' ? 'Native permission policy' : permission}</option>)}
    </select>
    <p className={css.note}>Skip mode lets worker tools run without permission approval. Codex and DSH also run without their own sandbox restrictions. Select a native policy to retain those checks.</p>
    {(['max_elapsed_s', 'max_tokens', 'validation_reserve_s', 'validation_reserve_tokens'] as const).map(key => <div className={css.field} key={key}>
      <FieldLabel htmlFor={`refactory-profile-${key}`} help={controlHelp[key]!}>{({ max_elapsed_s: 'Total elapsed seconds', max_tokens: 'Total tokens', validation_reserve_s: 'Seconds reserved for final validation', validation_reserve_tokens: 'Tokens reserved for final validation' })[key]}</FieldLabel>
      <input className={css.input} id={`refactory-profile-${key}`} type="number" step="any" value={String(value[key] ?? '')} placeholder={key.startsWith('validation') ? '0' : 'Unlimited'}
        disabled={backend !== 'dsh' && key.includes('tokens')} onChange={event => { change(key, event.target.value === '' ? null : Number(event.target.value)) }} />
    </div>)}
    {backend !== 'dsh' && <p className={css.note}>This backend uses native provider settings. Provider/effort overrides and token limits are unsupported; an installed provider and native authentication are required. Steering applies at the next node boundary.</p>}
    {(['role_routes', 'node_routes'] as const).map(key => <div className={css.field} key={key}>
      <FieldLabel htmlFor={`refactory-profile-${key}`} help={controlHelp[key]!}>{key === 'role_routes' ? 'Role route overrides (JSON object)' : 'Node route overrides (JSON object)'}</FieldLabel>
      <textarea className={css.input} id={`refactory-profile-${key}`} defaultValue={JSON.stringify(value[key] || {})} key={`${backend}-${key}-${JSON.stringify(value[key])}`} onBlur={event => {
        try { const parsed: unknown = JSON.parse(event.target.value); if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Enter a JSON object'); change(key, parsed); setRouteError('') }
        catch (error) { setRouteError(error instanceof Error ? error.message : String(error)) }
      }} />
    </div>)}
    {routeError && <p role="alert">{routeError}</p>}
  </details>
}
