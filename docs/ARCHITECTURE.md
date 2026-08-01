# 设计文档 09：架构总览 (ARCHITECTURE)

> 状态：**草案 v1** ｜ 优先级：**高（接手首读）** ｜ 关联：`PROJECT_OVERVIEW.md` §4 的扩展版
> 本文用图与表说清三层架构、模块调用关系、数据流、设计原则。是其它设计文档的"地图"。

---

## 1. 三层架构总图

```
┌──────────────────────────────────────────────────────────────┐
│  RL / 应用层（阶段3，当前空）                                   │
│  Agent (DQN/PPO) ← Trainer → 多智能体调度                      │
└──────────────────────────┬───────────────────────────────────┘
                           │ Gymnasium API
┌──────────────────────────▼───────────────────────────────────┐
│  Layer 1: Env 层  (src/env/)                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ MahjongEnv   │  │StateEncoder  │  │  Renderer    │         │
│  │ (gym.Env)    │  │(obs 编码)     │  │ (文本渲染)    │         │
│  └──────┬───────┘  └──────────────┘  └──────────────┘         │
│         │ 委托                                                 │
└─────────┼─────────────────────────────────────────────────────┘
          │
┌─────────▼─────────────────────────────────────────────────────┐
│  Layer 2: Controller 层  (src/env/core/GameController.py)     │
│  流程状态机：step() 主循环、auto-flow、响应收集、局间切换       │
│  持有 GameState + Wall + RulesEngine                            │
└─────────┬──────────────────────────────────┬──────────────────┘
          │ 委托 RulesEngine（校验/计分）      │ 读写
          │                                  │
┌─────────▼──────────────┐ ┌─────────────────▼─────────────────┐
│  Layer 3a: Rules 层     │ │  Layer 3b: 数据层 (core/)          │
│  (src/env/core/rules/)  │ │  GameState / PlayerState / Wall   │
│  ┌────────────────┐     │ │  / Meld / GamePhase               │
│  │ RulesEngine    │←门面│ │  + apply_action (纯数据变更)       │
│  │ (Facade)       │     │ │                                   │
│  └────┬───────────┘     │ └───────────────────────────────────┘
│       │ 委托             │
│  ┌────▼──────┐ ┌─────────┐ ┌──────────────┐                    │
│  │Action     │ │Hand     │ │  Scoring     │                    │
│  │Validator  │ │Analyzer │ │  (役/番/符)   │                    │
│  └────┬──────┘ └────┬────┘ └──────┬───────┘                    │
│       │             │             │                             │
│       └─────────────┴─────────────┘                             │
│         ActionValidator 依赖 HandAnalyzer + Scoring              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. 模块职责一览表

| 层 | 模块 | 文件 | 职责 | 是否有状态 | 完成度 |
|----|------|------|------|-----------|--------|
| Env | MahjongEnv | mahjong_env.py | Gym 接口封装、reward、action_mask | 有 | 🟡50% |
| Env | StateEncoder | state_encoder.py | GameState → observation | 无 | 🟡50% |
| Env | Renderer | renderer.py | 文本渲染 | 无 | 🟢80% |
| Controller | GameController | GameController.py | 流程状态机 | 有 | 🟡65% |
| Data | GameState/PlayerState | game_state.py | 完整游戏状态 + apply_action | 有 | 🟡70% |
| Data | Wall | game_state.py | 牌墙/宝牌/岭上 | 有 | 🟢85% |
| Data | Tile/Action/Meld | actions.py/game_state.py | 不可变核心数据结构 | 无 | 🟢90% |
| Rules | RulesEngine | rules_engine.py | 门面 + 高层流程控制 | 无(持引用) | 🟡60% |
| Rules | ActionValidator | action_validator.py | 候选动作生成 + 优先级 | 无 | 🟡55% |
| Rules | HandAnalyzer | hand_analyzer.py | 手牌分解/听牌 | 无 | 🔴30% |
| Rules | Scoring | scoring.py | 役/番/符/点/振听 | 无 | 🔴25% |

---

## 3. 调用关系（谁调用谁）

```
scripts/play.py (或 Trainer)
    └─> MahjongEnv.reset / step
            ├─> GameController.reset / step
            │       ├─> GameState.reset_*/apply_action/update_scores   [数据]
            │       ├─> Wall.shuffle/draw/reveal                       [数据]
            │       └─> RulesEngine.*
            │              ├─> generate_candidate_actions / resolve_response_priorities
            │              │       └─> ActionValidator.*
            │              │              └─> HandAnalyzer.is_tenpai/find_wait_tiles
            │              │              └─> Scoring.is_valid_win
            │              ├─> determine_next_phase / next_hand_state / is_game_over  [自身]
            │              └─> process_hand_outcome
            │                      └─> Scoring.calculate_win_details / get_final_score_and_payout
            │                              └─> HandAnalyzer.find_all_winning_forms
            ├─> StateEncoder.encode (读 GameState)
            └─> Renderer.render (读 GameState)
