# 设计文档 04：役种与计分设计 (YAKU_AND_SCORING_DESIGN)

> 状态：**草案 v1** ｜ 优先级：**高** ｜ 关联代码：`src/env/core/rules/scoring.py`
> 本文定义役种全集、番/符计算、点数与支付、宝牌、振听。当前 scoring.py 仅实现约 25%，本文是补全蓝图。

---

## 1. 计分总体流程（Scoring.calculate_win_details）

```
player + winning_tile + is_tsumo + game_state
    │
    ▼
1. 组装 final_hand (14张) + context (场风/自风/立直/岭上等)
    │
    ▼
2. 先查役满 (yakuman) —— 若有，直接定 32000×N，跳过普通役
    │ (无役满)
    ▼
3. hand_analyzer.find_all_winning_forms -> 所有 WinForm 分解
    │
    ▼
4. 对每个 WinForm: _find_yaku(役) + _calculate_fu(符) -> 取 (番最大, 符最大) 的最优形
    │
    ▼
5. 一番缚检查: 若 best_han==0 且无役种 -> 无效和 (INVALID_WIN)
    │
    ▼
6. _calculate_dora(宝牌) -> total_han = han + dora
    │
    ▼
7. _calculate_points(点数)
    │
    ▼
8. 振听检查 (非自摸时): _is_furiten -> 若振听则 INVALID_WIN
    │
    ▼
9. 返回 WinDetails
```

> 现有代码此流程框架已具备（scoring.py:121-205），缺的是各步骤的具体实现。

---

## 2. context 字典（役种判定上下文）

`_get_win_context` 收集所有役种所需信息（现有代码 scoring.py:277 已部分实现，需补全）：

```python
context = {
    # —— 现有 ——
    "is_tsumo": bool,
    "is_riichi": bool,           # player.riichi_declared
    "is_menzen": bool,           # player.is_menzen
    "is_dealer": bool,           # player_index == dealer_index
    "player_wind": int,          # 自风 value (27-30)
    "round_wind": int,           # 场风 value (27-30)
    "dora_indicators": List[Tile],
    "ura_dora_indicators": List[Tile],
    # —— 需补充 ——
    "is_double_riichi": bool,    # player.riichi_turn == 0/1 (双立直)
    "is_ippatsu": bool,          # player.ippatsu_chance (一发)
    "is_rinshan": bool,          # 刚摸岭上牌 (岭上开花) —— 需 GameState 标记
    "is_haitei": bool,           # 海底摸月 (最后一张自摸) —— wall 剩0
    "is_houtei": bool,           # 河底捞鱼 (最后一张荣和)
    "is_tenhou": bool,           # 天和 (庄家初始14张)
    "is_chiihou": bool,          # 地和 (闲家初始摸牌)
    "is_first_turn": bool,       # turn_number <= 1 (天/地/一双判定)
}
```

> ⚠️ `is_rinshan` / `is_haitei` 需 GameState/Controller 在恰当时机标记，当前缺失。建议 GameState 增加 `last_draw_was_rinshan: bool`。

---

## 3. 役种全集（Yaku Engine）

### 3.1 役种分级表（必须全部实现）

