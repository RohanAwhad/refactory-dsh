---
description: "交互式工作流运行器：一个按会话持有的 spine，加上按节点遍历 refactory 工作流图的 wf_* 工具；供 CEO 驱动的工作流运行的使用者与维护者阅读。"
kind: "package-reference"
---

# `@deepseek-ai/dsh-workflow-spine`

[English](README.md) | 中文

## 概述

本包把一张 refactory 工作流图（`Workflow.to_dict()` 产出的 JSON）变成一次可实时引导的运行：由驱动 agent 逐步走完，却从不自行决定下一步做什么。一个按会话持有的 `Spine` 拥有当前位置（“下一步是什么”）、gate 路由、迭代上限、fork/join 屏障、director 展开与图编辑，并通过 `wf_*` 工具把这些状态暴露出来。在当前节点完成之前，spine 拒绝交出下一个节点，因此一次运行无法跳过、重排或绕过检查点。当 `executeCommands` 打开且挂载了 `shell` 执行器时，确定性节点——`FnNode` 与 `Study` 的命令，以及 `fn` gate 求值器——可以在进程内运行。主要代价是：图必须在加载时通过校验，而且 CEO 自身的编码工具被禁用，因此节点工作只能发生在它派生出的子 agent 内部。

## 目录

- [为什么](#why)
- [工具](#tools)
- [配置](#config)
- [上游词汇](#upstream-vocabulary)
- [确定性节点](#deterministic-nodes)
- [Node → DSH 映射](#node-dsh-mapping)
- [模型体验](#model-experience)
- [已知限制与延期工作](#known-limitations-and-deferred-work)
- [开发备注](#dev-note)

-----

<a id="why"></a>
## 为什么

无头的 `workflow` 工具自上而下运行一个脚本然后返回。它无法为用户暂停，也无法在运行中途改变图。交互式运行器填补了这一空白：一个按会话持有的 `Spine` 拥有当前位置（“下一步是什么”）、gate 路由、迭代上限、fork/join 屏障、director 展开与图编辑。agent 只负责为每个节点填充内容，因此它无法跳过、重排或绕过检查点。

-----

<a id="tools"></a>
## 工具（默认前缀 `wf`）

| 工具 | 用途 |
|------|---------|
| `wf_next` | 当前到期的节点（agent 任务 / 一次性 LLM / 用户 gate / fork），或 `done`。确定性节点会替你运行，且从不出现。 |
| `wf_done(node)` | 将一个非 gate 节点标记为完成并前进。 |
| `wf_verdict(gate, verdict, feedback?)` | 依据该节点视图列出的某个 `verdicts` 来路由一个 gate；reloop 受迭代上限约束。 |
| `wf_edit(node, skip\|unskip)` | 热移除 / 恢复一个节点（引导）。 |
| `wf_expand(director, entries)` | 依据 director 的计划热插入它的 N 个子 agent。 |
| `wf_state` | 完整的实时状态（cursor、completed、skipped、verdicts、log）。 |

agent 遵循的循环：`wf_next` → 只做那个节点（为 `AgentNode` 派生一个子 agent）→ `wf_done` / `wf_verdict` / `wf_expand`。在当前节点完成之前，spine 拒绝交出下一个节点，因此结构不会漂移。

-----

<a id="config"></a>
## 配置

```yaml
- id: workflow-spine
  name: '@deepseek-ai/dsh-workflow-spine'
  config:
    graph: { ... }          # a refactory Workflow.to_dict() object (or a graph.json)
    toolPrefix: wf          # optional
    maxIterations: 3        # optional reloop cap
    executeCommands: false  # optional: run deterministic nodes in-process
    workdir: /runs/abc      # optional: working directory for node commands
    commandEnv: {}          # optional: extra environment for node commands
    commandTimeoutMs: 600000
```

`graph` 的字段名与 refactory 一致（`start_node`、`nodes`、`edges`、`prompt_template`、`evaluator_type`、`gate_prompt`、`writes`、`reads`、`expand`），因此 `graph.json` 文件可以原样加载。`validateGraph` 会拒绝那些游走无法遵循其结构的图——无法解析的 `start_node`、悬空的边、带有两条无条件边的节点、两条携带同一 gate verdict 的边、未知的节点类型，或游走永远无法到达的节点——因此坏图会在加载时失败，而不是让一次运行卡住。

边上的 `condition` 是标签，不是枚举。`proceed` 沿该边退出，`reloop` 回退，`halt` 停止，其他任何标签都是由 gate 自己命名的前向结果（`draft`、`merge`、……），按大小写不敏感方式匹配。`maxIterations` 只在 gate 自己没有声明 `max_iterations` 时才对 reloop 施加上限。

-----

<a id="upstream-vocabulary"></a>
## 上游词汇

`@deepseek-ai/dsh-workflow-spine` 是 refactory `Workflow` IR 的一个消费方；[named-gate-outcomes Agent Note](../../../.agents/notes/implemented/architecture/2026-09-09-workflow-spine-named-gate-outcomes.zh.md) 拥有词汇决策及其已验证的覆盖面。

-----

<a id="deterministic-nodes"></a>
## 确定性节点

当 `executeCommands: true` 且挂载了 `shell` 执行器时，只要图给节点提供了命令，插件就自己运行该节点：带 `command` 的 `FnNode`/`Study`，或 `evaluator_type` 为 `fn` 的 `GateNode`。`wf_next` 会运行这些节点，按**最后一行非空 stdout**（转小写，与边条件的比较方式一致）路由 gate，并且只报告必须由模型完成的节点。在没有挂载 shell，或执行关闭时，每个节点都像以前一样交给驱动者。

节点命令是通用的——它对调用它的图一无所知——因此插件把它自己的声明以 JSON 形式通过 `DSH_WORKFLOW_NODE` 交给它：`{ id, type, command, reads, writes }`。若命令以非零码退出、超时、被中止，或（对 gate 而言）没有打印任何 verdict，则该节点**失败**：`wf_next` 返回 `{ status: 'failed', node, exit_code, error, stdout, stderr }`，且 cursor 不移动，因为一个没有发生的步骤绝不能看起来像发生过。

-----

<a id="node-dsh-mapping"></a>
## Node → DSH 映射

| refactory 节点 | spine 行为 |
|----------------|----------------|
| `AgentNode` | `wf_next` 返回它；agent 运行一个子 agent，然后 `wf_done`。 |
| `LLMNode` | `wf_next` 返回它，并把它的 `system_prompt` 作为 `prompt_template`；agent 完成这次一次性调用，然后 `wf_done`。 |
| `AgentNode` + `expand`（director） | `wf_expand` 热插入 N 个子节点并展开。 |
| `FnNode` / `Study` | `wf_next` 返回它；agent 运行该确定性 op，然后 `wf_done`。 |
| `GateNode` fn/user/agent | `wf_next` 返回它；agent 调用 `wf_verdict`。 |
| `ForkNode` / `JoinNode` | `wf_done(fork)` 展开；每个分支各自前进，屏障则是 `afters` 栈。 |
| 具名 gate 条件 | `wf_verdict(gate, \"draft\")` 沿匹配的边前进。 |
| RELOOP 边 | `wf_verdict(reloop, feedback)` 回退，上限由 gate 的 `max_iterations` 或 `maxIterations` 决定。 |

-----

<a id="model-experience"></a>
## 模型体验

### `wf_*` 工具与 CEO prompt 段落

#### 模型看到什么

插件为每个动词注册一个工具——`wf_next`、`wf_done`、`wf_verdict`、`wf_edit`、`wf_expand` 与 `wf_state`——各带一份描述与参数 schema。`wf_next` 返回当前到期节点的节点视图，携带该节点的 `prompt_template`、`reads` 与 `writes`，对 gate 还携带该 gate 接受的 `verdicts`。一个已注册的 prompt 段落（first-party 顺序 `TOOL_WORKFLOW`）告诉 CEO：`wf_*` 工具是唯一的前进方式；而 `ceoDeny` 守卫在 `agent/created` 时把编码与外部工具从 CEO 自己的 scope 中移除，因此 CEO 看到的是 `wf_*` 工具、委派与只读工具，而它派生的子 agent 保留完整工具集。

#### Token 影响

在图与工具固定时，每次请求的开销恒定：六个工具定义加一个 prompt 段落，与图的规模无关。节点内容只通过 CEO 读到的工具结果到达模型，因此一个节点的 `prompt_template` 与某个命令的 stdout 只在那个节点到期时消耗一次 token。

#### KV Cache 影响

工具块与 prompt 段落在一次运行的生命周期内固定不变，位于被缓存的请求前缀中。节点视图若指向另一个节点，只会改变该前缀之后的消息后缀；因此在工具集与配置不变时，前缀的 provider 缓存复用得以保留。

## 已知限制与延期工作

<a id="known-limitations-and-deferred-work"></a>

这些限制界定了当前 spine 的行为。它们是当前的包约束，不是图编写方面的待办清单。

- `SubgraphForkNode` / `SelectionNode` 以 `unsupported` 标记带过；两者都需要一个隔离 worktree 的子执行器，而当前没有任何图声明了它。
- `LLMNode` 按一次性 agent 节点执行：由驱动 agent 发起调用。声明的 `model`、`temperature` 或工具集都不受强制。
- reloop 会把 cursor 坍缩到它的目标上，丢弃其他任何活动位置，因此在有 fork 在途时到达的 reloop gate 会丢掉其他分支。图作者必须把 reloop gate 放在 cursor 唯一的位置。
- `afters` 屏障只有一个栈，因此嵌套在 fork 分支内的 fork 会让内层屏障在外层 join 处触发。
- 图通过 `config.graph` 内联；在 apply 时加载 `graphPath`（经由 `fs` seam）尚未实现。
- “force the loop”指引是一个已注册的 prompt 段落；结构性强制（spine 只在收到完成报告后才前进）才是防漂移的保证，而该段落只是请求 agent 配合。

<a id="dev-note"></a>
### 开发备注

<details>
<summary>维护者的工作上下文——点击展开</summary>

None.

</details>

**运行时不变式：** 不发布运行时不变式伴生入口，因为 spine 的 cursor、gate verdict 与迭代计数都只通过插件在会话 fiber 上注册的 `wf_*` 工具读取。
