# 设计文档 07：奖励信号设计 (REWARD_DESIGN)

> 状态：**已落地** ｜ 关联代码：`src/env/mahjong_env.py(_step_reward / _episode_rewards)`
> 当前默认配置：**placement 模式**（纯顺位，零和）。

---

## 0. 实际实现（已落地，2026-08）

reward 由两层组成，全部在 `MahjongEnv` 实现：

### 每步稠密 reward（`step()` 返回值）
```python
def _step_reward(self, state, player_idx):
    return -abs(self.step_penalty)   # 默认 step_penalty=0.0 → 每步 reward=0
```
默认配置下中间步 reward 全 0，只有终局有非零 reward。

### 终局 episode reward（`info["rewards"]`，半庄结束时算）
支持 3 种模式，由 `config.reward.mode` 决定。**当前所有 config 用 `placement`**：

| 模式 | 公式 | 说明 |
|------|------|------|
| `placement` (默认) | `placement_rewards[rank]`，rank 按点数降序 | 纯顺位，零和 |
| `score_delta` | `(score-初始)/10000 - mean` | 点数差，零和归一化 |
| `hybrid` | `placement_r + score_alpha * (score_r - mean)` | 顺位+点数加权 |

**placement 默认值**：`[1.0, 0.3, -0.3, -1.0]`（1名/2名/3名/4名），零和。

### reward 如何分配到 transition（`ParallelCollector.accrue`）
奖励不每步立即给，而是累积到该玩家下次决策时才入 buffer：
- 每步 `accrue[player] += step_reward`（默认 0）
- 终局时 `final_r = accrue[p] + episode_rewards[p]`，回填到每个玩家的最后一个 pending transition
- 实际效果：默认配置下 transition reward 几乎全 0，只有半庄结束时的最后一个动作带非零 episode reward

---

## 1. 设计目标

麻将的奖励设计核心矛盾：
- **真实目标**：一局/一场结束时的顺位与点数（稀疏，几十步才有一次）。
- **训练效率**：纯稀疏奖励学习极慢，需要稠密 shaping 辅助但又不引入偏差。

设计原则：
1. **以点数为锚**：最终 reward 必须回归到真实点数（避免 shaping 主导）。
2. **多智能体**：每个 agent 只收自己的视角 reward。
3. **可配置**：稠密 shaping 作为可开关的辅助，便于消融实验。

---

## 2. 当前实现（临时，需重写）

`_calculate_reward`（mahjong_env.py:125）：
```python
if state._hand_over_flag:
    return (new_score - old_score) / 1000.0   # 局结算点数差
return -0.01                                   # 步惩罚
```
**问题**：
- reward 只针对 current_player，多智能体下其他玩家无 reward。
- 步惩罚 -0.01 会鼓励"快和"（巡目少），可能扭曲策略。
- 没有区分"自摸/荣和/流局/被飞"。

---

## 3. 推荐奖励结构（多层）

### 3.1 终局奖励（Terminal Reward，主导）
一场游戏结束时，按**顺位**给 reward（而非裸点数）：
```python
# 顺位 reward（标准 4 人麻将）
placement_reward = {
    1: +1.0,   # 头名
    2: +0.3,
    3: -0.3,
    4: -1.0,   # 末位
}
# 或结合点数差：reward = placement_reward + alpha * (score - start_score) / start_score
```
**理由**：顺位是真实目标，点数差作为微调（鼓励大和）。

### 3.2 局结算奖励（Hand-level Reward，稠密辅助）
每局结束时给中间信号（非终止）：
```python
hand_reward = 点数变化 / 1000   # 自摸+/放铳-
# + 可选 shaping（见 3.3）
```
**注意**：局结算 reward 要折现（gamma < 1），避免 Agent 只顾单局。

### 3.3 步内 shaping（可选，谨慎）
为加速学习引入，但必须**势函数性质（potential-based）**以免改变最优策略：
```python
shaping = gamma * Phi(s') - Phi(s)
# Phi: 手牌"质量"估值，如向听数改善、听牌、立直
```
可选 shaping 项（开关化）：
- 立直成立：+0.05
- 鸣牌降低向听：微小 +
- 放铳危险牌（他人可能荣和）：惩罚（需读牌模型，复杂）
- 被振听：-0.02

> ⚠️ shaping 易引入偏差，建议**默认关闭**，仅在 baseline 训练慢时开启消融。

---

## 4. 多智能体 reward 分配

Env 一个 step 推进当前玩家，但 reward 要为**所有 4 个 agent** 计算：
```python
# step 返回或 info 中携带 per-player reward
rewards = {
    p: self._calculate_reward_for_player(p, old_scores, new_scores, event)
    for p in range(4)
}
```
- Env 的标准 5 元组返回的 reward 仍是"当前玩家"的（兼容单 agent 模式）。
- 训练 Trainer 从 info["rewards"] 取其他玩家 reward。

### 4.1 自摸/荣和的支付方向
- 自摸：赢家 +X，其余 3 人按 -X 分摊。
- 荣和：赢家 +X，放铳者 -X。
- 这些由 Scoring 算好后，Trainer 据此分 per-player reward。

---

## 5. 点数归一化

- 原始点数 25000 起步，和牌点数 1000-32000。
- 归一化：除以 `initial_score`（25000）或固定常数（如 10000）。
- 顺位 reward 不需归一化。

---

## 6. Reward 配置 schema

```yaml
reward:
  mode: "placement"        # "placement"|"score_delta"|"hybrid"
  placement_rewards: [1.0, 0.3, -0.3, -1.0]
  score_alpha: 0.5         # 点数项权重
  hand_reward_scale: 0.001
  step_penalty: 0.0        # 默认关闭步惩罚
  shaping_enabled: false
  shaping:
    riichi: 0.05
    furiten: -0.02
  gamma: 0.99
```

---

## 7. 与现有代码对齐 & 修复清单

| 现状 | 设计要求 | 优先级 |
|------|----------|--------|
| reward 只算 current_player | per-player reward 写入 info | 阶段3 |
| 步惩罚硬编码 -0.01 | config 化，默认关 | 阶段3 |
| 无顺位 reward | 场结束时按顺位 | 阶段3 |

> 阶段3 前不实现，先把环境跑通。

---

## 8. 验收标准（阶段3）
1. ✅ 一场游戏结束每个 agent 收到正确顺位/点数 reward。
2. ✅ reward 配置可切换 mode。
3. ✅ shaping 开关可消融，开启后不显著降低最终顺位胜率。

---

## 9. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，顺位主导 + 局稠密 + 可选 shaping |
