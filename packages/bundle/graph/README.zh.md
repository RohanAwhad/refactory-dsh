---
description: "dsh graph bundle：在 dsh-base 之上，由 CEO agent 驱动 refactory 模式图的运行器；供端到端运行某一个已提交模式的用户阅读。"
kind: "package-bundle"
---

# @deepseek-ai/dsh-graph

[English](README.md) | 中文

## 概述

`graph` profile 层端到端驱动一张 refactory 模式图：它以打开 `executeCommands` 的方式挂载工作流 spine，把 base persona 替换为 CEO 的措辞，并新增一个接收 mode 与 task 的 `run` 子命令。当 agent 达到静止后，你会得到一份 JSON 报告——本次运行的 `best_score`、session id、工作目录与 CEO 的最终消息。用一条 `dsh plugin` 命令把它安装进一个 profile；该层叠加在 `dsh-base` 之上，由后者提供模型连接、工具集、会话存储，以及确定性节点运行所需的 `shell` 执行器。本 bundle 自身不持有任何图：它在启动时从 `--graphs` 加载 `<mode>.graph.json`，因此一个模式就是一个文件加上其命令所调用的 Python op。选择它是为了运行一个已提交的模式；当该模式声明了 `user` gate 时，终端应答方会把该 gate 呈现给人，因此同一个 profile 既能服务无人值守的运行，也能服务有人在 gate 处的运行。refactory 的 `python -m refactory_dsh.interactive <package>/<mode>` 就是准备这次运行的入口：它把意图 ledger 折入模式并启动本 profile，同时接上终端。

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

### 安装到 profile

```text
dsh plugin --profile graph add <checkout>/packages/bundle/graph
dsh plugin --profile graph remove @deepseek-ai/dsh-graph
```

`graph` 不是随仓库交付的 profile 模板之一，因此必须先让 profile 存在才能运行：在没有它的机器上执行 `dsh --profile graph` 会以 `profile "graph" does not exist; create it with 'dsh plugin --profile graph add <package>'` 停止。上面的 `add` 形式从检出目录链接该包，因为 `@deepseek-ai/dsh-graph` 并未发布到 npm registry。该命令会基于 `dsh-base` 初始化 profile 并安装这个包；由于它的 manifest 声明了 `dsh.bundle.patch`，reconcile 步骤会把 `@deepseek-ai/dsh-graph` 追加到 `dsh.profile.bundles` 中、位于 `@deepseek-ai/dsh-base` 之后，该层随即在下次启动时激活。没有该声明的依赖只会作为普通包安装，永远不会成为一层。`remove` 会同时移除该依赖与这一层。

### 运行一个模式

```text
dsh --profile graph run --mode gepa --task circle_packing --budget 20
```

`--mode` 与 `--task` 为必填，`run` 是默认子命令。`--budget` 默认为 10，并以 `SRF_BUDGET` 传给各个 op；`--knobs` 接收一个 `OptKnob` 覆盖的 JSON 对象；`--provider` 与 `--model` 选择 CEO 的路由。`--graphs` 默认指向检出目录的 `refactory/vendor/srf/graphs`（或 `$SRF_GRAPHS`），且必须包含 `<mode>.graph.json`；`--python-bin` 默认为 `.venv/bin`（或 `$VENV_BIN`），且必须包含已安装 `srf` 与 `factory` 的 Python。`--workdir` 默认为一个新的临时目录。图缺失时会在启动阶段失败，并给出它查找的路径以及生成该图的 `srf graphs` 命令。

运行会打印一个 JSON 对象，包含 `mode`、`task`、`budget`、`best_score`、`session_id`、`workdir` 与 `final`——即 CEO 最后一条消息的前 400 个字符。当没有任何被评估的提案被接受时，`best_score` 为 0。

### 你会得到什么

- `run` 子命令，以及它的 mode、task、budget、graph、Python、model 与 knob 选项。
- CEO persona：`You are the CEO of an interactive refactory workflow, powered by the {{model}} model.`，并把工作目录追加为 persona 后缀。
- [workflow spine](../../workflow/workflow-spine/README.zh.md) 行：前缀 `wf`、`executeCommands: true`、`maxIterations: 500`、一小时的节点命令超时，以及让 CEO 只保留 `wf_*` 工具的 `ceoDeny` 列表。
- worker-thread code runtime，使节点的 Python op 运行时不会阻塞 agent 循环。
- `DataNode` 迭代所用的条目加载器：spine 运行 `python -m refactory_dsh.items list|begin|end`，因此数据驱动的模式在本 profile 上选出的条目，与在 walker 上选出的完全相同。
- runner：驱动单个 CEO agent 直至静止，并报告本次运行。
- 终端应答方（terminal answerer）：在终端上把一个 `user` gate 呈现给人，并把其 verdict 写入本次运行的意图 ledger。没有终端时（stdin 被管道或捕获），它不作任何回答，因此无头运行会拒绝属于人的 gate，而不是编造一个 verdict。

-----

<a id="understand-the-implementation"></a>
## 理解实现

<details>
<summary>实现内部细节——点击展开</summary>