```

**关键依赖方向**：上层 → 下层，Rules 内部 HandAnalyzer ← Scoring ← ActionValidator ← RulesEngine。**无环依赖**。

---

## 4. 一次 step 的完整数据流

```
1. Env.step(action_idx)
2.   action = current_candidates[action_idx]            # 取 Action 对象
3.   current_player = controller.gamestate.current_player_index
4.   old_score = players[current_player].score
5.   controller.step(current_player, action):
       5a. 校验 player_idx 合法
       5b. 按 phase 分发:
            - PLAYER_DISCARD → apply_action + determine_next_phase
            - WAITING_FOR_RESPONSE → 记录响应, 收齐则 resolve + execute
       5c. _process_auto_flow():                        # 推进瞬态
            - ACTION_PROCESSING → 岭上摸牌
            - PLAYER_DRAW → 常规摸牌/流局
            - HAND_OVER_SCORES → 结算或开新局
       5d. done = (phase == GAME_OVER)
6.   reward = _calculate_reward(old_score, state)
7.   info = _get_info():
       7a. candidates = rules_engine.generate_candidate_actions(gs, current_player)
       7b. action_mask = build_mask(candidates)
8.   obs = _get_observation(): StateEncoder.encode(gs, current_player, candidates)
9.   return obs, reward, terminated, truncated, info
```

---

## 5. 核心设计原则

### 5.1 数据 / 流程 / 规则 三分离
- **数据层**（GameState.apply_action）：只改数据，不含校验、不含流程。
- **流程层**（Controller）：驱动状态机、自动推演，不含规则判断。
- **规则层**（RulesEngine + 三子件）：只读 GameState，做校验/计分，不改数据。
> 这是项目最关键的设计决策，使每层可独立测试。

### 5.2 摸牌是流程非动作
摸牌（DRAW）刻意从 ActionType 移除，作为 auto-flow 瞬态。玩家只在 PLAYER_DISCARD / WAITING_FOR_RESPONSE 决策。简化动作空间。

### 5.3 候选动作扁平化
Discrete(100) + action_mask + valid_actions，兼容标准 RL 算法。

### 5.4 RulesEngine 无状态 + 依赖注入
所有规则子件无状态，RulesEngine 持引用，便于测试替换。

### 5.5 不可变核心数据结构
Tile / Action / Meld 是 frozen dataclass，安全可哈希。

---

## 6. 包结构约定

```
src/
├── env/
│   ├── mahjong_env.py        # Env 层入口
│   ├── state_encoder.py
│   ├── renderer.py
│   └── core/
│       ├── actions.py        # Tile/Action/Meld/KanType/ActionType
│       ├── game_state.py     # GameState/PlayerState/Wall/GamePhase
│       ├── GameController.py # Controller
│       └── rules/
│           ├── rules_engine.py
│           ├── action_validator.py
│           ├── hand_analyzer.py
│           ├── scoring.py
│           └── constants.py
├── agent/                    # RL agent（阶段3）
├── trainning/                # 训练循环（阶段3）
└── utils/                    # 配置/日志/工具
```

> 拼写注意：`trainning`（双 n）是历史拼写，保持一致或统一改 `training`。

---

## 7. 跨层约束（禁止的反模式）

- ❌ Controller 直接调 HandAnalyzer/Scoring（必须经 RulesEngine 门面）。
- ❌ ActionValidator/Scoring 改 GameState（只读）。
- ❌ HandAnalyzer 读 wall.dora_indicators（宝牌是 Scoring 职责）。
- ❌ Scoring 自行分解手牌（用 HandAnalyzer.WinForm）。
- ❌ Env 跳过 Controller 直接改 GameState。
- ❌ StateEncoder 暴露隐藏信息（他人手牌/里宝牌）。

---

## 8. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，三层架构图 + 调用关系 + 设计原则 |
