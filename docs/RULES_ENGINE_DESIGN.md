# 设计文档 01：规则引擎设计 (RULES_ENGINE_DESIGN)

> 状态：**草案 v1** ｜ 优先级：**最高** ｜ 关联代码：`src/env/core/rules/`
> 配合 `PROJECT_OVERVIEW.md` §4.3、`ROADMAP_AND_DESIGN_PLAN.md` 第一/二部分阅读。
> 本文是接手者**必须第一个读完**的设计文档。

---

## 1. 设计目标

规则引擎是整个麻将环境的"裁判"，必须同时满足三个互相冲突的要求：

1. **正确性**：规则判定要与标准日麻（如天凤/雀魂规则集）一致。
2. **可测试性**：规则逻辑独立于流程控制，可用单元测试穷举覆盖。
3. **性能**：`generate_candidate_actions` 在每个决策点都会被调用，听牌/和牌判定尤其频繁，必须快。

为此，规则层采用 **"门面 + 职责分离 + 依赖注入"** 的架构，使每个子系统可独立开发、独立测试、独立替换。

---

## 2. 四模块职责矩阵

规则层由四个协作组件构成，职责严格互斥：

| 组件 | 角色比喻 | 是否有状态 | 输入 | 输出 | 核心职责 | **明确不做的事** |
|------|----------|------------|------|------|----------|------------------|
| **RulesEngine** | 裁判长 / 门面 | 无（持有子组件引用） | GameState, Action | Action 列表 / 阶段 / outcome dict | 协调子组件、高层流程控制（下一阶段/下一局/是否终局） | **不写任何具体规则判断**（如"能否碰"） |
| **ActionValidator** | 巡边员 | 无 | PlayerState, GameState | List[Action] / 优先级结果 | 候选动作生成 + 响应优先级解决 | **不分解手牌、不算分**，只调用 HandAnalyzer/Scoring |
| **HandAnalyzer** | 牌型鉴定师 | 无（只读） | List[Tile], List[Meld] | WinForm 列表 / 听牌集合 | 手牌结构分析：和牌形分解、听牌、可和牌枚举 | **不判定役种、不算符、不看点数** |
| **Scoring** | 计分员 | 无（只读） | PlayerState, GameState, WinForm | WinDetails / 点数支付 | 役种、番、符、点数、宝牌、振听 | **不分解手牌**，依赖 HandAnalyzer 的结果 |

> **铁律**：任何跨越职责边界的逻辑都是设计缺陷。例如 Scoring 想知道听牌，必须调 `HandAnalyzer.find_wait_tiles`，而不是自己再写一套分解。

---

## 3. 依赖与实例化顺序

组件之间存在单向依赖，必须按以下顺序实例化（依赖注入）：

```
HandAnalyzer(config)                         # 无依赖
      │
      ▼
Scoring(hand_analyzer, config)               # 依赖 HandAnalyzer
      │
      ▼
ActionValidator(hand_analyzer, scoring, config)  # 依赖前两者
      │
      ▼
RulesEngine(config)                           # 持有上述三者
      │ 内部组装
      ├─ self.hand_analyzer
      ├─ self.scoring
      └─ self.action_validator
```

**为什么 RulesEngine 负责组装？** 因为它是 Controller 唯一接触的规则层入口（门面模式），由它统一管理子组件生命周期，避免 Controller 直接耦合四个类。

> ⚠️ 当前代码现状：`RulesEngine.__init__` 已按此顺序组装（rules_engine.py:72-80），架构正确，只是子组件实现不全。

---

## 4. 公共数据契约（必须严格对齐的接口）

接口契约是规则层的"宪法"，任何改动都要同步更新本文档。下面用 Python 伪签名定义。

### 4.1 RulesEngine —— 对 Controller 的接口