本 bundle 是一份叠加在 `dsh-base` 之上的 patch 文档，外加三个运行时插件。patch 会重述被定位行的整份 config——persona 模板与 tools 模式——并插入五行：worker-thread code runtime、`graph-startup` 提供方、workflow spine、终端应答方，以及 `graph-runner` glue。`graph-startup` 解析命令行、加载模式的图、解析工作目录与节点命令环境，并发布 `graphStartup`；spine 行、应答方与 runner 都注入该服务，因此一次解析同时参数化它们，且图在启动时被内联。runner 启动单个 CEO agent，把驱动提示作为用户消息发送，等待其静止，刷新 session，从工作目录中读取模式的 state 文件里的 `best_score`，然后退出。确定性节点经由 `dsh-base` 挂载的 `shell` 执行器在进程内运行，并各自按最后一行非空 stdout 路由 gate。`DataNode` 的子图会为每个条目迭代一次：spine 先问加载器有多少条目，在每趟 pass 前物化一个、之后收尾一个，并像递交图中其余节点那样递交该趟的节点。`user` gate 不会被运行：spine 询问应答方，后者在终端提示并通过 `python -m refactory_dsh.console steer --verdict` 记录 verdict，或重放 ledger 中已有的 verdict。

### 源码映射

| 文件 | 作用 |
|---|---|
| [`cordis.patch.yml`](cordis.patch.yml) | 该层：persona 与 tools 覆盖，随后是四行插入 |
| [`src/startup.ts`](src/startup.ts) | `graph-startup` 提供方：命令行、图加载、工作目录、命令环境 |
| [`src/index.ts`](src/index.ts) | `graph-runner` glue：单个 CEO agent、静止、session 刷新与 JSON 报告 |

</details>

-----

<a id="further-exploration"></a>
## 进一步探索

当你想了解这一层所组合的各个部分、而不只是本次运行时，请阅读以下页面。

- [dsh-base](../base/README.zh.md)——本层所修补的共享核心。
- [workflow-spine](../../workflow/workflow-spine/README.zh.md)——spine 行与 CEO 驱动的 `wf_*` 工具。
- [refactory glue](../../../refactory/README.zh.md)——节点命令所运行的 Python 模式、图与 op。
- [Bundle 包地图](../README.zh.md)——其他可安装的 profile 层。

-----

<a id="model-experience"></a>
## 模型体验

### CEO persona 与工作流工具

#### 模型看到什么

本层替换 base persona：前缀为 `You are the CEO of an interactive refactory workflow, powered by the {{model}} model.`，后缀以 `Your working directory is {{cwd}}.` 说明工作目录。插入的 workflow-spine 行注册 `wf_*` 工具及其 prompt 段落，而正是该段落与 `ceoDeny` 列表让 CEO 只保留 `wf_*` 工具、委派、ask-user 与只读工具。code runtime 与两个 graph 插件自身不注册任何面向模型的文本。

#### Token 影响

每次请求一个 persona 前缀与后缀，外加六个 `wf_*` 工具定义及其 prompt 段落；图自身的节点内容通过 CEO 读到的工具结果到达模型，因此某个模式的 prompt 模板只在该节点到期时消耗一次 token。

#### KV Cache 影响

persona、工具定义与 prompt 段落在整个 profile 生命周期内固定不变，因此位于被缓存的请求前缀中。节点视图若指向下一个节点，只改变消息后缀；而图在启动时被内联，因此一次运行中没有任何消息会重写该前缀。

## 已知限制与延期工作

<a id="known-limitations-and-deferred-work"></a>

这些限制界定了当前的无头运行。它们是当前的包约束，不是模式编写方面的待办清单。

- **`graph` profile 不会自动初始化**——在 `dsh plugin --profile graph add` 创建该 profile 之前，`dsh --profile graph` 会带着创建提示停止；而 `@deepseek-ai/dsh-graph` 不在 npm registry 中，因此 `add` 需要指向检出目录的路径。
- **图在启动时被内联**——spine 读取 `ctx.graphStartup.graph`，因此在一次运行中途编辑 `<mode>.graph.json` 不会有任何效果；要拾取改动需重启运行。
- **节点命令不受限运行**——图命令是受信的 op，以完整的 sandbox 访问权限运行。`ceoDeny` 只约束 CEO 自己的工具列表，而 CEO 派生的子 agent 保留完整工具集。
- **`best_score` 从工作目录读取**——runner 优先取模式已知的 state 文件，否则取第一个带有数值 `best_score` 的 JSON 文件，因此把分数记录到别处的模式会报告 0。
- **每个进程一次运行**——runner 驱动单个 CEO agent 直至静止后退出；这个入口点没有交互式引导循环。
- **reloop 上限固定为 500**——`maxIterations` 是本 patch 的一个 config 值，而 `--budget` 只以 `SRF_BUDGET` 到达各个 op，因此忽略预算的 op 所属模式可以跑满该上限。

<a id="dev-note"></a>
### 开发备注

<details>
<summary>维护者的工作上下文——点击展开</summary>

None.

</details>

**运行时不变式：** 不发布运行时不变式伴生入口，因为这一层携带的是一份 patch 文档与一个一次性 runner，其唯一共享状态是 `graphStartup` 服务；每个插入行的行为与不变量都归该行的包所有。
