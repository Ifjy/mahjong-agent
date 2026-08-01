# 设计文档 05：动作校验设计 (ACTION_VALIDATION_DESIGN)

> 状态：**草案 v1** ｜ 优先级：**中** ｜ 关联代码：`src/env/core/rules/action_validator.py`、`actions.py`
> 本文定义每个阶段的候选动作生成规则、互斥逻辑、立直后限制、食替、振听对候选动作的影响。
> 与 `RULES_ENGINE_DESIGN.md §6` 互补：那篇是契约，本篇是规则细节。

---

## 1. Action 数据结构回顾（actions.py）

候选动作由 `Action` 表示，参数约束（actions.py:78-94 已校验）：

| ActionType | 必需参数 | 可选参数 |
|------------|----------|----------|
| DISCARD | tile | — |
| RIICHI | riichi_discard | — |
| CHI | chi_tiles (2张) | tile (被吃的牌) |
| PON | tile (碰的value) | — |
| KAN | tile + kan_type | — |
| TSUMO | — | winning_tile |
| RON | — | winning_tile |
| PASS | — | — |
| SPECIAL_DRAW | — | — |

> 注意：PON/KAN 的 `tile` 用"类型"表示（同 value 即可，不区分实例），CHI 的 `chi_tiles` 是手牌中具体的两张 Tile 实例。GameState.apply_action 据此在手牌中查找实例。

---

## 2. PLAYER_DISCARD 阶段候选动作（get_legal_actions_on_draw）

### 2.1 动作清单与条件

| 动作 | 条件（逐条校验，全满足才生成） |
|------|------------------------------|
| TSUMO | `player.drawn_tile` 非空 ∧ `scoring.is_valid_win(player, drawn_tile, is_tsumo=True)` |
| KAN(暗) | 手牌(含drawn)有4张同value ∧ (未立直 ∨ 立直后暗杠不改变听牌集合) |
| KAN(加) | 存在PON同value ∧ 手牌有1张同value ∧ (未立直 ∨ 加杠后听同一组牌) |
| RIICHI | 门清 ∧ 分数≥1000 ∧ 剩余活动牌≥4 ∧ 未立直 ∧ ∃打出后听牌的牌 |
| DISCARD | 手牌每张不同(value,is_red)组合一项；立直后仅"听牌那张" |
| SPECIAL_DRAW | turn_number==1 ∧ 门清 ∧ 9种以上不同幺九字牌 |

### 2.2 互斥逻辑（关键，当前代码已实现）

```python
candidates = []
if 可TSUMO: candidates.append(TSUMO)
candidates += 自摸回合可杠(暗/加)

can_tsumo = TSUMO in candidates
can_kan   = KAN in candidates

if not can_tsumo and not can_kan:
    # 只在既不自摸也不杠时，才允许立直/打牌/九种九牌
    candidates += 满足条件的RIICHI
    candidates += 所有DISCARD
    if 九种九牌: candidates.append(SPECIAL_DRAW)
```

**设计理由**：避免动作空间同时出现 TSUMO 与 DISCARD，防止 Agent 误选放弃自摸。
**简化代价**：玩家无法"放弃自摸继续打"。标准日麻允许放弃自摸（但战略价值极低），如需支持需重新设计（见 §6 未决）。

### 2.3 立直后暗杠/加杠限制（当前 TODO，需实现）

立直后允许的杠必须**不改变听牌集合**：
```python
def _ankan_changes_waits(player, tile, game_state):
    # 模拟暗杠该牌后的手牌，重新算听牌
    waits_before = hand_analyzer.find_wait_tiles(hand, melds)
    # 假装移除4张该牌（手牌变9张+副露），但暗杠后听牌应不变
    # 实际：暗杠不改变手牌可和形状，只移除一组刻子
    # 简化判定：暗杠的4张必须是当前某个"完整面子"的一部分，不破坏听牌
    waits_after = ...  # 模拟后
    return waits_before != waits_after
```
- 允许：暗杠的4张牌在所有有效 WinForm 中都构成刻子（不影响听牌）。
- 禁止：暗杠会改变听牌（如 1111m 听牌时暗杠1m 会丢失单骑听）。

### 2.4 立直后只能摸切（立直成立后）
立直宣言那一巡的打牌由 RIICHI action 的 riichi_discard 决定；之后每巡强制摸切（DISCARD drawn_tile）。ActionValidator 在 `player.riichi_declared` 时应只生成摸切 DISCARD（+暗杠/加杠/自摸）。

---

## 3. WAITING_FOR_RESPONSE 阶段候选动作（get_legal_actions_on_response）

### 3.1 动作清单与条件

