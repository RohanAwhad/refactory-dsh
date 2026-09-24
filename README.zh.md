# re:factory

[English](README.md) | 中文

**说出一类工作；一张由带类型节点组成的图会为它构建一个 harness，然后搜索这个 harness。**

```bash
git clone <this-repo> --recurse-submodules && cd <this-repo>
./install.sh
uv run refactory ceo "a mode that drafts a haiku and checks the syllable count" --mode create --single
```

这就是产品的全部。**[QUICKSTART.md](QUICKSTART.md)** 是按顺序排列的六条命令，附上各自的代价与出错时怎么办。下面都是它的组成部分。

| 层 | 它负责什么 |
|---|---|
| **模式（mode）** | 某一类工作的 agentic harness：一个 Python 类，装着绑定 `Task` 的带类型节点图、声明的旋钮（knob），以及在这些条目上的 `score()`。它是你创建、运行和搜索的单位。 |
| **外循环（outer loop）** | 对 harness 的搜索：提出带类型的编辑 → 校验 → 评估 → 保留有改进的，并报告它拒绝了什么。它只在一部分条目上被搜索，并在它没见过的另一部分上被审计，而且候选要为自身体积付一点代价。 |
| **CEO** | 一个 agent，逐节点驱动模式的图，为每次模型调用派生一个子 agent，并在只有人才能回答的 gate 前停下。 |
| **面板（panel）** | 浏览器里的同一批选项：选一个模式、按下 Start、读这次运行。 |

**harness 是为某一类工作而造的。** 这里一切东西的形状都是「为任意一棵 Java 源码树写构建脚本」，而不是「为这一棵树写脚本」。`Task` 命名那一类工作：它列出条目、准备每一条、说明每条要什么、并逐条判定，所以 harness 的分数是「多少条目真的做对了」，而不是「我试过的那一个例子做对了」。只跑一次的模式是特例，`create` 会让你说清楚你在造哪一种。

**harness 的含义不取决于谁来运行它。** `{graph, knobs, ports, memory}` 降级成唯一的一份 IR—— `Workflow.to_dict()`——那才是全部含义，运行时只看到那份 JSON。哪个模型作答、agent 节点可以用哪些工具、工作在哪个沙箱里发生：那是运行时的事，同一个 harness 在两边含义相同。**walker** 在进程内跑一张图，搜索用它；**DSH** 是发布用的那个；把同一个模式在两边各跑一次、比较事实，让两者保持一致。

---

<a id="run-from-source"></a>
## 安装

