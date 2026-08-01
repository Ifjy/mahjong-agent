# 项目缺口分析与下一步规划 (ROADMAP & DESIGN PLAN)

> 配合 `PROJECT_OVERVIEW.md` 阅读。本文档回答两个问题：**①现在缺什么？②下一步应该产出哪些设计文档、按什么顺序做？**
> 最后更新：2026-08-01

---

## 第一部分：现在缺什么（缺口清单）

### A. 致命缺口（不补就无法跑通一局）

| # | 缺口 | 位置 | 影响 |
|---|------|------|------|
| A1 | `draw_rinshan_tile` 方法名错误 | GameController.py:275 | 杠后崩 |
| A2 | `initial_dealer_index` 字段缺失 | rules_engine.py:341 | 庄家轮换/场风推进崩 |
| A3 | Meld 被 dict 访问（应为属性） | game_state.py, action_validator.py | 鸣牌/加杠崩 |
| A4 | `Action.get_tiles_from_hand()` 不存在 | game_state.py | 副露移牌崩 |
| A5 | `Scoring.get_final_score_and_payout()` 未实现 | scoring.py | 和牌无法结算分数 |
| A6 | 配置不一致（config key 对不上） | default_config.yaml vs 代码 | Env 构造报错 |

### B. 规则正确性缺口（环境能跑但规则不全/不准）

| # | 缺口 | 说明 |
|---|------|------|
| B1 | **手牌面子递归分解未实现** | `_find_melds_recursive_by_tile` 是 TODO，回退到 value-based，丢失"具体 Tile 实例" → 无法判断一杯口/三色/一气/符数细节。这是规则层最大技术债。 |
| B2 | 役种严重不全 | 仅实现：立直、自摸、断幺、役牌(部分)。缺：平和、一杯口、三色同顺/同刻、一气通贯、混全/纯全、混一色/清一色、二杯口、对对和、七对子(形有但役未挂)、国士(形有役未挂)、岭上/海底/河底/一发、天地和、所有役满等。 |
| B3 | 符数计算是 stub | `_calculate_fu` 只算底符+门清荣和+自摸+刻/杠，**缺雀头符、边张/嵌张/单钓听牌符、进位细节不全**。 |
| B4 | 振听未实现 | `_is_furiten` 返回 False。舍牌振听/同巡振听/立直振听全部缺失 → 错误允许荣和。 |
| B5 | 立直后杠的限制未实现 | 立直后暗杠/加杠不得改变听牌，目前无检查。 |
| B6 | 食替 (kuikae) 规则缺失 | 吃/碰后不能打出刚组成副露的牌，目前无校验。 |
| B7 | 多家荣和 / 头跳 / 流局满贯 / 途中流局(四杠/四风/四立直/三家和) | 未实现。 |
| B8 | 飞人 (tobi) 终局判断不全 | `is_game_over` 有 score<0 检查，但散家飞人/庄家飞人的精细规则、西入等未做。 |

### C. 工程/接口缺口

| # | 缺口 | 说明 |
|---|------|------|
| C1 | gym / gymnasium 混用 | StateEncoder 用 `gym.spaces`，Env 用 `gymnasium`。统一为 gymnasium。 |
| C2 | 测试失效 | test_env.py 用旧 4 元组 step 接口、无参构造 Env。需要重写。 |
| C3 | 缺少确定性 (seed) 支持 | reset 不处理 seed，无法复现/回归测试。 |
| C4 | 缺少日志/调试 | 大量 `print`，无 logger。utils/logger.py 为空。 |
| C5 | 缺少"黄金回放"测试基准 | 没有用真实牌谱验证规则正确性的机制。 |

### D. RL / Agent 缺口（环境稳定后再做）

| # | 缺口 |
|---|------|
| D1 | Agent 全空 (base/dqn/models) |
| D2 | Trainer 全空 |
| D3 | Reward 设计未定（当前临时点数差/步惩罚） |
| D4 | StateEncoder 字段是否充分（缺立直棒归属、振听可见性、各玩家副露来源方向等） |
| D5 | 多智能体协调（4 个 agent 共享一个 env 的轮转调度） |

---

## 第二部分：下一步规划（建议的执行顺序）

总体路线：**先让环境跑通且规则正确 → 再补测试与回归 → 最后做 RL**。

### 阶段 0：止血（1~2 天）—— 修致命 Bug，让一局能跑完

目标：`python scripts/play.py` 能完整跑完一局（哪怕计分简陋）。
- 修 A1~A6。
- 统一 Meld 访问方式（全局 grep `meld["` → `meld.`）。
- 修 StateEncoder 的 gym→gymnasium。
- 修 config 一致性。

### 阶段 1：规则核心补全（最大头，1~3 周）

这是项目真正的难点。建议拆成下面的设计文档逐个攻克（见第三部分）。

1. **手牌分解算法** (B1) —— 规则层的地基。建议采用成熟方案：
   - 预计算 1~9 数牌的"面子分解表"（每种牌型枚举所有 shuntsu/koutsu 组合），或
   - 用经典的"字典/位运算 shanten 算法"（开源 mahjong 库如 tenhou/akochan 的公开算法可参考其思路，不要直接拷贝版权代码）。
   - 必须返回 **Tile 实例级别**的分解，才能支持一杯口/三色/符数。
