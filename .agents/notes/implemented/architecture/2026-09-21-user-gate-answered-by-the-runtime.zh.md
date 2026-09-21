# Agent Note: A user gate is answered by the runtime, not the model

Status: implemented

[English](2026-09-21-user-gate-answered-by-the-runtime.md) | 中文

## Problem

`Node.gate_kind` 把 gate 类型化为 `fn`、`user` 或 `agent`，而 spine 会把 `user` gate 交给驱动 agent，并在 prompt 指令中要求它用 `ask_user_question` 呈现。没有任何机制强制这一呈现：`wf_verdict` 会路由模型传入的任何标签。因此，一次带有人工 gate 的模式的无头运行会凭空编造 verdict——`review_pack/review` 在无人被询问的情况下报告了 *approved by the person*——而编造的 verdict 在运行的产物中与真人给出的无法区分。Python walker 会拒绝同一个 gate，因此两个运行时对一次运行做过什么给出了不一致的结论。

## Decision

spine 自己询问人。对于 `evaluator_type` 为 `user` 的 gate，`wf_verdict` 会以该 gate 自身的 verdict 标签作为选项调用已组合的 `user-questions` 应答方，路由人所给出的答复，并忽略模型的 `verdict` 参数。当组合中没有挂载 user-questions 通道，或没有任何应答方接受该请求时，工具会抛出指名该 gate 的消息，运行就此停下，而不是路由一个编造的 verdict。CEO 的 prompt 段落说明由运行时发问，且模型不得替人回答。

插件把自己的 cursor 状态写入 `<workdir>/.refactory/spine-status.json`，取值为 `done`、`ready`、`failed` 或 `refused`。因此，一次停在 gate 处的运行会成为指名该 gate 的失败报告，而不是一次得分为 0 的运行；`refactory_dsh.dsh_runtime.evaluate_on_dsh` 会读取该文件并以其中的原因判定评估失败。

## How a person answers

`graph` profile 挂载一个终端应答方（`packages/bundle/graph/src/answerer.ts`）。它在终端上显示该 gate 及其选项，通过 `python -m refactory_dsh.console steer --verdict <gate>=<value>` 记录选择，并重放本次运行的 ledger 中已有的 verdict 而不再询问，因此续跑就是同一次运行。被捕获或管道化的 stdin 不是人：此时应答方改为委派，而 spine 会拒绝。`python -m refactory_dsh.interactive <package>/<mode> --intent "<text>"` 会折入 ledger 地 lowers 该模式，并在接上终端的情况下启动那个 profile。

ledger 也是 walker 一侧的续跑记录：`Ledger.verdict` 追加答复，`execute` 把 `Ledger.verdicts()` 合并进 `RunContext.verdicts`，而调用方显式传入的 verdict 仍然优先。

## Alternatives considered

**保留模型的 `ask_user_question` 并相信它确实被调用。** 拒绝：一个能在不询问的情况下路由 gate 的模型可以编造答案，而产物无法区分二者。那正是本缺陷。

**观察一次 `user-questions` 答复，并在路由前要求已有一条答复。** spine 本可以在作用域 waterfall 上监听，在见到答复之前拒绝 `wf_verdict`。拒绝，因为它更弱：该观察并不绑定到具体 gate，任何问题的答复都能解锁它，而且模型仍可能路由与人所选不同的标签。

**把人工 gate 降为读取 verdicts 文件的 `fn` gate。** 拒绝：该文件任何有文件系统访问权的东西都能写，缺失文件还必须与空答复区分开，而且运行时会失去这个 kind 存在的唯一意义——为人暂停，而不是替人决定。

**只把 verdict 记录进 ledger，让 gate 去读它。** 这正是续跑的工作方式，并且予以保留，但它没有回答第一个问题：第一次答复仍必须来自人，因此它无法取代询问。

**让 CEO 呈现 gate，并由 spine 校验答案是否匹配。** 拒绝：这一校验只能在模型已经选定一个标签之后，把答复绑定到该标签；因此只要人的答复碰巧匹配，未经询问的标签仍会被路由。

## Consequences

- 两个运行时现在都会拒绝无人答复的 gate，并且都会指名它。DSH 的拒绝信息为 `the graph did not finish: gate approve is answered by a person and could not be asked: no user-questions answerer accepted the request`。
- CEO 的 prompt 段落对模型可见且已改动，因此 spine README 的 Model Experience 文本随之更新。
- spine 取得对 `@deepseek-ai/dsh-user-questions` 的 peer 依赖。每个挂载 spine 的组合要么作答、要么拒绝；`dsh-base` 会挂载该服务，因此实际路径是「缺少应答方」式的拒绝。
- `refactory_packages/review_pack` 与 `refactory_dsh/interactive.py` 的存在，使这条路径有一个模式和一个入口可以运行；此前没有任何已发布模式声明过 `user` gate。
- `<workdir>/.refactory/spine-status.json` 是一种新的磁盘格式，且 Python 侧依赖它的四个状态词。
- 终端应答方通过 Python console 记录，而不是自己写 ledger，因此 ledger 的格式仍归 `refactory_dsh/steering.py` 所有。

## Related

[The workflow spine's executable graph vocabulary](2026-09-09-workflow-spine-named-gate-outcomes.zh.md) 决定 gate 可以路由哪些标签；本条决定由谁回答 gate。
