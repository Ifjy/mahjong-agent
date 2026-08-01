# 设计文档 03：游戏流程状态机设计 (GAME_FLOW_STATEMACHINE)

> 状态：**草案 v1** ｜ 优先级：**高** ｜ 关联代码：`src/env/core/GameController.py`、`game_state.py(GamePhase)`
> 本文定义 GamePhase 状态机、Controller 主循环、响应收集、各种流局与终局规则。

---

## 1. GamePhase 状态机总览

现有 9 个阶段（game_state.py:20-31），本文明确每个阶段的**进入条件、退出条件、是否需要玩家输入**：

```
                          reset()
                             │
                             ▼
        ┌───────────────── GAME_START ─────────────────┐
        │                                              │
        ▼                                              │
   HAND_START ──> DEALING ──> PLAYER_DISCARD ◄────────┐│ (开新局)
                                  │                   ││
                  ┌───────────────┼───────────────┐   ││
                  ▼ (DISCARD)     ▼ (TSUMO)       ▼ (KAN)
          WAITING_FOR_RESPONSE  HAND_OVER_SCORES  ACTION_PROCESSING
                  │                   │                   │
        ┌─────────┼─────────┐         │                   │ (岭上摸牌)
   全PASS    鸣牌CHI/PON   RON/KAN     │                   │
        │         │         │         │                   ▼
        ▼         ▼         ▼         │            回 PLAYER_DISCARD
   PLAYER_DRAW  PLAYER_     HAND_      │
        │      DISCARD     OVER        │
        │(摸牌)            SCORES      │
        ▼                    │         │
   PLAYER_DISCARD            ▼         │
                        ┌──────────────┘
                        │
                  (is_game_over?)
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
          GAME_OVER          开新局 HAND_START
```

### 阶段语义表

| 阶段 | 需要玩家输入? | 进入条件 | 退出条件 | Controller 动作 |
|------|--------------|----------|----------|----------------|
| GAME_START | 否 | reset | reset_new_hand | 初始化整场 |
| HAND_START | 否 | GAME_START/开新局 | DEALING | reset_new_hand 数据 |
| DEALING | 否（瞬态） | HAND_START | PLAYER_DISCARD | 发牌（庄家14张） |
| PLAYER_DRAW | 否（瞬态） | 上家全PASS/轮转 | PLAYER_DISCARD/流局 | auto-flow 摸牌 |
| **PLAYER_DISCARD** | **是** | 摸牌后/鸣牌后 | DISCARD→WAITING / KAN→PROCESSING / TSUMO→HAND_OVER | 等待玩家选动作 |
| **WAITING_FOR_RESPONSE** | **是（3人）** | 他人DISCARD后 | 凑齐响应→解决 | 收集 pending_responses |
| ACTION_PROCESSING | 否（瞬态） | KAN后 | PLAYER_DISCARD | auto-flow 岭上摸牌+翻宝牌 |
| HAND_OVER_SCORES | 否 | 和牌/流局 | GAME_OVER 或 开新局 | 结算分数 |
| GAME_OVER | 终态 | is_game_over | — | 结束 |

> **关键设计**：只有 `PLAYER_DISCARD` 和 `WAITING_FOR_RESPONSE` 需要玩家输入，其余都是 Controller auto-flow 瞬态推进。这与现有 `_process_auto_flow` 设计一致。

---

## 2. Controller step 主循环（详细时序）

### 2.1 Env → Controller 调用约定

```python
controller.step(player_idx, action) -> (GameState, reward, done, info)
```
- Env 必须保证 `player_idx` 是当前合法行动者。
- `action` 必须已在候选动作列表中（Env 通过 action_mask 保证）。

### 2.2 step 内部分发逻辑

```
step(player_idx, action):
    1. 校验：非响应阶段时 player_idx 必须 == current_player_index
    2. 按 phase 分发:
        PLAYER_DISCARD       -> _handle_player_discard_phase
        WAITING_FOR_RESPONSE -> _handle_response_phase
        其它 -> 报错（不应在此处step）
    3. _process_auto_flow()  # 推进瞬态阶段
    4. done = (phase == GAME_OVER)
    5. return (gamestate, reward, done, {})
```

### 2.3 _handle_player_discard_phase（自摸回合）

