---
description: "dsh Web 客户端右侧 Sidebar 的 refactory 选择器 tab 类型：控制台提供的「有描述的可选项」——模式、策略与包——据其启动一次运行，并读取该运行的状态。"
kind: "package-reference"
---

# @deepseek-ai/dsh-client-ui-refactory

[English](README.md) | 中文

## 概述

右侧 Sidebar 的选择器 tab 类型：从控制台目录里选一个模式，选好启动一次运行要用的外层循环、优化器与评估器，管理模式包，启动运行，并读取它走到了哪里。它是从引导页进入的页类型，不认领任何地址。它提供的每个选项都是 Host 经 `refactoryConsole` Remote 命名空间列出的、带描述的选项——模式、策略与包都绝不靠键入——唯一目录提供不了的，是导入包的来源，那也是它画出的唯一自由文本字段。`ui-sidebar-right` 里没有任何东西认识本包。

## 目录

- [注册了什么](#what-it-registers)
- [选择器](#the-picker)
- [模型体验](#model-experience)
- [已知限制与暂缓事项](#known-limitations-and-deferred-work)
- [开发备注](#dev-note)

-----

<a id="what-it-registers"></a>
## 注册了什么

- **类型**：`ctx.sidebarRightTabs.register(...)`，kind 为 `refactory`，id 为 `@deepseek-ai/dsh-client-ui-refactory`，档位 `builtin`，没有 patterns，另有一个打开该类型的引导页入口（order 10，标题取自 `sidebarRefactory` 命名空间）。
- **正文**：以该 id 为键的 `sidebar.right.pane.tab` 坑位：选择器本身，以及它唯一的控件、位于标题行右端的重新读取。

`src/client/` 下六个源文件：`definition.ts`（类型是什么）、`store.ts`（它保存什么）、`face.ts`（它如何发问，含 Remote 绑定）、`RefactoryBody.tsx`（它画什么，含失败行辅助函数）、`locales.ts`（它说什么）、`index.ts`（接线）。

它的 Host 半边是 [`@deepseek-ai/dsh-refactory-console`](../../refactory/refactory-console/README.zh.md)，运行与面向模型的工具同一条 `python -m refactory_dsh.console` 调用。

<a id="the-picker"></a>
## 选择器

挂载时经 `remote.refactoryConsole` 一次读三份列表：`modes`、`strategies` 与 `packages`。它们作为一次读取保存——读取中、就绪、或带着 Host 的理由失败——因为一个提供了模式却没有可用策略的选择器，提供的是无法使用的选项。

| 区块 | 做什么 |
|---|---|
| 开始一次运行 | 从目录里选一个模式（每个选项就是它自己的标识），再从三个注册表里选外层循环、优化器与评估器，各带实现给自己的描述。选定模式后按钮才出现；该模式能否运行由 Host 判定，拒绝会作为提示行回来。 |
| 运行状态 | 启动之后显示运行的根目录、状态、最好成绩（或明说没有）与评估次数，并有从游标读取下一帧的手势，以及请运行停止的手势。已结束的运行不提供停止。 |
| 模式包 | 每个包及其声明的模式数与启用状态；注册表报告了状态时给出启用/停用控件，另有移除控件，以及一个接收目录或 git 地址与可选名字的导入表单。 |

拒绝只落在一个地方：Host 精确指名的三个代码——未知模式、创作模式、已停用的包——有自己的文案，其余失败经 `error.failed` 显示控制台自己的消息。

状态保存在本类型自己的 store 里，按 tab id 分桶：读到的可选项、选中的模式、选中的三个名字、正在读取的运行及其最后一帧、最后一条提示，以及是否有包变更在途中。owner 的 `signal` 结束一个桶：中止时该 tab 被遗忘，之后才落定的读取或变更不会写入任何东西。

<a id="model-experience"></a>
## 模型体验

无：本包在浏览器里画选择器，不注册任何模型可见的东西。

#### KV Cache 影响

无；没有任何 Remote 回答或 store 写入会进入模型请求。

## 已知限制与暂缓事项

<a id="known-limitations-and-deferred-work"></a>

- **状态按需读取。** 运行中的运行不会自己更新：读者按重新读取（或启动一次运行，那会读第一帧）。实时轮询需要一个本选择器尚未拥有的节奏。
- **导入来源是自由文本** —— 目录或 git 地址是注册表自己的目录唯一提供不了的选择，所以导入表单是唯一的键入字段。
- **Host 的消息按 Host 的原样显示** —— 控制台的拒绝经本地化的 `error.failed` 行以控制台的英文到达读者；只有三个「有描述的选项」代码才有本包自己的文案。
- **没有运行历史。** 选择器只读取它启动的那次运行；`runs` 列表存在于 Host 上，尚无界面提供它。
- **包变更会重读整份包目录** —— 启用、停用、移除与导入都以当下的包列表作答，模式目录不会重读，所以新导入包的模式要重新读取后才出现。

<a id="dev-note"></a>
### 开发备注

<details>
<summary>维护者的工作上下文——点击展开</summary>

无。

</details>

**运行时不变式：** 不发布伴随文件。选择器唯一的运行时状态是每个 tab 一个 Slot store，由拥有它的正文写入，并在该 tab 的中止信号上被遗忘；没有第二处观察可与它比对。
