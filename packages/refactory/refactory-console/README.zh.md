---
description: "以 Typert Remote 服务暴露的 refactory 控制台：Web 选择器所需的各种「有描述的可选项」——模式、策略与包——以及启动一次运行、读取其状态并停止它，面向选择器的使用者与维护者。"
kind: "package-reference"
---

# @deepseek-ai/dsh-refactory-console

[English](README.md) | 中文

## 概述

`dsh-refactory-console` 把 refactory 控制台以 Typert Remote 服务的形式暴露出来：它列出一次运行可以据以开始的各种可选项——本检出能启动的模式、运行的设置要在其间选择的外层循环、优化器与评估器，以及注册表已知的模式包——并负责启动、读取与停止一次运行。它是 Web 选择器的 Host 半边：持有与面向模型的 `tool-refactory` 同一个 `Console`，挂载同一套部署设置，运行同一条 `python -m refactory_dsh.console` 调用，所以人在面板里做的选择就是模型经工具做的选择。它的代价是一个配置好的 Python 解释器和一个包含 `refactory/` 的检出；因为它不重新实现任何东西，它报告的可选项与拒绝理由就是控制台本身的。

## 目录

- [它暴露什么](#what-it-exposes)
- [有描述的可选项](#the-described-choices)
- [拒绝](#refusals)
- [模型体验](#model-experience)
- [已知限制与暂缓事项](#known-limitations-and-deferred-work)
- [开发备注](#dev-note)

-----

<a id="what-it-exposes"></a>
## 它暴露什么

把本包挂载到提供 `subprocess` 与 `typert` 服务的组装里；它注册 `ctx.refactoryConsole` 与 `refactoryConsole` Remote 命名空间。

| 方法 | 回答 |
|---|---|
| `modes` | 目录提供的每个模式，各带运行它的动作（`action`）、是否计分、其包是否启用 |
| `strategies` | `outer_loops`、`optimizers`、`evaluators`，各是一组带自我描述的有名选项 |
| `runs` | 运行根下每次运行，最新在前 |
| `packages` | 注册表已知的每个包，及其声明的模式 |
| `start` | 控制台记录的启动信息：运行 id、根目录、pid、设置与日志 |
| `status` | 运行状态、新的进度事件、下次读取的偏移、计数器与最好成绩 |
| `stop` | 运行的进程是否接受了停止信号 |
| `setPackageEnabled`、`removePackage`、`importPackage` | 变更之后当下的包目录 |

### 配置

四项部署设置与面向模型的工具相同，由同一张默认值表声明，因此上限只能在一个地方改动。

| 字段 | 默认值 | 含义 |
|---|---|---|
| `repoRoot` | 进程工作目录 | 包含 `refactory/` 与 `.venv/` 的检出；也是每次控制台调用的工作目录 |
| `pythonBin` | `<repoRoot>/.venv/bin` | 存放能 import `refactory_dsh` 的 `python` 的目录 |
| `timeoutMs` | `600000`（十分钟） | 单次控制台调用的挂钟上限 |
| `collectBytes` | `4194304`（4 MiB） | 单流收集上限；更长的流保留尾部 |

<a id="the-described-choices"></a>
## 有描述的可选项

本服务返回的每个列表都是带描述的可选项，选择器因此不必让人键入一个它本可提供名字的值。`modes` 读控制台自己的目录，只保留选择者会读的字段——标识、描述、运行该模式的动作、是否可启动——而控制台条目里其余部分，包括模式的旋钮、节点与端口，都原样留在线上。`strategies` 读三个二级注册表，`packages` 读注册表自己的目录，把一个包的模式归到它的名下，并报告每个模式携带的启用状态。

不声明任何模式的包报告 `enabled: null` 而非猜测：该状态由每个模式携带，空包则不指名任何状态。

<a id="refusals"></a>
## 拒绝

每个方法以一个 `RemoteError` 作答，选择器因此可以说出该改做什么，而不是展示一个失败的进程。

| 代码 | 何时 | 调用者应当做什么 |
|---|---|---|
| `gateway/bad-request` | 必须由选择器提供的地址为空 | 提供模式、运行或包名 |
| `gateway/cancelled` | 调用者的信号已被中止 | 不必做什么；该 tab 已经消失 |
| `refactory/unknown-mode` | `start` 被要求启动目录里没有的模式 | 读取 `modes` 并从中选择一个 |
| `refactory/authoring-mode` | `start` 被要求启动 `action` 为 `create`、`edit` 或 `design` 的模式 | 改用那个动作；details 里带着它 |
| `refactory/package-disabled` | `start` 被要求启动已停用包里的模式 | 先启用该包 |
| `refactory/console-failed` | 控制台拒绝，或其回答形状不对 | 读消息；`details.argv` 可手工复现该调用 |

<a id="model-experience"></a>
## 模型体验

无：本包不注册工具、提示词小节或任何模型可见输入；它是 Web 选择器的 Host 半边，它返回的一切都经 Typert Remote 送到浏览器。

#### KV Cache 影响

无；没有任何 Remote 回答会进入模型请求。

## 已知限制与暂缓事项

<a id="known-limitations-and-deferred-work"></a>

- **必须有能 import `refactory_dsh` 的 Python 解释器** —— 每个方法都 shell 出 `<pythonBin>/python -m refactory_dsh.console`，所以解释器、虚拟环境或检出缺失时调用会失败，而不会降级。
- **读取面就是控制台的面** —— 模式、策略、包与状态正是 `refactory_dsh/console.py` 暴露的内容；本服务一点也不改写它。
- **控制台的拒绝按控制台的原话报告** —— 控制台用英文写的消息原样到达浏览器，只有本服务自己抛出的代码才有本地化文案。
- **没有任何流式推送** —— `status` 是带 `after` 偏移的轮询；最后一帧之后继续运行的部分不会被推送到任何地方。
- **一次调用一个进程** —— 本服务不跨调用保存状态，所以每次读取都会启动一个新的解释器。

<a id="dev-note"></a>
### 开发备注

<details>
<summary>维护者的工作上下文——点击展开</summary>

无。

</details>

**运行时不变式：** 不发布伴随文件。本服务只持有一个 `Console`，自身没有持久状态：不变式所能断言的关系，要么属于平台（子进程会退出），要么属于循环（一个运行目录回放出同一批个体）。
