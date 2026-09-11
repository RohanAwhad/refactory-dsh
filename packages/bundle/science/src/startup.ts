/**
 * The science app's command-line provider: it parses `--mode/--task/--budget`
 * (and `--provider/--model/--graphs/--workdir`), loads the mode's committed
 * `graph.json`, and publishes {@link SCIENCE_STARTUP_SERVICE}. The workflow-spine
 * row and the runner both read from that service, so a run is parameterised by
 * the command line rather than baked into a profile.
 * @module @deepseek-ai/dsh-science/startup
 */

import { readFileSync, existsSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { Command } from 'commander'
import type { Context } from '@deepseek-ai/cordis'
import { parseCmdline } from '@deepseek-ai/dsh-cmdline'

/** Stable Cordis plugin name. */
export const name = 'science-startup'

/** Services required before the run can be resolved. */
export const inject = ['cmdlineArgs']

/** Service provided by this plugin and injected by the runner and spine row. */
export const SCIENCE_STARTUP_SERVICE = 'scienceStartup'

/** The mode's state file, used by the runner to report the best score. */
const STATE_FILES: Record<string, string> = {
  gepa: 'gepa_state.json',
  best_of_n: 'best_of_n_result.json',
  scs: 'scs_state.json',
  aide: 'aide_state.json',
  ai_sci_v1: 'autoresearch_state.json',
  ai_sci_v2: 'aide_state.json',
  openevolve: 'openevolve_state.json',
  shinka: 'shinka_state.json',
  adaevolve: 'adaevolve_state.json',
  evox: 'evox_state.json',
  autoresearch: 'autoresearch_state.json',
  karpathy: 'karpathy_state.json',
  autoscientists: 'autoscientists_state.json',
}

const DEFAULT_GRAPHS_DIR = '/Users/akash/dsh/refactory-dsh/srf/graphs'
const DEFAULT_VENV_BIN = '/Users/akash/dsh/refactory-dsh/.venv/bin'

/** Everything the run needs, resolved once from the command line. */
export interface ScienceStartupValues {
  /** The CEO driving prompt. */
  task: string
  /** The mode's graph, exactly refactory's `Workflow.to_dict()` output. */
  graph: unknown
  /** Working directory for node commands and the ops' artifacts. */
  workdir: string
  /** Environment entries for node commands (SRF_* plus the venv on PATH). */
  commandEnv: Record<string, string>
  /** The mode name, for reporting and state-file lookup. */
  mode: string
  /** The task name, passed to the ops as SRF_TASK. */
  taskName: string
  /** The eval budget, passed to the ops as SRF_BUDGET. */
  budget: number
  /** The CEO model route, for the runner's agent creation (absent = runtime default). */
  provider: string | undefined
  /** The CEO model id override, when the user named one. */
  model: string | undefined
}

function scienceCommand(): Command {
  return new Command()
    .name('dsh --profile science')
    .description('Drive one scientific-research workflow graph through the spine.')
    .helpOption('-h, --help', 'show this help')
    .argument('[command]', 'the subcommand; "run" drives the graph to completion', 'run')
    .requiredOption('-m, --mode <mode>', 'the SRF mode (e.g. gepa, best_of_n)')
    .requiredOption('-t, --task <task>', 'the SRF task (e.g. circle_packing)')
    .option('-b, --budget <n>', 'eval budget', '10')
    .option('-p, --provider <provider>', 'provider route for the CEO model (default: the runtime default)')
    .option('--model <model>', 'model id for the CEO (defaults to the provider route default)')
    .option('--graphs <dir>', 'directory of <mode>.graph.json', DEFAULT_GRAPHS_DIR)
    .option('--workdir <dir>', 'working directory for the run')
    .option('--python-bin <dir>', 'directory of the python with srf+factory installed', DEFAULT_VENV_BIN)
    .addHelpText('after', `
Examples:
  dsh --profile science run --mode gepa --task circle_packing --budget 20
`)
}

/** The mode's graph.json, or a thrown error naming the fix. */
function loadGraph(mode: string, graphsDir: string): unknown {
  const path = join(graphsDir, `${mode}.graph.json`)
  if (!existsSync(path)) {
    throw new Error(`no graph at ${path}; run \`srf graphs --out ${graphsDir}\` first`)
  }
  return JSON.parse(readFileSync(path, 'utf8'))
}

/**
 * Parse and provide the run as an ordinary Cordis service. `--help` and usage
 * errors provide nothing, which fails the runner row (and therefore boot) loudly.
 * @param ctx - plugin context carrying the command line.
 */
export function apply(ctx: Context): void {
  const program = scienceCommand()
  program.action(() => {
    const options = program.opts()
    const mode = String(options.mode)
    const taskName = String(options.task)
    const budget = Number(options.budget)
    const graphsDir = String(options.graphs)
    const workdir = String(options.workdir ?? join('/Users/akash/dsh/refactory-dsh/srf/outputs', `run-${mode}-${Date.now()}`))
    const pythonBin = String(options.pythonBin)

    const graph = loadGraph(mode, graphsDir)
    // A node command's cwd must exist before the first one spawns.
    mkdirSync(workdir, { recursive: true })
    const commandEnv: Record<string, string> = {
      SRF_WORK_DIR: workdir,
      SRF_TASK: taskName,
      SRF_KNOBS: '{}',
      SRF_BUDGET: String(budget),
      PATH: `${pythonBin}:${process.env.PATH ?? ''}`,
    }

    ctx.provide(SCIENCE_STARTUP_SERVICE, {
      task: 'Drive the workflow graph to completion. Call wf_next first and after every wf_done/wf_verdict. '
        + 'For each AgentNode or LLMNode, spawn a subagent to read the node\'s reads and write its writes, '
        + 'then report wf_done. Route each gate with wf_verdict. When wf_next returns done, stop and '
        + 'report the final best score you observed.',
      graph,
      workdir,
      commandEnv,
      mode,
      taskName,
      budget,
      provider: options.provider === undefined ? undefined : String(options.provider),
      model: options.model === undefined ? undefined : String(options.model),
    } satisfies ScienceStartupValues)
  })
  parseCmdline(ctx, program)
}

export { STATE_FILES }
