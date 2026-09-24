# Agent Note: The panel can set a mode's knobs and use it once

Status: implemented

[English](2026-09-24-the-panel-can-set-a-modes-knobs-and-use-it-once.md) | 中文

## Problem

**面板的全部手势就是选一个模式再按 Start。** `packages/client/ui-refactory` 可以选择模式、选择外循环、优化器与评估器，然后按下 **Start**。它无法设置该模式声明的旋钮，也无法把一个模式用一次。

**一个模式的输入就是它声明的旋钮，而面板一个都没有提供。** 一个输入路径、一条要运行的命令、一个目标——每一个都是模式声明的旋钮。因此从面板启动的搜索让每个候选都用声明里的默认输入运行，全部得 0.0 分，看起来像一个坏掉的模式，实际上是面板供不上输入（#73）。

**刚创建出一个模式的人只能去优化它。** console 的 `run` 动词与工具上对应的动作已经能把一个模式用一次（[一次从面板发起的创建被什么挡住了](2026-09-23-what-stopped-a-creation-from-the-panel.zh.md)）；面板却没有任何办法说「直接用它，把结果给我看」，而这正是刚写完一个模式的人最想做的第一件事。

**console 读回的运行目录，是 runner 从来没有写过的那个。** 没有显式的 `--workdir` 时，console 的 `run` 动词去 `<project>/interactive/<mode>` 找，而 runner 写的是 `.factory/interactive/<mode>`，于是工具被告知 console 没有留下运行目录，而证据就在一条路径之外。已有的 Python 测试传了 `--workdir`，所以默认分支从未被测试。

## Decision

1. **面板画出所选模式声明的每一个旋钮。** 模式目录本来就带着每个模式声明的对外面：`refactory_dsh/console.py` 构建每个目录条目时带上 `parameters: described["knobs"]`，而 `refactory/refactory_dsh/nn/mode.py` 的 `Mode.describe()` 对每个旋钮返回 `{value, kind, values, range, frozen, description}`。面板为每个条目画一个字段，按 kind 成形：声明了取值集合的旋钮是一个选择，`bool` 是 `true`/`false` 之间的选择，`int`/`float` 是受声明上下界约束的数字输入，开放的 `str` 是文本。冻结的旋钮也会画出来——冻结的意思是搜索不能移动它，不是人不可以说它是什么，而那些输入旋钮恰恰就是这个缺陷。每个声明的旋钮都以 `NAME=VALUE` 传递，包括留在默认值上的那些，所以字段显示什么，运行就发什么。

2. **`Run once` 使用这个模式，并用 console 自己的话报告发生了什么。** 它恰好在目录条目自己的 `action === 'start'` 时出现——console 的 `start` 拒绝创作模式时用的也是这个字段——所以创作模式那一行不会给出一个注定失败的按钮。它调用 console 服务上新加的 `@Remote run(...)`（[Web 选择器的 Remote 命名空间](2026-09-24-the-web-picker-reads-the-console-over-remote.zh.md)），后者转发到已有的 `interactive` 入口点。结果带着运行的 spine 状态、仍然待办的节点、spine 有错误时的那个错误，以及运行的事实。停在属于人的门上的运行被报告为在等人；面板不回答门，因为一道没有人的门正是它绝不能伪造的东西。

3. **console 读 runner 自己选定的运行目录。** `refactory_dsh/interactive.py` 新增 `default_workdir(identifier)`，作为该默认值的唯一所有者；runner 与 console 的读回都用它，而不是由 console 再算一次「运行大概会去哪里」。

4. **失败的 console 调用保留它已解码的载荷。** `packages/refactory/tool-refactory/src/console.ts` 的 `ConsoleResult` 失败分支新增 `value`，因为 console 把运行停在哪里写在它以非零退出的原因旁边，丢掉它就丢掉了唯一持久的答案。同一个文件还新增 `runInvocation`，让 console 的 `--knob` 拼写只有一个所有者，而不是每个调用方各有一个。

## Consequences

**文件。** 这次改动落在 `packages/refactory/refactory-console/src/{types.ts,index.ts}`、`packages/refactory/tool-refactory/src/console.ts`、`packages/client/ui-refactory/src/client/{store.ts,face.ts,RefactoryBody.tsx,locales.ts,index.ts,RefactoryBody.module.css}`、`ui-refactory` 与 `refactory-console` 的 README 及其配对记录、`refactory/refactory_dsh/{console.py,interactive.py}`，以及这些包下的测试。

**文案在两种语言里都由 locale 拥有。** 新的旋钮字段、`Run once` 控件与停在门上的那一行都同时存在于英文与中文，并且都经由该包的类型化字典抵达。

**证据。** `pnpm run test:gui` 报告 340 个文件 / 4762 通过 / 1 跳过；`pnpm run verify-client-ui-i18n` 通过；`pnpm run typecheck` 重新生成了 Typert 远端面相，其中现在声明了 `run`；收窄的插桩覆盖率显示每个被触及的文件都是 100%；`refactory/tests/test_gui_loop.py` 里新增的 Python 测试 `test_a_single_run_is_read_back_from_the_directory_the_runner_chose` 在读回修复之前会以 `KeyError: 'run'` 失败。

**还有两件事没有完成。** 没有录制浏览器的流程，因为此环境里没有可达的浏览器驱动。`packages/refactory/tool-refactory/src/index.ts` 有覆盖率缺口，收窄的插桩运行把它归因于本次改动没有触及的代码。

## Alternatives considered

**只为搜索可以移动的旋钮画字段。** 否决：冻结的旋钮就是模式的输入，而这正是要修的缺陷；一个省略它们的表单会让面板无法提供它存在的意义所在的那个输入。

**用优化器的状态词汇报告一次性运行的结果。** 否决：一次运行有 spine 状态与停止原因，而为面板发明第二套词汇，正是「同一个问题有两个答案」的开端。

**让面板回答门。** 否决：属于人的门由人来回答，而面板将不得不声称一个它并不拥有的身份。

**让 console 猜默认运行目录，而不是问 runner。** 否决：一条路径算两遍，正是产生这个 bug 的原因。
