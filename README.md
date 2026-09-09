<div align="center">

# 🐎 uma-ramen-teacher-lab

**NN 教师实验专用仓（EXP-013/014）**

![仓库](https://img.shields.io/badge/仓库-xf8410-8B5CF6?style=flat-square) ![分支](https://img.shields.io/badge/分支-15-10B981?style=flat-square) ![版本](https://img.shields.io/badge/版本-0-F59E0B?style=flat-square) ![CI](https://img.shields.io/badge/CI-2-3B82F6?style=flat-square)

</div>

---
> 📌 **一句话定位**：NN 教师实验专用仓（EXP-013/014）

## 🧭 项目定位

拉面杯 NN 教师实验专用仓：EXP-013 深教师 RamenMctsTrainer 首次闭环 bench（教师成绩单 #1），EXP-014 on-policy 教师重采（变量 C 采集+重训+裁决一条龙）。实验结论归档回 uma-ramen-nn-lab 主账本。

## ✨ 核心功能
- EXP-013 教师成绩单闭环 bench- EXP-014 on-policy 重采一条龙（采集+重训+裁决）

## 🌿 分支导览（共 15 个分支全览）

<details open>
<summary><b>点击收起/展开全部分支用途说明</b></summary>

| 分支 | 用途说明 |
|---|---|
| `main` | 主干 |
| `workbench/exp013-*（hotfix-shards/ledger-result/teacher-report-card/v3-ucb/v4-budget/v5-fixaggregation）` | EXP-013 六条修复与结果线：分片热修、账本结果、成绩单、UCB v3、预算 v4、聚合修复 v5 |
| `workbench/exp014-*（diag/fix2/fix4/fix5/notify/notifyfix/onpolicy/yamlfix）` | EXP-014 八条线：诊断、三次修复、通知双修、on-policy 主线、YAML 修复 |

</details>

## 🏷️ 版本历史

无 release，实验产物走 CI artifact 与账本。

完整版本列表 ➡️ [Releases 页](../../releases)

## ⚙️ CI 流水线（共 2 条）

| 流水线 | 用途说明 |
|---|---|
| EXP-013 教师成绩单 | 产标签机器首次闭环 bench |
| EXP-014 on-policy 教师重采 | 变量 C 采集+重训+裁决一条龙 |


---

## 📜 历史介绍存档

> 以下为仓库原有介绍，**内容未删改**，仅移入存档区（新版介绍以本页上方为准）。

<details>
<summary><b>点击展开原 README</b></summary>

# uma-ramen-teacher-lab

教师实验仓。主仓（`xf8410/uma-ramen-nn-lab`）管蒸馏线，这里管**教师本人**：给从未上过考场的教师补成绩单，然后按成绩单迭代教师。

## 为什么独立开仓

- 教师实验会持续迭代（sn 剂量、搜索门控、rollout 评估器变体），与蒸馏实验生命周期不同；
- 独立 Actions 页 = 独立红叉噪音隔离（主仓曾因历史红叉噪音被迫迁仓跑 EXP-010）。

## 与上游 / 其他仓的关系

- 上游代码**只读 checkout 锁 SHA**：`muxueliunian/umaai-rs-muxue @ d27a6ebd`（与主仓 008/009/011 同 pin）；
- 所有改动以 patch 注入 CI（`experiments/EXP-*/patch_*.py`），锚点断言 fail-fast，永不 fork-commit、永不向上游提 PR；
- 结论一行 append 回主仓 `experiments/LEDGER.md`（append-only）。

## 实验索引

| ID | 主题 | 状态 | plan |
|---|---|---|---|
| EXP-013 | **深教师成绩单 #1**：RamenMctsTrainer 全阶段搜索首次闭环 bench（教师 = 产 008/009 标签的同款机器） | 待跑 | [plan](experiments/EXP-013-teacher-report-card/plan.md) |

## 铁律（继承主仓 PRINCIPLES.md）

1. 只认闭环——标签自估分（如 65071.1）不是闭环分，两者永不互替；
2. 保真锚每轮逐位复现（65438.2 / 65554.2 / 66734.8），破位即作废；
3. 没有 t 检验不下结论（配对、t>2）；
4. 账本 append-only。


</details>
