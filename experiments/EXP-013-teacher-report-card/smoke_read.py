#!/usr/bin/env python3
"""EXP-013 冒烟读数：从 2 计划×8 局 CSV 量单局耗时，外推全量预算并对照 job 超时。"""
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

    full_games = 4200
    shards = 20  # exp-013.yml 矩阵：每臂 20 分片 × 27 计划
    per_cpu_h = mean * full_games / 3.6e6
    per_shard_games = 27 * 8
    per_shard_min = mean * per_shard_games / 4 / 6e4  # 4 vCPU 并行，折算分钟

    print(f"冒烟 n={n} 局（应为 16）  自选未达标={fails}")
    print(f"分数：mean={sum(scores)/n:.1f} min={min(scores):.0f} max={max(scores):.0f}")
    print(f"单局耗时：均值 {mean:.0f} ms / p50 {p50:.0f} ms / p95 {p95:.0f} ms")
    print(f"全量外推：单臂 {per_cpu_h:.1f} CPU·h；{shards} 分片时每片 "
          f"{per_shard_games} 局 ≈ {per_shard_min:.0f} min 墙钟（4 vCPU 并行已折算）")
    print(f"job 超时对照：每片 ≈ {per_shard_min:.0f} min vs bench job timeout 180 min "
          f"→ {'✅ 安全' if per_shard_min < 120 else '⚠️ 紧张，需加分片数'}")
    if fails > 0:
        print("⚠️ 自选未达标 > 0：深教师把自选比赛打挂了，这本身就是读数（nn 臂有 race_shield，教师没有）")
    Path("SMOKE.txt").write_text(
        f"n={n} mean_ms={mean:.0f} p50_ms={p50:.0f} p95_ms={p95:.0f}\n"
        f"score_mean={sum(scores)/n:.1f} fail={fails}\n"
        f"full_single_arm_cpu_h={per_cpu_h:.2f} per_shard_min={per_shard_min:.0f}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
