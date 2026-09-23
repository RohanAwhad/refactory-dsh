# Agent Note: What stopped a creation from the panel

Status: implemented

[English](2026-09-23-what-stopped-a-creation-from-the-panel.md) | 中文

## Problem

从 Web GUI 创建一个模式，失败信息是 `create failed (exit 2).`，别的什么都没有。这一次尝试被填成了六个 issue，而每一个都遮住了下一个。

**原因在往上走的路上被丢掉了。** 创作入口点把被处理的拒绝写成 stderr 上的 `{"error": …}`；`console._forward` 只捕获 stdout，于是载荷是 `{"output": ""}`，工具只能说「failed (exit 2)」（issue #61）。

**工具没有办法回答属于人的门。** `create`、`edit` 与 `design` 会停在 `gate_strategy`——一道属于人的门；工具的模式里没有 verdict 字段，所以从 GUI 发起的 headless 运行永远拒绝它（issue #60）。

**创作运行是同步的、上限十分钟、而且没法看。** 工具里的 console 超时是 10 分钟，运行又在进程内进行，所以一次真实的创建——写第一行代码之前要先研究、定策略好几分钟——会在半途被杀，既没有可恢复的东西，也没有可轮询的 run id（issue #62）。

**研究阶段从来没有被检查过。** `research-plan.json` 唯一的契约是 `writes=("research-plan.json",)`，任何字节都能满足它。两次独立的运行都写了一段说明子 agent「正在后台运行」的散文，`research/` 里空无一物，而设计就建立在未经核实的假设上继续了（issue #63）。真正要紧的原因是子 agent 为什么什么都没写：一个节点的模型调用跑在一次性 profile 上，它的 spawn subagent 工具默认 `backgroundMode: continuable`——于是 agent 在自己的研究者还在跑的时候就结束了回合，节点的产出变成了一句关于它们的话。

**节点的模型调用跑在检出里。** 评审节点是带工具的 agent 补全，而补全的工作目录是检出，于是那些 agent 把 `reviews/*.md` 写到了仓库根，而运行自己的 `reviews/` 空空如也。QA 综合读的是运行目录，所以它综合出的评审数为零，overwatch 门就把构建打回 `emit`——这次回环花了十五分钟，而且看起来像模型质量问题。这是证明另外五个修复的那次运行发现的。

**所有创建共用一个工作目录。** `--workdir` 默认是固定的 `.factory/create`，所以第二次运行会把它的 brief、研究与策略写在第一次的上面，而一次被取消的运行留下的残渣就是下一次读到的东西（issue #64）。

**emit 的提示词从来没说路径是相对谁的。** 把「relative path」读成相对检出的模型写下了 `refactory/refactory_packages/<name>/mode.py`；`materialise` 把它接到包根上，于是包被嵌进了自己里面，`is_relative_to` 这道防线通过了（嵌套路径确实在根里面），而 `validate` 在离错误四个目录远的地方报 `__init__.py is missing`——重试提示词又完全没提路径，于是循环把同一个错误重复了一遍（issue #65）。

## Decision

**被转发的拒绝保留它自己的话，而失败的转发就是失败的调用。** `_forward` 现在也捕获 stderr，优先取入口点自己那行 `{"error": …}` 里的消息，`console.main` 以 2 退出并把原因放在顶层。工具渲染的是那个原因，而不是一个空载荷。

**工具转发属于人的门的答案。** `create`/`edit`/`design` 上的 `approve` 是一个 `GATE=VALUE` 数组，作为重复的 `--verdict` 传下去。动作说明里写明这次调用无法停下来询问，所以模型要先向用户要计划批准，再随调用一起传。能力本来就在：命令行与控制台都有，只有工具没有。

**节点的模型调用不能把它自己的工作丢到后台。** `DshModelClient` 用一份签入仓库的 overlay（`refactory_dsh/patches/agent-node.yml`）运行一次性 profile，把 spawn subagent 工具改成 `backgroundMode: one-shot` 与 `enableRunInBackground: false`——这正是 graph profile 早就为 CEO 做过的同一个决定，理由相同。一个节点就是一次补全，而它的答案就是这个节点的产物。

**研究阶段被校验，而且它的发现必须存在。** 没有子 agent 留下笔记时，`record_findings` 会把每条方向的 finding 写进 `research/<slug>.md`；`gate_research` 要求一个 `{focus, slug, finding}` 的 JSON 数组，三到五条、字段非空、slug 唯一，并且文件都在盘上。失败时它写下 `research-review.txt`、设置 `research_review`，并回到会读它的那个提示词——这正是设计评审对策略做的事。

