# 设计文档 06：状态编码设计 (OBSERVATION_ENCODING_DESIGN)

> 状态：**草案 v1** ｜ 优先级：**中** ｜ 关联代码：`src/env/state_encoder.py`、`mahjong_env.py`
> 本文定义 observation 字段清单、语义、维度、信息可见性规则，以及与 RL 算法的对接。
> 当前 StateEncoder 框架在但有多处 bug，本文是修正与扩展蓝图。

---

## 1. 设计原则

麻将是一个**不完全信息博弈**，状态编码的核心难点是**信息可见性**：
- 每个玩家只能看到自己的手牌，看不到他人的手牌。
- 弃牌河、副露、宝牌指示牌、分数是公开的。
- 里宝牌指示牌立直前不可见。

因此 observation 必须是 **per-player（按观察者视角）** 的，而非全局的。

**设计原则**：
1. **视角化**：`encode(game_state, player_index, ...)` 永远从 player_index 视角编码。
2. **对称特征**：把 4 个玩家按相对位置（自己/下家/对家/上家）排列，使 Agent 学到位置无关性。
3. **变长动作处理**：候选动作用 padding + mask 对齐到固定上限。

---

## 2. Observation 整体结构（Dict 空间）

```python
observation = {
    "state": {                          # 游戏状态特征（dict of arrays）
        "hand": ...,
        "melds": ...,
        "discards": ...,
        "dora": ...,
        "wind": ...,
        "game_progress": ...,
        "last_action": ...,
        "scores": ...,
        # —— 建议补充（§5）——
        "riichi_flags": ...,
        "drawn_tile_flag": ...,
        " opponents_melds": ...,
        "furiten_flags": ...,
    },
    "action_candidates": ...,           # (max_actions, action_feature_dim)
    "action_mask": ...,                 # (max_actions,)
}
```

---

## 3. 现有字段详解（保留并修正）

### 3.1 hand（自己的手牌）
- 维度：`(34,)` uint8，每 value 的张数（0-4）。
- **是否含 drawn_tile？** 需明确：当前 `_encode_tiles(player.hand)` 不含 drawn_tile。建议**单独编码 drawn_tile**（§5.2），因为摸切/手切的区分对策略重要。

### 3.2 melds（自己的副露）
- 维度：`(34,)` uint8，副露中每 value 张数。
- ⚠️ **bug**：`_encode_player_melds` 用 `meld["tiles"]`（dict 访问，state_encoder.py:129），但 `Meld` 是 dataclass，应 `meld.tiles`。**必须修（A3 关联）**。

### 3.3 discards（4人弃牌河）
- 维度：`(4, 34)` uint8，每玩家弃牌河中每 value 张数。
- ⚠️ 当前按绝对玩家索引（0-3）排列。建议改为**相对视角**（自己=0，下家=1，对家=2，上家=3），见 §4。

### 3.4 dora（宝牌指示牌）
- 维度：`(34,)` uint8，已公开的 dora_indicators 每 value 张数。
- 注：编码的是"指示牌"而非"实际宝牌"，Agent 需自己学会换算（或额外编码实际宝牌 value，建议加）。

### 3.5 wind
- 维度：`(2,)` uint8，[场风, 自风]。
- ⚠️ 当前计算自风逻辑（state_encoder.py:62-66）用了 `dealer_index` 的 seat_wind，可简化为直接 `player.seat_wind`。

### 3.6 game_progress
- 维度：`(4,)` uint16，[局数, 本场, 立直棒数, 剩余活动牌数]。
- 可直接用。

### 3.7 last_action
- 维度：`(35,)` uint8，[tile one-hot(34), player_idx]。
- ⚠️ **bug**：`game_state.last_action_info["tile"]` 不一定存在（DISCARD 才有 tile，PASS 无）。当前直接访问会 KeyError（state_encoder.py:139）。需用 `.get`。

### 3.8 scores
- 维度：`(4,)` int32，4 玩家分数。
- 建议改为相对视角（自己排第一），或归一化（÷初始分）。

---

## 4. 视角对称化（建议改进）

当前编码按绝对玩家索引。改为**相对视角**利于 RL 泛化：

```python
def _relative_idx(absolute_idx, observer_idx, num_players=4):
    return (absolute_idx - observer_idx) % num_players
# observer=自己(idx0), 下家(idx1)=1, 对家(idx2)=2, 上家(idx3)=3
```
- discards、scores、riichi_flags、opponents_melds 全部按相对视角重排。
- 自己永远在 slot 0，Agent 学到的策略天然位置无关。

---

## 5. 建议补充字段（当前缺失但 RL 需要）

### 5.1 riichi_flags（立直状态）
- 维度：`(4,)` uint8（相对视角），每玩家是否已立直。
- 还可加 `double_riichi` / `ippatsu_chance` 子标志。
- 对策略极重要（立直玩家强制摸切、一发、里宝牌）。

### 5.2 drawn_tile_flag（摸切指示）
- 标记手牌中哪张是刚摸的 drawn_tile（用于区分摸切/手切）。
- 编码方式：hand 仍按张数，额外加 `(34,)` one-hot 标记 drawn_tile 的 value。

