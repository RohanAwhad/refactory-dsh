# re:factory

[English](README.md) | 中文

一个**自包含的单仓库**，把 refactory 的图语义变成可运行的产品：refactory 定义工作流，DSH 执行它。

```
refactory (the language)  →  graph.json  →  DSH (the runtime)
   Workflow.to_dict()                        workflow-spine + CEO + subagents
```

- **refactory**（`refactory/vendor/remote-factory`）是这门*语言*——带类型的节点、`Verdict`、`OptKnob`——外加外层循环优化器（`SwarmEngine`，8 算子 MAP-Elites）。
- **SRF**（`refactory/vendor/srf`）是科学模式（gepa、best_of_n、……）与任务（circle_packing、……）的第一个包。
- **DSH** 就是本仓库——一个从上游分叉的 DeepSeek Harness，带有 workflow-spine、science runner 与 re:factory 品牌。

三个**元模式**以 refactory 模式的形式交付，另有一个 DSH preset 承载循环：

| 模式 | 作用 |
|---|---|
| `python -m refactory_dsh.create` | 创建一个新模式：一个 Python 包加一个 `graph.json` |
| `python -m refactory_dsh.edit` | 依据明确意图修订已有模式 |
| `python -m refactory_dsh.design` | 设计软件（research → strategy → build → review） |
| **ceo** preset（`refactory` 工具） | 在 Web 视图中运行并读取一次外层循环运行 |

这三个元模式是 Python 入口而非 preset：它们产出 Python 代码与 `graph.json`，而这不是工作流图能承载的东西。循环本身也是一个模式——`loop_mode/loop` 就是它八个步骤组成的图。

---

## 前置条件

- **Node ≥ 22.19**（构建所依据的是 nvm 的 `v22.23.2`）
- **pnpm**（经由 corepack）
- **uv**（Python 包管理器——负责创建 venv；它自带管理 Python ≥ 3.11）
- **git**（带 `--recurse-submodules`）

---

## 安装

```bash
git clone <this-repo> --recurse-submodules
cd <this-repo>
./install.sh
```

`install.sh` 按顺序执行：

1. `git submodule update --init --recursive` —— 拉取 refactory + SRF
2. `pnpm install` + `pnpm run build` —— DSH workspace 及其 client/host bundle
3. `uv venv .venv --seed` —— 一个自包含的 venv（保留 pip 供模式创建入口使用）
4. `uv pip install` 安装 glue 与最小语义依赖（pydantic、structlog、networkx、pyyaml、filelock）
5. `uv pip install -e … --no-deps` 把 `factory`（refactory）与 `srf` 链接进 venv
6. 写出 Python 模式包与 science runner 所加载的 `graph.json`

没有任何绝对路径被写死——一切都从仓库根目录解析（`refactory/refactory_dsh/paths.py`）；若使用非默认布局，可用 `REFACTORY_DSH_HOME` / `DSH_ROOT` / `SRF_GRAPHS` / `VENV_BIN` / `NODE_BIN` 覆盖。

---

<a id="run"></a> <a id="run-from-source"></a>
## 运行

### 交互式（产品形态）

```bash
./refactory-dsh --port 3081 --no-open
```

打开打印出的 URL。默认 preset 是 **ceo**，它带有 `refactory` 工具：列出某个检出可运行的模式、启动一次外层循环运行、在运行进行期间观察它，并读取其排行榜、逐候选差异与产物——全部在对话中完成。若要驱动一张 refactory *图*，则由 `wf_*` 工具逐节点推进，你在 gate 处进行引导。

### 无头 science runner

```bash
node --import tsx/esm apps/cli/src/bin.ts --profile science run \
  --mode gepa \
  --task circle_packing \
  --budget 1
```

它会打印 `{"best_score": …}` 与会话日志。`--graphs` 与 `--python-bin` 默认指向检出目录自身的 `.venv/bin` 与 `refactory/vendor/srf/graphs`，因此全新 clone 无需任何 flag。用 `--provider` 选择 CEO 模型（例如 `deepseek-official`、`openai`、`anthropic`——pi-ai 路由会从 `settings.yaml` + `.env` 解析密钥）。

### SRF：模式包的参考实现

**SRF 是随仓库交付的示例，展示如何基于 refactory 构建一个*模式包*。** 它不属于运行时——它是一份供你复制的模板。每个模式是一个 `graph.json`（refactory 的 `Workflow.to_dict()`），外加其 `FnNode` 调用的 Python op，全部放在同一个包内：

