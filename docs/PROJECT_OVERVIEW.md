# Mahjong RL 项目概览 (PROJECT OVERVIEW)

> 本文档面向后续接手的 AI / 开发者，目的是用最短时间理解当前项目状态、架构与已知问题。
> 最后更新：2026-08-01

---

## 1. 项目目标

本项目本质是一个 **日本麻将 (Riichi Mahjong)** 游戏，最终目标是 **训练一个强化学习 (RL) Agent**。
但在实践过程中发现：**搭建一个规则正确、状态完整、可被 RL 使用的麻将环境，是整个工程中最难的部分**。
因此当前阶段的核心工作聚焦于：**构建一个正确、可运行、符合 Gym API 的日麻环境**。Agent / 训练相关代码目前仅为占位骨架，尚未实现。

---

## 2. 技术栈

- 语言：Python
- 环境接口：**Gymnasium** (`gym.Env`)
- 数值计算：numpy / pandas
- （计划）深度学习：torch（当前 `requirements.txt` 中被注释）
- 渲染：纯文本（Text），无 GUI

依赖见 `requirements.txt`（极简：gymnasium, numpy, pandas, matplotlib, # torch）。

---

## 3. 目录结构

```
mjagent/
├── configs/default_config.yaml        # 训练/环境配置 (目前很简略)
├── scripts/
│   ├── play.py                        # 人机/AI 对局入口 (已实现交互循环，含启发式 AI)
│   ├── train.py                       # 训练入口 (空文件)
│   └── evaluate.py                    # 评估入口 (空文件)
├── src/
│   ├── env/
│   │   ├── mahjong_env.py             # Gymnasium 环境封装 (Env 层)
│   │   ├── state_encoder.py           # 状态编码器 (GameState -> observation)
│   │   ├── renderer.py                # 文本渲染器
│   │   └── core/
│   │       ├── actions.py             # 核心数据结构: Tile / ActionType / KanType / Action
│   │       ├── game_state.py          # GameState / PlayerState / Wall / Meld / GamePhase
│   │       ├── GameController.py      # 游戏控制器 (流程状态机, "大脑")
│   │       └── rules/
│   │           ├── rules_engine.py    # 规则引擎门面 (Facade, 协调器)
│   │           ├── action_validator.py# 动作合法性校验 + 响应优先级
│   │           ├── hand_analyzer.py   # 手牌结构分析 (和牌形/听牌/分解)
│   │           ├── scoring.py         # 役种/番/符/点数 计分
│   │           └── constants.py       # 常量定义 (牌值/场风/优先级)
│   ├── agent/                         # 以下全部为空文件 (占位)
│   │   ├── base_agent.py
│   │   ├── dqn_agent.py
│   │   └── models.py
│   ├── trainning/trainer.py           # 空文件 (占位)
│   └── utils/                         # config_loader/helper/logger 全部为空文件
├── tests/test_env.py                  # 环境测试 (3 个极简测试，且接口已过时)
├── TODO.txt                           # RulesEngine 的方法签名清单 (CSV 注释)
└── README.md
```

> 注意：`src/agent/*`、`src/trainning/*`、`src/utils/*` 文件虽然存在，但 **内容为空 (0 行)**，仅为目录骨架。

---

## 4. 核心架构 (分层设计)

项目采用了清晰的三层架构，这是该项目最有价值的设计决策：

```
┌──────────────────────────────────────────────────────────┐
│  Gymnasium API 层 (mahjong_env.py)                        │
│  reset() / step(action_idx) / 观察空间 / 动作掩码          │
└───────────────┬──────────────────────────────────────────┘
                │ 委托
┌───────────────▼──────────────────────────────────────────┐
│  Controller 层 (GameController.py) —— 流程状态机          │
│  管理 GameState 生命周期、step() 主循环、自动流程推演     │
│  (摸牌/杠后岭上摸牌/局终结算/开新局)                      │
└───────────────┬──────────────────────────────────────────┘
        委托 RulesEngine        │   读写 GameState
┌───────────────▼──────────────────────────▼───────────────┐
│  Rules 层 (rules/)                                        │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────────┐  │
│  │ RulesEngine  │  │ ActionValidator│  │ HandAnalyzer  │  │
│  │ (Facade/协调) │  │ (合法性+优先级)│  │ (手牌分解)    │  │
│  └──────────────┘  └───────────────┘  └───────────────┘  │
│              ┌───────────────┐                            │
│              │    Scoring    │                            │
│              │ (役/番/符/点) │                            │
│              └───────────────┘                            │
└──────────────────────────────────────────────────────────┘
```

### 4.1 数据结构层 (`actions.py` / `game_state.py`)

