#!/usr/bin/env python3
"""EXP-014 补丁 v3：教师选手挂载 on-policy 采样导出（变量 C 采集引擎）。

v3 修订（run 33952797770 build 红，5 个编译错误，逐条对照 rustc 输出修复）：
- E0252 PathBuf 重复导入：原文件顶部已有 `use std::{collections::BTreeMap, path::PathBuf}`，
  注入块只补 `path::Path`。
- E0425 RamenGame 不在作用域：bench 原有 use 不含它，注入块 game 组补 `RamenGame`。
- E0308 run_idx 是 u64：sample_hook/sample_index 的 run_idx 形参改 u64。

做什么：
- RamenMctsTrainer 加 `on_search_output` 钩子（1 字段 + 1 builder + 2 个搜索点各 1 行调用）。
  只在**真正走过搜索**的决策点触发（转发的手写决策、缓存命中的 SpecialSelect 不触发）。
- ramen_space_bench 加 `--export-samples/--export-shard-size`：记录器（Mutex 批缓冲 +
  分片原子落盘 + manifest + 读回校验），容器格式与 ramen_teacher_collect 同源。

口径（与 EXP-013 教师臂 + 160k 采集器逐字同源）：
- sn64 / use_ucb=false / radical 1.4 / RamenSelect 合并动作 / 冠军 rollout 评估器；
- 采集模式追加前提 #1 `record_ordered_rollouts=true`（export 硬要求；上游文档：
  只影响记录，对局无关）→ 采集局终局分应与纯 bench 逐位一致 = 冒烟 CRN 对拍闸门。

index 语义（对齐 ramen_export_npy 的 meta 承诺与 train.py --split-by combo）：
- index = plan_id + 525 × (run×1000 + 局内序号)，逐样本满足 index % 525 = (马娘,卡组) 组合 id。
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
    path::Path,
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
        InheritInfo, RamenGame,
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

/// 采样空间组合数（index 语义：index % PLAN_COUNT = (马娘, 卡组) 组合 id；
/// train.py --split-by combo 的留出切分依赖它）
const PLAN_COUNT: u64 = 525;

/// 每局游戏在 index 中占的号段宽（局内搜索决策点实测 ~135，留 7× 余量）
const SEQ_STRIDE: u64 = 1000;

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

/// 样本全局序号：plan_id + 525 × (run×1000 + 局内序号)
///
/// 满足 index % 525 = plan_id（导出端/训练侧的组合切分语义），
/// 且 (plan, run, seq) 三元组到 index 一一对应（seq < SEQ_STRIDE）。
fn sample_index(plan_id: usize, run_idx: u64, seq: u64) -> u64 {
    plan_id as u64 + PLAN_COUNT * (run_idx * SEQ_STRIDE + seq)
}

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

/// 采集 manifest
///
/// 字段是 collector TeacherManifest 的对齐超集；导出端（ramen_export_npy）只反序列化
/// 其中 7 个字段并忽略未知字段，因此多出的字段无害。index_start/index_end 记录
/// 本片实际用到的 index 闭区间（信息性）。
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
    git_commit: String,
    gamedata_sig: Vec<FileSignature>,
    sampling_space_hash: Option<String>,
    recipe_hash_fnv1a64: String
}

struct RecorderInner {
    batch: RamenSampleBatch,
    next_part: usize,
    parts: Vec<ExportPart>,
    index_min: Option<u64>,
    index_max: Option<u64>
}

/// 线程安全采样记录器：每局教师选手共享（rayon 并行下 Mutex 串行化落盘，
/// 搜索才是成本大头，锁无竞争压力）
struct SampleRecorder {
    inner: Mutex<RecorderInner>,
    output_dir: PathBuf,
    shard_size: usize,
    search_n: usize,
    premises: ExportPremises,
    sampler_snapshot: ExportSamplerSnapshot,
    space_hash: String,
    git_commit: String,
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
                parts: Vec::new(),
                index_min: None,
                index_max: None
            }),
            output_dir,
            shard_size: args.export_shard_size,
            search_n: args.mcts_search_n,
            premises,
            sampler_snapshot,
            space_hash,
            // 导出端 SourceManifest.git_commit 是 String（非 Option）：取不到时回退占位，
            // 保证 manifest 永远可被导出端反序列化
            git_commit: try_get_git_commit(&root).unwrap_or_else(|| "unknown".to_string()),
            gamedata_sig,
            recipe_hash: compute_text_hash_fnv1a64(&recipe_text),
            started_at: chrono::Utc::now().to_rfc3339()
        })
    }

    /// 搜索钩子入口：index 已由钩子闭包按 (plan, run, 局内序号) 算好
    fn record(
        &self, game: &RamenGame, stage: &RamenStage, output: &RamenSearchOutput, index: u64
    ) -> Result<()> {
        let sample = output
            .export_ramen_sample(game, stage, index)
            .map_err(|e| anyhow::anyhow!("EXP-014 index={index} stage={stage:?} 导出样本失败: {e}"))?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| anyhow::anyhow!("EXP-014 记录器锁中毒"))?;
        inner.batch.push(sample);
        inner.index_min = Some(inner.index_min.map_or(index, |m: u64| m.min(index)));
        inner.index_max = Some(inner.index_max.map_or(index, |m: u64| m.max(index)));
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
        let (index_start, index_end) = match (inner.index_min, inner.index_max) {
            (Some(lo), Some(hi)) => (lo, hi + 1),
            _ => (0, 0)
        };
        let manifest = ExportManifest {
            format_version: SAMPLE_FORMAT_VERSION,
            input_dim: INPUT_DIM,
            policy_dim: POLICY_DIM,
            premises: self.premises.clone(),
            search_n: self.search_n,
            index_start,
            index_end,
            next_index: index_end,
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
            index_start,
            index_end,
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

/// 每局教师选手构造时取一份钩子：闭包持有**本局局内序号**（AtomicU64 按值捕获，
/// Fn 里走 &self 原子自增）与记录器 Arc；index 按 (plan, run, seq) 派生
fn sample_hook(plan_id: usize, run_idx: u64) -> Option<Arc<SearchHook>> {
    let rec = sample_recorder()?;
    let rec = Arc::clone(rec);
    let seq = AtomicU64::new(0);
    Some(Arc::new(
        move |game: &RamenGame, stage: &RamenStage, output: &RamenSearchOutput| {
            let local = seq.fetch_add(1, Ordering::SeqCst);
            rec.record(game, stage, output, sample_index(plan_id, run_idx, local))
        }
    ) as Arc<SearchHook>)
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
        "bench use 块扩展 + 采样记录器整体注入（v3：修 E0252/E0425/E0308）",
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
    export_shard_size: usize,""",
        "BenchArgs +export_samples/export_shard_size",
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
                    .with_on_search_output(sample_hook(plan_index, run_idx));
                let t = LoggingTrainer::new(trainer, base_seed + run_idx);""",
        "教师选手挂采样钩子（携带 plan/run 供 index 派生）",
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
    print("PATCH ALL OK: EXP-014 on-policy 采集引擎就绪（钩子 ×2 + 记录器 + 2 CLI 参数）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