```
srf/
  graphs/gepa.graph.json       # a mode: nodes + edges + knobs
  srf/ops/gepa/…               # the ops that FnNode commands run
  srf/tasks/…                  # benchmark tasks (circle_packing, …)
```

要添加自己的模式，你只需改动那个包：写一个 `graph.json`，实现它的 op，放进 `graphs/`，它就会在同一个 `--profile science` runner 下运行——无需修改 DSH。这就是完整的 refactory→DSH 约定：语言定义图，运行时执行图，而模式包提供介于两者之间的领域逻辑。

### 端到端示例：创建一个模式，再优化它

1. 以意图 *“a basic inner loop for circle packing”* 运行 `python -m refactory_dsh.create`。它会在 `.factory/packages/` 下写出一个包，注册表在下一次运行时会发现它。
2. 对它启动一次外层循环运行——在 Web 视图中通过 `refactory` 工具，或运行 `python -m refactory_dsh.loop <package>/<mode>`。它会提出改动、评估这些改动、保留能提高分数的部分，并报告所达到的最佳得分。

---

## 文档

先读 [refactory/docs/architecture.md](refactory/docs/architecture.md) 了解各部分如何组合，然后按顺序过一遍[教程](refactory/docs/tutorials/index.md)：从编写一个 mode 一直到在 Web GUI 中驱动它。

| 文档 | 回答的问题 |
| --- | --- |
| [architecture.md](refactory/docs/architecture.md) | 有哪些层，某个行为归哪一层负责 |
| [tutorials/](refactory/docs/tutorials/index.md) | 下一步该怎么做，按顺序 |
| [api/](refactory/docs/api/index.md) | 每个公开名称的用途 |
| [packing-tools.md](refactory/docs/packing-tools.md) | mode 如何声明它需要安装的工具 |
| [hardening.md](refactory/docs/hardening.md) | 哪些保证被强制执行，由谁执行 |
| [contributing.md](refactory/docs/contributing.md) | 如何修改本仓库 |

---

## 目录结构

```
apps/                    DSH apps (CLI, web)
packages/                DSH packages (workflow-spine, science, ui-brand-refactory, …)
refactory/               the glue
  refactory_dsh/           ops (outer_loop, scaffold) + path resolution
  graphs/                  create_v2 / outer_loop / design_v2 graph.json
  presets/                 the shipped CEO preset skeleton
  vendor/remote-factory/   refactory (git submodule)
  vendor/srf/              SRF (git submodule)
  gen_presets.py           regenerates the 3 agent presets
install.sh               one-command install
refactory-dsh            the launcher
```

---

## 开发

### 编辑图之后重新生成 preset

```bash
.venv/bin/python refactory/gen_presets.py
```

### 测试

```bash
# the outer-loop faithfulness harness (8 operators, archive, reflection)
FACTORY_CLAUDE_BIN=/bin/true .venv/bin/python -m pytest refactory/tests -q

# refactory's outer-loop suite
FACTORY_CLAUDE_BIN=/bin/true .venv/bin/python -m pytest \
  refactory/vendor/remote-factory/tests/test_outer_loop -q

# the DSH client/host GUI suite
pnpm run test:gui
```

### 在上游 DSH 之上变基

本仓库是 **`deepseek-ai/deepseek-harness` 的一个 fork**，位于分支 `feature/workflow-spine`。我们的改动都是彼此隔离的增量，因此变基后可以重新套用：

- 新增的包/文件（例如 `packages/client/ui-brand-refactory`、`refactory/`）
- 单行接线改动（`cordis.patch.yml` 行、`tsconfig` 引用、`paths` 映射）
- 两个 locale 字符串（`hero.headline`）

`refactory` + `srf` 是 git **submodule**（属于你自己的仓库），因此它们独立地变基/更新。

---

## 已知限制

- **`trimul`（GPU 任务）需要 `numpy`**——它不在最小语义依赖中；数学任务（`circle_packing`、`autocorrelation_inequality`）不需要它。它的测试使用 `importorskip("numpy")`，所以没有它测试套件也能通过。
- logo 是 `remote-factory/docs/assets/refactory_logo.png` 的内联降采样；若上游有变动，请在 `packages/client/ui-brand-refactory/src/client/logo.ts` 中刷新它。
- 交付形态是**仓库 + `install.sh`**，而非单个二进制——把 Node 与 Python 运行时打包成一个跨平台可执行文件曾被考虑但被推迟；本仓库已经自包含且可直接安装。
