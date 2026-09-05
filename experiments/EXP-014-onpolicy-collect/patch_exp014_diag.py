#!/usr/bin/env python3
"""EXP-014 diag：采样钩子零触发的定位探针。

run 33965115459 实测结果（重要正面证据）：
- build ✅（编译全过 + 1 局熔断出 manifest）
- smoke 两个 bench 均分 **68257 == 68257 逐位一致** → record_ordered_rollouts 中性实证通过，
  采集不改游戏行为（这是今晚最想钉死的前提，已钉死）
- 但 smoke_check 读 manifest：accepted=0 → 记录器存在、finalize 跑了、**record 一次都没成过**。
  静态推演矛盾（finalize 能看到记录器，hook 却疑似看不到），停止纸上分析，上运行时探针：
  init / hook 挂载 / record 前 3 次 / finalize 四个点全部打 DIAG 行，fuse 直接断言 accepted>0。
"""
from pathlib import Path
import sys

BENCH = Path("crates/umasim/tools/data_collection/ramen_space_bench.rs")

# (旧, 新, 说明)
PATCHES = [
    (
        """        let on_disk = std::fs::read_dir(&output_dir)
            .map_err(|e| anyhow::anyhow!("EXP-014 读取输出目录失败: {e}"))?
            .filter_map(|e| e.ok())
            .count();""",
        """        println!(
            "EXP-014 DIAG new: cwd={:?} dir={:?} abs={:?}",
            std::env::current_dir().map(|p| p.display().to_string()).unwrap_or_default(),
            output_dir.display().to_string(),
            std::fs::canonicalize(&output_dir).map(|p| p.display().to_string()).unwrap_or_else(|e| format!("?{e}"))
        );
        let on_disk = std::fs::read_dir(&output_dir)
            .map_err(|e| anyhow::anyhow!("EXP-014 读取输出目录失败: {e}"))?
            .filter_map(|e| e.ok())
            .count();""",
        "new(): 打 cwd 与输出目录绝对路径",
    ),
    (
        """fn init_sample_recorder(args: &BenchArgs) -> Result<()> {
    let recorder = if args.export_samples.is_empty() {
        None
    } else {
        Some(Arc::new(SampleRecorder::new(args)?))
    };
    SAMPLE_RECORDER""",
        """fn init_sample_recorder(args: &BenchArgs) -> Result<()> {
    let recorder = if args.export_samples.is_empty() {
        None
    } else {
        Some(Arc::new(SampleRecorder::new(args)?))
    };
    println!(
        "EXP-014 DIAG init: dir={:?} recorder={}",
        args.export_samples,
        recorder.is_some()
    );
    SAMPLE_RECORDER""",
        "init(): 打记录器是否构造",
    ),
    (
        """fn sample_hook(plan_id: usize, run_idx: u64) -> Option<Arc<SearchHook>> {
    let rec = sample_recorder()?;""",
        """fn sample_hook(plan_id: usize, run_idx: u64) -> Option<Arc<SearchHook>> {
    let rec = match sample_recorder() {
        Some(r) => r,
        None => {
            println!("EXP-014 DIAG hook: plan={plan_id} run={run_idx} 记录器缺失!!!");
            return None;
        }
    };
    println!("EXP-014 DIAG hook: plan={plan_id} run={run_idx} 已挂载");""",
        "hook(): 打每局挂载结果（缺失显式喊话）",
    ),
    (
        """    fn record(
        &self, game: &RamenGame, stage: &RamenStage, output: &RamenSearchOutput, index: u64
    ) -> Result<()> {
        let sample = output""",
        """    fn record(
        &self, game: &RamenGame, stage: &RamenStage, output: &RamenSearchOutput, index: u64
    ) -> Result<()> {
        static DIAG_N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let diag_n = DIAG_N.fetch_add(1, Ordering::SeqCst);
        if diag_n < 3 {
            println!("EXP-014 DIAG record#{diag_n}: index={index} stage={stage:?}");
        }
        let sample = output""",
        "record(): 前 3 次调用打点（确认钩子真的进来过）",
    ),
    (
        """        let accepted: u64 = inner.parts.iter().map(|p| p.samples as u64).sum();""",
        """        let accepted: u64 = inner.parts.iter().map(|p| p.samples as u64).sum();
        println!(
            "EXP-014 DIAG finalize: parts={} accepted={} batch={} index_min={:?} index_max={:?}",
            inner.parts.len(),
            accepted,
            inner.batch.len(),
            inner.index_min,
            inner.index_max
        );""",
        "finalize(): 打分片/条数/index 区间快照",
    ),
]


def main() -> int:
    text = BENCH.read_text(encoding="utf-8")
    ok = True
    for i, (old, new, note) in enumerate(PATCHES, 1):
        count = text.count(old)
        if count != 1:
            print(f"PATCH FAIL: 替换 #{i}（{note}）锚点出现 {count} 次（应为 1）")
            ok = False
            continue
        text = text.replace(old, new)
        print(f"PATCH OK #{i}: {note}")
    if not ok:
        return 1
    BENCH.write_text(text, encoding="utf-8")
    print("PATCH ALL OK: EXP-014 诊断探针就位（init/hook/record/finalize 四点）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