```python
class RulesEngine:
    def __init__(self, config: Dict): ...

    # —— 候选动作生成（每步决策点调用）——
    def generate_candidate_actions(
        self, game_state: GameState, player_index: int
    ) -> List[Action]:
        """根据 game_phase 分发：
           - PLAYER_DISCARD        -> ActionValidator.get_legal_actions_on_draw
           - WAITING_FOR_RESPONSE  -> ActionValidator.get_legal_actions_on_response
           - 其他阶段              -> 返回 []
        """

    # —— 响应优先级解决（凑齐所有响应后调用）——
    def resolve_response_priorities(
        self, response_declarations: Dict[int, Action], game_state: GameState
    ) -> Tuple[Optional[Action], Optional[int]]:
        """委托 ActionValidator。返回 (获胜动作, 获胜玩家idx)；全员PASS则(None,None)。"""

    # —— 局终结算（进入 HAND_OVER_SCORES 时调用）——
    def process_hand_outcome(
        self, game_state: GameState, end_reason: str,
        action: Optional[Action] = None,
        player_index: Optional[int] = None,   # 赢家
        loser_index: Optional[int] = None,     # 放铳者（仅RON）
    ) -> Dict[str, Any]:
        """返回 outcome dict（见 §4.4）。委托 Scoring 计算分数。"""

    # —— 高层流程控制（RulesEngine 自身实现）——
    def determine_next_phase(
        self, game_state: GameState, executed_action: Action
    ) -> GamePhase: ...

    def determine_next_hand_state(
        self, game_state: GameState, hand_outcome: Dict[str, Any]
    ) -> Dict[str, Any]:
        """返回 next_hand_state dict（见 §4.5）。"""

    def is_game_over(self, game_state: GameState) -> bool: ...
```

### 4.2 ActionValidator —— 对 RulesEngine 的接口

```python
class ActionValidator:
    def get_legal_actions_on_draw(
        self, player: PlayerState, game_state: GameState
    ) -> List[Action]:
        """PLAYER_DISCARD 阶段：TSUMO / KAN(暗/加) / RIICHI / DISCARD / SPECIAL_DRAW。
           互斥规则见 §6.2。"""

    def get_legal_actions_on_response(
        self, player: PlayerState, game_state: GameState
    ) -> List[Action]:
        """WAITING_FOR_RESPONSE 阶段：RON / PON / KAN(明) / CHI / PASS。
           立直后只允许 RON/PASS（以及特定 KAN）。"""

    def resolve_response_priorities(
        self, declarations: Dict[int, Action],
        discarder_index: int, num_players: int
    ) -> Tuple[Optional[Action], Optional[int]]: ...
```

### 4.3 HandAnalyzer —— 对 ActionValidator/Scoring 的接口

```python
class HandAnalyzer:
    def find_all_winning_forms(
        self, hand_tiles: List[Tile], melds: List[Meld], winning_tile: Tile
    ) -> List[WinForm]:
        """返回所有有效和牌分解（标准型/七对子/国士）。
           必须返回 Tile 实例级分解，供役种/符数使用。"""

    def check_win_shape(
        self, hand_tiles: List[Tile], melds: List[Meld], winning_tile: Tile
    ) -> bool:
        """find_all_winning_forms 的布尔包装。"""

    def is_tenpai(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool: ...

    def find_wait_tiles(self, hand_tiles: List[Tile], melds: List[Meld]) -> Set[int]:
        """返回听牌的 value 集合（用于振听）。"""

    def calculate_shanten(  # 【建议新增】
        self, hand_tiles: List[Tile], melds: List[Meld]
    ) -> int:
        """向听数。用于评估/启发式Agent，可选。"""
```

### 4.4 process_hand_outcome 返回的 outcome dict 契约

```python
outcome: Dict[str, Any] = {
    "end_type": str,                 # "TSUMO"|"RON"|"EXHAUSTIVE_DRAW"|"SPECIAL_DRAW"|"INVALID_WIN"|"NAGASHI_MANGAN"
    "winner_index": Optional[int],    # 赢家（多家荣和时建议改为 List[int]，见 §8.2）
    "loser_index": Optional[int],     # 放铳者
    "score_details": Optional[WinDetails],  # 和牌详情（流局时为 None）
    "score_changes": Dict[int, int],  # {player_idx: 分数变化}，由 Scoring.get_final_score_and_payout 产出
}
```

> ⚠️ **当前致命缺陷**：`Scoring.get_final_score_and_payout` 实质未实现（直接 `return {}`），且找不到 winner（ROADMAP A5）。本契约要求该函数签名增加 `winner_index` 参数。

### 4.5 determine_next_hand_state 返回的 next_hand_state dict 契约

```python
next_hand_state: Dict[str, Any] = {
    "next_dealer_index": int,
    "next_round_wind": int,    # 0东 1南 2西
    "next_round_number": int,  # 1-4
    "next_honba": int,
    "next_riichi_sticks": int, # 和牌时清零转给赢家，流局时累积
    "game_over": bool,         # 终局标志
}
```

