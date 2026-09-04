#!/usr/bin/env python3
"""EXP-013 补丁 v2：ramen_space_bench 加教师选手臂（--trainer mcts）+ 计划分片（--plan-offset）。

v2 修订（run 33906965488 / 33910064064 审计后）：
- **UCB 对齐**：标签采集器硬编码 `use_ucb=false`（四前提之一，manifest premises；
  理由：UCB 会把 radical_factor 经样本分配烘进标签）。v1 用了 SearchConfig::default()
  的 use_ucb=true——跑的不是逐字同款的标签教师。v2 在 mcts 臂显式 `.with_ucb(false)`。
- 其余口径不变：sn=64（=采集）、radical=1.4（=采集）、RamenSelect 合并动作（=采集）、
  rollout 评估器 = default_rollout_trainer（008 补丁后 = 冠军 tokens，=采集）、
  record_ordered_rollouts 不设（只影响记录，对局无关）。
- region 口径差异（已知、不修）：采集端强制 ramen_region_strategy=All；bench 用仓库
  默认（default_config.toml: fixed）——三锚（65438.2/65554.2/66734.8）全部锁定在仓库
  默认口径上，改了锚就破。bench 是"教师在三锚同一考场里考试"。

背景（见同目录 plan.md）：
- 标签教师不是一个选手——它只存在于采样器抓来的决策点上，没有"整局对局"可打分。
  给它补成绩单的唯一机械方式 = 同一台搜索机器装进选手壳（RamenMctsTrainer）
  从 turn 0 打到终局。本补丁只做三件事：
  1) bench 加 `--trainer mcts` 入口（阶段门控 / search_n / radical / 取分口径全部走 CLI）；
  2) bench 加 `--plan-offset` 计划分片（种子流按绝对计划号派生，与全量单跑逐位一致）；
  3) 不碰引擎、不碰手写路径、不碰任何采样/标签代码。

补丁链顺序（workflow 强制）：006c → 006d → 006e → 006e_fix → 008 → **013（本补丁）**。
锚点 2/3/4/5 依赖 006c 已注入的文本；锚点 1/6/7 是纯 pin 文本。每个锚点必须恰好
命中 1 次，否则拒绝打补丁（fail-fast，防 pin 漂移静默错配）。

保真论证（手写路径必须逐位不变）：
- offset=0 时切片/回查/映射与原逻辑完全等价；handwritten / handwritten-variant / nn
  三条臂的代码路径一字未动；
- 手写臂在 verdict 里有逐位保真锚（65438.2 / 65554.2 / 66734.8），锚破 = 实验作废。
"""
from pathlib import Path
import sys

TARGET = Path("crates/umasim/tools/data_collection/ramen_space_bench.rs")

