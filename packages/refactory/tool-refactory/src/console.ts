/**
 * The console process boundary. One call per console verb: build argv, run
 * `python -m refactory_dsh.console`, and return the JSON it printed.
 *
 * The loop is Python and stays there. This is the only place the tool layer
 * touches it, so the tool and the client card both read one JSON contract —
 * `refactory_dsh/console.py` — and neither reimplements the loop's file layout.
 * @module @deepseek-ai/dsh-tool-refactory/console
 */

import { join, resolve } from 'node:path'
import type { SubprocessHandle } from '@deepseek-ai/dsh-subprocess'
import type { SubprocessRuntime } from '@deepseek-ai/dsh-subprocess'

/** How one console invocation is addressed. */
export interface ConsoleInvocation {
  /** Console verb, e.g. `status`. */
  verb: string
  /** Positional arguments after the verb, e.g. a mode id. */
  positional?: readonly string[]
  /** Long options, already spelled without the leading `--`. */
  options?: Readonly<Record<string, string | number | boolean | undefined>>
}

/** Resolved deployment settings for reaching the console. */
export interface ConsoleSettings {
  /** Repository root: the checkout holding `refactory/` and `.venv/`. */
  repoRoot: string
  /** Directory holding the interpreter that has refactory_dsh installed. */
  pythonBin: string
  /** Wall-clock ceiling for one console call. */
  timeoutMs: number
  /** Per-stream collection cap; a longer stream keeps its tail. */
  collectBytes: number
}

/**
 * What a console call produced: the decoded JSON, or the reason there is none.
 * `argv` is the exact command, so a failure can be reproduced by hand.
 */
export type ConsoleResult =
  | { ok: true; value: unknown; argv: string[] }
  | { ok: false; error: string; argv: string[] }

/**
 * Deployment settings for reaching the console, before they are resolved.
 *
 * Both console consumers — the model-facing tool and the Web Remote service —
 * declare these same four fields, so a deployment edits one row per consumer and
 * the shared resolver turns both into the same absolute paths.
 */
export interface ConsoleConfig {
  /** Repository root holding `refactory/` and `.venv/` (default: the process cwd). */
  readonly repoRoot?: string
  /** Directory of the interpreter with refactory_dsh installed (default `<repoRoot>/.venv/bin`). */
  readonly pythonBin?: string
  /** Wall-clock ceiling for one console call (default 10 minutes). */
  readonly timeoutMs?: number
  /** Per-stream collection cap in bytes (default 4 MiB). */
  readonly collectBytes?: number
}

/**
 * The console's deployment defaults, in one place.
 *
 * Schemastery has no non-mutating object-schema composition (its `set` edits the
 * schema in place), so each consumer declares its own fields and takes every
 * default from here: a cap can only change in one place.
 */
export const CONSOLE_DEFAULTS = {
  /** Wall-clock ceiling for one console call. */
  timeoutMs: 10 * 60 * 1000,
  /** Per-stream collection cap in bytes. */
  collectBytes: 4 * 1024 * 1024,
} as const

/**
 * Resolve deployment settings into the absolute paths one `Console` needs.
 *
 * `pythonBin` defaults against the resolved repository root rather than in the
 * schema, because a schema default cannot read a sibling field's value.
 * @param config - loader-validated settings.
 * @returns the settings as the process boundary consumes them.
 */
export function resolveConsoleSettings(config: Required<ConsoleConfig>): ConsoleSettings {
  const repoRoot = resolve(config.repoRoot)
  return {
    repoRoot,
    pythonBin: resolve(config.pythonBin || join(repoRoot, '.venv', 'bin')),
    timeoutMs: config.timeoutMs,
    collectBytes: config.collectBytes,
  }
}

/** What one `start` call carries: the mode, where the run lives, and its settings. */
export interface StartRequest {
  /** Mode to run, as `<package>/<name>`. */
  readonly mode: string
  /** Run id to claim; the console derives one when absent. */
  readonly runId?: string | undefined
  /** Directory holding the runs; the console default when absent. */
  readonly runsRoot?: string | undefined
  /** Run settings, passed through as `--<name> <value>` flags; `undefined` is skipped. */
  readonly settings?: Readonly<Record<string, string | number | boolean | undefined>> | undefined
}

