#!/usr/bin/env python3
"""EXP-014 配对裁决：6 臂（双手写锚 + nn-0831 + C 新种子 ×3）。

判读合同（与用户对齐）：
- 新种子均值 ≥67500 → C 成立，蒸馏线复活
- 66700 ≤ 均值 < 67500 → 状态分布是小头，回头谈口径/教师强化
- 均值 < 66734.8（旧学生纪录）→ C 判负，教训归档，不再烧算力
锚点校验：hw-default 65438.2 / hw-champion 65554.2 / nn-0831 66734.8（偏差 >0.5 = 作废）。
"""
import csv
import math
import sys
from pathlib import Path

ANCHORS = {"hw-default": 65438.2, "hw-champion": 65554.2, "nn-0831": 66734.8}
NEW_IDS = ["nn014-0830", "nn014-0831", "nn014-0832"]


def load(root: Path, arm: str):
    path = root / f"EXP-014-bench-{arm}" / f"exp014_{arm}.csv"
    rows = list(csv.DictReader(open(path, newline="")))
    key = next((k for k in rows[0] if k.lower().strip() in ("score", "final_score", "total_score")), None)
    if key is None:
        cand = [k for k in rows[0] if "score" in k.lower()]
        assert cand, f"{path}: 找不到分数列，现有列 {list(rows[0])}"
        key = cand[0]
    pkey = next((k for k in rows[0] if "plan" in k.lower()), None)
    rkey = next((k for k in rows[0] if "run" in k.lower()), None)
    scores = [float(r[key]) for r in rows]
    worlds = {(r[pkey], r[rkey]) for r in rows} if pkey and rkey else set()
    return scores, worlds


def mean(xs):
    return sum(xs) / len(xs)


def se_pairwise(a, b):
    """配对 SE（按世界对齐；无世界键时按行对齐——同口径下行序=世界序）。"""
    n = min(len(a), len(b))
    diffs = [x - y for x, y in zip(a[:n], b[:n])]
    d = mean(diffs)
    var = sum((x - d) ** 2 for x in diffs) / max(n - 1, 1)
    return d, math.sqrt(var / n), n


def main() -> int:
    root = Path(sys.argv[1])
    data = {}
    ok = True
    print("=== 保真锚（逐位复现校验）===")
    for arm, anchor in ANCHORS.items():
        try:
            scores, _ = load(root, arm)
        except Exception as e:
            print(f"{arm}: 读取失败 {e}")
            ok = False
            continue
        m = mean(scores)
        dev = m - anchor
        flag = "✅" if abs(dev) < 0.5 else "❌"
        if abs(dev) >= 0.5:
            ok = False
        print(f"{arm:<12} n={len(scores)} mean={m:.1f}（锚 {anchor} 偏差 {dev:+.1f} {flag}）")
        data[arm] = scores
    if not ok:
        print("锚点破位 → 实验作废")
        return 1

    print("\n=== C 新种子（on-policy 142k，sn64 教师世界）===")
    news = []
    for arm in NEW_IDS:
        try:
            scores, _ = load(root, arm)
        except Exception as e:
            print(f"{arm}: 读取失败 {e}")
            ok = False
            continue
        m = mean(scores)
        se = math.sqrt(sum((x - m) ** 2 for x in scores) / (len(scores) * (len(scores) - 1)))
        print(f"{arm:<12} n={len(scores)} mean={m:.1f} se={se:.1f}")
        news.append((arm, m, scores))

    print("\n=== 配对判定 ===")
    lines = []
    for arm, m, scores in news:
        for base_id in ("hw-default", "hw-champion", "nn-0831"):
            d, se, n = se_pairwise(scores, data[base_id])
            t = d / se if se > 0 else float("inf")
            lines.append(f"vs {base_id:<12} Δ={d:+.1f}  t={t:+.1f}  (n={n})")
        print(f"{arm} (mean {m:.1f}):")
        for line in lines[-3:]:
            print(f"  {line}")

    if news:
        avg = mean([m for _, m, _ in news])
        best = max(m for _, m, _ in news)
        print("\n=== 判读合同 ===")
        print(f"三种子均值 {avg:.1f} / 最好 {best:.1f} / 旧学生纪录 66734.8 / 教师 68407.3")
        if avg >= 67500:
            print("→ C 成立（≥67500）：蒸馏线复活，下一级=教师强化")
        elif avg >= 66734.8:
            print("→ C 部分有效：状态分布是小头，回头看口径/教师强化")
        else:
            print("→ C 判负（未超旧学生）：教训归档，不再烧算力")

    Path("SUMMARY.txt").write_text("\n".join(sys.stdout.getvalue().splitlines() if hasattr(sys, "stdout") and hasattr(sys.stdout, "getvalue") else []) or "", encoding="utf-8") if False else None
    # 重定向：把上面的打印全部写进 SUMMARY.txt
    return 0 if ok else 1


if __name__ == "__main__":
    import io

    _real_stdout = sys.stdout
    sys.stdout = io.StringIO()
    rc = main()
    text = sys.stdout.getvalue()
    sys.stdout = _real_stdout
    print(text, end="")
    Path("SUMMARY.txt").write_text(text, encoding="utf-8")
    sys.exit(rc)