- **`Tile`**：frozen dataclass，`value` 0-33（0-8万/9-17筒/18-26索/27-30风/31-33三元），`is_red` 标记赤宝牌。可哈希、可排序。
- **`ActionType`**：枚举，DISCARD / RIICHI / CHI / PON / KAN / TSUMO / RON / PASS / SPECIAL_DRAW。**已刻意移除 `DRAW`**（摸牌被视为流程控制，不是玩家选择）。
- **`KanType`**：CLOSED(暗杠) / ADDED(加杠) / OPEN(大明杠)。
- **`Action`**：frozen dataclass，统一动作表示，自带 `to_feature_vector()`（独热编码）。
- **`Meld`**：副露（吃碰杠）的不可变表示。
- **`GamePhase`**：状态机枚举，覆盖 GAME_START / HAND_START / DEALING / PLAYER_DRAW / PLAYER_DISCARD / WAITING_FOR_RESPONSE / ACTION_PROCESSING / HAND_OVER_SCORES / GAME_OVER。
- **`PlayerState`**：手牌/副露/弃牌河/drawn_tile + 立直状态 + 缓存（门清/听牌/振听）。
- **`Wall`**：牌墙，含洗牌、宝牌指示牌、岭上牌、赤宝牌、宝牌值计算。**实现较完整**。
- **`GameState`**：完整游戏状态 + `apply_action()`（纯数据变更，不含流程/校验）。

### 4.2 Controller 层 (`GameController.py`)

- 这是流程的"大脑"，持有 `GameState` + `Wall` + `RulesEngine`。
- `step(player_idx, action)` 是主入口，按 `GamePhase` 分发到 `_handle_player_discard_phase` / `_handle_response_phase`。
- `_process_auto_flow()`：自动推演循环，处理摸牌、岭上摸牌、局终结算、开新局，直到需要玩家输入或游戏结束。
- 响应管理：`pending_responses` 收集所有玩家响应，凑齐后用 `resolve_response_priorities` 解决冲突。

### 4.3 Rules 层 (`rules/`)

- **`RulesEngine`**：纯 Facade/协调器，自身不含规则逻辑，只委托：
  - `generate_candidate_actions` / `resolve_response_priorities` → ActionValidator
  - `process_hand_outcome` → Scoring
  - `determine_next_phase` / `determine_next_hand_state` / `is_game_over` → 高层流程控制（自身实现）
- **`ActionValidator`**：生成合法动作（自摸回合 / 响应回合）+ 响应优先级解决（Ron > 杠碰 > 吃）。
- **`HandAnalyzer`**：手牌分解（标准型/七对子/国士无双）、听牌判断、和牌形检查。**核心递归分解函数仍是占位/回退实现**。
- **`Scoring`**：役种（部分实现）、番/符（部分实现）、点数表（部分实现）、宝牌、振听（stub）。

### 4.4 Env / Encoder 层

- **`MahjongEnv`**：候选动作扁平化方案。`action_space = Discrete(100)`，info 中带 `action_mask` + `valid_actions`。奖励目前是临时实现（点数变化/步惩罚）。
- **`StateEncoder`**：将 GameState 编码为 dict observation（hand/melds/discards/dora/wind/progress/last_action/scores + action_candidates + action_mask）。**注意：用的是旧版 `gym.spaces` 而非 `gymnasium.spaces`**。

---

## 5. 各模块完成度评估

| 模块 | 完成度 | 说明 |
|------|--------|------|
| 数据结构 (Tile/Action/Meld) | 🟢 90% | 设计成熟，可复用 |
| Wall (牌墙) | 🟢 85% | 宝牌/岭上牌/赤牌基本完整 |
| GameState.apply_action | 🟡 70% | 主流程打通，有重复代码与 bug（见 §7） |
| GameController (状态机) | 🟡 65% | 主循环跑通，部分自动流程边界未测 |
| ActionValidator | 🟡 55% | 吃碰杠荣和/立直/九种九牌逻辑在，但 **依赖未完成的 HandAnalyzer/Scoring** |
| HandAnalyzer | 🔴 30% | **核心递归面子分解是 TODO/占位**，回退到 value-based（丢失一杯口等役） |
| Scoring | 🔴 25% | 役种仅断幺/役牌/立直/自摸；**符计算是 stub；振听是 stub；支付分配函数未实现（NotImplemented）** |
| StateEncoder | 🟡 50% | 框架在，gym 版本混用、 melds 用 dict 访问（与 Meld dataclass 不一致） |
| Renderer | 🟢 80% | 文本渲染完整，含宝牌标记 |
| MahjongEnv | 🟡 50% | Gym 接口在，reward/seed 处理粗糙 |
| Agent / Training / Utils | ⚫ 0% | 全部空文件 |

---

## 6. 已确认的 Bug / 不一致点（接手优先核查）

> **更新 2026-08-01：阶段0止血已完成，A1~A6 + C1 已修复（见下方 ✅ 标记）。环境现可稳定推进 800+ 步、play.py 可正常交互。剩余 🔴 项属阶段1（规则补全）。**

