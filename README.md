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