**节点的模型调用跑在节点自己的运行目录里。** `DshModelClient.complete` 接收要运行在哪个目录，walker 传节点的运行目录，而没有运行目录的调用方拿到的是暂存目录而不是检出；启动脚本现在用绝对路径，因为子进程的工作目录不再是仓库。vendored 的 `ModelClient` 协议仍然是「文本进、文本出」，所以不接受该参数的客户端就用不带它的方式去问。

**创作运行相互隔离、可观察，并且给了足够长的时间。** 三个入口点把工作目录默认成 `.factory/create/<名字>`、`.factory/design/<名字>` 与 `.factory/edit/<名字>`；console 在调用方给了 `--workdir` 时转发它；`status` 会读运行的 `.refactory/spine-status.json`，报出已完成的节点、还欠的节点、错误与运行自己的事实；console 的超时是 90 分钟，因为创作动词就是那些长的——在这个检出上，一次真实的创建花了 48 分钟，它杀死了最初的 10 分钟上限，也会在离终点三分钟的地方杀死一个 45 分钟的上限。

**emit 的提示词写明路径约定，而重新声明包根的前缀会被剥掉。** 路径相对于包自己的根——`__init__.py` 就意味着 `__init__.py`——而 `_package_relative` 会剥掉开头的 `<名字>/`、`refactory/refactory_packages/<名字>/`、`refactory_packages/<名字>/` 或 `.factory/packages/<名字>/`，对绝对路径或越界路径点名拒绝。`sandbox.missing_init` 现在会说出它查的是哪个根、那个根里有什么、以及约定是什么，好让重试能据此行动。

## Consequences

人从面板走的这条路——以及它转发到的 console 那条路——现在要么产出包，要么说明为什么不能。每个修复都有一个去掉它就失败的测试：stderr 里的原因、verdict 的 argv、创作 status 的形状、研究 gate 的四种拒绝、按名字隔离的工作目录、被剥掉的前缀、可行动的缺清单消息，以及补全被告知要运行在哪个目录。

三个只有真实运行才能发现的修复来自同一次运行：45 分钟的上限是拿一次 48 分钟的创建量出来的；只允许前台子 agent 的补丁由五个 findings 文件出现在两次尝试只产出过一句话的地方所证实；补全的工作目录由 `reviews/*.md` 出现在检出根所证实。现在通过 console 跑一次 `create` 会以一个已校验、已注册的包结束（`buildroot_pack`，`smoke_score: 0.5`，37 次内部运行，48 分钟）。工具的 spec 覆盖面向 GUI 的那一半（verdict 转发、失败文本、状态卡片、超时）。

库的 README 与创作教程在读者会遇到的地方写明了变化：GUI 教程现在展示批准与那次 status 调用，创作教程把研究 gate 列进各阶段，而 README 的随包发布模式列表不再声称 `phase_pack`——它一直只是某台机器做出来的包。

**仍然欠缺的。** 创作运行可以被观察，但还不能被恢复：运行目录保留了一切，用同一个 `--workdir` 重跑是从头开始，而不是从游标继续。issue 要的是「可观察**或**可恢复」，这次交付的是可观察。

## Alternatives considered

**给工具一个 `steer` 那样的 verdict 字段，由模型决定何时询问。** 作为完整答案被否决：调用无法暂停，所以先跑 `create` 再问的模型已经把这次运行弄失败了。说明里让它先问，而 `approve` 是答案传递的方式。

**只把超时调大，其余不动。** 否决：报告里那次被杀掉的运行也没有任何办法看见自己走到了哪一步，而人在「超时了」之后要问的下一件事就是「它到哪儿了」。

**让工具启动一个后台创作运行并返回 run id。** 推迟而不是否决：对一次二十分钟的调用来说那才是诚实的形状，而它需要 console 目前还没有的作业句柄。这次改动提供的句柄是运行目录（`status --run <目录>`），恢复运行是下一步。

**只在提示词层面校验研究计划。** 否决：提示词本来就在要那个数组，而模型两次都回答了散文。gate 才是让契约成真的东西，而且它不花钱。

**等待后台子 agent，而不是禁止它们。** 否决：结束了自己回合的 agent 并没有在等，所以「等待」就会变成运行时尚在模型说完之后把回合继续挂着——为了糊住一个 profile 默认值而发明出来的运行时行为。

**让 `materialise` 接受嵌套，去更深的地方找包。** 否决：那会让盘上的布局取决于模型怎么措辞一个路径，而同一个错误之后会以「模块导入不了的包」的形式再次出现。只剥掉纯粹在重复包根的前缀，保持布局固定，并拒绝无法变成包内路径的东西。
