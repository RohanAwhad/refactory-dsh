---
description: "面向 Python 外循环优化器的模型侧 refactory 工具：每个 console 动词对应一个 action、运行生命周期、运行目录中的产物，以及浏览器运行卡片，供选择、配置或排查该工具的用户与维护者阅读。"
kind: "package-reference"
---

# @deepseek-ai/dsh-tool-refactory

[English](README.md) | 中文

## 概述

`dsh-tool-refactory` 为 agent 提供一个名为 `refactory` 的工具，用来启动并读取一次 refactory 外循环运行：优化器对 Python 模式提出编辑、评估这些编辑，并保留能提升得分的部分。用它列出某个检出中可运行的模式、在独立进程中启动一次运行、在运行进行期间观察它，并读回其排行榜、逐候选差异与磁盘上的产物。循环是 Python 程序，也始终保持如此：每个 action 都通过宿主的 subprocess 能力执行 `python -m refactory_dsh.console <verb>`，并原样返回该 console 的 JSON，因此工具、浏览器卡片与 shell 读到的是同一个答案。代价是需要一个已配置的 Python 解释器和一个包含 `refactory/` 的检出；由于工具不重新实现任何东西，它提供的 action 与产物恰好就是 console 暴露的那些。

## 目录

- [使用本包](#use-this-package)
- [理解实现](#understand-the-implementation)
- [进一步探索](#further-exploration)
- [模型体验](#model-experience)
- [已知限制与延期工作](#known-limitations-and-deferred-work)
- [开发备注](#dev-note)

-----

<a id="use-this-package"></a>
## 使用本包

把本包挂载进一个已经提供 `tools`、`subprocess` 与 `systemPrompt` 服务的组合；此后 agent 或 Web 视图即可通过 `refactory` 工具的八个 action 驱动一次运行。

### 何时选择

当某个会话需要启动、观察或读取一次 refactory 运行时选择本包：`ceo` preset 已经挂载它，Web 视图会把每次调用渲染成运行卡片，而不是通用工具行。当没有任何 Python 解释器能导入 `refactory_dsh` 时请避开：每个 action 都要调用该解释器，缺少它会让调用失败，而不会退化成部分答案。若要在循环自身进程内工作，直接运行 `refactory/README.md` 所记录的 console 入口；这样启动的运行只有在位于所配置的 runs 根目录之下时，才会出现在 `runs` 列表中。

### 最小配置

本包以一行 `cordis.yml` 挂载，没有必填配置，默认把解释器解析为 `<repoRoot>/.venv/bin/python`。`ceo` preset 挂载的是同一行，并把 `repoRoot` 固定为进程工作目录，因此换一个检出位置只需要改那一处。

```yaml
- name: '@deepseek-ai/dsh-tool-refactory'
```

| 字段 | 默认值 | 含义 |
|---|---|---|
| `toolName` | `refactory` | 模型侧工具名；它也用于命名提示词片段与浏览器卡片的派发键 |
| `repoRoot` | 进程工作目录 | 包含 `refactory/` 与 `.venv/` 的检出；它也是每次 console 调用的工作目录 |
| `pythonBin` | `<repoRoot>/.venv/bin` | 存放可导入 `refactory_dsh` 的 `python` 的目录 |
| `timeoutMs` | `600000`（十分钟） | 单次 console 调用的挂钟上限，从 1 毫秒到一小时；它不限定运行本身 |
| `collectBytes` | `4194304`（4 MiB） | 每个流的收集上限；更长的流保留其尾部 |

### 每个 action 做什么

每个 action 都返回 console 的 JSON 答案；工具据此为模型渲染一段简短摘要，并为浏览器卡片投影该载荷。

| Action | 还需要 | 报告内容 |
|---|---|---|
| `modes` | — | 检出中可启动的每个模式，含其参数与是否可启动 |
| `runs` | — | runs 根目录下的每次运行，最新在前，含其模式、最佳得分与是否已结束 |
| `start` | `mode` | 新运行的 id、根目录与 pid；该运行在自己的进程中继续 |
| `status` | `run` | 运行的状态、计数器、最佳得分，以及 `after` 之后的进度事件 |
| `artifacts` | `run` | 运行可读的产物，`contents` 为 true 时附带内容 |
| `leaderboard` | `run` | 运行按得分排序的候选，每个都带其相对父代的差异 |
| `candidate` | `run`、`candidate` | 单个候选，以及它相对来源父代所做的改动 |
| `stop` | `run` | 运行进程是否接受了停止信号 |

省略了 action 所需参数的调用会在任何进程启动之前失败；console 的拒绝会作为工具错误返回，并在 `refactory <action> failed:` 之后带上 console 自己的原因，因此模型会纠正调用，而不是读到一次静默的成功。

-----

<a id="understand-the-implementation"></a>
## 理解实现

<details>
<summary>实现细节——点击展开</summary>

本节说明一次调用如何抵达循环，以及一次运行会留下什么；可观察行为已在[使用本包](#use-this-package)中完整说明。

### 一次调用，一个 console 动词

`Console.argv` 在不经过 shell 的情况下构建 argv：解释器、`-m refactory_dsh.console`、动词，随后每个选项写作 `--flag value`（`true` 布尔值成为裸标志，`false` 或 `undefined` 被丢弃），最后是作为位置参数的 mode 或 candidate id。`Console.run` 以 `repoRoot` 为工作目录启动该 argv，忽略 stdin，在 `collectBytes` 上限内收集 stdout 与 stderr，并把调用方的中止信号与 `timeoutMs` 期限合并。非零退出不会被抛出：工具从 stdout 读取 console 自己的 `{"error": ...}`，把它作为工具错误报告，并保留确切的 argv，以便手工复现失败。

### 一份 JSON 答案，两个读者

console 每次调用打印一份 JSON 文档。工具返回 `{ action, data }`；`output.render` 把 `data` 变成模型读到的摘要行，`output.presentationMeta` 把同一对值投影进会话日志。浏览器卡片从这份落库元数据推导自己的行，因此回放一次调用渲染出的是运行视图，而不是关于运行的一句话。

### 运行目录

一次运行就是一个文件目录，console 读取它而不是读取某个服务。`start` 会在循环写出第一个产物之前写入 `console.json`，未完成的运行因此仍然可见、可观察。

| 文件 | 存放内容 |
|---|---|
| `console.json` | console 对一次 start 的记录：运行 id、mode、根目录、pid 与设置 |
| `progress.jsonl` | 循环每一步一行 JSON；`status` 从 `after` 偏移处读取其后续行 |
| `journal.jsonl` | 循环的每个事件，也是候选完整状态的唯一书写位置 |
| `run.json` | 运行目录的布局版本，让读取方在遇到陌生布局时明确失败 |
| `report.json` | 已结束运行的摘要：最佳得分、排行榜、血缘，以及拒绝与接受操作直方图 |
| `report.md` | 同一份报告的 Markdown 形式，是人最先阅读的产物 |
| `gradient.json` | 优化器读取的梯度事实：旋钮归因、成本、运行失败与接受操作构成 |
| `loss.json` | 该批次的聚合得分：最佳、均值与改进量 |
| `rejections.jsonl` | 校验器拒绝的每个提案及其原因 |
| `manifest.json` | `artifacts` action 背后的产物清单 |
| `runs/<candidate-id>/candidate.json` | 单个候选记录的评估结果 |

### 外循环

循环的八个步骤借用了 PyTorch 的命名：`init` 从模式构建创世状态，`forward` 执行并给每个未评估的候选打分，`loss` 聚合该批次，`backward` 记录梯度事实，`optimizer` 提出由校验器接受或拒绝的编辑，`step` 把接受的编辑施加到其父代上，`scheduler` 决定是否再跑一个 epoch，`report` 写出排行榜与直方图。每个候选都要先通过校验，才可能消耗一次内层运行。完整约定——包括编辑代数与校验器的不变式——见 [Pythonic 模式计划](../../../refactory/docs/plans/pythonic-modes.md)。

### 源码地图

| 文件 | 职责 |
|---|---|
| [`src/index.ts`](src/index.ts) | 插件入口：`Config` schema、八个动词、提示词片段、工具注册与模型侧摘要渲染器 |
| [`src/console.ts`](src/console.ts) | 进程边界：argv 组装、spawn、JSON 解码与拒绝报告 |

**运行时不变式：** 本包不发布运行时不变式伴随模块，因为它自身不持有任何持久状态：一次调用只派生一个 console 进程、返回其 JSON，而它可能启动的那次运行位于循环所拥有的运行目录中。不变式本可断言的每一种关系，要么属于平台（子进程会退出），要么属于循环（日志可重放出同一个种群）。

</details>

-----

<a id="further-exploration"></a>
## 进一步探索

当包级约定不够用时阅读以下页面。它们从本工具所驱动的 Python 循环，走到它所调用的能力，再到它满足的工具约定。

- [Pythonic 模式与外循环优化器](../../../refactory/docs/plans/pythonic-modes.md)——循环的步骤、编辑代数与验收关卡；`refactory/` 项目自己的 `README.md` 是其入口。
- [refactory 组映射](../README.zh.md)——同级组页面及其包表格。
- [Subprocess 能力](../../subprocess/subprocess/README.zh.md)——本包用来生成 console 的 `ctx.subprocess` 服务。
- [添加工具的 cookbook](../../../docs/cookbook/adding-a-tool.zh.md)——工具注册、呈现与 Model Experience 约定。
- [ceo preset](../../preset/agent-presets/presets/ceo/agent.cordis.yml)——挂载本包的那一行。

-----

<a id="model-experience"></a>
## 模型体验

### 工具定义

#### 模型看到什么

模型看到的是一个名为 `refactory` 的工具，其必填的 `action` 参数从八个动词中选一个——`modes`、`runs`、`start`、`status`、`artifacts`、`leaderboard`、`candidate`、`stop`——其可选参数用于定位一次运行或一个候选（`run`、`runId`、`candidate`、`mode`）、限定列表规模（`limit`）、扩展产物读取（`contents`）、移动 runs 根目录（`runsRoot`）、从偏移处轮询（`after`），或传入运行设置（`settings`）。描述说明循环的用途与每个动词的一行帮助；这些参数完全按模型书写的拼法，以 `--flag value` 选项与位置参数抵达 console。

#### Token 影响

在给定 `toolName` 与动词集合下，定义与描述都是固定的，因此能看到该工具的每个请求都付出相同成本，与运行规模无关。每个结果只增加一段简短摘要：一行计数，加上所列每个模式、运行、产物或候选各一行。

#### KV Cache 影响

在组合与该工具可见性不变时，定义保持前缀稳定。结果作为普通工具消息追加在可复用请求前缀之后；通过 `toolName` 改名会同时改变定义与提示词片段，从此处起的复用随之失效。

### 系统提示词片段

#### 模型看到什么

本包注册一个名为 `tool:refactory` 的系统提示词片段，其位置由 `ctx.systemPrompt.getSectionOrder('TOOL_REFACTORY')` 给出，文本跟随所配置的 `toolName`。该片段说明运行在自己的进程中启动并在调用返回后继续，因此模型会先启动运行，再去观察它，而不是阻塞等待。

##### tool:refactory

```markdown
Use the refactory tool to run and read a refactory outer-loop run. A run starts in its own process and continues after the call returns, so start it, then watch it with the "status" action (pass an "after" offset to read only what is new) instead of blocking on it.
```

#### Token 影响

在本包挂载且该片段可见期间，每个请求都有固定开销；文本只随 `toolName` 变化。

#### KV Cache 影响

除非 `toolName` 改变或组合重新排列提示词片段，否则保持前缀稳定；这两种情况都会从此片段起使复用失效。

## 已知限制与延期工作

<a id="known-limitations-and-deferred-work"></a>

这些限制说明该工具何时不合适。它们是当前包约束，不是任务积压。

- **Python 解释器必须能导入 `refactory_dsh`**——每个 action 都调用 `<pythonBin>/python -m refactory_dsh.console`，因此缺少解释器、虚拟环境或检出时调用直接失败，而不会降级。
- **运行卡片只认默认工具名**——浏览器行注册在线名 `refactory` 之下，因此只有该 `toolName` 会渲染出运行卡片，而不是通用工具行。
- **运行活得比调用久，且没有任何流式推送**——`start` 在进程一 spawn 就返回；观察意味着用 `after` 偏移轮询 `status`，而 `stop` 只是向进程发信号并报告它当时是否还活着。
- **`runs` 列表受 runs 根目录约束**——`start` 会拒绝目录已存在的运行 id，而 `runs` 只列出所配置根目录（默认 `<repoRoot>/.factory/loop`）之下的目录。
- **每个 action 都要付一个进程的代价**——工具在调用之间不保留状态，因此每次 `status` 轮询都会启动一个新解释器并重新导入 `refactory_dsh`。
- **读取面就是 console 的面**——console 未暴露的 action 与产物不可用，工具也无法读取 console 解析不了的运行文件。

<a id="dev-note"></a>
### 开发备注

<details>
<summary>维护者的工作上下文——点击展开</summary>

本开发备注是维护者的工作上下文：尚未决定的问题与方向。它明确不具权威性——已交付行为、限制与既定理由以上文、包代码与所链接来源为准。

#### 待决方向

- **用推送取代轮询**——运行视图从 `after` 偏移轮询 `status`。改为跟踪进度文件并推送帧可以去掉轮询，但运行目录布局归 console 所有，目前尚不存在设计。
- **按 action 定制卡片**——浏览器卡片把每个 action 都渲染成扁平行。为排行榜差异与产物树设计专门布局仍是开放的。
- **runs 根目录之外的运行**——`run` 接受绝对目录，而 `runs` 只列出所配置根目录；两者是否应当一致尚未决定。

</details>
