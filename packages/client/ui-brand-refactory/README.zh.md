---
description: "Web 客户端的 re:factory 品牌占据者：侧边栏与 hero 品牌槽位，外加 rose 主强调色；供 re:factory Web 表层的使用者与维护者阅读。"
kind: "package-reference"
---

# @deepseek-ai/dsh-client-ui-brand-refactory

[English](README.md) | 中文

## 概述

本包为 Web 客户端赋予 re:factory 身份：把侧边栏中的 DeepSeek 鲸鱼标记与 harness 字标替换为 re:factory 的 logo 与名称，用同一个 logo 填充空白会话的 hero 标记，并把主强调色调成 rose。在交付 re:factory 产品的部署中选择它；这些占据者纯粹是展示，因此这里没有任何东西会到达模型请求。本包自己不声明任何槽位——它占据由 `ui-sidebar` 与 `ui-conversation` 声明的三个槽位——因此移除本包会恢复这些声明方渲染的兜底内容。hero 占据者替换的是动画鲸鱼兜底，而不是在其上做装饰；并且同一个位图 logo 服务于两种配色方案。

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

把浏览器插件挂载到客户端名册中；侧边栏标记、侧边栏名称、hero 标记与 rose 强调色随即在下次页面加载时出现。

### 你会得到什么

- `sidebar.brand.mark` → re:factory logo（内联位图）。
- `sidebar.brand.name` → `refactory`。
- `conversation.hero.brand.mark` → re:factory logo（替换动画鲸鱼兜底）。
- `--dsw-alias-brand-primary` → rose（浅色 `#e11d48` / 深色 `#fb7185`），经由主题 token 覆盖层。

-----

<a id="understand-the-implementation"></a>
## 理解实现

<details>
<summary>实现内部细节——点击展开</summary>

浏览器半边是 [`src/client/index.ts`](src/client/index.ts)；节点半边是一个空的 Loader 座位，与其他品牌占据者一样。侧边栏标记与名称作为一整组感知声明的注册安装——嵌套的 `ctx.slots.inject()` 调用会等待侧边栏声明——因此无论本行在声明方之前还是之后激活，这一对都能工作，并在声明崩塌时一起撤销两个占据者。hero 标记是单个槽位注册，自带 34 px 的默认高度。rose 强调色是通过 `ctx.effect()` 注册的主题覆盖 token 层，因此会随 fiber 一起释放。logo 本身是 [`src/client/logo.ts`](src/client/logo.ts) 中的 base64 位图常量。

</details>

-----

<a id="further-exploration"></a>
## 进一步探索

当品牌表层还不够时，请阅读以下页面。它们从本包占据的槽位走向渲染这些槽位的外壳。

- [ui-sidebar](../ui-sidebar/README.zh.md)——声明 `sidebar.brand.mark` 与 `sidebar.brand.name` 并渲染它们的兜底内容。
- [ui-conversation](../ui-conversation/README.zh.md)——在 hero 中声明 `conversation.hero.brand.mark`。
- [ui-brand-official](../ui-brand-official/README.zh.md)——同样占据这些侧边栏槽位的上游实现，仅在 official 构建中生效。
- [Web 客户端架构](../../../.agents/notes/implemented/architecture/2026-07-19-gui-web-client-architecture.zh.md)——浏览器插件行如何加载并注册槽位。

-----

<a id="model-experience"></a>
## 模型体验

### 纯展示型品牌占据者

#### 模型看到什么

没有内容：本包占据 `sidebar.brand.mark`、`sidebar.brand.name` 与 `conversation.hero.brand.mark` 三个槽位并注册一个主题 token 覆盖，且不注册任何工具、prompt 段落或会话事件，因此模型请求中没有任何字节发生变化。

#### Token 影响

无：这些占据者只渲染浏览器外壳，从不进入请求组装。

#### KV Cache 影响

无：本包不添加任何 prompt 内容，因此无论是否挂载它，被缓存的请求前缀都完全相同。

## 已知限制与延期工作

<a id="known-limitations-and-deferred-work"></a>

这些限制界定了 re:factory 品牌表层的行为。它们是当前的包约束，不是品牌设计对比或待办清单。

- **logo 不跟随配色方案**——本包从不在上游的 `logo-dark`/`logo-light` 变体之间切换，因此一个位图同时服务两种方案。
- **logo 是内联的降采样**自 `remote-factory/docs/assets/refactory_logo.png`——若上游资源有变动，请刷新 `src/client/logo.ts` 中的 base64。
- **字标是文本，而不是位图**——`sidebar.brand.name` 把 `refactory` 渲染为带样式的文本，以便继承主题的标签颜色并清晰缩放。

<a id="dev-note"></a>
### 开发备注

<details>
<summary>维护者的工作上下文——点击展开</summary>

None.

</details>

**运行时不变式：** 不发布运行时不变式伴生入口，因为本包不保留任何可变状态；它的三个槽位占据者与主题 token 覆盖都随浏览器 fiber 由注册表释放。