> ⚠️ **当前缺陷**：`determine_next_hand_state` 引用了不存在的 `game_state.initial_dealer_index`（ROADMAP A2），需在 GameState 增加 `initial_dealer_index` 字段。

---

## 5. RulesEngine 高层流程控制（自身实现的部分）

这三方法是 RulesEngine **唯一保留实质逻辑**的地方，因为它们是"流程"而非"规则"。

### 5.1 determine_next_phase（动作 → 下一阶段）

| 执行的动作 | 下一阶段 |
|------------|----------|
| TSUMO / RON / SPECIAL_DRAW | HAND_OVER_SCORES |
| CHI / PON | PLAYER_DISCARD（鸣牌者打牌） |
| KAN（任意） | ACTION_PROCESSING（Controller 接管岭上摸牌） |
| DISCARD | WAITING_FOR_RESPONSE |
| PASS | PLAYER_DRAW（Controller 接管，下家摸牌） |

> 注意：牌山是否摸空的检查属于 Controller（auto-flow），不在此处。

### 5.2 determine_next_hand_state（庄家轮换 / 场风推进 / 本场）

核心规则（标准半庄规则集）：

```
和牌(TSUMO/RON):
    庄家和  -> 连庄, honba+1
    闲家和  -> 换庄(dealer+1), honba=0
流局(EXHAUSTIVE/SPECIAL):
    庄家听牌-> 连庄, honba+1
    庄家未听-> 换庄, honba=0
罚符(INVALID_WIN/Chombo):
    不换庄, honba 不变

换庄后:
    若 next_dealer 绕回 initial_dealer_index -> 场风+1, round_number=1
    否则 round_number+1

立直棒:
    和牌时 -> 清零(归赢家)，点数在 process_hand_outcome 里加给赢家
    流局时 -> 累积保留
```

> ⚠️ 当前代码硬编码 `max_game_wind = 1`（半庄），且 `dealer_is_tenpai` 硬编码为 True（rules_engine.py:321）。需改为委托 Scoring/HandAnalyzer 判断，并从 config 读取。

### 5.3 is_game_over（终局判定）

终止条件（满足任一）：
1. **飞人 (tobi)**：任一玩家 `score < 0`（注意：规则集可选"被飞即终局"或"庄家被飞才终局"，需 config 化）。
2. **完成最后一场风**：`round_wind > max_game_wind`（max 从 config）。
3. **细规则**：南入后的西入、最后一局庄家和牌是否延长的"オーラス"规则 —— 需 config 化（详见 GAME_FLOW_STATEMACHINE.md）。

---

## 6. ActionValidator 候选动作生成规则

### 6.1 PLAYER_DISCARD 阶段（自摸回合，`get_legal_actions_on_draw`）

| 候选动作 | 触发条件（委托校验） |
|----------|----------------------|
| TSUMO | `player.drawn_tile` 存在 且 `Scoring.is_valid_win(..., is_tsumo=True)` 为真 |
| KAN(暗杠) | 手牌(含drawn)有4张同value 且（未立直 **或** 立直后该暗杠不改变听牌） |
| KAN(加杠) | 已有PON同value + 手牌1张 且（未立直 **或** 加杠后仍听同一组牌） |
| RIICHI | 门清 + 分数≥1000 + 剩余牌≥4 + 未立直 + 存在打出后听牌的牌 |
| DISCARD | 手牌中每张不同 (value,is_red) 组合一项（立直后只允许听牌那张） |
| SPECIAL_DRAW | 第1巡 + 门清 + 9种以上不同幺九字牌 |

**互斥规则（当前代码已实现，必须保留）**：
```
if 可自摸 or 可杠:
    不生成 RIICHI 和 DISCARD   # 自摸/杠优先，玩家若想打牌需先放弃自摸
else:
    生成 RIICHI(满足条件时) + DISCARD + SPECIAL_DRAW
```

> 设计理由：避免动作空间同时出现"自摸"和"打牌"导致 Agent 误选打牌放弃和牌。但这也意味着**当前模型下玩家无法"放弃自摸继续打"**——这是简化，标准日麻允许放弃自摸（但几乎无意义）。若要支持，需重新设计互斥逻辑。

### 6.2 WAITING_FOR_RESPONSE 阶段（响应回合，`get_legal_actions_on_response`）

