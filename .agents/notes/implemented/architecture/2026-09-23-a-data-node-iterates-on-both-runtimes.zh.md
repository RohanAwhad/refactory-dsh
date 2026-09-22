# 智能体笔记：data 节点在两个运行时上都会迭代

Status: implemented

[English](2026-09-23-a-data-node-iterates-on-both-runtimes.md) | 中文

## 问题

模式可以声明 `data` 节点，walker 也会执行它，但 DSH 不会：spine 把该节点的视图交给驱动智能体，游标随即移动到 data 节点的后继节点，子图从未被进入。因此，数据集模式在正式运行时上什么也没有选中、什么也没有迭代，却仍然报告自己已经完成。remote-factory 的 `WorkflowExecutor._execute_data` 会为每个条目迭代一次子图，而运行时之间的这一差异正是本次移植要消除的东西。

## 决定

**spine 会迭代 data 节点，而条目循环由三条命令组成。** 游标本身无法进入子图，所以由插件驱动这个循环：游标上的 `DataNode` 先被问出条目数（`<dataCommand> list --node <id>`），`startPass` 把游标放到 `subgraph_entry`，`begin --index <n>` 把该条目写入 `current_item.json` 并运行 Task 的 `setup`，随后普通游标走完子图，完成 `subgraph_exit` 即结束一趟，`end --index <n>` 运行 Task 的 `verify` 并把 `item_scores` 汇入本次运行的事实。之后 `endPass` 要么开始下一个条目，要么完成该 data 节点并抵达其后继。空选择会完成该节点并继续：条目数为零是关于数据的事实，不是崩溃。条目随 `wf_next` 的返回值一起传递，所以子智能体拿到的是它正在处理的条目，而不必自己去找。

**加载器只有一份实现，而且它是命令，不是服务。** `refactory_dsh.items` 调用 `refactory_dsh.nn.executor.load_items`，也就是 walker 调用的同一个函数，因此 `inline_items`、以 `directory`/`jsonl`/`csv` 读取的 `source_path`，以及 Task 的 `instances()`，在两个运行时上的选择结果完全一致。它是命令，是因为组合已经为确定性节点提供了命令，而这属于同一类东西：图说明节点选中了哪些条目，而读取它们的命令属于部署状态。`DataNode` 在 IR 中没有新增字段——节点的数据源随运行目录中的声明一起传递，IR 无法承载的事实本来就放在那里。

**带 data 节点却没有加载器的图在加载时即被拒绝。** 另一种做法——像以前那样把节点交给模型——会让一次运行看起来像是成功了。它依赖的两个事实（`executeCommands` 与 `dataCommand`）都是配置，所以拒绝应当发生在加载时。

**pass 状态机的规则是被检查的，而不是被假定的。** `validateGraph` 会拒绝作为门的 `subgraph_exit`（一趟 pass 以完成其出口结束，而门是通过判决完成的）、无法抵达出口的入口、不存在的子图节点，以及从 data 节点指向其自身子图的显式边——那会让第一个节点每个条目运行两次。它在判定可达性时也会沿着 `subgraph_entry` 前进，否则整张子图都会被读成不可达。

## 后果

两个运行时现在对数据驱动的模式产出相同的事实，`refactory/tests/test_equivalence.py` 会在两边各跑一个模式并加以比较。仍有两处差异，且都写在读者能看到的地方：walker 在一个进程里跑完所有 pass，而 DSH 每个动词启动一个进程；两者都不理会节点的 `parallelism`，所以 pass 依次执行。修复加载器的过程中暴露出一个实际缺陷：`load_items` 为 JSONL 数据源调用 `json.loads`，却没有导入 `json`，因此任何 JSONL 选择都会抛出 `NameError`。此前没有模式用过它，这正是它得以存活的原因。

## 考虑过的其他方案

在 IR 中给 `DataNode` 增加 `command` 字段被否决：为了承载声明中已有的东西而改动两个仓库共用的语言并不值得，而且这个循环需要三个动词而不是一条命令。把加载器做成能力接缝（capability seam）被否决，因为那会是一个只有一份实现、一个消费者的服务。把节点继续留给驱动智能体被否决，因为游标根本不会进入子图，那条指令无法被执行。
