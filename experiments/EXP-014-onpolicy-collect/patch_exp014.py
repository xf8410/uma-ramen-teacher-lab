#!/usr/bin/env python3
"""EXP-014 补丁 v1：教师选手挂载 on-policy 采样导出（变量 C 采集引擎）。

做什么：
- RamenMctsTrainer 加 `on_search_output` 钩子（1 字段 + 1 builder + 2 个搜索点各 1 行调用）。
  只在**真正走过搜索**的决策点触发（转发的手写决策、缓存命中的 SpecialSelect 不触发）——
  与"教师动作 = 搜索动作"严格同集合。
- ramen_space_bench 加 `--export-samples/--export-shard-size/--export-index-base`：
  记录器（Mutex 批缓冲 + 分片落盘 + manifest + 读回校验），容器格式与
  ramen_teacher_collect 完全同源（RamenSampleBatch::save_binary / SAMPLE_FORMAT_VERSION /
  part_XXXXXX.bin / manifest 字段一一对应）→ 下游 convert 管线直接可吃。

口径（与 EXP-013 教师臂 + 160k 采集器逐字同源）：
- sn64 / use_ucb=false / radical 1.4 / RamenSelect 合并动作 / 冠军 rollout 评估器；
- 采集模式追加前提 #1 `record_ordered_rollouts=true`（export 硬要求；上游文档：
  只影响记录，对局无关）→ 采集局的终局分应与 EXP-013 CSV 逐位一致 = 冒烟 CRN 对拍闸门。

设计约束（v4 教训制度化）：
- 全部 11 个锚点必须恰好命中 1 次，否则拒绝打补丁（fail-fast）；
- 锚点全部取自本会话逐行实读的源码文本（trainer 60KB / collector 38KB / bench=013v3 已验证注入文本）；
- 采集走 --export-index-base 分片错位（片 s 基址 = s×8192 > 片内样本上限 ~5900），convert 合并无重叠。

无钩子时零开销：--export-samples 为空 → 记录器不构造、config 不加 record_ordered、钩子 None。
"""
from pathlib import Path
import sys

TRAINER = Path("crates/umasim/src/trainer/ramen_mcts_trainer.rs")
BENCH = Path("crates/umasim/tools/data_collection/ramen_space_bench.rs")

HOOK_FIELD_TYPE = "Option<Arc<dyn Fn(&RamenGame, &RamenStage, &RamenSearchOutput) -> Result<()> + Send + Sync>>"