/**
 * The console invocation that starts one run. The flags are the console's, so a
 * setting the console adds is usable here without an edit.
 * @param request - the mode, run addressing, and settings.
 * @returns the verb, positionals, and options for one console call.
 */
export function startInvocation(request: StartRequest): ConsoleInvocation {
  return {
    verb: 'start',
    positional: [request.mode],
    options: { 'run-id': request.runId, 'runs-root': request.runsRoot, ...request.settings },
  }
}

/**
 * The console invocation for one package-registry verb.
 * @param action - the registry verb, e.g. `list` or `enable`.
 * @param target - the package or directory it acts on; empty addresses the catalogue.
 * @param args - extra registry flags, as a shell-quoted string.
 * @returns the verb, positionals, and options for one console call.
 */
export function registryInvocation(action: string, target: string, args?: string): ConsoleInvocation {
  return { verb: 'registry', positional: [action, target], options: { args } }
}

/** One console call against the real process. */
export class Console {
  constructor(
    private readonly subprocess: SubprocessRuntime,
    private readonly settings: ConsoleSettings,
  ) {}

  /**
   * Build the argv for one invocation.
   * @param invocation - verb, positionals, and options.
   * @returns the argv, starting with the interpreter.
   */
  argv({ verb, positional = [], options = {} }: ConsoleInvocation): string[] {
    const argv = [join(this.settings.pythonBin, 'python'), '-m', 'refactory_dsh.console', verb]
    for (const [name, value] of Object.entries(options)) {
      if (value === undefined || value === false) continue
      const flag = `--${name.replaceAll('_', '-')}`
      if (value === true) argv.push(flag)
      else argv.push(flag, String(value))
    }
    argv.push(...positional)
    return argv
  }

  /**
   * Run one invocation and decode its JSON.
   * @param invocation - verb, positionals, and options.
   * @param signal - caller's abort signal, forwarded to the child.
   * @returns the decoded result; a nonzero exit or undecodable stdout is reported, not thrown.
   */
  async run(invocation: ConsoleInvocation, signal?: AbortSignal): Promise<ConsoleResult> {
    const argv = this.argv(invocation)
    const timer = AbortSignal.timeout(this.settings.timeoutMs)
    const abort = signal === undefined ? timer : AbortSignal.any([signal, timer])
    const handle: SubprocessHandle = this.subprocess.spawn({
      argv,
      cwd: this.settings.repoRoot,
      stdio: {
        stdin: 'ignore',
        stdout: { maxBytes: this.settings.collectBytes },
        stderr: { maxBytes: this.settings.collectBytes },
      },
      graceMs: 2_000,
      signal: abort,
    })
    const outcome = await handle.done
    const stdout = handle.collected.stdout
    const stderr = handle.collected.stderr
    if (stdout === undefined || stderr === undefined) {
      // Both were requested as collect above, so a missing reader is a provider
      // that did not honour its own spec. Report it rather than read undefined.
      return { ok: false, argv, error: 'the subprocess provider returned no collected output' }
    }
    const stdoutText = stdout.readFrom(0).text
    const stderrText = stderr.readFrom(0).text
    if (outcome.exitCode !== 0) {
      // The console prints its own `{"error": ...}` on a handled refusal, so a
      // failed call still carries the reason the user needs.
      const reported = decode(stdoutText)
      return {
        ok: false,
        argv,
        error: reported.kind === 'json' && isRecord(reported.value) && typeof reported.value.error === 'string'
          ? reported.value.error
          : stderrText.trim() || `exited with ${outcome.exitCode ?? outcome.signal ?? 'no status'}`,
      }
    }
    const decoded = decode(stdoutText)
    if (decoded.kind === 'json') return { ok: true, argv, value: decoded.value }
    return { ok: false, argv, error: `the console printed no JSON: ${stdoutText.trim() || stderrText.trim()}` }
  }
}

/** A record with string keys, as JSON objects decode. */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

type Decoded = { kind: 'json'; value: unknown } | { kind: 'not-json' }

/** Parse stdout, tolerating surrounding whitespace and nothing else. */
function decode(text: string): Decoded {
  try {
    return { kind: 'json', value: JSON.parse(text) }
  } catch {
    return { kind: 'not-json' }
  }
}