### ✅ 已修复（阶段0 止血）
1. ✅ **`GameController._perform_rinshan_draw()` 调用了不存在的 `self.wall.draw_rinshan_tile()`** → 改为 `draw_replacement_tile()`，并补上杠后 `reveal_new_dora()`。
2. ✅ **`rules_engine.py` 引用 `game_state.initial_dealer_index`** → GameState 新增 `initial_dealer_index` 字段（+ `last_draw_was_rinshan` 供岭上开花判定）。
3. ✅ **`meld` 被当 dict 访问**（action_validator / renderer / state_encoder）→ 全部改为属性访问 `meld.type` / `meld.tiles`。
4. ✅ **死代码 `_apply_kan_tile_removal` / `_apply_meld_tile_removal`**（引用不存在的 `action.get_tiles_from_hand()`、字符串枚举比较、frozen Meld `.append`）→ 已删除；并修复 apply_action 中 **暗杠 drawn_tile 引用顺序 bug**（先置 None 再 remove 导致崩溃）。
5. ✅ **`StateEncoder` 用 `from gym.spaces`** → 改为 `gymnasium.spaces`；并修复 `_encode_last_action` 的 KeyError（last_action_info 无 "tile" 键，改为从 action_obj 提取）。
6. ✅ **config 不一致** → `default_config.yaml` 重写对齐代码；StateEncoder/Env 所有 config 读取加默认值；`MahjongEnv(config=None)` 可无参构造；play.py `max_actions` 30→100。
7. ✅ **响应阶段多智能体调度**（新增）→ Env 增加 `_next_responder()`，WAITING_FOR_RESPONSE 阶段自动推进到下一个未响应玩家，使单 agent 外层循环能驱动 4 人游戏。

### 🔴 未修复（阶段1 规则补全，非崩溃项）
> **更新 2026-08-02**：经全面审计，下列大部分项已在规则补全阶段(v2-v4)实现。当前真实剩余项：

- **Scoring 支付已实现**（含 winner_index/honba/riichi棒），不再是 return {}。
- **振听三种已全实现**（舍牌/同巡/立直），符数已重写，立直后杠限制已实现，食替已实现。
- **真实剩余规则项**（低频，非阻塞）：
  - 多家荣和（当前头跳为默认，config 开关待加）。
  - 抢杠 chankan（需加杠/暗杠触发响应的架构改动）。
  - 西入（南4局后延长西场）。
- **`MahjongEnv.reset()` 已支持 seed**（注入 Wall.rng，C3 已修）。
- **岭上牌摸完后禁止第5杠**（四杠散了规则已实现）。
- **config_loader.py 仍空**（default_config.yaml schema 与代码读取 key 不对齐，切换 ruleset 无效）→ 阶段 RL 准备时补。
- **黄金牌谱回放未实现**（规则正确性缺金标准验证）。
- **observation 缺 5 个 RL 字段**（opp_melds/riichi_flags/drawn_tile/actual_dora/my_furiten）。
- **reward 仍是临时实现**（per-player reward 未实现）。
- **大量裸 print**（无 logger，训练时 I/O 性能问题）。

---

## 7. 数据流概览（一局之内）

```
reset()
  └─> GameController.reset() -> _start_new_hand() (洗牌+发牌+设庄家阶段=PLAYER_DISCARD)

step(action_idx)
  ├─ Env 从 current_candidates[action_idx] 取出 Action
  ├─ Env 调 controller.step(current_player_idx, action)
  │    ├─ 按 game_phase 分发: PLAYER_DISCARD / WAITING_FOR_RESPONSE
  │    ├─ game_state.apply_action()   # 纯数据变更
  │    └─ rules_engine.determine_next_phase() -> 设新阶段
  ├─ controller._process_auto_flow()  # 自动摸牌/岭上/结算/开新局
  ├─ Env._get_info() -> rules_engine.generate_candidate_actions() # 生成下一批合法动作+mask
  └─ Env._get_observation() -> StateEncoder.encode()
```

---

## 8. 关键设计取舍（接手者须知）

- **候选动作扁平化 (Discrete + mask)**：动作空间是固定 `Discrete(100)`，真实候选通过 `info["action_mask"]` / `info["valid_actions"]` 暴露。优点：兼容标准 RL 算法；缺点：上限 100 候选，且需要把变长候选编码进 observation（StateEncoder 用 `to_feature_vector`）。
- **摸牌非动作**：刻意把摸牌设计为流程控制（auto-flow），玩家只在 PLAYER_DISCARD / WAITING_FOR_RESPONSE 阶段做决策。这简化了动作空间。
- **RulesEngine 无状态 + 依赖注入**：HandAnalyzer / Scoring / ActionValidator 全部注入，便于测试与替换。
- **GameState.apply_action 只改数据**：与 Controller（流程）/ RulesEngine（校验）严格分离。

---

## 9. 如何快速跑起来（现状）

```bash
pip install -r requirements.txt
python scripts/play.py
```
> 预期：能发牌、能渲染、人类/启发式 AI 交互。但由于 §6 的 bug（尤其是杠后、和牌结算、场风推进），完整一局多半会在中后期崩溃。**当前环境尚不能稳定跑完一整局。**

---

## 10. 一句话总结

> 骨架与分层设计是优秀的，数据结构层成熟；但 **规则层（手牌分解、计分、振听、符数）与 Controller 的若干 bug 尚未补齐**，环境无法稳定完整地跑完一局。Agent/训练完全为空。当前首要任务是把"环境跑通且规则正确"，再谈 RL。
