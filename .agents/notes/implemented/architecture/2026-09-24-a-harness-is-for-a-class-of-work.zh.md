# 智能体笔记：harness 是为某一类工作而造的

Status: implemented

[English](2026-09-24-a-harness-is-for-a-class-of-work.md) | 中文

## 问题

**代码没有说明这个系统产出的是什么。** 这个系统的产出是为某一类工作而造的 agentic harness——「为任意 Java 源码树写构建脚本」，而不是「为这一棵树写脚本」——而一次运行据以行动的任何地方都没有这么说。`create` 默认产出的是一次性的模式，而命名 Task 只是一个可选的第二个标志，它本身并不会产出逐条迭代的 harness，于是一个 harness 可以被调到某一个硬编码的例子通过，然后被当作能泛化一样交付。所有随仓库发布的示例包要么是单实例的，要么是确定性的，因此目录里没有任何东西能反驳这种读法。

**外循环分不清「背下来」与「真能用」。** 每个候选者都在搜索被调优的同一批条目上评分，而且分数对规模没有成本，所以搜索可以靠背下这些条目、或靠把图长得更大来取胜。一次硬性 sanity 运行——为 CWE-78 补丁 harness 跑的 `create`——已经显出这种形状：产出的 harness 的评分器可以被钻空子（删掉那个测试，或用一个收窄收集范围的 `pytest.ini`，都能得 1.0），而尽管流水线拒绝把它发出去，外循环本身却无从看见。

**文档描述了一个模式，却没说明它是为什么而造。** 教程里的定义是「A Python class: a graph of nodes with typed knobs, and a `score()`」，读起来是一个程序，而不是一个 harness。

## 决定

**`create` 在任何模型调用之前先问你要造的是两者中的哪一个。** `--task <module:Class>`（即 `task_ref` 旋钮）命名这一类工作：产出的包把一个 `data` 节点绑定到该 Task，为 Task 列出的每个条目运行一次其子图，并由该 Task 的 `verify` 逐条判定。`--single`（即 `single_shot` 旋钮）是那个被明确要求的特例。两者都不给会在任何模型调用之前被拒绝：`refactory_dsh/cli.py` 在命令行上拒绝，`refactory_dsh/console.py` 的 `create` 动词把 `--task`/`--single` 转交给 `create.py` 在模式被加载之前抛出的拒绝，而 `refactory_dsh/create_ops.py` 的 `prepare` 为其余每个入口点拒绝。Task 绑定见《[v2 模式与它们所需的流程词汇](2026-09-22-v2-modes-and-the-workflow-vocabulary.zh.md)》。

**`data` 节点是 harness 的输入端口。** `create_ops.py` 的 `baked_item_problems` 解析产出的源码，拒绝用 `inline_items` 把条目写进图里的包，并指名文件与行号；`unfeedable_data_node_problems` 拒绝声明了 `data` 节点、却既没有非空 `task_ref`、也没有带 `source_format` 的 `source_path` 的包。`check_package` 在包被注册之前运行这两项，而且是解析而不是导入模型写的代码。harness 被指向什么是材料，而不是意义：条目写在图里的 harness 就是为那些条目而造的 harness。该节点在两个运行时上的迭代见《[data 节点在两个运行时上都会迭代](2026-09-23-a-data-node-iterates-on-both-runtimes.zh.md)》。

**一个分数是关于这一类工作的主张。** 外循环在 Task 条目的训练部分上评估每个候选者，并在留出部分上重新评估胜出者；审计记录 `train`、`holdout`、`gap`、`overfit`、`threshold` 与 `instances`，落到 `<run root>/holdout.json` 和运行报告里，并随 `refactory best` 一起给出；`refactory adopt` 会拒绝过拟合的胜出者，除非给出 `--allow-overfit`。`refactory_dsh/task.py` 按条目 id 排序划分条目，并把该部分作为 `subset.json` 记录在运行目录里，而不是作为搜索能移动的模式旋钮，因此搜索无法自己挑考卷。只有一条条目的选择无法划分，审计会记录这一点，而不是编造一个数字。默认比例是 0.3，`overfit` 为 `train − holdout > 0.15`。

**规模是搜索的偏好，不是模式的语义。** 候选者的搜索分数是 `raw_score − parsimony × nodes`，只在 `refactory_dsh/loop.py` 的 `Loop._evaluate` 中施加；模式的 `score()` 不受影响，未惩罚的 `raw_score` 与它并排记录在每一行候选者记录与流水账里，其字段位于 `optim/base.py`。系数是 `LoopConfig.parsimony`，默认 0.01，由人来设置——`cli.py` 中 `refactory optimize` 的 `--parsimony`，以及 `console.py` 中控制台的设置表。

## 后果

`create` 会拒绝没有命名任何一类工作的简述，而 `--single` 是那个特例的名字；`refactory/tests/test_create.py`、`refactory/tests/test_cli.py` 与 `refactory/tests/test_gui_loop.py` 都覆盖了两者。

产出的 harness 带着 Task 绑定和一个敞开的输入端口，因此之后可以从命令行、控制台或面板把它用在这一类工作里的任何条目上。

一次运行报告两个数字：胜出者在训练条目上得多少（未惩罚，`raw_score`），以及在留出条目上得多少，还有两者之差。只在被调优过的东西上有效的 harness 会以本来面目示人，而不是看起来已经完工。

`adopt` 可以拒绝：过拟合的胜出者需要 `--allow-overfit`，而无论哪种情况审计都会落在包旁边。

有两个测试改变的是含义而不是数字。教程的选项表由 `refactory/tests/test_docs_tutorial.py` 对照外循环自己的 `--help` 检查其完整性，所以新增一个标志不可能不让那一页知道。`Loop.install` 会把同一批里两个状态相同的提案作为 `E-duplicate` 拒绝，因为排序改变之后，暴露出一个被花在已经评过分的点上的批次名额。

## 考虑过的其他方案

**保留单次执行为默认，只在需要时才要一个 Task。** 被否决：那正是产出「背下一个例子」的 harness 的状态。

**把条目划分放进图里，做成搜索能移动的旋钮。** 被否决：能自己挑考卷的搜索没有被考。

**在 subset 文件里记录留出条目的 id，而不是比例。** 被否决：那样一个新增 data 节点的候选者会被过滤成空，而不是得到属于它自己的确定性划分。

**把规模成本放进模式的 `score()` 里。** 被否决：那会让「score」有两重含义，并把搜索偏好塞进模式的语义。