# (旧, 新, 说明)
PATCHES = [
    # 1) use 块：引入 SearchConfig / RamenMctsTrainer / RamenSearchStages / RamenSelection（纯 pin 锚点）
    (
        """use umasim::{
    bench::{self, GameOutcome},
    gamedata::init_global_with_config,
    sampler::{DeckPlan, DeckShape, SamplingSpace, gen1_inherit},
    trainer::{LoggingTrainer, RandomTrainer, RecommendedRamenTrainer},
    utils::{get_workspace_root, load_game_config}
};""",
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
        "use 块引入教师选手三件套 + SearchConfig",
    ),
    # 2) CLI 参数：mcts 臂四参 + 计划分片（锚点依赖 006c 注入的 variant 字段）
    (
        """    /// EXP-006c 手写变体 token 串（如 `wisf0-capd0`）；仅 trainer=handwritten 时生效
    #[arg(long)]
    variant: Option<String>,""",
        """    /// EXP-006c 手写变体 token 串（如 `wisf0-capd0`）；仅 trainer=handwritten 时生效
    #[arg(long)]
    variant: Option<String>,

    /// EXP-013 教师选手：搜索阶段门控（RamenSearchStages::parse 语法：all / train,ramen / …）
    #[arg(long, default_value = "all")]
    mcts_stages: String,

    /// EXP-013 教师选手：每候选搜索次数（与标签教师采集同预算 64）
    #[arg(long, default_value_t = 64)]
    mcts_search_n: usize,

    /// EXP-013 教师选手：激进度因子最大值（与标签教师采集同值 1.4）
    #[arg(long, default_value_t = 1.4)]
    mcts_radical: f64,

    /// EXP-013 教师选手：取分口径 pt=weighted_mean(radical)（与标签采集选择口径一致）/ score=结算分均值
    #[arg(long, default_value = "pt")]
    mcts_selection: String,

    /// EXP-013 计划分片起点（含）。种子流按全局计划号派生，与全量单跑逐位一致
    #[arg(long, default_value_t = 0)]
    plan_offset: usize,""",
        "BenchArgs +mcts_stages/mcts_search_n/mcts_radical/mcts_selection/plan_offset",
    ),
    # 3) 选手枚举：加 Mcts 变体（锚点依赖 006c 注入的 HandwrittenVariant）
    (
        """    /// 手写规则
    Handwritten,
    /// 手写规则 EXP-006c token 变体（trainer 按 --variant 现场构造，无需 Clone）
    HandwrittenVariant,""",
        """    /// 手写规则
    Handwritten,
    /// 手写规则 EXP-006c token 变体（trainer 按 --variant 现场构造，无需 Clone）
    HandwrittenVariant,
    /// EXP-013 教师选手：RamenMctsTrainer 全阶段搜索（每局现场构造，无需 Clone）
    Mcts,""",
        "SelectedTrainer +Mcts",
    ),
    # 4) dispatch：加 mcts 臂（锚点依赖 006c 重写的 handwritten 分派）
    (
        """        "handwritten" => Ok(if args.variant.as_deref().is_some_and(|v| v != "base") {
            SelectedTrainer::HandwrittenVariant
        } else {
            SelectedTrainer::Handwritten
        }),""",
        """        "handwritten" => Ok(if args.variant.as_deref().is_some_and(|v| v != "base") {
            SelectedTrainer::HandwrittenVariant
        } else {
            SelectedTrainer::Handwritten
        }),
        "mcts" => Ok(SelectedTrainer::Mcts),""",
        "select_trainer +mcts 臂",
    ),
    # 5) run_plan：mcts 臂（锚点依赖 006c 注入的 HandwrittenVariant 臂）
    (
        """            SelectedTrainer::HandwrittenVariant => {
                let tokens = args.variant.as_deref().unwrap_or("base");
                let t = LoggingTrainer::new(RecommendedRamenTrainer::with_tokens(tokens)?, base_seed + run_idx);
                bench::run_seeded(plan.uma, &plan.deck, &inherit, base_seed, run_idx, &t)?
            }""",
        """            SelectedTrainer::HandwrittenVariant => {
                let tokens = args.variant.as_deref().unwrap_or("base");
                let t = LoggingTrainer::new(RecommendedRamenTrainer::with_tokens(tokens)?, base_seed + run_idx);
                bench::run_seeded(plan.uma, &plan.deck, &inherit, base_seed, run_idx, &t)?
            }
            SelectedTrainer::Mcts => {
                let stages = RamenSearchStages::parse(&args.mcts_stages)?;
                let selection = match args.mcts_selection.as_str() {
                    "pt" => RamenSelection::Pt,
                    "score" => RamenSelection::Score,
                    other => bail!("未知 --mcts-selection: {other}（可选 pt / score）")
                };
                // 逐字对齐标签采集四前提（ramen_teacher_collect.rs 硬编码）：
                // use_ucb=false（前提 #2，防 UCB 把样本分配烘进选择）、radical=1.4、
                // sn=每候选 rollout 数、RamenSelect 合并动作（trainer 默认开）。
                // rollout 评估器 = default_rollout_trainer（008 补丁后 = 冠军 tokens）。
                // record_ordered_rollouts 只影响记录，对局无关，不设。
                // （v1 漏了 with_ucb(false)，跑成 UCB 变体——run 33910064064 已作废。）
                let config = SearchConfig::default()
                    .with_search_n(args.mcts_search_n)
                    .with_ucb(false)
                    .with_radical_factor_max(args.mcts_radical);
                let trainer = RamenMctsTrainer::new(config)
                    .with_stages(stages)
                    .with_selection(selection);
                let t = LoggingTrainer::new(trainer, base_seed + run_idx);
                bench::run_seeded(plan.uma, &plan.deck, &inherit, base_seed, run_idx, &t)?
            }""",
        "run_plan +Mcts 臂（每局现场构造，UCB 对齐采集前提 #2）",
    ),
    # 6) 计划分片切片（纯 pin 锚点；offset=0 时与原逻辑逐位等价）
    (
        """    let plans = match args.plans {
        Some(n) => &all_plans[..n.min(all_plans.len())],
        None => all_plans
    };""",
        """    let plan_offset = args.plan_offset.min(all_plans.len());
    let plans = match args.plans {
        Some(n) => &all_plans[plan_offset..(plan_offset + n).min(all_plans.len())],
        None => &all_plans[plan_offset..]
    };""",
        "plans 切片支持 plan_offset（越界 clamp）",
    ),
    # 7) 全局计划号：种子流按全量计划号派生（纯 pin 锚点；offset=0 逐位等价）
    (
        """    let mut results: Vec<PlanResult> = plans
        .par_iter()
        .enumerate()
        .map(|(i, plan)| run_plan(plan, i, &args, &kind))
        .collect::<Result<Vec<_>>>()?;""",
        """    let mut results: Vec<PlanResult> = plans
        .par_iter()
        .enumerate()
        .map(|(i, plan)| run_plan(plan, plan_offset + i, &args, &kind))
        .collect::<Result<Vec<_>>>()?;""",
        "run_plan 传全局计划号（保种子流与全量一致）",
    ),
]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    ok = True
    for i, (old, new, note) in enumerate(PATCHES, 1):
        count = text.count(old)
        if count != 1:
            print(f"PATCH FAIL: 替换 #{i}（{note}）锚点出现 {count} 次（应为 1）——"
                  f"pin 漂移或前置补丁未按序应用，拒绝打补丁")
            ok = False
            continue
        text = text.replace(old, new)
        print(f"PATCH OK #{i}: {note}")
    if not ok:
        return 1
    TARGET.write_text(text, encoding="utf-8")
    print("PATCH ALL OK: ramen_space_bench --trainer mcts（EXP-013 教师选手，UCB 对齐采集）+ --plan-offset 就绪")
    return 0


if __name__ == "__main__":
    sys.exit(main())
