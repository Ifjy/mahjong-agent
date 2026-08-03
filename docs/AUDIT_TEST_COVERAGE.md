# 测试覆盖自查报告 (AUDIT_TEST_COVERAGE)

> 日期：2026-08-03 ｜ 关联：tests/ 全部文件
> 本文记录测试覆盖现状、发现的缺口与修复计划。

---

## 1. 现状（90 个测试用例）

| 文件 | 用例数 | 覆盖面 | 主要问题 |
|------|--------|--------|----------|
| test_env.py | 4 | Env 冒烟 | 仅 smoke，不验证流程正确性 |
| test_hand_analyzer.py | 21 | shanten/waits/forms | 覆盖最好，缺负向用例 |
| test_rules_scenarios.py | 40 | apply_action/候选/计分/流程/振听 | 多个永真式/无断言占位 |
| test_scoring.py | 25 | yaku/fu/payout | payout 未测 honba/立直棒 |

---

## 2. 致命缺口（按优先级）

### A. GameController 状态机零端到端测试
- `step → _handle_*_phase → _process_auto_flow → 结算 → 新局` 链路从未被测试
- 所有修复（_advance_to_next_turn 下家计算、turn_number 自增等）无回归保护

### B. apply_action 5 个分支零测试
- RIICHI（扣分/立直棒/tsumogiri 手切两条路径）
- KAN OPEN（明杠：3手牌+弃牌）
- KAN ADDED（加杠：drawn_tile/手牌两条路径 + PON→KAN替换 + drawn清除）
- RON（仅测候选，未执行 apply_action）
- SPECIAL_DRAW（九种九牌）

### C. RulesEngine 流程层零测试
- resolve_response_priorities（优先级+头跳）
- determine_next_phase / determine_next_hand_state / is_game_over
- process_hand_outcome（含 honba/立直棒 结算）

### D. 高危边界零测试
- 杠后岭上摸牌 + reveal_new_dora
- 立直后强制摸切
- 立直后杠听牌变更检查 (_kan_changes_waits)
- 食替禁止 (_kuikae_forbidden_values)
- 途中流局（四杠散了/四风连打/四家立直）
- 飞人终局
- 庄家轮换/场风推进

### E. 永真式/无断言占位测试（等同于没测）
- test_riichi_candidate_when_tenpai：无 assert
- test_pinfu：放弃，无 assert
- test_toitoi：if 包着 assert，无效和牌静默通过
- test_no_yaku_invalid（两处）：`not X or Y` 永真
- test_multi_wait_tenpai：注释说"不对"

### F. 其他缺失
- seed 复现性零测试
- payout 未测 honba/立直棒
- reveal_new_dora/get_current_dora_tiles 零测试
- DQN checkpoint 权重往返无测试
- Trainer resume / Collector 无测试
