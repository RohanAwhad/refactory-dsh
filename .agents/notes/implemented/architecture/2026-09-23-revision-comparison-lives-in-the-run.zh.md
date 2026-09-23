# Agent Note: A revision is compared, or it says why it was not

Status: implemented

[English](2026-09-23-revision-comparison-lives-in-the-run.md) | 中文

## Problem

`refactory ceo "…" --mode edit` 文档里说，修订会「拒绝变差」。真跑一次就知道它并没有。这次运行只在 `facts.json` 里记了 `revision_of`，别的什么都没有：没有修订前的分数，没有修订后的分数，包的 `.origin.json` 只写着 `origin: create-mode`。比较、拒绝，以及修订所依据的那个状态的摘要，全都住在 `edit.edit()` 里——一个 console script 从来不调用的函数。只有 `python -m refactory_dsh.edit` 能走到它，而没有任何文档让人去跑那条命令。

同一次运行还暴露了两个较小的不实之处。对根本没有 `score()` 的创作模式，graph runner 打印 `"best_score": 0`，读起来像「得了零分」，而真相是「没有测量」。输出里也没有任何地方说明包落在哪里、得了多少分，所以这次运行自己的事实只有事后打开 `facts.json` 的人才能看到。

那条记录 `--mode edit` 的 recipe 一直断言输出里含有 `before_score` 和 `after_score`——这次运行根本产生不了的字符串。它属于 `model` 档，没有 gate 跑它；等到有人花二十分钟跑一次，它第一次就会失败。

## Decision

**比较属于运行的 `smoke` 节点。** 它是刚刚给新模式打过分的那一步，因此只有它同时握着两个数字而不必把数字传来传去；对修订而言，它还会给被修订的模式打分并记下这一对。DSH 上的 `--mode edit` 现在会以失败的方式拒绝变差，失败信息同时点名两个分数；而 `python -m refactory_dsh.edit` 会从自己的比较得出同一个决定，因为 `EditConfig.allow_regression` 会作为旋钮进入 graph。

**只有一次 smoke 运行能同时测量两者时，两个模式才被比较。** 源模式没有 `score()`，或它的图会调用模型，都会被记成「未比较」并附上原因，运行继续。模型调用的一次 smoke 采样不是测量，凭一次采样拒绝修订会拒错东西；危险的方向是另一个——一个从未真正检查过、却默默读作「没有变差」的结果。所以事实是 `before_score: null` 加上一条说明，绝不暗示通过。

**来源写在包旁边，而不只写在运行里。** `revision_of`、`revision_of_state`、它的摘要、`before_score`、`after_score` 与 `regressed` 写进工作区里那个包的 `.origin.json`，因为「它改自什么、改自哪个状态、当时得了多少分」是关于这个包的事实，必须比产生它的运行目录活得更久。运行目录里也留一份同样的记录 `revision.json`，这样被拒绝的修订也留下自己的证据。

**逃生口是一个旋钮，而且能从命令行够到。** `allow_regression` 在创作模式上声明，可以用 `ceo --knob allow_regression=1` 设置。人可能有意用分数换分数不衡量的东西，而在此之前从 CLI 根本没有办法这样表态——这个开关只存在于 Python 入口点上。

**跑完的运行会说自己记了什么。** 成功结束后，`interactive.run` 会打印一行这次运行自己的事实：包落在哪里、得了多少分；若是修订，还包括两个分数以及是否变差。DSH 的报告是 agent 的最后一句话；人要据此行动的事实不该需要去开一个文件。

**`best_score: null` 表示没有测量。** 当没有任何状态文件记下分数时，graph runner 的 `bestScore` 返回 `0`，这与真实的零分无法区分。现在它返回 `null`，与 smoke 路径对无法测量的模式早已报告的 `score: null` 一致。

## Consequences

在人真正会走的路径上，`--mode edit` 说到做到了，`tools/recipes.py` 里的 `edit-mode` recipe 在会真正跑它的 `model` 档通过。教程在声称拒绝变差的地方说明了「未比较」是什么意思，而 `revision.json` 是 smoke 节点新增的声明写入，validator 现在会强制它。

比较要对源模式多跑一次 smoke，所以一次修订比同等规模的创建多花一次沙箱运行。若修订的对象是图会调用模型的模式，则不多花，因为那种比较是出于决定被跳过，而不是出于偶然。

被拒绝的修订仍会把它的包留在工作区：`register` 在 `smoke` 之前运行，所以运行是在包已经存在之后才停下的。这与 Python 入口点原有的顺序相同，也正是拒绝信息要点名那个可以接受它的旋钮的原因——包留在那里供人查看，失败的是这次运行。

`python -m refactory_dsh.edit` 为自己的调用方保留了自己的比较。现在有两个地方决定同一件事，下次动那个入口点时值得去掉；这次没有去掉，因为它的返回值正是它的调用方要打印的东西。

## Alternatives considered

**只把缺口写进文档，不去补。** 也可以把 README 和教程改写成「`ceo --mode edit` 记录来源，拒绝变差在 Python 入口点上」。否决：拒绝变差正是修订自成一种模式而不是一段提示词的原因，而修法只是让一个节点读两个它本来就有的数字。

**在 `register` 与 `smoke` 之间新增一个 graph 节点。** 两点否决：新模式分数存在的地方就是 smoke 那一步，单独一个节点还得把分数递过去；而 create 的图只接线一次，非 gate 节点只允许一条出边，所以插入节点意味着移动那条边，而不是加一道检查。

**比较两个都会调用模型的模式的分数。** 否决：对会调用模型的模式做一次 smoke，是一个分布里的一次采样，据此计算的拒绝就是把抛硬币当成变差来报告。

**在打印数字的地方把零分改掉。** 也可以让 Python 侧在输出时把 `0` 改写成 `null`。否决：这个数字是 graph runner 从运行目录自己的状态文件里读出来的，而「记了零」与「什么都没记」的区别正是在那里才存在。
