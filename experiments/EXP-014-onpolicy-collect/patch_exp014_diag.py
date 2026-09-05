#!/usr/bin/env python3
"""EXP-014 diag v2：采样钩子零触发定位探针 + 分片登记修复（fix5）。

run 33979393137 的 DIAG 实锤（探针目的达成）：
- record#0-2 正常调用（index=0/525/1050，stage=Train/RegionSelect）
- 「写入 part_000000.bin (120 条)」→ 分片文件落盘成功
- finalize: parts=0 accepted=0 batch=0 index_max=Some(62475)
→ 根因：flush() 写盘后返回的 ExportPart 从未 push 进 inner.parts
  （record 分片路径 + finalize 尾批路径两处同 bug）→ manifest parts=[]/accepted=0。
  读回校验遍历空 parts，total==accepted==0 自检"通过"。
→ 修复：两处 flush 后各补 inner.parts.push(part)。

其余探针保留（下一轮确认后可删）：init/hook/record前3次/finalize 快照。
"""
from pathlib import Path
import sys

BENCH = Path("crates/umasim/tools/data_collection/ramen_space_bench.rs")

# (旧, 新, 说明)
PATCHES = [
    # ---------- fix5：分片登记缺失（run 33979393137 根因） ----------
    (
        """        if inner.batch.len() >= self.shard_size {
            let part = self.flush(&mut inner)?;
            println!("EXP-014 写入 {} ({} 条)", part.name, part.samples);
        }""",
        """        if inner.batch.len() >= self.shard_size {
            let part = self.flush(&mut inner)?;
            println!("EXP-014 写入 {} ({} 条)", part.name, part.samples);
            inner.parts.push(part);
        }""",
        "fix5a: record 分片路径登记 part",
    ),
    (
        """        if !inner.batch.is_empty() {
            let part = self.flush(&mut inner)?;
            println!("EXP-014 写入 {} ({} 条)", part.name, part.samples);
        }""",
        """        if !inner.batch.is_empty() {
            let part = self.flush(&mut inner)?;
            println!("EXP-014 写入 {} ({} 条)", part.name, part.samples);
            inner.parts.push(part);
        }""",
        "fix5b: finalize 尾批路径登记 part",
    ),
    # ---------- diag 探针（保留一轮） ----------
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
        "hook(): 打每局挂载结果",
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
        "record(): 前 3 次调用打点",
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
    print("PATCH ALL OK: EXP-014 fix5（分片登记）+ diag 探针就位")
    return 0


if __name__ == "__main__":
    sys.exit(main())