```
apply_action(idx, action)           # 纯数据
next_phase = rules_engine.determine_next_phase(gs, action)

switch next_phase:
    HAND_OVER_SCORES:  (TSUMO)
        _process_hand_outcome("TSUMO", action, winner=idx)
    ACTION_PROCESSING: (KAN 暗/加)
        phase = ACTION_PROCESSING   # auto-flow 接管岭上摸牌
    WAITING_FOR_RESPONSE: (DISCARD)
        phase = WAITING_FOR_RESPONSE
        pending_responses.clear()
    其它: 报错
```

### 2.4 _handle_response_phase（响应回合）

```
pending_responses[player_idx] = action   # 记录

if len(pending_responses) < num_players - 1:
    return  # 还没收齐，等待其他玩家step

# 收齐 -> 解决优先级
win_action, win_idx = rules_engine.resolve_response_priorities(pending_responses, gs)

if win_action and win_action.type != PASS:
    _execute_response(win_idx, win_action)   # 应用鸣牌/荣和
else:
    _advance_to_next_turn()                   # 全PASS，下家摸牌
```

> ⚠️ **当前缺陷**：响应阶段要求 Env **连续多次 step**（每个响应玩家一次），直到收齐。Env 必须按"逆时针顺序逐个询问"。这隐含一个**多智能体调度问题**（详见 OBSERVATION_ENCODING_DESIGN / 后续 Agent 设计）。当前 play.py 用单循环模拟。

### 2.5 _execute_response（鸣牌/荣和应用）

```
apply_action(win_idx, win_action)
next_phase = rules_engine.determine_next_phase(gs, win_action)

switch next_phase:
    HAND_OVER_SCORES: (RON)
        _process_hand_outcome("RON", action, winner=win_idx, loser=last_discard_player)
    PLAYER_DISCARD: (CHI/PON)
        current_player = win_idx; phase = PLAYER_DISCARD
    ACTION_PROCESSING: (明杠)
        current_player = win_idx; phase = ACTION_PROCESSING
```

---

## 3. auto-flow 自动推演（_process_auto_flow）

循环执行，直到进入需要输入的阶段或结束：

```
while True:
    switch phase:
        ACTION_PROCESSING:
            _perform_rinshan_draw()   # ⚠️ 修方法名 bug
            phase = PLAYER_DISCARD
            # 注意：岭上摸牌后若杠可继续杠，或可岭上开花自摸
            continue

        PLAYER_DRAW:
            _perform_regular_draw()   # 摸1张；牌山空则流局
            phase = PLAYER_DISCARD
            continue

        HAND_OVER_SCORES:
            if rules_engine.is_game_over(gs):
                phase = GAME_OVER
            else:
                _start_new_hand()      # 开新局
            break

        PLAYER_DISCARD / WAITING_FOR_RESPONSE / GAME_OVER:
            break   # 需要输入或已结束
```

---

## 4. 发牌逻辑（_start_new_hand）

标准日麻发牌（现有 GameController._start_new_hand 已实现）：
1. `reset_new_hand()`：清手牌、设座风、洗牌墙。
2. 三轮每人 4 张 + 一轮每人 1 张 = 每人 13 张。
3. 庄家多摸 1 张（第14张）作为 `drawn_tile`。
4. 理牌（hand.sort）。
5. `current_player = dealer_index`，`phase = PLAYER_DISCARD`。

> 座风计算（game_state.py:338）：`seat_wind = (i - dealer_index + num_players) % num_players`。

---

## 5. 各种流局（中途流局 / 荒牌流局）

### 5.1 荒牌流局（EXHAUSTIVE_DRAW）
- 触发：`wall.draw_tile()` 返回 None（活动牌墙摸完）。
- `_process_hand_outcome("EXHAUSTIVE_DRAW")`。
- 结算：Scoring 计算听牌罚符（听/未听分摊），写入 outcome。
- 连庄判断：庄家听牌则连庄（需 Scoring 提供 tenpai 状态）。

### 5.2 途中流局（ABORTIVE / SPECIAL，建议补充）

标准日麻 4 种途中流局，当前代码几乎未实现：

| 类型 | 触发条件 | 处理 |
|------|----------|------|
| 九种九牌 (Kyuushu kyuuhai) | 第1巡门清，手牌9种以上不同幺九字牌 → 玩家可声明 SPECIAL_DRAW | 连庄，本场+1 |
| 四风连打 (Suuufu renda) | 第1巡4人打同一风牌 | 连庄，本场+1 |
| 四家立直 (Suuu riichi) | 4人全部立直且成立 | 连庄，本场+1 |
| 四杠散了 (Suuu kan sanma) | 4个杠（非同一玩家）后 | 连庄，本场+1 |