**前提：**[uv](https://docs.astral.sh/uv)、Node `^22.19` 或 `>=24`（npm 随它一起来——脚本会自己找到 pnpm）、git，以及 在 `.env` 里放一个 DeepSeek API key（任何会调用模型的东西都需要它）。

```bash
git clone <this-repo> --recurse-submodules
cd <this-repo>
./install.sh
```

`install.sh` 依次做四件事：

1. `git submodule update --init --recursive` —— 取回 vendored 的语言（`factory`）与参考模式（`srf`）。如果你克隆时没带 `--recurse-submodules`，请在 `uv sync` 之前自己跑一遍：缺了这些 submodule，`uv sync` 会报 uv 自己的 *“does not appear to be a Python project”*，而那句话不会告诉你原因。
2. `pnpm install --frozen-lockfile && pnpm run build` —— DSH 工作区，以及 Web 视图所服务的 client/host 包。这一步需要 pnpm； 机器上没有时，脚本会先把固定版本放到 PATH 上：有 `corepack` 就用它，否则用 `npm install -g pnpm@<pin>`， 所以只要有 Node 和 npm 就够了（用 `REFACTORY_SKIP_GLOBAL_PNPM=1` 可以跳过）。
3. `uv sync` —— 根目录下**唯一**一个 Python 项目：它创建 `.venv`、以可编辑方式安装那三个本地包， 并把 `refactory` 命令放进 `.venv/bin`，由 `uv.lock` 锁定。
4. 创建模式运行所需的 DSH profile（`dsh plugin --profile graph add …`）。profile 是机器状态， 所以这一步每台机器只需一次；用 `REFACTORY_SKIP_DSH_PROFILE=1` 可以跳过。

然后检查这台机器，而不是相信文档：

```bash
uv run refactory doctor
```

```
  ok    python >= 3.12        3.13.2
  ok    uv                    /Users/you/.local/bin/uv
  ok    node >= 22.19         /Users/you/.nvm/versions/node/v22.23.2/bin/node
  ok    import factory        the vendored graph language (refactory/vendor/remote-factory)
  ok    import refactory_dsh  this package
  ok    DSH profile 'graph'   /Users/you/.dsh/profiles/graph
  ok    packages of modes     14 package(s)

refactory doctor: ready — try `refactory list`, or `refactory ceo "an idea" --mode design`
```

---

<a id="run"></a>
## 使用

四步，四条命令。

###
1. 创建一个模式

```bash
uv run refactory ceo "one agent drafts a product blurb; one op checks its length and cliches" \
  --mode create --name blurb
```

`create` 就是 remote-factory 的 **create-v2**：研究阶段、**由你批准**的策略阶段、构建、 三个并行评审、对它们证据的审计，然后注册并做一次 smoke 运行。它会在 `.factory/packages/<name>` 写下一个 Python 包并注册它。

- `--mode design`（默认）设计一个会被检查的**产物**，而不是包。它需要 `--check "<证明它能工作的命令>"`。
- `--mode edit` 修订一个已有模式，并拒绝变差；它需要指名被修订的模式：`--mode edit --revise <package>/<mode>`。
- 名字可以省略：不给 `--name` 时，你说的话就变成名字。
- 在你说的话里要一次模型调用（"一个 agent 起草……"），生成的模式就会有一个 `agent` 节点； 否则它是确定性的——这是好事：离线的模式可以被搜索评估成百上千次。
- `data` 节点与 `Task` 也用同样的方式提出，而 validate gate 会**检查**你确实拿到了它们： `--data-node` 与 `--task my_pack.tasks:MyTask`。

###
2. 使用它

```bash
uv run refactory run blurb/writer --intent "a blurb about teapots"
```

在 **DSH** 上运行该模式，并把你的终端接上，因此只有人才能回答的 gate 会问到你。 运行目录里有 `facts.json`、`trace.json`、`state.json`、`result.json`、意图台账，以及降级后的图。

`uv run refactory package smoke <package>/<mode>` 是快速检查，它**故意不调用模型**—— 带 `agent` 节点的模式无法 smoke，它会说明这一点，而不是以晦涩的方式失败。

###
3. 优化它

```bash
uv run refactory optimize blurb/writer --epochs 4 --batch-size 6 --optimizer random --seed 5
```

这就是**外循环**：它对模式提出带类型的编辑——`set_knob`、`duplicate`、`insert_after` 等—— 在任何一个候选被允许消耗一次运行之前先校验它，评估活下来的，并保留有改进的。 它既报告排行榜，也报告每一次拒绝，因此一次什么都没找到的运行会说明原因。

`uv run refactory list` 显示它可以搜索什么。外循环自身的选项、十种策略、七种优化器、 两种评估器与四种编辑预设都在[HTML 教程](refactory/docs/tutorial.html#loop)里。

###
4. 再次使用它

```bash
uv run refactory adopt --run <the run id the last command printed> --as blurb_tuned
uv run refactory run blurb_tuned/writer --intent "another blurb"
```

`adopt` 把胜出的状态写成一个新包，并且不改动它的输入，因此调优后的模式与任何模式一样。

### 读一次运行

```bash
uv run refactory runs                                       # every run, newest first
uv run refactory status --run <id>                          # where it is
uv run refactory leaderboard --run <id>                     # candidates, with diffs against their parents
uv run refactory candidate <candidate> --run <id>           # one candidate, exactly what it changed
uv run refactory best --run <id>                            # the winner, with the state that scored it
uv run refactory artifacts --run <id> --contents            # what it left on disk
uv run refactory steer --run <id> --note "keep it small"    # what the run is for, while it runs
uv run refactory stop --run <id>
```

### 管理包

```bash
uv run refactory list                                       # every package and mode
uv run refactory package verify my_pack                     # does it import and validate
uv run refactory package smoke my_pack/<mode> --json        # run it once, offline
uv run refactory package add|import|remove|enable|disable …
```

---

## 命令行

每条命令都是 `uv run refactory <动词>`。这里是参考；[QUICKSTART.md](QUICKSTART.md) 是同一件事的走查版，而 `uv run refactory <动词> --help` 永远是一个动词当前的事实来源。

<a id="cli-create"></a>
### 创建与构建

| 命令 | 作用 |
|---|---|
| `refactory ceo "<你想要什么>" --mode create --task <module:Class>` | 为**一类工作**构建一个 harness：Task 列出条目并逐条判定，产出的包把一个 `data` 节点绑定到它（调研、由你批准的策略、构建、三路并行评审、审计，然后注册并冒烟运行；20–30 分钟） |
| `refactory ceo "<你想要什么>" --mode create --single` | 特例：一个只跑一次、只处理一个输入的 harness，不绑定 Task |
| `refactory ceo "<你想要什么>" --mode design --check "<命令>"` | 设计一个带校验的产物，而不是包；`--check` 是判定它是否管用的命令（这是默认模式） |
| `refactory ceo "<你想要什么>" --mode edit --revise <包>/<模式>` | 修订一个已有模式并拒绝变差；`--revise` 指明它改自什么 |
| `refactory ceo <包>/<模式>` | 直接运行那个模式，而不创作任何东西 |

| 选项 | 作用 |
|---|---|
| `--name <名字>` | 包或产物的名字；不给就用你说的话当名字 |
| `--task <module:Class>` | 仅 create：那一类工作。产出的 harness 把一个 `data` 节点绑定到该 Task，并让子图按 Task 列出的条目逐条运行，所以它以后能用在同类中的任何条目上，而不只是你试过的那一个 |
| `--single` | 仅 create：构建一个只跑一次、只处理一个输入的 harness。`--task` 与 `--single` 必须给一个，因为为一个写死的例子构建的 harness 是特例，必须被当作特例明确要求 |
| `--check "<命令>"` | 仅 design：在产物目录里运行；门会反复重跑，直到它退出码为 0 |
| `--revise <包>/<模式>` | 仅 edit：被修订的那个模式 |
| `--model <id>` | 这次运行用的模型，例如 `deepseek-v4-pro`（默认：你设置里的默认） |
| `--provider <路由>` | 那个模型所在的服务路由，例如 `deepseek-official` 或 `openai` |
| `--knob name=value` | 为这次运行设置创作模式的一个旋钮，例如 `allow_regression=1` 接受一个分数更低的修订，或 `requires_input=package` 要求产出的模式接受一个包 |
| `--approve <门>=<值>` | 事先给出某道门的答案，例如 `gate_strategy=proceed`，用于无人值守的运行 |
| `--workdir <目录>` | 运行的证据落在哪里（默认 `.factory/interactive/<包>-<模式>`） |

<a id="cli-manage"></a>
### 管理模式包

| 命令 | 作用 |
|---|---|
| `refactory list` | 这个检出能跑的每个包与模式 |
| `refactory list modes --json` | 同一份目录的 JSON，带每个模式的 `action`、参数与说明 |
| `refactory package verify <包>` | 导入每个模式并校验它的图；某个工具不在不算问题 |
| `refactory package show <包>/<模式>` | 描述一个模式 |
| `refactory package tools <包>` | 它声明的工具，以及这台机器有没有 |
| `refactory package add <目录>` | 从目录安装一个包（`--as` 改名） |
| `refactory package import <目录或 git URL>` | 导入一个，并记录它来自哪里（`--ref`、`--as`） |
| `refactory package remove <包>` | 移除它 |
| `refactory package enable/disable <包>` | 纳入目录，或把它排除在外 |

<a id="cli-run"></a>
### 运行与使用一个模式

| 命令 | 作用 |
|---|---|
| `refactory run <包>/<模式> --intent "<它是干什么的>"` | 在 DSH 上运行这个模式，接上你的终端，所以门能到你这儿 |
| `refactory run <包>/<模式> --knob name=value` | 为这次运行设置这个模式的一个旋钮 |
| `refactory run <包>/<模式> --workdir <目录>` | 运行的证据落在哪里 |
| `refactory run <包>/<模式> --timeout <秒>` | 在门前等多久；`0` 表示等你 |
| `refactory run <包>/<模式> --model <id> [--provider <路由>]` | 这次运行用的模型 |
| `refactory run <包>/<模式> --approve <门>=<值>` | 在运行前而不是运行中回答一道门 |
| `refactory package smoke <包>/<模式>` | 在沙箱里跑一次，不用模型（免费） |
| `refactory package smoke <包>/<模式> --model` | 给图里有 `agent` 节点的模式打分，办法是提供一个模型 |

<a id="cli-optimize"></a>
### 优化它

`refactory optimize <包>/<模式>` 搜索一个模式的架构与旋钮：提出带类型的编辑、校验、评估，留下有改进的。

| 选项 | 作用 | 默认 |
|---|---|---|
| `--epochs <n>` | 提出 → 校验 → 评估 → 选择的轮数 | 2 |
| `--batch-size <n>` | 每轮提出的候选数 | 4 |
| `--optimizer <名字>` | 候选怎么提出来：`random`、`hill_climb`、`evolutionary`、`archive`、`islands`、`map_elites`、`autoresearch` | `autoresearch` |
| `--strategy <名字>` | 驱动搜索的外循环：`default`、`best_of_n`、`reflective`、`reflective_sampler`、`tree_search`、`staged_curriculum`、`staged_v1`、`adaptive_meta`、`autoscientists`、`research_then_optimize` | 用 harness 自己的（`default`） |
| `--evaluator walker\|dsh` | 用哪个运行时给候选打分：进程内的快，或者真正发布的那个 | `walker` |
| `--preset <名字>` | 允许哪些编辑：`broad`、`structure`、`conservative`、`knob_only` | `broad` |
| `--seed <n>` | 精确复现一次搜索 | 0 |
| `--elite <n>` | 每轮拿多少个父代来对照提出候选 | 3 |
| `--max-evaluations <n>` | 评估过的候选数的硬上限 | 64 |
| `--holdout <比例>` | 一个 Task 的条目里有多大比例被挡在搜索之外，用来在搜索结束后审计胜者；只有一个条目时无法切分，审计会记录这件事 | 0.3 |
| `--parsimony <代价>` | 候选图里每个节点让它分数付出多少代价：搜索比较的是 `raw_score − parsimony × 节点数` | 0.01 |
| `--attempts <n>` | 一批被拒后重试几次 | 3 |
| `--runs-root <目录>` | 运行目录本身；用 `--run <这个路径>` 读回来 | `.factory/loop` |
| `--intent "<话>"` | 这次运行是为什么；它给 agent 读的账本做种子 | 空 |
| `--model <名字>` | 推理优化器要问的模型（不是模式自己的 agent） | 配置好的路由 |
| `--provider <路由>` | 那个模型所在的服务路由 | 你设置里的默认 |

**每个候选都只在条目的训练部分上被搜索，胜者随后在留出的那部分上被重新评估。** 用哪一部分随运行目录走（每个候选旁边的 `subset.json`），而不是作为旋钮，所以一次搜索没法自己挑考卷；审计结果落在 `<运行根目录>/holdout.json`，也出现在报告里，形如 `{"train", "holdout", "gap", "overfit", "threshold", "instances"}`，当 `train − holdout` 超过 0.15 时 `overfit` 为真。搜索用来比较的分数已经扣掉了体积代价，未扣的那个数作为 `raw_score` 记在旁边，所以靠本事赢的 harness 和靠体积赢的能分得开。`refactory best` 会报告这份审计，而 `refactory adopt` 会拒绝一个过拟合的胜者，除非你传 `--allow-overfit`。

<a id="cli-read"></a>
### 读一次运行，并引导它

| 命令 | 作用 |
|---|---|
| `refactory runs` | 根目录下的每次运行，最新在前 |
| `refactory status --run <id>` | 它在哪，以及某个偏移之后的事件 |
| `refactory leaderboard --run <id>` | 按分数排的候选，每个都带它与父代的差异 |
| `refactory candidate <id> --run <id>` | 一个候选，精确到它改了什么 |
| `refactory best --run <id>` | 胜者，带打出那个分数的状态，以及它是否在自己的训练条目之外也管用的留出审计 |
| `refactory artifacts --run <id> [--contents]` | 它在磁盘上留下了什么 |
| `refactory adopt --run <id> --as <包>` | 把胜者写成一个新包，输入原封不动；当审计说胜者过拟合时拒绝 |
| `refactory adopt --run <id> --as <包> --allow-overfit` | 仍然采用它，并把审计记录在包旁边 |
| `refactory steer --run <id> --note "<话>" [--kind info\|correction\|constraint]` | 把你说的加进这次运行的意图账本 |
| `refactory steer --run <id> --verdict <门>=<值>` | 记下某道门的答案，好让恢复运行时重放它 |
| `refactory stop --run <id>` | 请一次运行停下来 |

**引导是上下文，不是结构。** 说的一句话会写进运行的 `intent-ledger.md`，而每个 agent 节点的提示词都会点名那个文件、并要求它在动手前先读一遍——所以 `--kind constraint`（必须被遵守）、`info` 与 `correction` 会到达在这句话之后才运行的节点，`run` 与搜索都一样。模式的图从不改变；搜索图是 `optimize` 的活。`user` 门是另一半：运行时来问你，把你的回答记成一条 `verdict`，恢复运行时重放它，而不是再问一次。

<a id="cli-other"></a>
### 其余

| 命令 | 作用 |
|---|---|
| `refactory doctor` | 这台机器能不能跑一个模式，不能的话要装什么 |
| `refactory ui [--port <n>] [--no-open]` | Web GUI（见下） |

## GUI：在浏览器里运行模式

<a id="gui-launch"></a>
### 启动它

```bash
uv run refactory ui --port 3081 --no-open
```

它只打印一行——打开那个 URL，令牌就在里面：

```
dsh web: http://127.0.0.1:3081/?token=<per-process token>
```

令牌是每进程一次的；不带它的裸地址 `http://127.0.0.1:3081/` 会返回 `401`。去掉 `--no-open`，浏览器就会替你打开。

<a id="gui-first-run"></a>
### 第一次运行

1. **选工作区。** 选这个检出。下次它会记住。
2. **发一条消息。** 什么都行——"hello"。得先有会话，标题栏里 Sidebar 的展开控件才会出现，这一步最容易卡住人。
3. **打开面板：** 按对话标题栏里的展开控件（它的提示是 `Open the sidebar`），再切到 **Modes** 标签。

<a id="gui-panel"></a>
### 从面板运行一个模式

| 控件 | 它做什么 |
|---|---|
| **Mode** | 目录列出的每个模式，各带运行它的动词 |
| **Outer loop**、**Optimizer**、**Evaluator** | 第二层的三个选择，各带控制台自己的描述；`Use the default` 就留用控制台的默认 |
| **Start** | 在它自己的进程里启动选中的模式，面板会指名它开启的那次运行 |
| **Run status** | 状态、最好分数与评估次数；**Refresh status** 读下一帧，**Stop the run** 请它停下 |
| **Packages of modes** | 按包：**Enable**、**Disable**、**Remove**，以及从目录或 git URL **Import a package** |

面板读的控制台和 CLI、模型读的是同一个，所以在这里开启的运行，就是 `refactory status --run <id>` 读回来的那次运行，产物也一样。

<a id="gui-ceo"></a>
### 在对话里运行与创作

用话问就行；CEO 会调用 `refactory` 工具，那是同一个控制台。它的编码工具是被故意禁用的：它可以选择、启动、读取、引导与创作，但不能跑偏去自己实现什么东西。

| 你说 | 会发生什么 |
|---|---|
| *"我能跑哪些模式？"* | 目录，带每个模式的参数 |
| *"用 random 优化器启动一次 counting_pack/fixed_step。"* | 一次运行开始了；CEO 用运行 id 回答 |
| *"那次运行在哪？把最好分数给我看看。"* | `status` 然后 `best`，用话回答 |
| *"把排行榜和胜者改了什么给我看看。"* | `leaderboard`，再对胜出那行来一次 `candidate` |
| *"把胜者保留成一个叫 my_tuned 的包。"* | `adopt`，输入原封不动 |
| *"这次运行是为了让计数正好落点；步子留小点。"* | `steer`：这句话进入运行的意图账本 |
| *"创建一个用 agent 写文案并检查它的模式。"* | `create`，和 `ceo --mode create` 同一个创作模式——CEO 会先请你批准计划，并把你的回答随调用一起传过去 |
| *"那次创作进行到哪了？"* | 对运行目录（`.factory/create/<名字>`）用 `status`，它会报出创作运行自己的逐节点进度 |
| *"把那个模式跑一次给我看看。"* | `run`，就是 CLI 的 `refactory run`：它在 DSH 上使用这个模式，并报出运行目录与它记录下来的东西 |
| *"那次创作被网络抖动弄死了——接着跑。"* | 对运行目录用 `resume`：它从失败的那个节点继续，已经完成的节点全部保留 |
| *"把包列出来，然后 disable annealing_pack。"* | `registry` 先 `list`，再 `disable` |

<a id="gui-gates"></a>
### 只有人才能回答的门，依然属于人

创作调用还接受 `inputs`（简述说这个模式要操作什么）、`sample`（旋钮值，例如真实的输入，好让冒烟跑的是那条路径而不是内置 fixture）与 `resume`（要继续的运行目录）。产出物不是它自称的格式时运行会被拒绝：`.whl` 缺少与 `RECORD` 并列的 `METADATA` 与 `WHEEL`，或者压缩包打不开。

如果某个模式走到一道 `user` 门（create-v2 的计划批准就是一道），运行时来问**你**：`refactory run` 里是终端，GUI 里是对话框。CEO 回答不了它，模型也回答不了。要做到无人值守，就事先说出答案：`--approve gate_strategy=proceed`——注意从对话里发起的创作调用会在这次工具调用内部跑上好几分钟，所以批准必须在它开始之前给出，而不是等它停下来问。

[GUI 教程](refactory/docs/tutorials/05-drive-it-from-the-gui.md)带你走一遍对话界面，[面板教程](refactory/docs/tutorials/07-pick-and-watch-from-the-panel.md)讲那个控件。

## 模型

<a id="models-list"></a>
### 名单是从哪来的

adapter 的目录是**建议性**的：它是选择器会展示的东西，不是路由会接受的东西。DeepSeek 路由自带 `deepseek-v4-flash`、`deepseek-v4-pro` 与 `deepseek-v4-flash-vision-exp`，而它会把你点名的任何 id 发出去——所以没被列出的模型照样能用，只是不会被摆出来。有哪些路由、每个路由提供哪些模型，由你的**设置文档** `~/.dsh/settings.yaml` 决定，它是热重载的：什么都不用重启。

<a id="models-gui"></a>
### 在 GUI 里

**Settings → Models。** 加一个 provider，粘贴它的 API key，需要的话填 base URL，然后列出模型——或者按 **Fetch available models**，从 provider 报回来的清单里挑。**Add a custom provider** 要一个 id、一个显示名、三种线上协议之一（`openai-completions`、`openai-responses`、`anthropic-messages`）和一个 base URL，本机服务器（Ollama、LM Studio、vLLM）就是这样接进来的。这个页面会替你写设置文档和凭据库；之后输入框旁的模型选择器就会提供新的模型。

<a id="models-terminal"></a>
### 在终端里

同一个文件，手写：

```yaml
# ~/.dsh/settings.yaml
agent-default-model:
  provider: deepseek-official
  model: deepseek-v4-pro          # the default for every run that names none

llm-pi-ai:
  providers:
    openai:
      apiKeyEnv: OPENAI_API_KEY   # the *name* of the variable, never the key
    anthropic:
      apiKeyEnv: ANTHROPIC_API_KEY
      models:
        - id: claude-sonnet-4-5
          contextWindow: 200000
    ollama:                       # a local OpenAI-compatible server
      displayName: Ollama
      api: openai-completions
      baseURL: http://127.0.0.1:11434/v1
      models:
        - id: qwen3-coder
          contextWindow: 262144
```

key 从环境里读（`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`……包括检出根目录的 `.env`），或从 Models 页面写的托管凭据库 `~/.dsh/.credentials.yaml` 里读。`llm-deepseek:` 段以同样的方式覆盖直连 DeepSeek 路由：`models:`、`baseURL:`、`apiKeyEnv:`、`thinking:`、`reasoningEffort:`。

本机的 OpenAI 兼容服务器走同一条路由，而且仍然需要*一个* key——pi-ai 的 OpenAI 协议会要一个，所以起一个占位变量名并随便导出个值（`apiKeyEnv: LOCAL_TEST_KEY`，然后 `LOCAL_TEST_KEY=dummy`），或者自己设一个 `Authorization` 头。这一点是跑出来的：一条指向本机服务器的 `openai-completions` 路由回答了一次 headless 运行，服务器记下的请求里点名的正是这次运行所选的模型。

<a id="models-one-run"></a>
### 一次运行，一个模型

```bash
uv run refactory run counting_pack/fixed_step --model deepseek-v4-pro
uv run refactory ceo "an idea" --mode create --model deepseek-v4-pro
uv run refactory optimize circle_packing/adaptive --provider deepseek-official --model deepseek-v4-pro
```

`--model` 给 id，`--provider` 给路由；两者都回落到你设置里的 `agent-default-model`，而且都不写回任何东西，所以为一次运行选的模型不会改变下一次。不存在的路由是响亮的失败（`NO_ADAPTER: no adapter registered for provider …`），而不是悄悄退回默认模型。再往下一层，`dsh --profile headless|graph` 也有同样两个开关，`refactory` 调用的就是它们。

## 文档

| 文档 | 它回答什么 |
|---|---|
| **[`refactory/docs/tutorial.html`](refactory/docs/tutorial.html)** | 每个模式、两条 v2 流水线、外循环及其全部选项、GUI —— 生成出来的一页 |
| [tutorials/](refactory/docs/tutorials/index.md) | 按顺序，下一件事怎么做 |
| [architecture.md](refactory/docs/architecture.md) | 有哪些层，某个行为归哪一层 |
| [datasets.md](refactory/docs/datasets.md) | 逐条迭代的模式，以及提供并评判条目的 Task |
| [api/](refactory/docs/api/index.md) | 每个公开名字是做什么的 |
| [hardening.md](refactory/docs/hardening.md) | 哪些保证是被强制执行的，由什么执行 |
| [packing-tools.md](refactory/docs/packing-tools.md) | 模式如何声明它需要安装的工具 |
| [contributing.md](refactory/docs/contributing.md) | 如何修改本仓库 |

HTML 教程、API 参考与配置目录都是**从代码生成的**，而本 README 展示的每条命令都是一份 recipe， 由 `uv run python -m refactory_dsh.tools.check_recipes` 执行——会撒谎的文档过不了门禁，只会骗到读者。

---

## 有什么

**模式。** `default_pack` 是创作包：`create`（create-v2）、`design`（design-v2）与 `edit`， 每个都有一个由人回答的计划批准 gate。其余是测试夹具——刻意做得小、确定性、离线， 这样整个系统不需要 provider 就能被测试与搜索：`circle_packing`、`counting_pack`、`sympy_pack`、 `lean_pack`、`review_pack`、`annealing_pack`、`loop_mode`， 以及 `dataset_probe`（一个绑定 Task 的 data 节点）。

**外循环。** 十种策略（SRF 自身循环的移植：`default`、`best_of_n`、`reflective`、`tree_search`、 `staged_curriculum` 等）、七种优化器（`random`、`hill_climb`、`evolutionary`、`archive`、`islands`、 `map_elites`、`autoresearch`）与两种评估器（`walker`、`dsh`）。

**面板。** `packages/client/ui-refactory` + `packages/refactory/refactory-console`： 一个 Typert Remote 服务，读的是与工具同一个控制台，以及架在其上的侧栏标签页。

---

## 目录结构

```
apps/                        DSH apps (CLI, web)
packages/                    DSH packages (workflow-spine, graph bundle, ui-refactory, …)
refactory/                   the glue — the modes, the loop, the console
  refactory_dsh/               cli.py is the entry point; nn/ is the language; tools/ generates docs
  refactory_packages/          the packages of modes
  docs/                        the HTML tour, tutorials, architecture, API reference
  vendor/remote-factory/       the language (git submodule)
  vendor/srf/                  the reference modes (git submodule)
pyproject.toml               the one Python project (`uv sync`, `uv run refactory …`)
uv.lock                      the pinned environment
install.sh                   one-command install
refactory-dsh                the GUI launcher (what `refactory ui` runs)
```

模式是 `refactory_packages/<package>/` 里的一个 Python 类，而不是一份 `graph.json`； 决策记录见 [pythonic-modes.md](refactory/docs/plans/pythonic-modes.md)。

---

## 开发

```bash
uv run python -m pytest refactory/tests -q          # the glue: 700+ tests, offline
pnpm run test                                        # the runtime and the client
pnpm run typecheck && pnpm run lint                  # TypeScript
pnpm run test:docs                                   # documentation gates
uv run python -m refactory_dsh.tools.check_recipes   # every command this repo documents
```

集成层会真正调用模型：

```bash
uv run python -m pytest refactory/tests -q --run-integration   # one mode on both runtimes
uv run python -m refactory_dsh.tools.check_recipes --tier model
```

### 跟随上游 DSH 变基

本仓库是 **`deepseek-ai/deepseek-harness` 的 fork**。我们的改动是彼此隔离的增量， 因此变基后可以重新套用：新增的包/文件、一行的接线改动（`cordis.patch.yml` 行、 `tsconfig` 引用、`paths` 映射），以及两条 locale 字符串。`factory` 与 `srf` 是 git **submodule**， 各自独立变基。

---

## 已知限制

- **调用模式的运行要花钱。** 别的都不花：夹具与 walker 都是离线的，而外循环的 `walker` 评估器除了模式自身有 `agent` 节点之外，从不会调用模型。
- **`trimul`（GPU 任务）需要 `numpy`** —— 不在项目依赖里；数学任务不需要它。
- **面板需要从本检出启动的 GUI。** 在这次工作之前安装的 `dsh` 不带那个客户端插件； `uv run refactory ui` 启动的才是对的。
- **以仓库 + `install.sh` 的形式发布**，而不是单一二进制：把 Node 与 Python 打包进 一个各平台可执行文件被考虑过并推迟了。
