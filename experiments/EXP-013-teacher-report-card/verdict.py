#!/usr/bin/env python3
"""EXP-013 裁决：深教师闭环分 vs 三锚基线（逐局按 seed 配对，t>2 才算数）。

输入 = bench artifact 目录（各含 exp013_<tag>.csv）。CSV 头为上游 RESULTS_HEADER
（31 列，`build/seed/score/...`）。`seed` 列是 rule_master = derive_seed(base+plan*1000003, [run_idx])，
对 (计划, 局) 唯一 → 跨臂同 seed 即同世界，天然 CRN 配对。

保真锚（不逐位复现 = 实验作废，先查补丁链与口径再谈结论）：
  hw-default  65438.2   （EXP-006b/008b/010b/011a 四轮复现）
  hw-champion 65554.2   （同上）
  nn-0831     66734.8   （EXP-009b 纪录模型，artifact 主仓 run 33716171772）

主口径 = 深教师 --mcts-selection pt（与标签采集的选择口径一致）。
判读三档写死在 verdict 行内（plan.md 合同），人不现场解释数字。
"""
from __future__ import annotations

import csv
import glob
import math
import sys
from pathlib import Path

ANCHORS = {
    "hw-default": 65438.2,
    "hw-champion": 65554.2,
    "nn-0831": 66734.8,
}
# 主口径臂（各 4200 局）；副口径 mcts-score 只报不做裁决
ARMS = ["hw-default", "hw-champion", "nn-0831", "mcts-pt", "mcts-score"]


def load(tag: str, root: Path) -> list[dict]:
    hits = glob.glob(str(root / f"**/exp013_{tag}.csv"), recursive=True)
    assert hits, f"缺 {tag} 的 CSV"
    with open(hits[0], newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, f"{tag} CSV 为空"
    return rows


def stats(scores: list[float]) -> tuple[int, float, float]:
    n = len(scores)
    mean = sum(scores) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in scores) / (n - 1)) if n > 1 else 0.0
    return n, mean, sd / math.sqrt(n)


def paired(diffs: list[float]) -> tuple[float, float]:
    m = sum(diffs) / len(diffs)
    se = math.sqrt(sum((x - m) ** 2 for x in diffs) / (len(diffs) - 1) / len(diffs))
    return m, (m / se if se > 0 else 0.0)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "downloaded")
    data: dict[str, list[dict]] = {}
    for tag in ARMS:
        try:
            data[tag] = load(tag, root)
        except AssertionError as e:
            print(f"WARN: {e}")
    missing = [t for t in ANCHORS if t not in data]
    assert not missing, f"保真锚缺臂: {missing}"
    assert "mcts-pt" in data, "缺主口径 mcts-pt 臂——本轮唯一要测的选手没上场，拒绝出结论"

    # ---- 保真锚 ----
    print("=== 保真锚（逐位复现校验）===")
    lines = []
    anchors_ok = True
    for tag, want in ANCHORS.items():
        s = [float(r["score"]) for r in data[tag]]
        n, mean, se = stats(s)
        drift = mean - want
        flag = "✅" if abs(drift) < 0.05 else "❌ 漂移超标"
        if abs(drift) >= 0.05:
            anchors_ok = False
        print(f"{tag:<12} n={n} mean={mean:.1f} se={se:.1f}（锚 {want} 偏差 {drift:+.1f} {flag}）")
        lines.append(f"{tag}: mean={mean:.1f} se={se:.1f} anchor={want} drift={drift:+.1f} {flag}")
    assert anchors_ok, "保真锚破位：补丁链/口径漂移，实验作废（PLAN.md §方法）"

    # ---- 深教师主口径 ----
    m_rows = data["mcts-pt"]
    m_scores = [float(r["score"]) for r in m_rows]
    n, m_mean, m_se = stats(m_scores)
    fails = sum(1 for r in m_rows if r["free_race_ok"] == "0")
    elapses = [float(r["elapsed_ms"]) for r in m_rows]
    print(f"\n=== EXP-013 深教师（全阶段 sn64，主口径 pt，n={n}）===")
    print(f"均分 {m_mean:.1f}  se {m_se:.1f}  sd {math.sqrt(n)*m_se:.0f}  自选未达标 {fails}")
    print(f"单局耗时：均值 {sum(elapses)/len(elapses):.0f} ms / p50 {sorted(elapses)[len(elapses)//2]:.0f} ms / p95 {sorted(elapses)[int(len(elapses)*0.95)]:.0f} ms")
    lines.append(f"mcts-pt: n={n} mean={m_mean:.1f} se={m_se:.1f} fail={fails}")

    # ---- 配对 vs 三锚 ----
    print("\n=== 逐局配对（同 seed = 同世界）===")
    verdict = None
    for tag in ["hw-default", "hw-champion", "nn-0831"]:
        b = {r["seed"]: float(r["score"]) for r in data[tag]}
        diffs = [s - b[r["seed"]] for s, r in zip(m_scores, m_rows) if r["seed"] in b]
        assert len(diffs) >= 0.9 * len(m_scores), f"vs {tag}: 可配对局数 {len(diffs)}/{len(m_scores)} 过低，疑似种子流漂移"
        d, t = paired(diffs)
        win = sum(1 for x in diffs if x > 0)
        print(f"vs {tag:<12} Δ={d:+.1f}  t={t:+.2f}  胜率 {win}/{len(diffs)}")
        lines.append(f"mcts-pt vs {tag}: d={d:+.1f} t={t:+.2f} win={win}/{len(diffs)}")

    # ---- 副口径（只报）----
    if "mcts-score" in data:
        s_scores = [float(r["score"]) for r in data["mcts-score"]]
        _, s_mean, s_se = stats(s_scores)
        print(f"\n副口径 score 口径 mean={s_mean:.1f} se={s_se:.1f}（只报，不裁决）")
        lines.append(f"mcts-score: mean={s_mean:.1f} se={s_se:.1f} (信息项)")

    # ---- 三档判读（plan.md 合同）----
    print("\n=== 天花板判定（三档合同，主口径）===")
    if m_mean >= 69000:
        verdict = (
            f"深教师 {m_mean:.1f} ≥ 69000 → 标签里还有肉，蒸馏线继续：\n"
            f"  教师还能再做强（sn128/256 剂量实验同协议只改一个数），v5 重采。"
        )
    elif m_mean >= 66000:
        verdict = (
            f"深教师 {m_mean:.1f} ∈ [66000, 69000) → 学生已接近教师：\n"
            f"  训练侧到此为止；70000 只能靠换更强教师（或口径侧 PRINCIPLES §6.3）。"
        )
    else:
        verdict = (
            f"深教师 {m_mean:.1f} < 66000 → 学生（66734.8）已超过深教师：\n"
            f"  对蒸馏的理解要重看：单步标签的增量上限存疑，教师侧先修搜索口径再谈蒸馏。"
        )
    gap = 66734.8 - m_mean
    print(f"深教师 − 学生纪录 = {m_mean - 66734.8:+.1f}（教师相对学生 {('弱' if gap > 0 else '强')} {abs(gap):.1f}）")
    print(verdict)
    lines.append(f"verdict: teacher={m_mean:.1f} gap_to_student={m_mean - 66734.8:+.1f}")
    lines.append(verdict)

    Path("SUMMARY.txt").write_text(
        "EXP-013 深教师闭环 bench（教师成绩单 #1）\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print("\nSUMMARY.txt 已写盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