> 当前 `ActionType.SPECIAL_DRAW` 仅支持九种九牌（action_validator.py:418）。其余3种需 Controller 在特定时机检测。

### 5.3 流局满贯 (Nagashi mangan)
- 荒牌流局时，若某玩家弃牌河全是幺九字牌且未被鸣，可声明流局满贯 = 满贯点数。
- 当前未实现，建议在荒牌结算时由 Scoring 检测。

---

## 6. 响应收集与优先级（详见 RULES_ENGINE_DESIGN §6.3）

补充流程层细节：
- WAITING_FOR_RESPONSE 阶段，**除打牌者外的 3 人**都要响应。
- 每个响应玩家通过 step 提交动作（RON/KAN/PON/CHI/PASS）。
- 收齐后 `resolve_response_priorities`：RON > 杠碰 > 吃。
- **头跳 vs 多家荣和**：config 化（RULES_ENGINE_DESIGN §8.2）。

---

## 7. 局间切换 / 场风推进（determine_next_hand_state）

```
本局结束 -> process_hand_outcome -> outcome
        -> determine_next_hand_state(gs, outcome) -> next_hand_state
        -> gs.apply_next_hand_state(next_hand_state)
        -> 若 game_over: phase=GAME_OVER 否则 _start_new_hand
```

推进规则（标准半庄）：

| 结束类型 | 庄家 | 本场 | 场风/局数 |
|----------|------|------|-----------|
| 庄家和 | 连庄 | +1 | 不变 |
| 闲家和 | 换庄 | 0 | dealer绕回initial→场风+1,局=1；否则局+1 |
| 流局(庄听) | 连庄 | +1 | 不变 |
| 流局(庄不听) | 换庄 | 0 | 同上 |
| 途中流局 | 连庄 | +1 | 不变 |
| 罚符 | 不变 | 不变 | 不变 |

**西入 (西场)**：半庄南4局后若无人被飞且平局，可选延长到西场（config `"extensions": True`）。

> ⚠️ **当前缺陷**：`initial_dealer_index` 缺失（A2），`dealer_is_tenpai` 硬编码 True。需修。

---

## 8. 终局判定（is_game_over）

满足任一即终局（详见 RULES_ENGINE_DESIGN §5.3）：
1. 飞人：`score < 0`（config: `"tobi_rule": "any"|"dealer_only"|"none"`）。
2. 完成最后场风：`round_wind > max_game_wind`（半庄=1，东风=0）。
3. オーラス细规则：最后局庄家和是否延长（config）。

---

## 9. 与现有代码的对齐 & 修复清单

| 现状 | 设计要求 | 动作 |
|------|----------|------|
| ✅ `_perform_rinshan_draw` 用 `draw_replacement_tile` + `reveal_new_dora` | 已修 (A1) | 完成 |
| ✅ `initial_dealer_index` 字段 | 已修 (A2) | 完成 |
| ✅ `dealer_is_tenpai` 由 Scoring 提供 | 已修 (B8) | 完成 |
| ✅ 4 种途中流局检测 | `_check_abortive_draw` + 杠后/响应检测 | 完成 v3 |
| ✅ 流局满贯 | Scoring 近似实现 | 完成 v3 |
| ✅ 岭上摸牌后翻新宝牌 | `reveal_new_dora()` | 完成 |
| ✅ seed 复现 | Wall 接受 rng, Env.reset(seed) 注入 | 完成 v4 |
| ❌ 西入 (extensions) | 南4局后延长西场 | 待实现 |
| ❌ 多家荣和 | 头跳为默认, config 开关待加 | 待实现 |

---

## 10. 验收标准

1. ✅ §1 状态机所有转换都有代码路径覆盖。
2. ✅ 一局能从 DEALING 走到 GAME_OVER 或 HAND_OVER→开新局，无死锁/异常。
3. ✅ 响应阶段正确收齐 3 人响应。
4. ✅ 杠→岭上摸牌→翻宝牌 链路通。
5. ✅ 4 种途中流局 + 流局满贯可触发（九种九牌为玩家声明，其余自动检测）。
6. ✅ seed 固定下，reset 的洗牌/发牌可复现（同 seed 同初始手牌）。

---

## 11. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，确立状态机图、auto-flow、流局/终局规则 |
| 2026-08-02 | v2-v4 实现：途中流局检测、流局满贯、岭上翻宝、seed 复现；修复清单 7/9 完成 |
