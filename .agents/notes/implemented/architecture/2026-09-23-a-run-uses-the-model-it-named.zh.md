# Agent Note: A run's model is the one the invocation named

Status: implemented

[English](2026-09-23-a-run-uses-the-model-it-named.md) | 中文

## Problem

选模型这件事在三个地方同时不工作，而且每一处都遮住了另外两处。

**运行时的 `--model` 是空的。** 两个命令行 bundle 都会在它们创建的 agent 上装模型选择中间件，而两个都用 `agentDefaultModel.currentSelection()`——用户设置里的默认值——来构造它。那个中间件在委派之后会用自己 `current` 重写每一个请求，所以 `agentOptions` 里的 provider 与 model 过一刻就被丢掉了。graph profile 一直都能解析 `--provider`/`--model`；一次点名了某个模型的运行，用的是另一个模型，而且什么都不说。

**一次性 profile 根本没有这个开关。** `dsh --profile headless --model X` 得到的是 `error: unknown option '--model'`，这也正是每一次 `autoresearch` 提议都失败的原因：`DshModelClient` 从写下的第一天起就传这个开关。于是运行报告出一次「什么都没找到」的搜索，而那和一次真的什么都没找到的搜索长得一模一样。

**不存在的路由也不是错误。** provider 名字是未知的，请求照样被默认模型答了回来。以为自己在一个 provider 上的人其实在另一个上面，唯一的症状是回答看起来一样。

发现这件事的问题是「我该怎么配置模型？」——而当时诚实的回答是「改全局默认；按运行选还没接上」，这与 README 里 `optimize --model` 那一行说的不一样。

## Decision

**中间件的 `current` 就是生效的选择。** 决定一个请求走哪条路由的是 `installModelSelection`，所以两个 CLI bundle 现在都从这次调用来构造它：`config.provider || selection.provider`、`config.model || selection.model`，而继承来的推理强度只在模型没变时保留，因为强度属于带来它的那个模型。组合时的选择是回退，不是权威。

**一次性 profile 和 graph profile 一样接受 `--provider`/`--model`。** 在 startup provider 里解析，发布到 startup 服务，通过该行的 config 接到 runner，并用在它创建的 agent 上。什么都不写回设置，所以为一次运行选的模型不可能改变下一次。

**不存在的路由会响亮地失败。** 现在它会以 `NO_ADAPTER: no adapter registered for provider "…"` 结束运行并退出 1。悄悄用默认模型作答是两者中更糟的失败：它产出一个被归到错误模型名下的结果。

**refactory 在人能碰到的地方暴露这两个开关。** `refactory run`、`refactory ceo` 与 `refactory optimize` 接受 `--model` 与 `--provider`；创作与 loop 的模块入口在它们本来就有的 `--model` 旁边接受 `--provider`。设置的默认值仍是 `~/.dsh/settings.yaml` 里的 `agent-default-model`——由 Web GUI 的 Models 页面写，或手写——两个开关则是每次运行的。

## Consequences

模型可以按运行来选，从终端或 GUI 输入框都行，而目录仍然和从前一样是**建议性**的。`deepseek-v4-flash`、`deepseek-v4-pro` 与 `deepseek-v4-flash-vision-exp` 是选择器会摆出来的东西；DeepSeek 路由上没被列出的 id 照样发给 API；加一个 provider 或一个模型是对 `settings.yaml` 的一次编辑（key 放在凭据库里或环境里），不是改代码。

这是跑出来的：通过 `--patch` 指向本机服务器的 `openai-completions` 路由，用那台服务器的一段文字回答了一次 headless 运行，服务器记下的请求点名的正是这次运行所选的模型。把中间件那一行改回去，新写的 spec 就会断言到设置默认值并失败。未知的 provider 现在会退出 1 并点名那条路由。

adapter 自己的目录、`Models` 页面、`settings.yaml` 与凭据库现在一起写在 README 的模型一节里，因为「我能用哪些模型」以前要靠读三个包才能回答。

## Alternatives considered

**把选中的模型在运行期间写进 `agent-default-model`。** 否决：这让按运行的选择变成了全局设置，而写入与恢复之间的一次崩溃会把用户的默认值改掉，而用户并没有要求。

**用一个 adapter 会读的环境变量传模型。** 否决：选择是请求层的事实，另一个 profile 已经有文档化的按调用开关，而环境变量会为同一件事造出第二套机制，还不会出现在 `--help` 里。

**保留 `agentOptions` 作为唯一载体，教 agent loop 优先用它。** 否决：中间件存在的意义是让**之后**的一次选择在下一步生效，Web UI 依赖这一点——模型选择器会在会话中途更新 `selection.current`。缺陷在于 CLI bundle 用它播种了哪个选择，而不在中间件。

**让 profile 的目录成为添加模型的唯一途径。** 对 DeepSeek 路由否决：它的目录按设计只是建议，请求不受限制，在这里拒绝未列出的 id 是在加一条新限制，而不是在修缺陷。