2. **符数计算** (B3)。
3. **役种引擎** (B2) —— 基于分解结果逐役判定。
4. **振听** (B4) + **立直后杠限制** (B5) + **食替** (B6)。
5. **特殊终局** (B7) + **飞人/西入** (B8)。

### 阶段 2：测试与正确性保证（贯穿，重点 1 周）

- 重写 `tests/`（C2）：单元测试覆盖 Wall / apply_action / 各 ActionValidator 分支 / Scoring。
- 引入 **seed 化的回归测试**（C3）：固定牌山，断言每一步候选动作与计分。
- （可选但强烈推荐）**黄金回放**：找一份公开牌谱，逐 step 重放，比对其每个 declared action 是否在我们的 `generate_candidate_actions` 中、计分是否一致。这是验证规则正确性的金标准 (C5)。

### 阶段 3：RL Agent（环境稳定之后）

- 先定义 Reward 设计文档 (D3)。
- 实现一个随机/启发式 baseline agent 作对照。
- 实现 DQN/PPO agent (D1) + Trainer (D2)。
- 多智能体调度（D4/D5）。

---

## 第三部分：建议产出的设计文档清单

建议在 `docs/` 下按以下清单逐个产出，每个文档聚焦一个子系统，**含数据结构、算法选择、接口签名、边界 case**。

| 文档 | 优先级 | 内容要点 |
|------|--------|----------|
| `docs/PROJECT_OVERVIEW.md` | ✅已完成 | 项目现状总览（本文档的姊妹篇） |
| `docs/ARCHITECTURE.md` | 高 | 三层架构 + 模块职责 + 调用关系图 + GamePhase 状态转换图（细化 §4） |
| `docs/RULES_ENGINE_DESIGN.md` | **最高** | RulesEngine/ActionValidator/HandAnalyzer/Scoring 四件套的职责边界、依赖关系、接口契约。**这是当前最需要的设计文档。** |
| `docs/HAND_DECOMPOSITION_DESIGN.md` | **最高** | 手牌面子分解算法选型（shanten 表 vs 递归 vs 位运算）、Tile 实例级分解的数据结构、性能考量、与役种/符数的对接。**最大技术债，必须先设计再实现。** |
| `docs/YAKU_AND_SCORING_DESIGN.md` | 高 | 役种判定矩阵（全役清单 + 判定条件 + 番数）、符数计算规则、点数表、自摸/荣和分配公式、役满/累积役满。 |
| `docs/GAME_FLOW_STATEMACHINE.md` | 高 | GamePhase 完整状态转换图、Controller auto-flow 规则、响应收集与优先级解决、多家长和/头跳、各种流局触发条件。 |
| `docs/ACTION_VALIDATION_DESIGN.md` | 中 | 每个阶段的候选动作生成规则、互斥逻辑（自摸/杠/立直/打牌）、立直后限制、食替、振听对候选动作的影响。 |
| `docs/OBSERVATION_ENCODING_DESIGN.md` | 中 | 状态编码字段清单（私有/公共/全局）、各字段语义与维度、可见性规则（信息不对称）、与 RL 算法的对接。 |
| `docs/REWARD_DESIGN.md` | 中（阶段3） | 奖励信号设计：稠密 vs 稀疏、点数归一化、立直/鸣牌 shaping、多智能体信用分配。 |
| `docs/TESTING_STRATEGY.md` | 中 | 单元测试范围、seed 回归、黄金牌谱回放方案、规则正确性验收标准。 |
| `docs/CONFIG_AND_RULESET.md` | 低 | 可配置规则集（赤宝牌数量、食断、东风/半庄、一番缚、祝仪等）、config schema 与 default 对齐。 |

---

## 第四部分：推荐的"第一个设计文档"重点（RULES_ENGINE_DESIGN 雏形）

接手者最该先写的，是 `docs/RULES_ENGINE_DESIGN.md`，建议至少覆盖：

1. **四模块职责矩阵**（已初步见 `TODO.txt`，需正式化）：
   - RulesEngine：协调 + 流程控制（next_phase / next_hand_state / is_game_over）
   - ActionValidator：候选动作生成 + 优先级解决
   - HandAnalyzer：手牌分解 / 听牌 / 和牌形
   - Scoring：役 / 番 / 符 / 点 / 振听 / 宝牌
2. **数据流契约**：`generate_candidate_actions` 的输入输出、`process_hand_outcome` 返回的 `outcome` dict schema、`WinDetails` 字段。
3. **依赖与实例化顺序**：HandAnalyzer → Scoring → ActionValidator → RulesEngine。
4. **未决问题清单**：B1~B8 每一项的负责人/方案/验收 case。

---

## 第五部分：一句话路线图

> **止血 1 周 → 手牌分解 + 计分役种 2~3 周 → 用 seed/牌谱回放验证正确性 1 周 → 才进入 RL agent 开发。** 环境的正确性是这个项目的命门，宁可花 80% 时间在环境，也不要在规则有 bug 时就开始训练 agent。
