#!/usr/bin/env python3
"""EXP-013 裁决：深教师闭环分 vs 三锚基线（逐局按 seed 配对，t>2 才算数）。

输入 = bench artifact 下载目录（默认 downloaded/），每臂一个 CSV：
  exp013_hw-default.csv   exp013_hw-champion.csv   exp013_nn-0831.csv
  exp013_mcts-pt-*.csv    exp013_mcts-score-*.csv   ← 全量分片，按文件名拼接
CSV 头为上游 RESULTS_HEADER（31 列）。`seed` 列 = rule_master =
derive_seed(base + plan_index*1000003, [run_idx])，对 (计划, 局) 唯一 →
跨臂同 seed 即同世界，天然 CRN 配对（分片切片不改种子流，见 patch #6/#7）。

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


def load_one(root: Path, tag: str) -> list[dict]:
    hits = glob.glob(str(root / "**" / f"exp013_{tag}.csv"), recursive=True)
    assert hits, f"缺 exp013_{tag}.csv"
    assert len(hits) == 1, f"exp013_{tag}.csv 命中 {len(hits)} 个文件（应为 1）"
    with open(hits[0], newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, f"exp013_{tag}.csv 为空"
    return rows


def load_shards(root: Path, tag: str) -> list[dict]:
    """按 tag 前缀拼接全量分片（文件名 exp013_<tag>-*.csv），并校验 seed 无重复。"""
    hits = sorted(glob.glob(str(root / "**" / f"exp013_{tag}-*.csv"), recursive=True))
    assert hits, f"缺 exp013_{tag}-*.csv 分片"
    rows: list[dict] = []
    seen: set[str] = set()
    for h in hits:
        with open(h, newline="") as f:
            for r in csv.DictReader(f):
                assert r["seed"] not in seen, f"{tag} 出现重复 seed {r['seed']}——分片区间重叠"
                seen.add(r["seed"])
                rows.append(r)
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
    lines: list[str] = []

    # ---- 三锚基线（全量单文件臂）----
    data: dict[str, list[dict]] = {}
    for tag in ANCHORS:
        data[tag] = load_one(root, tag)

    # ---- 深教师两臂（分片拼接；score 臂允许缺席，缺席只 WARN）----
    m_rows = load_shards(root, "mcts-pt")
    try:
        s_rows = load_shards(root, "mcts-score")
    except AssertionError as e:
        print(f"WARN: {e}")
        s_rows = []

    # ---- 保真锚 ----
    print("=== 保真锚（逐位复现校验）===")
    lines.append("=== 保真锚 ===")
    anchors_ok = True
    for tag, want in ANCHORS.items():
        s = [float(r["score"]) for r in data[tag]]
        n, mean, se = stats(s)
        drift = mean - want
        flag = "✅" if abs(drift) < 0.05 else "❌ 漂移超标"
        if abs(drift) >= 0.05:
            anchors_ok = False
        print(f"{tag:<12} n={n} mean={mean:.1f} se={se:.1f}（锚 {want} 偏差 {drift:+.1f} {flag}）")
        lines.append(f"{tag}: n={n} mean={mean:.1f} se={se:.1f} anchor={want} drift={drift:+.1f} {flag}")
    assert anchors_ok, "保真锚破位：补丁链/口径漂移，实验作废（plan.md §方法）"
    assert len({r["seed"] for r in data["hw-default"]}) == 4200, "hw-default seed 去重后 != 4200"

    # ---- 深教师主口径 ----
    assert len(m_rows) == 4200, f"mcts-pt 拼接后 {len(m_rows)} 局 != 4200"
    m_scores = [float(r["score"]) for r in m_rows]
    n, m_mean, m_se = stats(m_scores)
    fails = sum(1 for r in m_rows if r["free_race_ok"] == "0")
    elapses = sorted(float(r["elapsed_ms"]) for r in m_rows)
    print("\n=== EXP-013 深教师（全阶段搜索 / sn64 / 主口径 pt）===")
    print(f"n={n}  均分 {m_mean:.1f}  se {m_se:.1f}  自选未达标 {fails}")
    print(f"单局耗时：均值 {sum(elapses)/n:.0f} ms / p50 {elapses[n//2]:.0f} ms / p95 {elapses[int(n*0.95)]:.0f} ms")
    lines.append(f"mcts-pt: n={n} mean={m_mean:.1f} se={m_se:.1f} fail={fails}")

    # ---- 配对 vs 三锚 ----
    print("\n=== 逐局配对（同 seed = 同世界，CRN）===")
    bases = {tag: {r["seed"]: float(r["score"]) for r in data[tag]} for tag in ANCHORS}
    for tag in ANCHORS:
        diffs = [s - bases[tag][r["seed"]] for r, s in zip(m_rows, m_scores) if r["seed"] in bases[tag]]
        assert len(diffs) == 4200, f"vs {tag}: 可配对局数 {len(diffs)}/4200，种子流漂移"
        d, t = paired(diffs)
        win = sum(1 for x in diffs if x > 0)
        print(f"vs {tag:<12} Δ={d:+.1f}  t={t:+.2f}  胜 {win}/4200")
        lines.append(f"mcts-pt vs {tag}: d={d:+.1f} t={t:+.2f} win={win}/4200")

    # ---- 副口径（只报，不裁决）----
    if s_rows:
        assert len(s_rows) == 4200, f"mcts-score 拼接后 {len(s_rows)} 局 != 4200"
        _, s_mean, s_se = stats([float(r["score"]) for r in s_rows])
        print(f"\n副口径 score：mean={s_mean:.1f} se={s_se:.1f}（只报，不裁决）")
        lines.append(f"mcts-score: mean={s_mean:.1f} se={s_se:.1f}（信息项）")

    # ---- 三档判读（plan.md 合同，主口径）----
    print("\n=== 天花板判定（三档合同，主口径 pt）===")
    if m_mean >= 69000:
        verdict = (
            f"深教师 {m_mean:.1f} ≥ 69000 → 标签里还有肉，蒸馏线继续：\n"
            "  教师还能再做强（sn128/256 剂量实验，同协议只改一个数），v5 重采。"
        )
    elif m_mean >= 66000:
        verdict = (
            f"深教师 {m_mean:.1f} ∈ [66000, 69000) → 学生已接近教师：\n"
            "  训练侧到此为止；70000 只能靠换更强教师（或口径侧 PRINCIPLES §6.3）。"
        )
    else:
        verdict = (
            f"深教师 {m_mean:.1f} < 66000 → 学生（66734.8）已超过深教师：\n"
            "  对蒸馏的理解要重看：单步标签的增量上限存疑，教师侧先修搜索口径再谈蒸馏。"
        )
    print(f"深教师 − 学生纪录 = {m_mean - 66734.8:+.1f}")
    print(verdict)
    lines.append(f"teacher_gap_to_student={m_mean - 66734.8:+.1f}")
    lines.append(verdict)

    Path("SUMMARY.txt").write_text(
        "EXP-013 深教师闭环 bench（教师成绩单 #1）\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print("\nSUMMARY.txt 已写盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