| 役 | 番 | 门清? | 现状 | 判定要点 |
|----|----|-------|------|----------|
| 立直 Riichi | 1 | - | ✅ | context.is_riichi |
| 一发 Ippatsu | 1 | - | ❌ | context.is_ippatsu |
| 门清自摸 Menzen Tsumo | 1 | 必须 | ✅ | is_tsumo and is_menzen |
| 断幺九 Tanyao | 1 | 食断config | ✅ | 全牌无 TERMINAL_HONOR |
| 平和 Pinfu | 1 | 必须 | ❌ | 全顺子+雀头非役牌+两面听 |
| 一杯口 Pinfu(Iipeikou) | 1 | 必须 | ❌ | 两个同值顺子（需WinForm实例） |
| 役牌 Yakuhai | 1/个 | 否 | ✅(部分) | 三元/自风/场风的刻或杠 |
| 海底摸月 Haitei | 1 | - | ❌ | is_tsumo and is_haitei |
| 河底捞鱼 Houtei | 1 | - | ❌ | (not is_tsumo) and is_houtei |
| 岭上开花 Rinshan | 1 | - | ❌ | is_rinshan |
| 抢杠 Paarenchan/Chankan | 1 | - | ❌ | 荣和他人加杠（需特殊流程） |
| 双立直 Double Riichi | 2 | - | ❌ | riichi_turn==0 |
| 三色同顺 Sanshoku doujun | 2 | 食下1番 | ❌ | 万筒索同顺子各1（需WinForm） |
| 一气通贯 Ikkitsuukan | 2 | 食下1番 | ❌ | 同花123/456/789（需WinForm） |
| 混全带 Yaochuu | 2 | 食下1番 | ❌ | 全副露+幺九字 |
| 七对子 Chiitoitsu | 2 | 必须 | ❌(形有) | WinForm.hand_type==chiitoitsu |
| 对对和 Toitoi | 2 | 否 | ❌ | 全刻子无顺子 |
| 三暗刻 Sanankou | 2 | 否 | ❌ | 3个暗刻 |
| 三杠子 Sankantsu | 2 | 否 | ❌ | 3个杠子 |
| 小三色 Shousangen | 2 | 否 | ❌ | 白发中两刻+雀头 |
| 混老头 Honroutou | 2 | 否 | ❌ | 全幺九 |
| 纯全带 Junchan | 3 | 食下2番 | ❌ | 全顺子+每副含幺九 |
| 混一色 Honiisou | 3 | 食下2番 | ❌ | 一数牌+字 |
| 清一色 Chin iisou | 6 | 食下5番 | ❌ | 单一数牌 |
| **—— 役满 (Yakuman, 各32000) ——** | | | | |
| 国士无双 Kokushi | 役满 | 必须 | ❌(形有) | 13幺九字 |
| 国士十三面 | 役满 | 必须 | ❌ | 13面听 |
| 四暗刻 Suuankou | 役满 | 必须 | ❌ | 4暗刻 |
| 四暗刻单骑 | 役满 | 必须 | ❌ | 单骑和 |
| 大三元 Daisangen | 役满 | 否 | ❌ | 白发中各刻 |
| 小四喜 Shousuushi | 役满 | 否 | ❌ | 3风刻+1风雀头 |
| 大四喜 Daisuushi | 役满 | 否 | ❌ | 4风刻 |
| 字一色 Tsuuiisou | 役满 | 否 | ❌ | 全字牌 |
| 绿一色 Ryuuiisou | 役满 | 否 | ❌ | 全绿(23468索+发) |
| 清老头 Chinroutou | 役满 | 否 | ❌ | 全幺九数牌刻 |
| 九莲宝灯 Chuuren poutou | 役满 | 必须 | ❌ | 1112345678999+X |
| 真九莲 | 役满 | 必须 | ❌ | 纯九莲单骑 |
| 天和 Tenhou | 役满 | 必须 | ❌ | 庄家初始和 |
| 地和 Chiihou | 役满 | 必须 | ❌ | 闲家初始和 |
| 大车轮 Dai-sharin | (规则可选) | 必须 | ❌ | 222-888筒七对 |

**食下规则**：门清时按表番数，副露时番数 -1（标注"食下"的役）。
**一番缚**：标准规则要求至少 1 番（不含宝牌）才能和牌。

### 3.2 役种判定实现策略
- **手牌役**：依赖 `WinForm.components`，逐役判定函数 `_check_yaku_*`。
- **状况役**：依赖 `context`，不依赖分解。
- **取最优**：遍历所有 WinForm，对每个算 (han, fu)，取最大。

---

## 4. 符数计算（Fu Engine）

### 4.1 符数构成（标准型）

```
fu = 底符(20) + 副底 + 面子符 + 雀头符 + 听牌符 + (进位到10)
```

