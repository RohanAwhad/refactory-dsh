# 智能体笔记：唯一的入口，以及必须跑完的运行

Status: implemented

[English](2026-09-24-one-entry-point-and-a-run-that-must-finish.md) | 中文

## 问题

有两件事让这个仓库很难交给别人。

**它没有入口。** 所有东西都能用——模式、外循环、控制台、GUI——但要够到任何一样，都得知道解释器路径与模块名（`.venv/bin/python -m refactory_dsh.loop …`），而 README 开篇是一张架构图，而不是一个人实际要做的事。这个项目所移植自的那个项目恰好相反：`factory ceo "<一个想法>" --mode design`，一条命令，模式是一个标志。我们自己 README 里的四步是四种不同的模块调用，而且其中一条（`interactive`）根本不能用：它的默认运行根目录是相对路径，而 DSH 拒绝相对的会话工作目录，所以一个人「使用模式」时要跑的那条命令在图开始之前就停下了。

**一次运行可以被半途丢弃，却看起来像已经完成。** DSH 上的 `refactory ceo … --mode create` 跑到 QA fork，CEO 把三个评审子 agent **放到后台**启动，结束了自己的回合，图运行器看到静止（quiescence）便以 0 退出，`best_score: 0`——这个分数运行器已经不再凭空造出，见[A revision is compared, or it says why it was not](2026-09-23-revision-comparison-lives-in-the-run.zh.md)。spine 自己的状态文件一直写着 `ready`——还有三个节点没跑——但没有人被告知。walker 在进程内运行 agent 节点，所以同一个模式在那里能跑完；而这正是本仓库存在的意义所要消除的那类运行时差异。

## 决定

**一个控制台脚本 `refactory`，以及一个接受「想法」的 `ceo` 动词。** 根目录的 `pyproject.toml` 把三个本地包声明为可编辑来源（`refactory/`、vendored 的 `factory`、vendored 的 `srf`），因此 `uv sync` 就是全部的 Python 安装，而每条被记录的命令都是 `uv run refactory …`。`ceo "<一个想法>" --mode design|create|edit` 会降级创作模式，并在接上终端的情况下于 DSH 上运行它，于是计划批准 gate 会问到你；无人值守时用 `--approve GATE=VALUE` 预先给出那个答案，它写进的是同一次恢复运行会重放的同一份台账。其余是 `run`、`optimize`、`list`、`doctor`、控制台的读取动词、`package` 与 `ui`。每个动词都派发到本就拥有它的模块，因此每个行为仍然只有一份实现，`cli.py` 只决定人如何够到它。

**简述随旋钮传递，因为 DSH 上的运行没有 task。** `create` 与 `design` 在 walker 路径上从运行的 task 读 `name`/`brief`/`check`，在 DSH 路径上从旋钮读；只给一句简述也能得出名字（`_slug`），因为当那句话已经说清想要什么时，却因为缺一个名字而拒绝开始，是把严格用错了地方。

**驱动图的 CEO 不能把某个节点的子 agent 放到后台。** graph profile 以 `enableRunInBackground: false` 与 `backgroundMode: one-shot` 挂载 `tool-subagent`。base profile 的「后台且可续」默认值，适合那种先铺开、稍后再收的 agent，在这里却是错的：把节点的子 agent 放到后台**就是**丢弃这张图。这是在工具里强制，而不是在提示里请求——因为请求等待的提示是可以被忽略的，而它确实被忽略了。

**静止只是一个需要检查的结果，不是完成。** 图运行器在回合结束后读取 `.refactory/spine-status.json`；只要不是 `done`，就写下一行指名状态与仍然待办的节点，并以 1 退出。spine 在状态为 `ready` 时会把游标一并记进该文件，因此失败会说明哪些节点从未运行。没有工作目录的组合仍然以 0 退出。

**读者会去运行的文档是生成的，而且只从仓库真正发布的东西生成。** `refactory_dsh/tools/recipes.py` 是唯一一份「被记录的命令 + 每条应当产出什么」的清单；HTML 教程渲染它，`tools/check_recipes.py` 执行它，因此页面上展示的命令就是能跑的命令。教程的目录被限定在 `refactory_packages/`，因为从某台机器自己的 `.factory/packages` 生成的页面在另一台机器上永远不可能是最新的——教程索引引用 `committed_listing()`、并由一条测试把两者钉住，也是同一个原因。

## 后果

新来者的路径是 `./install.sh`、`uv run refactory doctor`、`uv run refactory list`、`uv run refactory ceo "…" --mode create`。新鲜 clone 已端到端验证：安装、doctor、十五条离线 recipe，以及测试套件。文档所依赖的两个示例包（`counting_pack`、`dataset_probe`）必须随仓库发布，因为它们此前只存在于作者的工作区，缺了它们每条被记录的命令都会失败。`dataset_probe` 得到了 `min_items`——一个它的 Task 检查会读取的旋钮——因为发布的模式若没有旋钮就没有可搜索面，而有一道门禁会这么说。

停下的运行现在是一个带名字的失败，这正是 graph profile 自己的 README 所承诺的。代价是：一个 CEO 提前结束回合的组合，过去以 0 退出，现在以非零退出——这是对的，而且是可见的。

## 考虑过的其他方案

把 `factory ceo` 做成包住那些 Python 模块的 shell 包装被否决：包装是第二个知道命令的地方，而命令会变。把创作模式的 name 与 brief 放进意图台账而不是旋钮被否决，因为台账是被折进提示的散文，不是 op 能按名字读取的值。只在提示里修复「丢弃」被否决了——它在实践中失败过：约束属于那个允许它的工具。把状态文件缺失当作失败被否决，因为一个组合完全可能没有工作目录。