# (文件, 旧, 新, 说明)
PATCHES = [
    # ---------- trainer ----------
    (
        TRAINER,
        """    pub reason_sink: Arc<dyn crate::output::DecisionReasonSink>
}""",
        """    pub reason_sink: Arc<dyn crate::output::DecisionReasonSink>,
    /// EXP-014 on-policy 采集钩子：每次真实搜索完成后收到（决策前局面, 阶段, 搜索输出）
    ///
    /// 只在真正走过搜索的决策点触发（转发的手写决策、缓存命中的 SpecialSelect 不触发），
    /// 与「教师动作 = 搜索动作」严格同集合。`None` = 不采集，零开销。错误向上传播（fail-fast）。
    pub on_search_output: """ + HOOK_FIELD_TYPE + """
}""",
        "trainer 结构体 +on_search_output 钩子字段",
    ),
    (
        TRAINER,
        """            reason_sink: Arc::new(NoopSink)
        }""",
        """            reason_sink: Arc::new(NoopSink),
            on_search_output: None
        }""",
        "trainer new() 初始化钩子为 None",
    ),
    (
        TRAINER,
        """    pub fn with_reason_sink(mut self, sink: Arc<dyn crate::output::DecisionReasonSink>) -> Self {
        self.reason_sink = sink;
        self
    }""",
        """    pub fn with_reason_sink(mut self, sink: Arc<dyn crate::output::DecisionReasonSink>) -> Self {
        self.reason_sink = sink;
        self
    }

    /// EXP-014：设置 on-policy 采样导出钩子（每次真实搜索后调用）
    pub fn with_on_search_output(mut self, hook: """ + HOOK_FIELD_TYPE + """) -> Self {
        self.on_search_output = hook;
        self
    }""",
        "trainer +with_on_search_output builder",
    ),
    (
        TRAINER,
        """                let best = combined
                    .get(idx)
                    .ok_or_else(|| anyhow!("合并搜索最优下标 {idx} 超出候选数 {}", combined.len()))?;""",
        """                if let Some(hook) = &self.on_search_output {
                    hook(game, &game.stage, &output)?;
                }
                let best = combined
                    .get(idx)
                    .ok_or_else(|| anyhow!("合并搜索最优下标 {idx} 超出候选数 {}", combined.len()))?;""",
        "合并搜索点挂钩（RamenSelect）",
    ),
    (
        TRAINER,
        """        let idx = Self::break_super_ramen_tie(game, actions, &output, self.selection, idx);
        self.stash_search_breakdown(&output);""",
        """        let idx = Self::break_super_ramen_tie(game, actions, &output, self.selection, idx);
        if let Some(hook) = &self.on_search_output {
            hook(game, &game.stage, &output)?;
        }
        self.stash_search_breakdown(&output);""",
        "普通搜索点挂钩（Train/Special/Region/Super + race_turn RamenSelect）",
    ),
    # ---------- bench ----------
    (
        BENCH,
        """use umasim::search::SearchConfig;
use umasim::{
    bench::{self, GameOutcome},
    gamedata::init_global_with_config,
    sampler::{DeckPlan, DeckShape, SamplingSpace, gen1_inherit},
    trainer::{
        LoggingTrainer, RamenMctsTrainer, RamenSearchStages, RamenSelection, RandomTrainer,
        RecommendedRamenTrainer
    },
    utils::{get_workspace_root, load_game_config}
};""",
        """use std::{
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicU64, Ordering}
    }
};

use umasim::search::{RamenSearchOutput, SearchConfig};
use umasim::{
    bench::{self, GameOutcome},
    collector::{
        FileSignature, compute_file_signature, compute_text_hash_fnv1a64, try_get_git_commit
    },
    gamedata::{GAMECONFIG, RamenRegionStrategy, init_global_with_config},
    game::{
        InheritInfo,
        ramen::{
            RamenStage,
            features::INPUT_DIM,
            policy_schema::POLICY_DIM,
            training_sample::{RamenSampleBatch, SAMPLE_FORMAT_VERSION}
        }
    },
    sampler::{DeckPlan, DeckShape, SamplingSpace, gen1_inherit},
    trainer::{
        LoggingTrainer, RamenMctsTrainer, RamenSearchStages, RamenSelection, RandomTrainer,
        RecommendedRamenTrainer
    },
    utils::{get_workspace_root, load_game_config}
};

// ============================================================================
// EXP-014：on-policy 教师采集（--export-samples；容器格式与 ramen_teacher_collect 同源）
// ============================================================================

/// 记录签名的复现基座文件（与 ramen_teacher_collect.rs 同表）
const GAMEDATA_SIG_PATHS: &[&str] = &[
    "gamedata/constants.json",
    "gamedata/events.json",
    "gamedata/umaDB.json",
    "gamedata/cardDB.json",
    "gamedata/scenario_ramen.json",
    "gamedata/default_config.toml",
    "game_config.toml"
];

/// 四条采集前提的**实际取值**（schema 与 collector 的 TeacherPremises 对齐）
#[derive(serde::Serialize, Clone)]
struct ExportPremises {
    record_ordered_rollouts: bool,
    use_ucb: bool,
    radical_factor_max: f64,
    ramen_region_strategy: RamenRegionStrategy
}

/// 采样器快照占位（schema 对齐 collector；on-policy 采集不使用采样器，字段仅记账）
#[derive(serde::Serialize, Clone)]
struct ExportSamplerSnapshot {
    epsilon: f64,
    min_actions: usize,
    inherit: InheritInfo,
    max_turn: i32,
    seed_base: u64,
    region_quota_permille: [u32; 2]
}

/// 已落盘分片记录（schema 对齐 collector 的 TeacherPart）
#[derive(serde::Serialize, Clone)]
struct ExportPart {
    name: String,
    samples: usize,
    signature: FileSignature
}

/// 采集 manifest（schema 与 ramen_teacher_collect 的 TeacherManifest 字段一一对应）
#[derive(serde::Serialize)]
struct ExportManifest {
    format_version: u32,
    input_dim: usize,
    policy_dim: usize,
    premises: ExportPremises,
    search_n: usize,
    index_start: u64,
    index_end: u64,
    next_index: u64,
    sampler: ExportSamplerSnapshot,
    shard_size: usize,
    parts: Vec<ExportPart>,
    started_at: String,
    updated_at: String,
    finished_at: Option<String>,
    skipped_uncaptured: u64,
    accepted: u64,
    git_commit: Option<String>,
    gamedata_sig: Vec<FileSignature>,
    sampling_space_hash: Option<String>,
    recipe_hash_fnv1a64: String
}

struct RecorderInner {
    batch: RamenSampleBatch,
    next_part: usize,
    parts: Vec<ExportPart>
}

/// 线程安全采样记录器：每局教师选手共享（rayon 并行下 Mutex 串行化落盘，
/// 搜索才是成本大头，锁无竞争压力）
struct SampleRecorder {
    inner: Mutex<RecorderInner>,
    output_dir: PathBuf,
    shard_size: usize,
    base_index: u64,
    next_index: AtomicU64,
    search_n: usize,
    premises: ExportPremises,
    sampler_snapshot: ExportSamplerSnapshot,
    space_hash: String,
    git_commit: Option<String>,
    gamedata_sig: Vec<FileSignature>,
    recipe_hash: String,
    started_at: String
}

impl SampleRecorder {
    fn new(args: &BenchArgs) -> Result<Self> {
        let root = get_workspace_root()?;
        std::env::set_current_dir(&root)
            .map_err(|e| anyhow::anyhow!("EXP-014 切换到工作空间根失败: {e}"))?;
        let output_dir = PathBuf::from(&args.export_samples);
        if output_dir.exists() {
            anyhow::ensure!(
                output_dir.is_dir(),
                "EXP-014 输出路径存在但不是目录: {}",
                output_dir.display()
            );
        } else {
            std::fs::create_dir_all(&output_dir)
                .map_err(|e| anyhow::anyhow!("EXP-014 创建输出目录失败: {e}"))?;
        }
        let on_disk = std::fs::read_dir(&output_dir)
            .map_err(|e| anyhow::anyhow!("EXP-014 读取输出目录失败: {e}"))?
            .filter_map(|e| e.ok())
            .count();
        anyhow::ensure!(
            on_disk == 0,
            "EXP-014 输出目录非空（{on_disk} 项），拒绝覆盖"
        );
        let strategy = GAMECONFIG
            .get()
            .ok_or_else(|| anyhow::anyhow!("EXP-014 GAMECONFIG 未初始化"))?
            .ramen_region_strategy
            .clone();
        let premises = ExportPremises {
            record_ordered_rollouts: true,
            use_ucb: false,
            radical_factor_max: args.mcts_radical,
            ramen_region_strategy: strategy
        };
        let sampler_snapshot = ExportSamplerSnapshot {
            epsilon: 0.0,
            min_actions: 2,
            inherit: gen1_inherit(),
            max_turn: 77,
            seed_base: 0,
            region_quota_permille: [20, 30]
        };
        let recipe_text = serde_json::to_string(&serde_json::json!({
            "format_version": SAMPLE_FORMAT_VERSION,
            "input_dim": INPUT_DIM,
            "policy_dim": POLICY_DIM,
            "premises": premises.clone(),
            "search_n": args.mcts_search_n,
            "sampler": sampler_snapshot.clone()
        }))
        .map_err(|e| anyhow::anyhow!("EXP-014 配方序列化失败: {e}"))?;
        let space_hash = SamplingSpace::gen1()?.content_hash();
        let mut gamedata_sig = Vec::new();
        for rel in GAMEDATA_SIG_PATHS {
            let p = Path::new(rel);
            if p.exists() {
                let hash = std::fs::metadata(p)
                    .map(|m| m.len() <= 32 * 1024 * 1024)
                    .unwrap_or(false);
                gamedata_sig.push(compute_file_signature(p, hash).map_err(|e| {
                    anyhow::anyhow!("EXP-014 计算 {rel} 签名失败: {e}")
                })?);
            }
        }
        Ok(Self {
            inner: Mutex::new(RecorderInner {
                batch: RamenSampleBatch::new(),
                next_part: 0,
                parts: Vec::new()
            }),
            output_dir,
            shard_size: args.export_shard_size,
            base_index: args.export_index_base,
            next_index: AtomicU64::new(0),
            search_n: args.mcts_search_n,
            premises,
            sampler_snapshot,
            space_hash,
            git_commit: try_get_git_commit(&root),
            gamedata_sig,
            recipe_hash: compute_text_hash_fnv1a64(&recipe_text),
            started_at: chrono::Utc::now().to_rfc3339()
        })
    }

    /// 搜索钩子入口：导出样本 → 入批 → 满该片
    fn record(
        &self, game: &RamenGame, stage: &RamenStage, output: &RamenSearchOutput
    ) -> Result<()> {
        let index = self.base_index + self.next_index.fetch_add(1, Ordering::SeqCst);
        let sample = output
            .export_ramen_sample(game, stage, index)
            .map_err(|e| anyhow::anyhow!("EXP-014 index={index} stage={stage:?} 导出样本失败: {e}"))?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| anyhow::anyhow!("EXP-014 记录器锁中毒"))?;
        inner.batch.push(sample);
        if inner.batch.len() >= self.shard_size {
            let part = self.flush(&mut inner)?;
            println!("EXP-014 写入 {} ({} 条)", part.name, part.samples);
        }
        Ok(())
    }

    /// 原子落盘一个分片（tmp + rename，与 collector 同套）
    fn flush(&self, inner: &mut RecorderInner) -> Result<ExportPart> {
        anyhow::ensure!(!inner.batch.is_empty(), "EXP-014 不能写空分片");
        let name = format!("part_{:06}.bin", inner.next_part);
        let final_path = self.output_dir.join(&name);
        anyhow::ensure!(
            !final_path.exists(),
            "EXP-014 part 文件已存在，疑似分片下标算错: {}",
            final_path.display()
        );
        let tmp_path = PathBuf::from(format!("{}.tmp", final_path.display()));
        inner
            .batch
            .save_binary(&tmp_path)
            .map_err(|e| anyhow::anyhow!("EXP-014 写临时分片失败 {}: {e}", tmp_path.display()))?;
        std::fs::rename(&tmp_path, &final_path)
            .map_err(|e| anyhow::anyhow!("EXP-014 重命名分片失败: {e}"))?;
        let samples = inner.batch.len();
        inner.batch = RamenSampleBatch::new();
        inner.next_part += 1;
        let signature = compute_file_signature(&final_path, true)
            .map_err(|e| anyhow::anyhow!("EXP-014 分片签名失败: {e}"))?;
        Ok(ExportPart {
            name,
            samples,
            signature
        })
    }

    /// 收尾：flush 尾批 + 读回校验 + manifest
    fn finalize(&self) -> Result<()> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| anyhow::anyhow!("EXP-014 记录器锁中毒"))?;
        if !inner.batch.is_empty() {
            let part = self.flush(&mut inner)?;
            println!("EXP-014 写入 {} ({} 条)", part.name, part.samples);
        }
        let accepted: u64 = inner.parts.iter().map(|p| p.samples as u64).sum();
        let next_index = self.base_index + self.next_index.load(Ordering::SeqCst);
        // 读回校验（与 collector 同约定：磁盘条数必须与 manifest 一致）
        let mut total = 0usize;
        for part in &inner.parts {
            let path = self.output_dir.join(&part.name);
            let batch = RamenSampleBatch::load_binary(&path).map_err(|e| {
                anyhow::anyhow!("EXP-014 读回分片 {} 失败: {e}", path.display())
            })?;
            anyhow::ensure!(
                batch.len() == part.samples,
                "EXP-014 分片 {} 条数对不上: 磁盘 {} vs manifest {}",
                part.name,
                batch.len(),
                part.samples
            );
            total += batch.len();
        }
        anyhow::ensure!(
            total as u64 == accepted,
            "EXP-014 读回条数 {total} != accepted {accepted}"
        );
        let now = chrono::Utc::now().to_rfc3339();
        let manifest = ExportManifest {
            format_version: SAMPLE_FORMAT_VERSION,
            input_dim: INPUT_DIM,
            policy_dim: POLICY_DIM,
            premises: self.premises.clone(),
            search_n: self.search_n,
            index_start: self.base_index,
            index_end: next_index,
            next_index,
            sampler: self.sampler_snapshot.clone(),
            shard_size: self.shard_size,
            parts: inner.parts.clone(),
            started_at: self.started_at.clone(),
            updated_at: now.clone(),
            finished_at: Some(now),
            skipped_uncaptured: 0,
            accepted,
            git_commit: self.git_commit.clone(),
            gamedata_sig: self.gamedata_sig.clone(),
            sampling_space_hash: Some(self.space_hash.clone()),
            recipe_hash_fnv1a64: self.recipe_hash.clone()
        };
        let manifest_path = self.output_dir.join("manifest.json");
        let tmp = PathBuf::from(format!("{}.tmp", manifest_path.display()));
        {
            use std::io::Write as _;
            let f = std::fs::File::create(&tmp)
                .map_err(|e| anyhow::anyhow!("EXP-014 创建 manifest 失败: {e}"))?;
            let mut w = std::io::BufWriter::new(f);
            serde_json::to_writer_pretty(&mut w, &manifest)
                .map_err(|e| anyhow::anyhow!("EXP-014 写 manifest 失败: {e}"))?;
            w.flush().map_err(|e| anyhow::anyhow!("EXP-014 flush manifest 失败: {e}"))?;
        }
        std::fs::rename(&tmp, &manifest_path)
            .map_err(|e| anyhow::anyhow!("EXP-014 重命名 manifest 失败: {e}"))?;
        println!(
            "EXP-014 采集完成: {} 样本 / {} 分片 / index [{}, {}) / 目录 {}",
            accepted,
            inner.parts.len(),
            self.base_index,
            next_index,
            self.output_dir.display()
        );
        Ok(())
    }
}

static SAMPLE_RECORDER: OnceLock<Option<Arc<SampleRecorder>>> = OnceLock::new();

type SearchHook = dyn Fn(&RamenGame, &RamenStage, &RamenSearchOutput) -> Result<()> + Send + Sync;

fn sample_recorder() -> Option<&'static Arc<SampleRecorder>> {
    SAMPLE_RECORDER.get().and_then(|opt| opt.as_ref())
}

fn sample_recorder_enabled() -> bool {
    sample_recorder().is_some()
}

/// 每局教师选手构造时取一份钩子（Arc 克隆，静态生命周期兜底）
fn sample_hook() -> Option<Arc<SearchHook>> {
    sample_recorder().map(|rec| {
        let rec = Arc::clone(rec);
        Arc::new(
            move |game: &RamenGame, stage: &RamenStage, output: &RamenSearchOutput| {
                rec.record(game, stage, output)
            }
        ) as Arc<SearchHook>
    })
}

fn init_sample_recorder(args: &BenchArgs) -> Result<()> {
    let recorder = if args.export_samples.is_empty() {
        None
    } else {
        Some(Arc::new(SampleRecorder::new(args)?))
    };
    SAMPLE_RECORDER
        .set(recorder)
        .map_err(|_| anyhow::anyhow!("EXP-014 SAMPLE_RECORDER 重复初始化"))?;
    Ok(())
}

fn finalize_sample_recorder() -> Result<()> {
    match sample_recorder() {
        Some(rec) => rec.finalize(),
        None => Ok(())
    }
}""",
        "bench use 块扩展 + 采样记录器整体注入",
    ),
    (
        BENCH,
        """    /// EXP-013 计划分片起点（含）。种子流按全局计划号派生，与全量单跑逐位一致
    #[arg(long, default_value_t = 0)]
    plan_offset: usize,""",
        """    /// EXP-013 计划分片起点（含）。种子流按全局计划号派生，与全量单跑逐位一致
    #[arg(long, default_value_t = 0)]
    plan_offset: usize,

    /// EXP-014 on-policy 采集：样本输出目录（空 = 不采集）
    #[arg(long, default_value = "")]
    export_samples: String,

    /// EXP-014 每分片样本条数（RamenSampleBatch part）
    #[arg(long, default_value_t = 256)]
    export_shard_size: usize,

    /// EXP-014 本片样本全局序号起点（片间按 8192 错位，convert 合并用）
    #[arg(long, default_value_t = 0)]
    export_index_base: u64,""",
        "BenchArgs +export_samples/export_shard_size/export_index_base",
    ),
    (
        BENCH,
        """    let plan_offset = args.plan_offset.min(all_plans.len());
    let plans = match args.plans {
        Some(n) => &all_plans[plan_offset..(plan_offset + n).min(all_plans.len())],
        None => &all_plans[plan_offset..]
    };""",
        """    let plan_offset = args.plan_offset.min(all_plans.len());
    let plans = match args.plans {
        Some(n) => &all_plans[plan_offset..(plan_offset + n).min(all_plans.len())],
        None => &all_plans[plan_offset..]
    };
    // EXP-014：初始化 on-policy 采样记录器（--export-samples 为空则 no-op）
    init_sample_recorder(&args)?;""",
        "main 在分片切片后初始化记录器",
    ),
    (
        BENCH,
        """                let config = SearchConfig::default()
                    .with_search_n(args.mcts_search_n)
                    .with_ucb(false)
                    .with_radical_factor_max(args.mcts_radical);""",
        """                let mut config = SearchConfig::default()
                    .with_search_n(args.mcts_search_n)
                    .with_ucb(false)
                    .with_radical_factor_max(args.mcts_radical);
                if sample_recorder_enabled() {
                    // EXP-014 采集前提 #1：record_ordered_rollouts=true（export 硬要求）。
                    // 上游文档：只影响记录，对局无关 → 采集局分数应与 EXP-013 逐位一致（冒烟对拍闸门）。
                    config = config.with_record_ordered_rollouts(true);
                }""",
        "采集模式下补前提 #1（record_ordered_rollouts=true）",
    ),
    (
        BENCH,
        """                let trainer = RamenMctsTrainer::new(config)
                    .with_stages(stages)
                    .with_selection(selection);
                let t = LoggingTrainer::new(trainer, base_seed + run_idx);""",
        """                let trainer = RamenMctsTrainer::new(config)
                    .with_stages(stages)
                    .with_selection(selection)
                    .with_on_search_output(sample_hook());
                let t = LoggingTrainer::new(trainer, base_seed + run_idx);""",
        "教师选手挂采样钩子",
    ),
    (
        BENCH,
        """    for r in &results {
        // EXP-013 v3：plan_index 已是全局计划号（patch #7），聚合回查必须走 525 计划全表，""",
        """    // EXP-014：所有对局已结束，记录器收尾（flush 尾批 + 读回校验 + manifest）
    if let Err(err) = finalize_sample_recorder() {
        eprintln!("EXP-014 样本落盘失败: {err:#}");
        bail!("EXP-014 样本落盘失败: {err:#}");
    }

    for r in &results {
        // EXP-013 v3：plan_index 已是全局计划号（patch #7），聚合回查必须走 525 计划全表，""",
        "聚合前 finalize 采集器（fail-fast）",
    ),
]


def main() -> int:
    texts = {}
    ok = True
    for i, (path, old, new, note) in enumerate(PATCHES, 1):
        if path not in texts:
            texts[path] = path.read_text(encoding="utf-8")
        count = texts[path].count(old)
        if count != 1:
            print(f"PATCH FAIL: 替换 #{i}（{note}）锚点出现 {count} 次（应为 1）——拒绝打补丁")
            ok = False
            continue
        texts[path] = texts[path].replace(old, new)
        print(f"PATCH OK #{i}: {note}")
    if not ok:
        return 1
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")
    print("PATCH ALL OK: EXP-014 on-policy 采集引擎就绪（钩子 ×2 + 记录器 + 3 CLI 参数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
