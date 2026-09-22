# Refactory

[English](refactory.md) | 中文

refactory seam 是 refactory 外循环运行在 DeepSeek Harness 一侧的部分：该优化器对 Python 模式提出编辑、评估这些编辑，并保留能提升得分的部分。循环本身是本仓库包树之外的 Python 程序，DSH 侧的两个读者都经过同一个进程边界——`python -m refactory_dsh.console`——因此工具与浏览器读到同一份 JSON 契约。面向模型的 Consumer 是 [dsh-tool-refactory](../../packages/refactory/tool-refactory/README.zh.md)（`ctx.tools`）；Web 选择器的 Host 半边是 [dsh-refactory-console](../../packages/refactory/refactory-console/README.zh.md)（`ctx.refactoryConsole`），它把该控制台经 [Typert Remote](typert.zh.md) 暴露出来。循环自己的词汇、步骤与产物见[refactory 架构总览](../../refactory/docs/architecture.md)。

源码：[`packages/refactory/refactory-console/src/index.ts`](../../packages/refactory/refactory-console/src/index.ts)

## 有描述的可选项

控制台对每个选项都连同描述一起报告，而 Remote 服务保持这个样子，不改写它们。模式目录条目携带标识 `<package>/<mode>`、描述、是否计分，以及指名运行它的动作的 `action` 字段——计分模式为 `start`，循环无法搜索的模式则为创作动作 `create`、`edit` 或 `design`。策略、优化器与评估器名来自三个二级注册表，包列表来自注册表自己的目录，后者在其每个模式上报告该包的启用状态与位置。因此不声明任何模式的包报告的是没有状态，而不是猜测。

## 控制台边界

两个读者持有同一个 `Console`，其 argv 组装、进程启动、JSON 解码与拒绝报告都归该包所有。`resolveConsoleSettings` 把四项部署设置——`repoRoot`、`pythonBin`、`timeoutMs`、`collectBytes`——变成一次调用所需的绝对路径，工具与服务因此不会在检出位置或调用时限上产生分歧。服务不跨调用保存状态：每次读取都启动一个新的解释器，而启动开启的运行在它自己的进程与循环拥有的运行目录里继续存在。

## 拒绝

每个方法以一个 Remote 错误作答，选择器因此可以说出该改做什么，而不是展示一个失败的进程。

| 代码 | 何时 |
|---|---|
| `gateway/bad-request` | 必须由调用者提供的地址为空 |
| `gateway/cancelled` | 调用者的信号已被中止 |
| `refactory/unknown-mode` | `start` 被要求启动目录里没有的模式 |
| `refactory/authoring-mode` | `start` 被要求启动自身 `action` 是创作动作的模式 |
| `refactory/package-disabled` | `start` 被要求启动注册表报告为已停用包里的模式 |
| `refactory/console-failed` | 控制台拒绝，或以其并不拥有的形状作答 |

<!-- BEGIN GENERATED cordis-surface (gen-cordis-catalog.ts) — do not edit between markers -->

<a id="cordis-surface"></a>

## Cordis API

Generated from source by `scripts/gen-cordis-catalog.ts` (verified fresh by `pnpm run verify-cordis-catalog` in doc-sync; regenerate with `pnpm run gen-cordis-catalog`) — the language sides differ only in locale-specific paired document paths. Signature blocks use a `ts cordis-catalog` fence and keep the original source JSDoc; dispatch modes are defined in the [primer](../cordis-primer.zh.md#dispatch-modes), and the framework-inherited `ctx` API lives in [cordis-api/inherited.md](../cordis-api/inherited.md).

<a id="ctxrefactoryconsole--refactoryconsole"></a>

### `ctx.refactoryConsole` — `RefactoryConsole`

Host Remote service over the refactory console.

```ts cordis-catalog
/**
 * The modes a run can be started from, each with the verb that runs it.
 * @param signal - caller cancellation.
 * @returns the console's catalogue, as the choices it describes.
 */
@Remote async modes(signal: AbortSignal): Promise<readonly RefactoryModeChoice[]>

/**
 * The named outer loops, optimizers and evaluators a run's settings choose between.
 * @param signal - caller cancellation.
 * @returns the three registries, each a list of named choices.
 */
@Remote async strategies(signal: AbortSignal): Promise<RefactoryStrategies>

/**
 * Every run under the run root, newest first.
 * @param signal - caller cancellation.
 * @returns the runs, each with where it got to.
 */
@Remote async runs(signal: AbortSignal): Promise<readonly RefactoryRunSummary[]>

/**
 * The packages the registry knows, with the modes each declares.
 * @param signal - caller cancellation.
 * @returns the packages as described choices.
 */
@Remote async packages(signal: AbortSignal): Promise<readonly RefactoryPackageChoice[]>

/**
 * Start a run from one described mode.
 *
 * The mode must be one the catalogue offers and one whose `action` is `start`;
 * an authoring mode is refused by name, with the verb that runs it, because a
 * person who picked it needs the action rather than a failed process.
 * @param request - the mode, run addressing, and settings.
 * @param signal - caller cancellation.
 * @returns what the console recorded about the start.
 */
@Remote async start(request: RefactoryStartRequest, signal: AbortSignal): Promise<RefactoryStartReceipt>

/**
 * Where one run is, and what is new since the reader last looked.
 * @param run - the run id or absolute run directory.
 * @param after - how many progress lines the caller has already read.
 * @param signal - caller cancellation.
 * @returns the run's state, the new events, and the offset for the next look.
 */
@Remote async status(run: string, after: number, signal: AbortSignal): Promise<RefactoryRunStatus>

/**
 * Ask one run to stop.
 * @param run - the run id or absolute run directory.
 * @param signal - caller cancellation.
 * @returns whether the run's process is gone.
 */
@Remote async stop(run: string, signal: AbortSignal): Promise<RefactoryStopReceipt>

/**
 * Enable or disable one package, and answer with the catalogue as it now stands.
 * @param name - the package name, as the catalogue lists it.
 * @param enabled - the state to set.
 * @param signal - caller cancellation.
 * @returns the packages as described choices, after the change.
 */
@Remote async setPackageEnabled( name: string, enabled: boolean, signal: AbortSignal, ): Promise<readonly RefactoryPackageChoice[]>

/**
 * Remove one workspace package, and answer with the catalogue as it now stands.
 * @param name - the package name, as the catalogue lists it.
 * @param signal - caller cancellation.
 * @returns the packages as described choices, after the change.
 */
@Remote async removePackage(name: string, signal: AbortSignal): Promise<readonly RefactoryPackageChoice[]>

/**
 * Bring a package in from a directory or git URL, and answer with the catalogue
 * as it now stands.
 * @param source - the directory or git URL to import from.
 * @param name - the name to import it as; empty keeps the package's own name.
 * @param signal - caller cancellation.
 * @returns the packages as described choices, after the import.
 */
@Remote async importPackage( source: string, name: string, signal: AbortSignal, ): Promise<readonly RefactoryPackageChoice[]>
```

Source: [`packages/refactory/refactory-console/src/index.ts`](../../packages/refactory/refactory-console/src/index.ts)
<!-- END GENERATED cordis-surface -->