### 5.3 opponents_melds（他人副露详情）
- 当前只编码自己 melds。他人副露（含来源方向）对推断其手牌重要。
- 维度：`(3, 34, *)` 或扁平化 `(3, 34)` 计数 + 来源标记。

### 5.4 furiten_flags（振听状态）
- `(4,)` uint8，每玩家是否振听（自己的振听可见，他人振听不可见 → 仅自己 slot）。
- 注意：他人振听是隐藏信息，不应编码（否则作弊）。只有自己的振听可编码。

### 5.5 actual_dora（实际宝牌 value）
- dora_indicators 是指示牌，额外编码换算后的实际宝牌 value，降低 Agent 学习难度。

### 5.6 隐藏信息边界（重要！）
- **不可编码**：他人手牌、牌墙内容、里宝牌指示牌（立直前）、他人是否听牌/振听。
- 编码前必须过"可见性审计"，防止信息泄露。

---

## 6. 候选动作编码（action_candidates）

### 6.1 当前方案
- `(max_actions=100, action_feature_dim)` float32。
- 每个 Action 调 `to_feature_vector(feature_size)`（actions.py:98），独热编码 type + tile + chi_tiles + kan_type。
- 不足 100 用零填充，`action_mask` 标记有效位。

### 6.2 问题与改进
- **action_feature_dim 不一致**：config 里写 128，但 `to_feature_vector` 实际需要 `len(ActionType)+34+2*34+len(KanType)` ≈ 9+34+68+3 = 114。需统一并校验。
- **动作语义信息丢失**：当前仅独热，丢了"该动作是否是摸切""是否立直后强制"等。可附加特征维度。
- **上限 100 够用？** 门清中盘 DISCARD 最多 13 种 + 杠/立直等，远小于 100。安全。

---

## 7. Action/Observation Space 定义（gymnasium）

⚠️ **bug**：StateEncoder 用 `from gym.spaces import ...`（state_encoder.py:2），但 Env 用 gymnasium。**必须统一为 gymnasium**（C1）。

修正后的 `get_observation_space`（含补充字段）：
```python
from gymnasium import spaces
spaces.Dict({
    "state": spaces.Dict({
        "hand":             spaces.Box(0,4,(34,),uint8),
        "drawn_tile":       spaces.Box(0,1,(34,),uint8),     # 新增
        "melds":            spaces.Box(0,4,(34,),uint8),
        "discards":         spaces.Box(0,4,(4,34),uint8),
        "opp_melds":        spaces.Box(0,4,(3,34),uint8),    # 新增
        "dora_indicators":  spaces.Box(0,4,(34,),uint8),
        "actual_dora":      spaces.Box(0,4,(34,),uint8),     # 新增
        "wind":             spaces.Box(0,3,(2,),uint8),
        "game_progress":    spaces.Box(0,100,(4,),uint16),
        "scores":           spaces.Box(-1e5,1e5,(4,),int32),
        "last_action":      spaces.Box(0,3,(35,),uint8),
        "riichi_flags":     spaces.Box(0,1,(4,),uint8),      # 新增
        "my_furiten":       spaces.Box(0,1,(1,),uint8),      # 新增
    }),
    "action_candidates": spaces.Box(0,1,(100, action_feature_dim),float32),
    "action_mask":       spaces.Box(0,1,(100,),int8),
})
```

---

## 8. 与 RL 算法的对接

### 8.1 候选动作变长问题的处理
- 标准 DQN/PPO 假定固定动作空间。本环境用 Discrete(100) + mask：
  - Agent 输出 100 维 Q/π，乘 action_mask 屏蔽非法动作。
  - 这是 "parametric/variable-action-space" 标准做法。
- 替代方案：把候选动作特征 concat 到 state，用 pointer/attention 网络选动作（更复杂，阶段3再考虑）。

### 8.2 多智能体调度
- 4 人麻将：一个 env，4 个 agent 轮转。
- Env 的 step 只推进"当前玩家"。响应阶段需依次询问 3 个玩家。
- 实现：外层 loop 维护 current_player，每个 agent 看自己视角的 observation，选 action，调 step。
- 训练时可用 self-play（4 个同策略 agent）或 vs 启发式。

---

## 9. 与现有代码对齐 & 修复清单

| 现状 | 设计要求 | 优先级 |
|------|----------|--------|
| `from gym.spaces` | 改 `from gymnasium import spaces`（C1） | **致命** |
| `meld["tiles"]` | 改 `meld.tiles`（A3） | **致命** |
| `last_action_info["tile"]` KeyError | 用 `.get` | 高 |
| 无视角对称化 | 按 §4 相对视角 | 中 |
| 缺 riichi/drawn/furiten 等字段 | 按 §5 补充 | 中 |
| action_feature_dim 不一致 | 统一并校验 | 高 |

---

## 10. 验收标准

1. ✅ §7 observation_space 用 gymnasium 定义，且与 encode 输出形状一致。
2. ✅ 无隐藏信息泄露（§5.6 审计）。
3. ✅ 候选动作编码与 Action.to_feature_vector 维度对齐。
4. ✅ seed 固定下，同一 (game_state, player_index) 编码结果确定。
5. ✅ 编码耗时 < 1ms（中盘场景）。

---

## 11. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，字段清单 + 视角对称 + 可见性规则 + gymnasium 修正 |
