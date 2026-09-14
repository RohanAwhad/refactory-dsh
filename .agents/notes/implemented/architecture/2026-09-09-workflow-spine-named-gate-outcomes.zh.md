# Agent Note: 工作流 spine 的可执行图词汇表

Status: implemented

[English](2026-09-09-workflow-spine-named-gate-outcomes.md) | 中文

## 问题

spine 消费 refactory 的 `Workflow.to_dict()`，却只执行了该词汇表的一部分，其方式有三种，使得真实图无法被走通。

第一，gate 只能路由 `proceed`、`reloop` 或 `halt`。出于同样的理由，refactory 自己的 `Conditional` 算子也把分支标签限制为这三种 verdict；然而，在若干*前向*延续之间做选择的 gate 其实就是一个普通的 switch：AIDE 选择 draft/improve/debug，GEPA 选择 mutate/merge，AdaEvolve 选择 normal/meta。把它们编码为 `reloop` 不只是命名错误——spine 把 `reloop` 视为回退，会取消目标与 gate 之间节点的完成状态，并计入迭代上限，因此以这种方式路由的替代分支会回退而不是前进。

第二，每个节点都被交给驱动 agent，包括完全不发起模型调用的节点。带 `command` 的 `FnNode` 与带 `evaluator_command` 的 `fn` `GateNode` 都是插件自己就能完成的确定性工作；让它们经由语言模型处理，会使图的结构变成建议性的，因为没有任何机制保证命令确实运行、或确实由它的输出决定 gate。

第三，`complete()` 只在 cursor 被清空时才推进。一个 `ForkNode` 会一次性把每个分支入口放进 cursor，因此完成其中一个分支入口后，兄弟入口仍然留在原地，而该入口自己的后继永远不会被调度。于是，只有当每个分支都恰好只有一个节点长时，fork 才可被走通，这就排除了 Best-of-N 的 prompt → generate → eval 分支与 AutoScientists 的 prompt → generate → eval scientists。

## 决策

`WorkflowEdge.condition` 是标签而非枚举，spine 以大小写不敏感方式比较标签。`proceed`、`reloop` 与 `halt` 保留各自含义；其他任何标签都是由 gate 命名的前向结果，会被路由到匹配的条件边。`Spine.verdict` 会拒绝没有标签的结果，并列出该 gate 确实接受的结果，而 gate 的 `NodeView` 把该列表作为 `verdicts` 携带，因此驱动者依据 gate 自己的声明作答，而不是依据一个固定集合。当图声明了 `GateNode.max_iterations` 时，它会为该 gate 自己的 reloop 施加上限，因此受预算约束的循环不再被 runner 默认的三次所限制。

`complete()` 用已完成节点的无条件后继替换 cursor 中的该节点，同时不触碰并发位置；`afters` 栈仍然提供 fork/join 屏障，而现在它会在每个分支都排空后触发。Director 展开不再发出子节点 → 后继的边，因为有多个子节点时，第一次完成会在兄弟节点尚未结束前就调度后继。

确定性节点成为插件自己的工作。在 `executeCommands` 打开且挂载了 `shell` 执行器时，`wf_next` 会运行图给出命令的每个节点——`FnNode`/`Study` 的 `command`，以及 `fn` `GateNode` 的 `evaluator_command`——按最后一行非空 stdout（转小写，与边条件的比较方式一致）路由 gate，并且只报告必须由模型完成的节点。节点命令是通用的，因此插件通过 `DSH_WORKFLOW_NODE`（`{ id, type, command, reads, writes }`）把该节点自己的声明以 JSON 形式交给它，而不是使用某个包专属的变量。非零退出、超时、中止，或 `fn` gate 未打印任何内容，都会使该节点失败：`wf_next` 返回 `{ status: 'failed', … }` 且 cursor 不移动。执行是可选启用的，在没有挂载 shell 时退化为先前的行为，因此没有 shell 的部署仍可运行图。

`validateGraph` 检查 cursor 所依赖的不变量——可解析的 `start_node`、边的端点、每个节点至多一条无条件边、唯一的 gate 标签、fork 目标与 join 来源、可执行的节点类型，以及从起点可达——而 `apply` 会在加载时拒绝无效的图。`LLMNode` 加入可执行类型：一次性模型调用以与 agent 节点相同的方式交给驱动 agent，这正是 graph-spine 设计早已记录为 v1 选择的映射。

