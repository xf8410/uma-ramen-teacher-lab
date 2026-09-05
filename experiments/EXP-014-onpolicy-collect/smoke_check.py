#!/usr/bin/env python3
"""EXP-014 冒烟闸门：manifest 一致性 + CRN 对拍 + 体量投影。

用法：
  python3 smoke_check.py --samples DIR --smoke-csv A.csv --ref-csv B.csv \
      [--expect-min-samples 50] [--project-games 1080]

三道检查：
1. manifest：accepted ≥ 下限、parts 求和 == accepted、finished_at 已置、
   全部前提为真值（record_ordered_rollouts=true / use_ucb=false / radical=1.4）、
   index % 525 语义抽查（index_start % 525 == plan_offset）。
2. CRN 对拍：同 (plan, run) 的终局分必须与参考 CSV（EXP-013 教师臂）**逐位一致**——
   采集只加 record_ordered_rollouts（上游文档：只影响记录，对局无关），若不一致 =
   采集改变了游戏行为 = 全量必须停。列名用启发式匹配，匹配不到则 WARN 不拦（人工看）。
3. 体量投影：按 part 字节数 ×（目标局数 / 冒烟局数）外推全量数据集大小。
"""
import argparse
import csv
import json
import sys
from pathlib import Path


def load_rows(path: str):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def pick_col(header, candidates):
    lowered = {h.lower().strip(): h for h in header}
    for cand in candidates:
        for low, orig in lowered.items():
            if low == cand:
                return orig
    for cand in candidates:
        for low, orig in lowered.items():
            if cand in low:
                return orig
    return None


def crn_compare(smoke_csv: str, ref_csv: str) -> int:
    """返回 0 = 通过/WARN 放行，1 = 硬失败。"""
    a = load_rows(smoke_csv)
    b = load_rows(ref_csv)
    pa = pick_col(a[0], ["plan_index", "plan", "plan_id"])
    ra = pick_col(a[0], ["run_index", "run_idx", "run"])
    sa = pick_col(a[0], ["score", "final_score", "total_score"])
    pb = pick_col(b[0], ["plan_index", "plan", "plan_id"])
    rb = pick_col(b[0], ["run_index", "run_idx", "run"])
    sb = pick_col(b[0], ["score", "final_score", "total_score"])
    if not all([pa, ra, sa, pb, rb, sb]):
        print(
            f"[WARN] 无法自动识别列（smoke={list(a[0].keys())} ref={list(b[0].keys())}）"
            "——CRN 对拍转人工，不拦截"
        )
        return 0
    ref = {(r[pb], r[rb]): r[sb] for r in b}
    checked = mism = 0
    for r in a:
        key = (r[pa], r[ra])
        if key not in ref:
            continue
        checked += 1
        if abs(float(r[sa]) - float(ref[key])) > 1e-6:
            mism += 1
            print(f"[MISMATCH] plan={key[0]} run={key[1]} 采集={r[sa]} 参考={ref[key]}")
    print(f"[CRN] 可比对拍 {checked} 组，不一致 {mism}")
    if checked == 0:
        print("[WARN] 无可比对（plan/run 无交集）——转人工，不拦截")
        return 0
    if mism:
        print("[FAIL] record_ordered_rollouts 改变了游戏行为（对拍不一致）——全量必须停")
        return 1
    print("[OK] 采集与 EXP-013 教师臂逐位一致（record_ordered 中性实证）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", required=True)
    ap.add_argument("--smoke-csv", required=True)
    ap.add_argument("--ref-csv", required=True)
    ap.add_argument("--expect-min-samples", type=int, default=50)
    ap.add_argument("--project-games", type=int, default=0)
    args = ap.parse_args()

    samples = Path(args.samples)
    manifest = json.loads((samples / "manifest.json").read_text())
    parts = manifest["parts"]
    accepted = manifest["accepted"]
    assert accepted >= args.expect_min_samples, f"样本过少: {accepted} < {args.expect_min_samples}"
    assert sum(p["samples"] for p in parts) == accepted, "parts 求和 != accepted"
    assert manifest["finished_at"], "manifest 未置 finished_at"
    premises = manifest["premises"]
    assert premises["record_ordered_rollouts"] is True, "前提#1 未开"
    assert premises["use_ucb"] is False, "前提#2 use_ucb 必须为 false"
    assert abs(premises["radical_factor_max"] - 1.4) < 1e-9, "前提#3 radical 必须 1.4"
    total_bytes = sum(p["signature"]["size"] for p in parts)
    print(f"[OK] manifest: accepted={accepted} parts={len(parts)} bytes={total_bytes}")
    print(f"[OK] 前提: sn={manifest['search_n']} ucb=off radical=1.4 record=on")

    smoke_games = args.expect_min_samples and 0
    if args.project_games:
        # 冒烟局数 = accepted / 平均每局样本数；这里用外部传入的冒烟局数近似：
        # smoke 阶段 expect_min_samples 即样本下限，真实局数由 CSV 行数推出
        rows = load_rows(args.smoke_csv)
        n_games = len(rows)
        if n_games and accepted:
            per_game = accepted / n_games
            print(f"[OK] 每局样本 ≈{per_game:.0f}（{accepted}/{n_games} 局）")
            print(
                f"[投影] 全量 {args.project_games} 局 ≈ {per_game * args.project_games / 1e3:.0f}k 样本 / "
                f"{total_bytes * args.project_games / max(n_games, 1) / 1e6:.0f} MB"
            )

    rc = crn_compare(args.smoke_csv, args.ref_csv)
    return rc


if __name__ == "__main__":
    sys.exit(main())