| 来源 | 符 | 条件 |
|------|----|------|
| 底符 | 20 | 固定 |
| 门清荣和 | +10 | is_menzen and not is_tsumo |
| 自摸 | +2 | is_tsumo（门清/副露都加） |
| 雀头役牌 | +2 each | 雀头是三元/自风/场风 value |
| 中张刻子 | 2(明)/4(暗) | 非幺九 |
| 幺九刻子 | 4(明)/8(暗) | 幺九字 |
| 中张杠子 | 8(明)/16(暗) | 非幺九 |
| 幺九杠子 | 16(明)/32(暗) | 幺九字 |
| 边张/嵌张/单骑听 | +2 | 听牌形式（非两面） |

**特殊**：
- 七对子：固定 25 符，不计算其它。
- 平和门清自摸：20 符（不加自摸2符，特殊规则）。
- 进位：除 20（七对25）和特殊平和外，向上取整到 10 的倍数。

### 4.2 听牌符判定（需 WinForm 的 pair + winning_tile）
- **两面**：雀头 + 顺子两端，如 34 听 2/5 → 0 符。
- **边张**：12听3 / 89听7 → +2。
- **嵌张**：13听2 / 24听3 等 → +2。
- **单骑/边张单骑**：听雀头 → +2。

> ⚠️ 现有 `_calculate_fu`（scoring.py:397）只算了底符+门清荣和+自摸+刻/杠，**缺雀头符、听牌符、进位细节**，且对平和无特例。需重写。

---

## 5. 点数计算（Points Engine）

### 5.1 基础点数（base points）
```
满贯以下:  base = fu × 2^(han+2)，上限 2000（满贯线）
满贯(5番): 8000（不论符）
跳满(6-7): 12000
倍满(8-10): 16000
三倍满(11-12): 24000
役满(13+): 32000（多役满倍乘）
```

现有 `score_table`（满贯以下常用番符组合）+ `mangan_scores`（满贯及以上）已基本可用（scoring.py:88-115），但需补全所有 (han,fu) 组合与边界。

### 5.2 支付分配（get_final_score_and_payout）—— 当前致命缺陷

```python
def get_final_score_and_payout(
    self, win_details, game_state, winner_index, loser_index
) -> Dict[int, int]:  # {player_idx: 分数变化}
```

**自摸 (TSUMO)**：
```
庄家自摸: 每个闲家付 base×2，向上取整到100
闲家自摸: 庄家付 base×2，其余闲家付 base×1
```

**荣和 (RON)**：
```
放铳者付: base×4(闲家和) / base×6(庄家和)，向上取整到100
```

**立直棒 / 本场**：
- 和牌时场上所有 riichi_sticks 归赢家（多家荣和按头跳/规则）。
- 本场 honba：自摸每人+honba×100，荣和放铳者+honba×300。

> ⚠️ 现有函数找不到 winner（scoring.py:256-271 全是注释和 `return {}`）。**必须按本签名重写**，winner_index 由 RulesEngine 从 outcome 透传。

---

## 6. 宝牌（Dora）

`_calculate_dora`（scoring.py:447）已基本正确，需确认：
- **表宝牌**：根据 dora_indicators，每个指示牌指向"下一张"（万筒索9→1，风东→南→西→北→东，三元白→发→中→白）。
- **里宝牌**：仅立直和牌时，按 ura_dora_indicators 计算。
- **赤宝牌**：手牌+副露中所有 `is_red=True` 的牌。
- **杠宝牌**：每次杠后 `wall.reveal_new_dora()` 增加一组表+里指示牌。

现有 `_get_dora_values_from_indicators`（scoring.py:477）逻辑正确但冗长，可复用 Wall `_calculate_next_tile_value`（game_state.py:215）。

---

## 7. 振听（Furiten）

`_is_furiten`（scoring.py:436）当前 stub 返回 False。三种振听：

