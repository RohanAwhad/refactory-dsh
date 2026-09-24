# Agent Note: The Web picker reads the console through one Remote service

Status: implemented

[English](2026-09-24-the-web-picker-reads-the-console-over-remote.md) | 中文

## Problem

Issue #49 要求两种抵达 refactory 运行的方式：人用语言请 CEO 去做，以及人在 Web 上直接选。对话那一半已作为 `refactory` 工具交付。选择器这一半此前无从存在：控制台在 DSH 侧的所有读者都是工具，而工具要靠模型调用抵达，于是按下按钮的人没有东西可按。控制台自己的回答正是正确的可选项集合——模式携带运行它的 `action`，二级注册表携带带自有描述的名字，注册表携带每个包的启用状态——但这些都没有跨越 Host 与浏览器之间的边界。

## Decision

**第二个 Host 读者，形态是 Typert Remote 服务。** `@deepseek-ai/dsh-refactory-console` 继承 `TypertRemoteService`，注入 `subprocess` 与 `typert`，并注册 `ctx.refactoryConsole`。它的方法就是选择器的词汇：`modes`、`strategies`、`runs`、`packages`、`start`、`run`、`status`、`stop`，以及三个包变更方法，每个都以当下的注册表目录作答。

**一个进程边界，两个调用者。** 服务持有与工具同一个 `Console`，来自 `@deepseek-ai/dsh-tool-refactory/console`，并经共享的 `resolveConsoleSettings` 解析四项部署设置，因此工具与服务不会在检出位置、解释器或时限上产生分歧。`startInvocation`、`runInvocation` 与 `registryInvocation` 出于同样理由移入该模块：控制台的各种旗标只有一个归属。工具保留自己的 `Config` schema 与自己的字段，因为 schemastery 唯一的对象组合方式是会原地修改的 `set`；共享的默认值表（`CONSOLE_DEFAULTS`）是让各项上限只有一份来源的东西。

**「有描述的可选项」就是控制台的，不改写。** 目录的 `action` 字段决定运行一个模式的动作，所以 `start` 读它而不是重新推导：创作模式在任何进程启动之前就被拒绝，并附上运行它的动作。不声明任何模式的包报告 `enabled: null` 而非猜测，因为注册表把该状态报告在它的每个模式上。

**浏览器半边是一个右侧面板 tab 类型。** `@deepseek-ai/dsh-client-ui-refactory` 把 kind `refactory` 注册进 `ctx.sidebarRightTabs`，并把正文注册进以键寻址的 `sidebar.right.pane.tab` 坑位——与 `ui-sidebar-files` 相同的两段式路径。正文只画有描述的可选项：模式、策略、优化器与评估器都是选出来的，绝不键入；所选模式自己声明的旋钮也由声明画出——声明了取值范围的旋钮是一个选择，数字受其声明区间的约束，只有开放的 `str` 旋钮才需要键入。另一个自由文本字段是导入来源，那是任何目录都提供不了的。

## Consequences

该服务是同一份 JSON 契约的第三个读者，面板渲染的就是工具返回的内容。人在面板里选一个模式、模型调用 `start`，运行的是同一个控制台动词、同一组旗标；控制台的拒绝在两处读起来也一样——除了服务自己抛出的三个代码，面板对一切都显示控制台自己的消息。

状态按需读取：面板在运行启动时读一帧，读者再次要求时再读一帧。会自行更新的运行视图需要一个选择器尚未拥有的轮询节奏，Host 的 `runs` 列表也还没有界面；两者都记在面板的已知限制里。

子系统多了一个页面：[Refactory](../../../../docs/subsystems/refactory.zh.md) 拥有该 seam、那些有描述的可选项与拒绝代码，`ctx.refactoryConsole` 的参考也在那里生成。

## Alternatives considered

**把控制台藏在现有工具后面。** 一个调用该工具的 Remote 方法会让人手的手势变成一次工具调用，而没有模型轮次的工具调用没有调用者：面板将不得不伪造一次工具调用并读取面向模型的结果。控制台不是某个工具的私有实现；它是带 JSON 契约的进程边界，服务直接读它。

**用 TypeScript 重新实现控制台的读取。** 解析运行目录、读取进度文件、重新推导哪些模式可启动，都是对控制台已有答案的第二个回答，而循环布局第一次变化时两个回答就会分岔。

**把控制台从 CEO preset 而非 host 组装里暴露。** preset 是按会话挂载的，其服务并不是 Typert gateway 枚举的对象；host 组装是所有其他 Remote 命名空间挂载的地方，而 `web-app` patch 就是这个命名空间挂载的地方。

**让面板在浏览器里拒绝不可启动的模式。** 面板将因此拥有控制台已经陈述的规则的一份副本，而在两次读取之间变得不可启动的模式会被错的那一半拒绝。面板提供每个有描述的模式；由 Host 拒绝，并指名该用哪个动作。
