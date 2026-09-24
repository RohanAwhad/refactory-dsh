# refactory-dsh

[English](README.md) | 中文

模式就是 Python 类。它们的架构与参数构成一份可序列化的状态字典。优化器针对该状态提出带类型的更新，每个提案在被允许消耗一次内层运行之前都会先通过校验。

模式是**为某一类工作而造的 agentic harness**：一张绑定 `Task` 的带类型节点图，Task 列出条目、准备每一条、说明每条要什么，并逐条判定——所以模式的 `score()` 是关于这一类工作的主张，而不是关于试过的那一个例子的主张。`create` 产出的就是这个 harness，只有命名 Task 才让「逐条」的绑定成为真的，而 `--single` 是那个被明确要求的特例。[docs/datasets.md](docs/datasets.md) 讲了全部。

这套设计刻意遵循 PyTorch，因为这一类比正是承重部分：模式是 `nn.Module`，它的 `state_dict()` 同时携带参数*与*架构，knob 是 `nn.Parameter`，优化器则是 `torch.optim.Optimizer`，只能写入模式声明为可写的内容。`score()` 与模式的不变量都是方法，因此任何提案都无法触及它们。

- **DSH** 是运行时与模型客户端（本仓库是 DSH 的一个 fork）。
- **`refactory/refactory_dsh/nn/`** 是库：模式、图、knob、编辑代数、校验器、执行器。
- **`refactory/refactory_dsh/optim/`** 是优化器层。
- **`refactory/refactory_packages/`** 存放模式包。库本身不认识任何具体的包名；它只负责发现、列出并导入它们。

## 目录结构

```
apps/                       DSH apps (CLI)
packages/                   DSH packages (workflow-spine, science, brand, …)
refactory/                  the glue
  refactory_dsh/nn/           the mode library (torch.nn analogue)
  refactory_dsh/optim/        the optimizers (torch.optim analogue)
  refactory_dsh/loop.py       init → forward → loss → backward → optimizer → step
                              → scheduler → report
  refactory_dsh/registry.py   package discovery, enable/disable, catalogue
  refactory_dsh/create.py     create-mode: a package from a brief
  refactory_packages/<pkg>/   the packages of modes (torchvision analogue)
  docs/architecture.md        the system as built, and why it looks like that
  docs/tutorials/             five walkthroughs, run against this checkout
  docs/packing-tools.md       packing a tool that is not Python, such as Lean
  docs/plans/pythonic-modes.md  the design and its acceptance gates
  vendor/remote-factory/      refactory (git submodule)
  vendor/srf/                 SRF (git submodule)
install.sh                  one-command install
refactory-dsh               the launcher (opens DSH web)
```

## 安装

```bash
git clone <repo> --recurse-submodules
./install.sh
```

## 运行

列出各个包及其拥有的模式：

```bash
uv run refactory package list
uv run refactory package show circle_packing/adaptive
```

优化一个模式。`autoresearch` 读取 trace 并向模型询问下一次编辑；`random` 从编辑目录中采样。两者运行的都是同一个带校验的循环，因此一个无法运行的提案只会消耗 token，而不会消耗预算。

```bash
uv run refactory optimize circle_packing/adaptive \
    --epochs 2 --batch-size 4 --optimizer autoresearch

# what came out of it
cat .factory/loop/report.md
```

根据一段简述创建模式包。设计与生成是模型步骤；gate 则是确定性的——包会被导入、其图会被构建、它命名的每个调用都会被解析，并且在注册之前它会先被运行一次。

```bash
uv run refactory ceo "what the package should do" \
    --mode create --name my_pkg --approve gate_strategy=proceed
```

## 测试

```bash
uv run python -m pytest refactory/tests -q
```

`tests/test_registry.py::test_every_installed_package_mode_validates` 断言的是这个库对并非由它编写的包所能做出的最强陈述：每个已安装包的每个模式都能构建出一张图，且该图在所有调用都被解析的情况下通过校验。

## 尚未接线

过去用于驱动 JSON 图的那些 DSH agent preset 已不再重新生成，因为创建与优化如今都是 Python 入口点。把它们重新接到 `refactory_dsh.create` 与 `refactory_dsh.loop` 上以供 GUI 使用，是下一项工作；参见 `docs/plans/pythonic-modes.md`。
