---
description: "refactory 组地图：驱动 Python 外循环优化器的模型侧 refactory 工具，供浏览该家族的用户与维护者阅读。"
kind: "package-group"
---

# packages/refactory

[English](README.md) | 中文

## 概述

refactory 组承载 refactory 外循环运行的 DeepSeek Harness 一侧：该优化器对 Python 模式提出编辑、评估这些编辑，并保留能提升得分的部分。它的两个包是同一控制台在 DSH 侧的两个读者：`tool-refactory` 注册模型侧的 `refactory` 工具，用它列出检出中可运行的模式、在独立进程中启动一次运行、观察该运行，并读回其排行榜、候选与产物；`refactory-console` 把同一控制台以 Typert Remote 暴露为 Web 选择器提供的那些「有描述的可选项」。一条 preset 行挂载该工具，Web 视图会把每次调用渲染成运行卡片，而不是通用工具行。运行本身是本组之外的 Python 程序：本组负责跨越进程边界的调用及其返回的 JSON，运行目录内部的一切则归循环自己的仓库所有。

## 目录

- [包](#packages)
- [相关文档](#related-documentation)
- [开发备注](#dev-note)

-----

<a id="packages"></a>
## 包

`packages/refactory/` 下的每个包都挂载进某个组合；本组自身没有运行时。

| 包 | 职责 | ctx 键 |
|---|---|---|
| [`tool-refactory`](tool-refactory/README.zh.md) | 让模型与 Web 视图运行并读取 refactory 外循环：列出模式、启动运行、观察运行，并读取其候选、差异与产物 | `ctx.tools`、`ctx.subprocess`、`ctx.systemPrompt` |
| [`refactory-console`](refactory-console/README.zh.md) | 以 Typert Remote 暴露控制台：Web 选择器所需的各种「有描述的可选项」——模式、策略与包——以及启动、读取与停止一次运行 | `ctx.refactoryConsole` |

-----

<a id="related-documentation"></a>
## 相关文档

- [Refactory 子系统](../../docs/subsystems/refactory.zh.md)——控制台进程边界、选择器提供的「有描述的可选项」，以及 `refactoryConsole` Remote 的拒绝。
- [构建完成的 refactory 系统](../../refactory/docs/architecture.md)——循环的各个步骤、状态模型、校验器规则与编辑代数。[决策记录](../../refactory/docs/plans/pythonic-modes.md) 说明它为何是这个形状；`refactory/` 项目自己的 `README.md` 记录其安装路径与启动器。
- [生成的工具目录](../../docs/tool-catalog.zh.md)——模型所接收工具的全量参考，由其源码生成。
- [ceo preset](../preset/agent-presets/presets/ceo/agent.cordis.yml)——挂载 `tool-refactory` 的那一行。

-----

<a id="dev-note"></a>
## 开发备注

<details>
<summary>维护者的工作上下文——点击展开</summary>

无。

</details>
