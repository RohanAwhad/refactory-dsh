# 代理笔记：v2 模式与它们所需的流程词汇

Status: implemented

[English](2026-09-22-v2-modes-and-the-workflow-vocabulary.md) | 中文

## 问题

默认包中的 `create`、`edit` 和 `design` 模式原本只有一趟：调研、选择、评审、产出、校验。remote-factory 的 `create-v2` 与 `design-v2` 做得更多，而它们新增的部分——自主选择方向的研究阶段、记录每个视角覆盖了哪些诉求的策略阶段、构建之前由人给出的批准、并行进行的三项评审，以及审计这些评审背后证据的 overwatch 复核——根本无法表达。模式词汇只有三种节点（`op`、`agent`、`gate`）：既没有屏障，也没有迭代。运行时自诞生起就能执行 `ForkNode`、`JoinNode` 和 `DataNode`，但模式无法声明它们。

## 决定

**词汇新增 `fork`、`join` 和 `data`。** fork 通过 `targets` 抵达各分支，而不是每个分支一条出边，因为后者会让后继节点在第一个分支结束时就运行；join 等待 `sources`；data 节点指明它迭代的子图。`to_state` 仅在字段被设置时才输出它们，因此既有的状态字典——以及由它派生的候选者 id——都不会改变。lowering 输出 IR 自有的节点，`V12` 拒绝不是节点的名字，而 `Graph.reachable` 会沿 fork 的 targets 与 data 节点的入口前进；否则每个分支和每个子图都会被判为不可达。

**`create` 与 `edit` 即 create-v2；`design` 即 design-v2，含 `design_doc`。** 研究主管、策略主管、`synthesize_strategy`、设计文档，然后是构建；构建结果由三项评审并行攻击，合并后的综述中标记为阻塞的发现由 QA 门读取，最后由 overwatch 复核给出其明确结论，供最后一道门读取。

**由人批准计划，无头运行则拒绝该门。** 两种模式都像 remote-factory 一样把 `gate_strategy` 设为 `user` 门：能自行批准自身策略的运行并没有被评审。无头调用方显式传入结论，测试说明批准者是谁，而不是凭空编造。

**`data` 节点会执行，Task 绑定提供它的条目。** walker 为每个条目运行一次子图，并把当前条目写入 `current_item.json`；绑定到 Task 的节点从 `instances()` 取得条目，并在每一趟之后由 `verify()` 判定，记录为 `item_scores`。创建过程若被要求生成 data 节点或 Task 绑定，现在由校验门去*检查*，而不再只是在提示词里请求。

## 后果

walker 的 fork 是顺序的，而 DSH 的是并发的：每个分支按顺序跑到同一个屏障，因此结果一致而并发方式不同。DSH 的 spine 仍把 data 节点交给驱动代理而不是自行迭代，这是两个运行时对「模式意味着什么」仍然不一致的唯一之处。

## 考虑过的其他方案

把新增阶段放进同一个代理的提示词里被否决：顺序、屏障与门必须由运行时而非模型掌控。把 `gate_strategy` 改为函数也被否决，理由正是这道人门存在的理由。walker 最初按名称拒绝 data 节点；改为真正执行它，才在 walker 一侧消除了差异。
