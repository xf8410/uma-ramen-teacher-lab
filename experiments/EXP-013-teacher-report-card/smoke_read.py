#!/usr/bin/env python3
"""EXP-013 冒烟读数：从 2 计划×4 局 CSV 量单局耗时，外推全量预算并对照 job 超时。"""
from __future__ import annotations

import csv
import glob
import sys
from pathlib import Path


def main() -> int:
    hits = glob.glob(sys.argv[1])
    assert hits, f"找不到冒烟 CSV: {sys.argv[1]}"
    rows = list(csv.DictReader(open(hits[0], newline="")))
    assert rows, "冒烟 CSV 为空"
    elapses = sorted(float(r["elapsed_ms"]) for r in rows)
    n = len(elapses)
    mean = sum(elapses) / n
    p50 = elapses[n // 2]
    p95 = elapses[int(n * 0.95)]
    scores = [float(r["score"]) for r in rows]
    fails = sum(1 for r in rows if r["free_race_ok"] == "0")

    # 全量预算（v4）：单臂 4 rep = 2100 局；20 片 × 27 计划 × 4 局 = 108 局/片
    full_games = 2100
    shards = 20
    per_shard_games = 27 * 4
    per_cpu_h = mean * full_games / 3.6e6
    # 单片墙钟：108 局 × 单局核时 / 4 核。冒烟在 4 核上跑 2 并发局，
    # 单局 elapsed_ms 已含并发折减 → 用 mean×games/4 是核时口径的下界近似，
    # 真实值可能到 mean×games（完全串行）。打印区间。
    lo = mean * per_shard_games / 4 / 6e4
    hi = mean * per_shard_games / 6e4

    print(f"冒烟 n={n} 局（应为 8）  自选未达标={fails}")
    print(f"分数：mean={sum(scores)/n:.1f} min={min(scores):.0f} max={max(scores):.0f}")
    print(f"单局耗时：均值 {mean:.0f} ms / p50 {p50:.0f} ms / p95 {p95:.0f} ms")
    print(f"全量外推：单臂 {per_cpu_h:.1f} CPU·h；{shards} 分片 × {per_shard_games} 局/片")
    print(f"单片墙钟区间：≈ {lo:.0f}–{hi:.0f} min（取决于搜索多线程效率）vs timeout 180 min "
          f"→ {'✅ 安全' if hi < 170 else '⚠️ 上界贴线，需减每片计划数'}")
    if fails > 0:
        print("⚠️ 自选未达标 > 0：教师选手把自选比赛打挂了，这本身就是读数（nn 臂有 race_shield，教师没有）")
    Path("SMOKE.txt").write_text(
        f"n={n} mean_ms={mean:.0f} p50_ms={p50:.0f} p95_ms={p95:.0f}\n"
        f"score_mean={sum(scores)/n:.1f} fail={fails}\n"
        f"full_single_arm_cpu_h={per_cpu_h:.2f} per_shard_min_range={lo:.0f}-{hi:.0f}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
