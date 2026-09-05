#!/usr/bin/env python3
"""EXP-014 fix3：RamenGame 导入路径修正。

run 33964793677 红：E0432 `no RamenGame in game`——RamenGame 在
`umasim::game::ramen::RamenGame`（trainer 原文即 `crate::game::ramen::{RamenGame, ...}`），
v3 注入块把它挂到了 `game::` 一级。本补丁在 patch_exp014.py 之后运行，把它移进 `ramen::` 组。

证据链：trainer 原文 import `crate::game::{Game, Trainer, ramen::{Operation, RamenGame, ...}}`；
rustc 建议 BaseGame 恰好说明 game:: 一级没有 RamenGame。
"""
from pathlib import Path
import sys

BENCH = Path("crates/umasim/tools/data_collection/ramen_space_bench.rs")

OLD = """    game::{
        InheritInfo, RamenGame,
        ramen::{
            RamenStage,"""
NEW = """    game::{
        InheritInfo,
        ramen::{
            RamenGame, RamenStage,"""

text = BENCH.read_text(encoding="utf-8")
count = text.count(OLD)
if count != 1:
    print(f"PATCH FAIL: 锚点出现 {count} 次（应为 1）")
    sys.exit(1)
BENCH.write_text(text.replace(OLD, NEW), encoding="utf-8")
print("PATCH OK: RamenGame 移入 ramen:: 组（路径修正）")