| 类型 | 定义 | 影响 |
|------|------|------|
| **舍牌振听 (Kamen)** | 听的牌中任一张在自己弃牌河里（含本局之前巡目） | 不能荣和，只能自摸 |
| **同巡振听 (Temporary)** | 本巡自己曾有机会荣和但PASS（或荣和被振听阻断） | 本巡不能荣和，过自己摸牌后解除 |
| **立直振听 (Riichi)** | 立直后曾荣和机会但PASS | 之后只能自摸（永久） |

**实现**：
```
_is_furiten(player, winning_tile, game_state):
    waits = hand_analyzer.find_wait_tiles(player.hand, player.melds)
    discard_values = {t.value for t in player.discards}
    # 舍牌振听：听的牌任一在弃牌河
    if waits & discard_values: return True
    # 同巡振听：本巡有荣和机会但未荣和（需 GameState 记录"本巡见过的可荣和牌"）
    if game_state.current_turn_passed_furiten_tiles: ...
    # 立直振听：立直后PASS过荣和
    if player.riichi_declared and player.riichi_passed_ron: return True
    return False
```

> ⚠️ 同巡/立直振听需要 GameState 新增字段记录"本巡/立直后见过的可荣和牌 value 集合"。ActionValidator 在生成 RON 候选时也会查振听（已委托 Scoring.is_valid_win）。

---

## 8. 振听 / 和牌合法性公共入口

ActionValidator 调用：
```python
scoring.is_valid_win(player, winning_tile, is_tsumo, game_state) -> bool
```
= `calculate_win_details(...).is_valid_win`（即：有形状 + 一番缚 + 非振听）。

> 注意：`is_valid_win` 在 ActionValidator 生成 RON/TSUMO 候选时频繁调用，性能敏感。可缓存：同一玩家同一手牌的 WinForm 分解可在一巡内复用。

---

## 9. 流局罚符（calculate_ryuukyoku_penalty_tenpai）

荒牌流局时：
```
全员听牌/不听 按 config 分摊（默认：听-3000 / 不听+3000 等分给听者，或全听/全不听则无变化）
具体：听牌者 = +3000×(不听人数)/听人数，反之
```
现有 `process_hand_outcome` 已调用此函数（rules_engine.py:222），但函数体需实现，并在 outcome 中带 `tenpai_players: List[int]`。

---

## 10. 与现有代码对齐 & 修复清单

| 现状 | 设计要求 | 优先级 |
|------|----------|--------|
| `_get_final_score_and_payout` return {} | 按 §5.2 重写，加 winner_index | **A5 致命** |
| 符计算缺雀头/听牌/进位 | 按 §4 重写 | 高 |
| 役仅 4 种 | 按 §3 补全 | 高（分批） |
| ✅ `_is_furiten` 三种振听 | 舍牌/同巡/立直 全部实现 | 完成 v2 |
| ✅ context rinshan/haitei 等 | GameState 标记 + context 补全 | 完成 v1 |
| ✅ 流局罚符 | 按 §9 实现 | 完成 v1 |
| ❌ 流局满贯 nagashi mangan | 需追踪弃牌河被鸣标记 | 待实现 |
| ❌ 罚符 Chombo | INVALID_WIN 分支未计分 | 待实现 |
| ❌ 役满细节 | 国士十三面/真九莲/大车轮 未区分 | 待实现 |

---

## 11. 验收标准

1. ✅ §3 役种表基本实现（普通役22种 + 役满11种）；缺：国士十三面、真九莲、大车轮。
2. ✅ §4 符数与天凤/雀魂一致（覆盖 1-4 番 20-110 符常见组合）。
3. ✅ §5.2 支付分配正确，和牌能结算分数。
4. ✅ §7 三种振听生效（舍牌/同巡/立直）。
5. ⚠️ 黄金牌谱回放：基础和牌点数一致；流局满贯/罚符场景未覆盖。

---

## 12. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，役种全集 + 符/点/振听蓝图 |
| 2026-08-01 | v1 实现：役种22+役满11、符数、支付、舍牌振听、context补全 |
| 2026-08-02 | v2 实现同巡振听 + 立直振听（PlayerState 新增 temporary_furiten/riichi_furiten，Controller PASS 时标记） |