| 动作 | 条件 |
|------|------|
| RON | `scoring.is_valid_win(player, last_discard, is_tsumo=False)` ∧ 非振听 ∧ 非自己打牌 |
| PON | 手牌≥2张同value ∧ 非立直（立直后见§3.2） |
| KAN(明杠) | 手牌≥3张同value ∧ 非立直 |
| CHI | (last_discard_player+1)%num_players==player ∧ 数牌 ∧ 能组顺子 ∧ 非立直 |
| PASS | 永远（兜底） |

### 3.2 立直玩家的响应限制
立直玩家在响应阶段**只能 RON 或 PASS**（不能鸣牌）。当前代码已用 `if not player.riichi_declared:` 包裹鸣牌生成（action_validator.py:117），正确。

### 3.3 CHI 细节（_find_chi_actions）
当前实现（action_validator.py:256）已正确枚举三种模式：
- 模式1：手里有 T-2,T-1（吃 T 组成 T-2,T-1,T）
- 模式2：手里有 T-1,T+1
- 模式3：手里有 T+1,T+2

字牌(value≥27)不能吃。去重按 chi_tiles 元组。

### 3.4 RON 的振听检查
`scoring.is_valid_win` 内部已含振听检查（YAKU_AND_SCORING_DESIGN §7/§8）。若振听，is_valid_win 返回 False，RON 不生成 → 只能 PASS。这是振听影响候选动作的入口。

---

## 4. 食替（Kuikae）规则（当前缺失，需补）

鸣牌后不得立即打出"刚组成副露的牌"。

### 4.1 碰的食替
- PON 后打出的牌不能是所碰的 value（虽然手里已无该 value，自动满足，但需确认）。

### 4.2 吃的食替（关键，易错）
- 例：手牌 45m，吃 6m 组成 456m，不能立即打 3m 或 6m（若手里还有）。
- 实现：CHI 后生成的 DISCARD 候选，需排除"与刚吃顺子同 value 的牌"中会复刻该顺子的情况。
- 复杂情形：手里 4566m 吃 3m 组成 345m，不能打 6m（否则等同没吃）。

**实现位置**：在 ActionValidator 生成 DISCARD 时，若上一步是 CHI，过滤掉食替牌。或更通用：`_generate_discard_actions` 接收 "刚鸣牌的 meld" 参数，排除食替 value。

> 当前 `_generate_discard_actions`（action_validator.py:400）有 `# TODO: kuikae` 注释，待实现。

---

## 5. 响应优先级解决（resolve_response_priorities）

已在 RULES_ENGINE_DESIGN §6.3 定义，本节补充实现细节：

```
优先级: RON(3) > KAN/PON(2) > CHI(1) > PASS(0)
同优先级: 从打牌者上家逆时针取第一个 (头跳)
```

当前实现（action_validator.py:145）正确。需注意：
- **多家 RON**：当前头跳取一家。config `multiple_ron_allowed` 可改为返回 List（RULES_ENGINE_DESIGN §8.2）。
- 返回类型注解 `Optional["int"]` 笔误（应为 `Optional[int]`），需修。

---

## 6. 未决问题

### 6.1 [设计] 放弃自摸
当前互斥逻辑强制 TSUMO 时无 DISCARD。若要支持放弃自摸：
- 候选同时含 TSUMO + DISCARD + KAN(若可杠)。
- ActionValidator 不做互斥，由 Agent 选择。
- 代价：动作空间更复杂，Agent 可能误放弃自摸。
**建议**：保持当前简化（强制自摸），RL 阶段再评估。

### 6.2 [实现] 立直后摸切强制
当前 `player.riichi_declared` 时 DISCARD 应只含 drawn_tile。需在 `_generate_discard_actions` 加分支。当前未实现。

### 6.3 [实现] 性能：is_valid_win 缓存
TSUMO/RON 判定调 is_valid_win，内部跑完整计分。可缓存：同玩家同手牌同巡内 WinForm 分解复用。

---

## 7. 与现有代码对齐 & 修复清单

| 现状 | 设计要求 | 优先级 |
|------|----------|--------|
| `_find_self_kans` 用 `meld["type"]`(dict) | 改 `meld.type` 属性（A3） | **致命** |
| `_find_riichi_discards` 调 `is_tenpai`(慢) | 复用 HandAnalyzer 表查 | 高 |
| 立直后杠限制 TODO | 按 §2.3 实现 | 高 |
| 食替 TODO | 按 §4 实现 | 中 |
| 立直后摸切强制未实现 | 按 §6.2 | 中 |
| `Optional["int"]` 笔误 | 改 `Optional[int]` | 低 |

---

## 8. 验收标准

1. ✅ §2/§3 各阶段候选动作齐全且正确。
2. ✅ §2.2 互斥逻辑稳定。
3. ✅ 立直后杠/摸切/响应限制生效。
4. ✅ 食替禁止打牌生效。
5. ✅ 振听时 RON 不生成。
6. ✅ 黄金牌谱回放：每个 declared action 都在候选列表中。

---

## 9. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，候选动作规则 + 立直限制 + 食替 + 振听入口 |
