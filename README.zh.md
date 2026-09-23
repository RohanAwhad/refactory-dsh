# re:factory

[English](README.md) | 中文

**说出你想要什么；一张由带类型节点组成的图会设计它、构建它、并改进它。**

```bash
git clone <this-repo> --recurse-submodules && cd <this-repo>
./install.sh
uv run refactory ceo "a mode that drafts a haiku and checks the syllable count" --mode create
```

这就是产品的全部。下面都是它的组成部分。

| 层 | 它负责什么 |
|---|---|
| **模式（mode）** | 一个 Python 类：一张带类型的节点图、声明的旋钮（knob），以及 `score()`。它是你创建、运行和搜索的单位。 |
| **外循环（outer loop）** | 对模式的搜索：提出带类型的编辑 → 校验 → 评估 → 保留有改进的，并报告它拒绝了什么。 |
| **CEO** | 一个 agent，逐节点驱动模式的图，为每次模型调用派生一个子 agent，并在只有人才能回答的 gate 前停下。 |
| **面板（panel）** | 浏览器里的同一批选项：选一个模式、按下 Start、读这次运行。 |

两个运行时执行同一张图，并通过「把同一个模式在两边各跑一次、比较事实」来保持一致： **walker**（进程内，搜索用它）与 **DSH**（发布用的那个）。模式被降级为唯一的一份 IR—— `Workflow.to_dict()`——运行时只看到那份 JSON。

---

<a id="run-from-source"></a>
## 安装

**前提：**[uv](https://docs.astral.sh/uv)、Node ≥ 22.19 + pnpm（corepack）、git，以及 在 `.env` 里放一个 DeepSeek API key（任何会调用模型的东西都需要它）。

```bash
git clone <this-repo> --recurse-submodules
cd <this-repo>
./install.sh
```

`install.sh` 依次做四件事：

1. `git submodule update --init --recursive` —— 取回 vendored 的语言（`factory`）与参考模式（`srf`）。
2. `pnpm install --frozen-lockfile && pnpm run build` —— DSH 工作区，以及 Web 视图所服务的 client/host 包。
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
- `--mode edit` 修订一个已有模式，并拒绝变差。
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

## GUI：用问的，而不是打命令

```bash
uv run refactory ui --port 3081 --no-open
```

打开它打印出的 `http://127.0.0.1:3081/?token=…`——token 是每进程一次的，不带它的裸地址会返回 `401`。

- **对话**里带着 `refactory` 工具：*"我能跑哪些模式？"*、*"用 random 优化器启动一次 counting_pack/fixed_step"*、*"把排行榜给我看看"*、*"把胜者保留成一个叫 my_tuned 的包"*、 *"这次运行是为了让计数正好落点"*。CEO 的编码工具是被故意禁用的：它可以选择、启动、读取、 引导与创作，但不能跑偏去自己实现什么东西。
- **面板**（右侧栏 → **Modes**）是同一批选项的点选版本：模式、外循环、优化器与评估器全是 从同一个控制台读来的、**带描述的**选项，按下 **Start** 会指名它开启的那次运行。
- **只有人才能回答的 gate 依然属于人。** 运行时来问你——`refactory run` 里是终端，GUI 里是对话框。 模型无法回答它。

[GUI 教程](refactory/docs/tutorials/05-drive-it-from-the-gui.md)带你走一遍， [面板教程](refactory/docs/tutorials/07-pick-and-watch-from-the-panel.md)讲那个控件。

---

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

**模式。** `default_pack` 是创作包：`create`（create-v2）、`design`（design-v2）与 `edit`， 每个都有一个由人回答的计划批准 gate。其余是测试夹具——刻意做得小、确定性、离线， 这样整个系统不需要 provider 就能被测试与搜索：`circle_packing`、`counting_pack`、`sympy_pack`、 `lean_pack`、`review_pack`、`phase_pack`、`annealing_pack`、`loop_mode`， 以及 `dataset_probe`（一个绑定 Task 的 data 节点）。

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