| 候选动作 | 触发条件 |
|----------|----------|
| RON | `Scoring.is_valid_win(..., is_tsumo=False)` 为真 **且 非振听** |
| PON | 手牌≥2张同value（立直后不允许） |
| KAN(明杠) | 手牌≥3张同value（立直后不允许） |
| CHI | 仅下家 且 数牌 且 能组成顺子（立直后不允许） |
| PASS | 永远可用（兜底） |

**立直后限制**：立直玩家只能 RON / PASS（外加满足条件的加杠/暗杠，见 §6.1，但响应阶段只有 RON）。

### 6.3 响应优先级解决（`resolve_response_priorities`）

优先级（已在代码实现）：
```
RON (优先级3) > KAN/PON (优先级2) > CHI (优先级1) > PASS (优先级0)
```

同优先级冲突解决（**头跳规则**）：
- 多家 RON：从**打牌者的上家开始逆时针**找第一个声明 RON 的玩家。
- 实现：`for i in range(1, num_players): idx = (discarder - i) % num_players`
- ⚠️ 当前代码是"头跳"（只取一家），但标准规则允许**多家同时荣和**（头跳只在部分规则集）。需 config 化（见 §8.2）。

---

## 7. 与其它层的边界

```
Controller 调用顺序（一次 step）:
  1. game_state.apply_action(idx, action)      # 纯数据变更
  2. next_phase = rules_engine.determine_next_phase(gs, action)
  3. (若进入结算) rules_engine.process_hand_outcome(...)
  4. (Controller 自动流程: 摸牌/岭上/开新局)
  5. (下一决策点) rules_engine.generate_candidate_actions(gs, current_player)
```

**禁止的反模式**：
- ❌ Controller 直接调用 HandAnalyzer / Scoring（绕过门面）。
- ❌ ActionValidator 修改 GameState（它是只读校验器）。
- ❌ HandAnalyzer 读取 `game_state.wall.dora_indicators`（它只看手牌，宝牌是 Scoring 的职责）。
- ❌ Scoring 自己分解手牌（必须用 HandAnalyzer 的 WinForm）。

---

## 8. 未决问题清单（接手者必须决策）

### 8.1 [BLOCKER] Scoring 支付分配缺 winner_index
`get_final_score_and_payout(win_details, game_state, loser_index)` 找不到赢家。
**决策**：签名改为 `get_final_score_and_payout(win_details, game_state, winner_index, loser_index)`。outcome dict 已携带 winner_index，由 RulesEngine 透传。

### 8.2 [设计] 多家荣和 vs 头跳
当前实现头跳（单家）。建议：
- outcome 中 `winner_index` 改为 `Union[int, List[int]]`。
- config 增加 `"multiple_ron_allowed": True`。
- 支付按头跳顺序计算（同巡不同巡会影响符）。

### 8.3 [设计] 庄家听牌判断
`determine_next_hand_state` 硬编码 `dealer_is_tenpai = True`。
**决策**：流局结算时由 Scoring 一并计算所有玩家的 tenpai 状态并写入 outcome（`"tenpai_players": List[int]`），RulesEngine 据此判断连庄。

### 8.4 [BLOCKER] initial_dealer_index 缺失
GameState 无此字段，场风推进崩溃。
**决策**：GameState 增加 `self.initial_dealer_index = 0`（reset_game 时固定为0，标准日麻东1局庄家固定）。

### 8.5 [实现] 性能预算
`generate_candidate_actions` 在立直判定时要对每张可打牌做一次 `is_tenpai`，而 `is_tenpai` 内部对 34 种牌做 `check_win_shape`。复杂度较高。
**决策**：HandAnalyzer 必须用预计算的 shanten/分解表，禁止每次全量递归（详见 HAND_DECOMPOSITION_DESIGN.md）。

---

## 9. 验收标准（Definition of Done）

规则引擎视为完成，当且仅当：
1. ✅ 四组件职责严格按 §2 矩阵，无越界（代码审查 + 单元测试隔离）。
2. ✅ §4 所有接口签名与文档一致。
3. ✅ 一局能从发牌打到和牌/流局并正确结算分数，无 AttributeError。
4. ✅ 用 seed 固定牌山的回归测试通过（详见 TESTING_STRATEGY.md）。
5. ✅ 至少一份公开牌谱的黄金回放：每个 declared action 都在 `generate_candidate_actions` 输出中，且计分一致。
6. ✅ `generate_candidate_actions` 单次调用 < 5ms（4人门清中盘场景）。

---

## 10. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，确立四模块职责矩阵与接口契约 |