对照 refactory 进行核查：`Edge.condition` 携带任意标签，`Conditional` 接受任意分支标签（对非 verdict 名称转小写），`OptKnob` 携带人类可读的 `description`，而 `Workflow.to_dict()` 会对 `reads`/`writes` 排序——它们是集合，其迭代顺序在不同进程间并不稳定，否则会让图序列化后的字节逐次运行而变化。

## 考虑过的替代方案

**把 switch 降级为阶梯式二元 gate。** 一个三路结果会变成两个嵌套 gate，但第二个 gate 只能沿第一个 gate 的 `proceed` 边到达，因此更下面的层级仍然必须用 `reloop` 来表达——同样的回退语义，只是被隐藏了起来。

**把分支选择推进到数据产物中，只发出一个分支。** 这保留了二元 gate，却把分支的 prompt、model 与 temperature 从图里移到了某个 op 中，于是图不再描述该模式做什么。

**让每个 cursor 条目拥有自己的延续栈，而不是共用一个 `afters` 栈。** 这样嵌套 fork 就能正确地设置屏障。当前图集合中没有任何图把 fork 嵌套在 fork 分支内，而单一栈正是让屏障的含义局限于一个 fork 的原因。

**始终执行确定性节点，并在没有挂载 shell 时大声失败。** 那么，入口节点写入问题的图会在没有 shell 的部署中拒绝运行，而这是一个合法的部署。`executeCommands` 让该选择变得显式，其默认值保留了此前已交付的行为。

**为每个包给每个命令单独的 environment 变量。** 通用 spine 包中的 `SRF_*` 名称会把插件与某一位图作者绑定；一个由 harness 拥有、承载整份节点声明的键可以服务每一位作者。

## 后果

gate 如今既是图的 switch，也是它的循环条件，而 fork 分支可以是任意长度。用 `reloop` 表示“另一个分支”的图在旧读法下是错误的，必须重新打标签——SRF 的 GEPA 求值器现在打印 `mutate`/`merge`，而不是 `PROCEED`/`RELOOP`。

`executeCommands` 默认为 `false`，因此已有的组合在被显式启用前不受影响；当它关闭，或未挂载 `shell` 执行器时，确定性节点会完全像以前那样交给驱动者。命令的失败如今是一种被报告的运行状态（`status: 'failed'`），而不是驱动者可能没有执行的某个步骤。

`NodeView.verdicts`、`NodeView.max_iterations` 与 `NodeView.evaluator_command` 是 gate 视图上新增的模型可见字段，而 `wf_verdict` 的参数不再声明固定枚举，因为可接受集合属于图。reloop 仍会回退并把 cursor 坍缩到目标上，因此在 fork 进行中到达的 reloop gate 会丢弃其他活动位置；这仍然是图编写层面的约束，而不是被强制执行的约束。

## 验证

`packages/workflow/workflow-spine/tests/vocabulary.spec.ts` 覆盖具名结果路由、大小写不敏感匹配、对未声明结果的拒绝、`reloop` 仍会回退、gate 声明的 reloop 上限、两条双节点分支对经由 join 的独立推进，以及 `validateGraph` 每条规则的一个用例。既有的测试套件原样覆盖线性、gate、director 展开、fork/join 与 skip 路径。

`packages/workflow/workflow-spine/tests/execute.spec.ts` 通过一个真正派生子进程的 shell 执行器运行真实进程，覆盖成功、非零退出、工作目录、环境、节点清单、从最后一行 stdout 提取 verdict，以及什么都不打印的 gate。`tests/plugin.spec.ts` 驱动该组合：在执行打开时，`wf_next` 运行 `FnNode` 与 `fn` gate 并只报告 agent 节点；在它关闭时，同一张图把确定性节点交出去；而失败的命令会报告 `status: 'failed'`，并把 cursor 留在原处。

Scientific Research Factory 产出的每一张图（13 个模式，最多 57 个节点）都能加载进 `Spine`、通过 `validateGraph`、走到 `done`，并到达每一个已声明的节点，包括每个 switch gate 的替代分支：由 `srf/tests/test_spine_load.py` 驱动的 `srf/tools/spine_probe.mjs`。
