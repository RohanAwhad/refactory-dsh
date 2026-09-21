# Agent Note: The `refactory` tool is the Web view's whole surface

Status: implemented

[English](2026-09-21-refactory-web-surface.md) | 中文

## Problem

Web 视图能运行外层循环并读取一次运行，除此之外什么也做不了。创作一个模式、管理一个包、引导一次运行、读取它的赢家，这些本来都只是 Python 入口点，没有任何 tool 动作，因此本产品存在的两个目的——创建一个模式、搜索一个模式——在页面上完全无法触及。该 tool 只暴露了 console 十三个动词中的八个。

另有一件独立的事：在 Web 视图里选择一个 workspace 没有任何反应。picker 本身没问题；**是 session 创建失败了**。`ceo` preset 挂载了 `tool-refactory`，而 `packages/bundle/web-app` 从未依赖 `@deepseek-ai/dsh-tool-refactory`，于是 profile 被修复的 `node_modules` 镜像里没有这个包，每个 session 都以 *preset "ceo" failed to mount: row "tool-refactory" names a plugin that cannot be resolved* 停下。客户端捕获了这次拒绝并把待定 workspace 设回原值，因此点击看起来是惰性的，浏览器控制台也保持干净。

## Decision

`refactory` tool 为每个 console 动词暴露一个 action，而 console 也补齐了系统其余部分所需的动词。`strategies` 按名字返回三个 level-2 seam，并带上各自类 docstring 的首行描述，直接从注册表读取，因此 optimizer、strategy 与 evaluator 是从一份有描述的目录中挑选的。`registry`、`create`、`edit` 与 `design` 转发给已经拥有那项工作的入口点并返回其 JSON；不是 JSON 的一行会作为文本保留而不是被丢弃；`create` 还额外携带它写下的 design review，使所选方案与 review 拒绝过什么可见。`steer`、`best` 与 `adopt` 补全了 console 的读取面。客户端卡片依据 console 的 JSON 渲染每个 action，因此对话是这次运行的一个视图，而不是关于这次运行的一句话的视图。

`packages/bundle/web-app` 声明了 `@deepseek-ai/dsh-tool-refactory`，而 `packages/preset/agent-presets/tests/preset-plugin-dependencies.spec.ts` 断言：已发布 preset 所命名的每个插件，都能从 profile 镜像所自愈的那棵 CLI 包传递依赖闭包中抵达——正是这道检查本可以抓住那个 workspace 缺陷。

空白 session 的 hero 按决定是纯文本的。仓库中唯一的图稿是位于不透明背景上的 5.26:1 lockup，hero 的下一个 mark 列宽 34px，而它旁边那一列已经是文字商标；mark 槽位保持被占用且为空，这样 harness 自己的鲸鱼就不会出现在它的位置上。

## Alternatives considered

**做一个带自有 host RPC seam 的客户端面板。** 被推迟而非拒绝，并已作为后续项归档：一个旁边列出 strategy 描述的 picker，才是「命名选择」的正确归宿，而它需要 Service Definition、provider、Typert remote 与客户端注册。tool 面向模型，因此客户端无法调用它，而搭建那条 seam 比它所替代的表面更大。

**在客户端再放一份目录副本。** 拒绝：console 的 JSON 是两个表面共同读取的契约，`tool-refactory/src/console.ts` 是唯一的边界；平行存储会在任一侧改动的瞬间与 tool 漂移。

**为每种产物（report.md、gradient、rejection 直方图）做专门卡片。** 目前拒绝：`artifacts` 已经返回每一项及其 media type 与内容，通用卡片即可渲染它们，因此 markdown 卡片是同一份 JSON 之上的后续视图，而不是新数据。

**解除 hero 槽位的占用，让 harness 自己的鲸鱼渲染。** 拒绝：页面会带有第二个品牌。

## Consequences

- 每个 action 都 shell out 到与人工运行相同的 `python -m refactory_dsh.console`，因此从页面启动的运行就是 shell 会启动的那次运行，产物相同、报告相同。
- 「命名选择」是模型读取的有描述目录，不是下拉控件；picker 是未完成的工作，而不是动词上的缺口。
- 移除该依赖时，preset 依赖闸门会失败并指名 `ceo: @deepseek-ai/dsh-tool-refactory`，因此那个 workspace 缺陷无法悄悄回归。
- tool 的 action 枚举及其对模型可见的描述会随 console 增长；客户端卡片的 action 列表是第二份列表，必须与之同步，而卡片测试覆盖每个新增 action。
- `create`、`edit` 与 `design` 由模型驱动且很慢；一次调用会阻塞数分钟，这就是 tool 超时为十分钟、且它们启动的运行不是后台任务的原因。
