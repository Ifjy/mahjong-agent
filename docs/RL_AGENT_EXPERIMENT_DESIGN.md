# 设计文档 11：RL Agent 实验框架设计 (RL_AGENT_EXPERIMENT_DESIGN)

> 状态：**草案 v1** ｜ 优先级：**高（RL 阶段总纲）** ｜ 关联：`src/agent/`、`src/trainning/`、`scripts/`
> 本文定义从环境到训练、断点恢复、评估、多算法接入、真实玩家数据导入的完整实验框架。
> 目标：支撑**长期、可复现、可扩展**的麻将 RL 研究。

---

## 0. 前置条件（训练前必须就绪）

本框架假设以下"环境完备性"项已满足（当前状态见括号）：

| 项 | 状态 | 说明 |
|----|------|------|
| Env gymnasium API（reset/step 5元组/action_mask） | ✅ | 已实现 |
| seed 复现 | ✅ | reset(seed) 注入 Wall.rng |
| 飞人/终局 terminated | ✅ | BLOCKER 已修 |
| 手牌守恒（无张数膨胀） | ✅ | tot≤15（含摸牌瞬态） |
| 规则层（役种/计分/振听/食替） | ✅ | ~22役+11役满+三种振听 |
| **Reward 设计** | ❌ 需补 | 当前临时点数差，需实现 §3 |
| **config_loader** | ❌ 需补 | 当前空，需实现 §4 |
| **observation 补全** | 🟡 建议 | 缺 opp_melds/riichi_flags/drawn_tile 等，见 §5 |
| **黄金牌谱验证** | ❌ 强烈建议 | 训练前至少做 seed 回归确认规则正确 |

**建议路径**：先补 Reward + config_loader（§3/§4，1-2 天），跑通随机 agent baseline 确认环境闭环，再开始正式训练。

---

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────────┐
│                    实验管理层 (Experiment)                       │
│  ExperimentConfig (yaml/hydra) → 实验目录 runs/<exp_name>/      │
│  - 训练日志 / 指标 / checkpoint / 配置快照                      │
└──────────────────────────┬─────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌────────────────┐  ┌──────────────┐
│  Trainer     │  │  Agent Registry│  │  Evaluator   │
│  (训练循环)   │  │  (算法注册)     │  │  (评估/测试)  │
│  - rollout   │  │  DQN/PPO/自定义│  │  - vs 启发式  │
│  - update    │  │  - 统一接口     │  │  - self-play  │
│  - checkpoint│  │  BaseAgent     │  │  - 棋谱生成   │
└──────┬───────┘  └────────┬───────┘  └──────────────┘
       │                   │
       ▼                   ▼
┌──────────────────────────────────────────┐
│        MahjongEnv (4人环境)              │
│  4 个 Agent 共享一个 Env, 轮转决策        │
│  per-player observation + reward          │
└──────────────────────────────────────────┘
```

### 核心设计原则
1. **算法可插拔**：所有 agent 实现统一 `BaseAgent` 接口，Trainer 不感知具体算法。
2. **实验可复现**：每次实验保存完整 config + seed + 代码 commit hash。
3. **断点可恢复**：checkpoint 含模型权重 + optimizer 状态 + 训练步数 + replay buffer。
4. **数据可导入**：真实玩家牌谱可转为 experience 喂给 agent（模仿学习/离线 RL）。

---

## 2. Agent 接口设计 (BaseAgent)

所有算法实现此接口，Trainer 通过它统一调用。

```python
# src/agent/base_agent.py
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import numpy as np

class BaseAgent(ABC):
    """所有 RL agent 的统一接口。"""

    def __init__(self, config: Dict, agent_id: int):
        self.config = config
        self.agent_id = agent_id  # 玩家座位 (0-3)

    @abstractmethod
    def select_action(
        self, observation: Dict, action_mask: np.ndarray, valid_actions: list,
        deterministic: bool = False
    ) -> int:
        """根据观察和动作掩码选择动作索引。
        Args:
            observation: StateEncoder 编码的 dict
            action_mask: (max_candidates,) 有效动作掩码
            valid_actions: List[Action] 实际候选动作对象
            deterministic: True=评估模式(贪婪), False=训练模式(探索)
        Returns:
            action_idx: 选择的动作在 valid_actions 中的索引
        """

    @abstractmethod
    def store_transition(
        self, observation, action_idx, reward, next_observation, done, info
    ):
        """存储一条转移 (用于在线 RL 的 experience buffer)。"""

    @abstractmethod
    def update(self) -> Dict[str, float]:
        """从 buffer 采样训练, 返回 loss 等指标 dict。"""

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """返回可序列化的完整状态 (权重+optimizer+步数+buffer), 用于 checkpoint。"""

    @abstractmethod
    def load_state(self, state: Dict[str, Any]):
        """从 checkpoint 恢复状态。"""

    # —— 可选：离线 RL / 模仿学习 ——
    def store_offline_experience(self, trajectory: Dict):
        """注入真实玩家牌谱的 experience (见 §7)。默认空实现。"""
        pass
```

### Agent Registry（算法注册）

```python
# src/agent/registry.py
AGENT_REGISTRY: Dict[str, type] = {}

def register_agent(name: str):
    """装饰器: 注册算法。@register_agent("dqn")"""
    def decorator(cls):
        AGENT_REGISTRY[name] = cls
        return cls
    return decorator

def build_agent(algo_name: str, config: Dict, agent_id: int) -> BaseAgent:
    """工厂: 按名称构造 agent。"""
    if algo_name not in AGENT_REGISTRY:
        raise ValueError(f"未知算法 {algo_name}, 已注册: {list(AGENT_REGISTRY)}")
    return AGENT_REGISTRY[algo_name](config, agent_id)
```

内置算法（均注册到 registry）：
- `random`：随机基线（从 action_mask 随机选），用于验证环境闭环。
- `heuristic`：启发式基线（优先和牌/立直，否则摸切），用于评估对照。
- `dqn`：值函数法，处理变长动作（action embedding + mask）。
- `ppo`：策略梯度法，适合 self-play。
- `custom`：用户自定义，实现 BaseAgent + `@register_agent("xxx")` 即可。

---

## 3. 多智能体训练循环 (Trainer)

### 3.1 核心：4 agent 共享 1 env 轮转

```python
# src/trainning/trainer.py (核心逻辑)
class Trainer:
    def __init__(self, config):
        self.config = config
        self.env = MahjongEnv(config["env"])
        # 4 个 agent (可同算法 self-play, 也可混合)
        self.agents = [
            build_agent(config["algo"], config, agent_id=i)
            for i in range(4)
        ]

    def train(self, total_episodes: int, resume_from: Optional[str] = None):
        start_ep = self._maybe_resume(resume_from)
        for ep in range(start_ep, total_episodes):
            trajectory = self._rollout_one_episode(ep)
            rewards = self._distribute_rewards(trajectory)
            for agent in self.agents:
                agent.update()
            self._log_metrics(ep, rewards)
            if ep % self.config["checkpoint_freq"] == 0:
                self._save_checkpoint(ep)
```

### 3.2 单局 rollout（多智能体调度）

```python
def _rollout_one_episode(self, ep):
    obs, info = self.env.reset(seed=self._seed_for(ep))
    # 每个决策点: 当前 agent 选动作, Env 推进
    while True:
        acting_player = info["current_player"]
        agent = self.agents[acting_player]
        action_idx = agent.select_action(
            obs, info["action_mask"], info["valid_actions"],
            deterministic=False
        )
        # 记录 transition (per-agent)
        agent.store_transition(obs, action_idx, reward=None, ...)
        next_obs, reward, terminated, truncated, info = self.env.step(action_idx)
        if terminated or truncated:
            break
        obs = next_obs
    return info  # 含本局结算
```

> **关键**：响应阶段（WAITING_FOR_RESPONSE）需要 3 个玩家依次决策。Env 的 `_next_responder` 已实现轮转调度，Trainer 只需按 `info["current_player"]` 调对应 agent。

### 3.3 奖励分配（per-player）

```python
def _distribute_rewards(self, info):
    """一局结束后, 为 4 个 agent 分配 reward。"""
    rewards = {}
    mode = self.config["reward"]["mode"]
    if mode == "placement":
        # 按顺位: [1.0, 0.3, -0.3, -1.0] + 点数微调
        scores = [p.score for p in self.env.controller.gamestate.players]
        ranking = sorted(range(4), key=lambda i: -scores[i])
        placement_r = self.config["reward"]["placement_rewards"]
        for rank, pid in enumerate(ranking):
            rewards[pid] = placement_r[rank]
    elif mode == "score_delta":
        for i in range(4):
            rewards[i] = (scores[i] - initial) / 1000.0
    # ... hybrid 等
    return rewards
```

> 每个 agent 的 `store_transition` 在 rollout 时 reward=None，局结束后用 `_distribute_rewards` 回填（麻将的稀疏奖励特性）。

---

## 4. Reward 设计（落地实现）

详见 `docs/REWARD_DESIGN.md`，此处定义实现规范。

```yaml
# config 中的 reward 段
reward:
  mode: "hybrid"              # placement | score_delta | hybrid
  placement_rewards: [1.0, 0.3, -0.3, -1.0]
  score_alpha: 0.5            # 点数项权重 (hybrid)
  score_normalize: 10000      # 点数归一化常数
  hand_reward_scale: 0.0      # 局间稠密 reward (默认关)
  step_penalty: 0.0           # 步惩罚 (默认关)
  shaping:
    enabled: false            # 势函数 shaping (默认关, 消融用)
    riichi: 0.05
    furiten: -0.02
  gamma: 0.99
```

**实现要点**：
- `MahjongEnv.step` 返回的 `reward` 仍是"当前玩家"的（兼容单 agent），但 `info["rewards"]` 必须携带 per-player dict。
- 终局 reward（顺位/点数）在 `terminated=True` 那步返回；局间 reward（shaping）每步返回。

---

## 5. Observation 补全（RL 关键字段）

当前 StateEncoder 缺 5 个 RL 关键字段（审计指出）。补全计划：

```python
# 补充到 _encode_game_state, 全部按相对视角 (自己=slot0)
state_features = {
    # —— 现有 8 字段 ——
    "hand": ..., "melds": ..., "discards": ..., "dora": ...,
    "wind": ..., "game_progress": ..., "last_action": ..., "scores": ...,
    # —— 新增 5 字段 (见 OBSERVATION_ENCODING_DESIGN §5) ——
    "drawn_tile": (34,) one-hot,       # 刚摸的牌 (区分摸切/手切)
    "opp_melds": (3, 34),               # 3 个对手的副露 (推断手牌)
    "actual_dora": (34,),               # 实际宝牌 value (降低学习难度)
    "riichi_flags": (4,),               # 各玩家立直状态 (相对视角)
    "my_furiten": (1,),                 # 自己的振听 (仅自己可见)
}
```

**视角对称化**：discards/scores/riichi_flags 按 `(idx - observer) % 4` 重排，自己永远 slot 0。这让 agent 学到位置无关策略。

**动作编码**：`action_candidates` (max_actions, action_feature_dim) + `action_mask`，agent 输出对每个候选打分。

---

## 6. 断点恢复 (Checkpoint & Resume)

### 6.1 checkpoint 内容
每次 checkpoint 保存到 `runs/<exp_name>/ckpt/`：
```
runs/<exp_name>/
├── config.yaml            # 实验配置快照 (复现用)
├── commit_hash.txt        # 代码版本
├── ckpt/
│   ├── ep_10000.pt        # checkpoint (含下面所有)
│   ├── ep_20000.pt
│   └── latest.pt          # -> 最新
├── metrics.csv            # 训练指标 (episode, reward, loss, ...)
├── tensorboard/           # TB 日志
└── eval/                  # 评估结果
```

`ep_NNNN.pt` 内容：
```python
{
    "episode": N,
    "global_step": ...,
    "env_seed_offset": ...,     # 恢复 seed 序列
    "agents": [
        {"weights": ..., "optimizer": ..., "steps": ...,
         "buffer": ... (可选, 大文件单独存)},
        ...  # 4 个 agent
    ],
    "rng_state": ...,           # python/numpy/torch RNG 状态 (精确复现)
}
```

### 6.2 resume 实现
```python
def _maybe_resume(self, resume_from):
    if resume_from is None:
        return 0
    ckpt = torch.load(resume_from)
    for i, agent in enumerate(self.agents):
        agent.load_state(ckpt["agents"][i])
    self._env_seed_offset = ckpt["env_seed_offset"]
    self._restore_rng(ckpt["rng_state"])
    return ckpt["episode"]

# CLI: python scripts/train.py --resume runs/exp1/ckpt/latest.pt
```

> **buffer 恢复**：replay buffer 可能很大（GB 级），可选不存（`save_buffer: false`），恢复后重新积累。或压缩单独存 `.buffer.pt`。

---

## 7. 真实玩家数据导入 (Experience Injection)

### 7.1 目标
将真实玩家（天凤/雀魂）的牌谱转为 experience，用于：
- **模仿学习 (BC)**：监督学习模仿高手策略（快速初始化）。
- **离线 RL (CQL/IQL)**：从固定数据集学策略，无需环境交互。
- **经验回放增强**：将真实数据混入 online RL 的 buffer（warm start）。

### 7.2 牌谱格式与加载

```python
# src/data/kifu_loader.py
@dataclass
class KifuStep:
    """牌谱单步记录。"""
    player_idx: int
    action: Action               # 该玩家实际选择的动作
    observation: Dict            # 该时刻的 per-player observation (需重建)
    valid_actions: List[Action]  # 该时刻合法动作 (需用我们的规则引擎重建)
    reward: Optional[float]      # 终局回填

@dataclass
class KifuGame:
    """一局牌谱。"""
    wall_seed: int               # 牌山 seed (复现发牌)
    steps: List[KifuStep]
    final_scores: List[int]
    final_ranking: List[int]

class KifuLoader:
    def load(self, path: str) -> List[KifuGame]:
        """加载牌谱文件 (支持 tenhou JSON / 自定义格式)。"""

    def to_experience(self, games: List[KifuGame]) -> List[Dict]:
        """转为 agent 可用的 experience: {obs, action, reward, ...}。"""
```

### 7.3 关键：observation 重建
真实牌谱没有我们的 observation 编码，必须**用我们的环境重放**重建：
```python
def replay_kifu(self, kifu: KifuGame) -> List[Dict]:
    """用我们的 Env 重放牌谱, 在每步记录 observation。"""
    env = MahjongEnv(config)
    obs, info = env.reset(seed=kifu.wall_seed)
    experiences = []
    for step in kifu.steps:
        # 验证: 牌谱的动作必须在我们的合法动作中 (规则正确性校验!)
        if step.action not in info["valid_actions"]:
            self._log_mismatch(step)  # 规则不一致, 用于发现 bug
            continue
        action_idx = info["valid_actions"].index(step.action)
        experiences.append({
            "observation": obs, "action_idx": action_idx,
            "valid_actions": info["valid_actions"], ...
        })
        obs, reward, terminated, truncated, info = env.step(action_idx)
    return experiences
```

> **这同时实现了 TESTING_STRATEGY 要求的"黄金牌谱回放"**——一举两得：既验证规则正确性，又产出训练数据。

### 7.4 数据接入训练
```python
# 模仿学习预训练
def pretrain_with_kifu(agent, kifu_experiences, epochs=10):
    for exp in kifu_experiences:
        agent.store_offline_experience(exp)  # 注入
    for _ in range(epochs):
        agent.supervised_update()            # BC loss

# 然后切换到 online RL
trainer.train(total_episodes=100000, resume_from=pretrained_ckpt)
```

### 7.5 数据格式约定（自定义牌谱）
```json
{
  "wall_seed": 42,
  "actions": [
    {"player": 0, "type": "DISCARD", "tile_value": 5},
    {"player": 1, "type": "PASS"},
    {"player": 0, "type": "TSUMO"}
  ],
  "final_scores": [32000, 25000, 22000, 21000]
}
```
> 真实牌谱（天凤 .mjlog 等）需写转换器转为上述格式（注意版权：用其规则作对照，不拷贝数据）。

---

## 8. 评估与测试 (Evaluator)

### 8.1 三种评估模式

```python
# src/trainning/evaluator.py
class Evaluator:
    def evaluate(self, agent_ckpt, mode="vs_heuristic", n_games=100):
        if mode == "vs_heuristic":
            # 1个待评估 agent + 3个启发式 agent
            return self._vs_baseline(agent_ckpt, n_games)
        elif mode == "self_play":
            # 4个同版本 agent 对打
            return self._self_play(agent_ckpt, n_games)
        elif mode == "league":
            # 多版本 agent 循环赛 (联赛评估, 防过拟合)
            return self._league_play(agent_ckpt, n_games)
```

### 8.2 指标
- **平均顺位**（越接近1越好）、**top-1 率**（头名家率）。
- **平均点数**、**和牌率**、**放铳率**、**立直率**。
- **vs 启发式胜率**（标准化为 Elo）。

### 8.3 棋谱生成（用于后续训练/调试）
评估时可导出 agent 的对局棋谱（自定义格式 §7.5），既可人看，也可回喂训练。

```python
evaluator.export_kifu(agent_ckpt, "runs/exp1/eval/kifu_ep10000.json", n_games=50)
```

---

## 9. 配置系统 (Experiment Config)

采用 **YAML + 层级覆盖**（不引入 hydra 重依赖，保持简单）：

```yaml
# configs/experiment/dqn_hanchan.yaml
experiment:
  name: "dqn_hanchan_v1"
  seed: 42
  total_episodes: 100000
  checkpoint_freq: 1000
  eval_freq: 2000
  log_dir: "runs/"

env:
  num_players: 4
  initial_score: 25000
  game_length: "hanchan"
  state_encoder_config:
    max_actions: 100
    action_feature_dim: 128

algo: "dqn"           # 注册名

algo_config:
  learning_rate: 0.0001
  gamma: 0.99
  buffer_size: 200000
  batch_size: 256
  target_update_freq: 1000
  epsilon_start: 1.0
  epsilon_end: 0.05
  epsilon_decay_steps: 50000
  hidden_dim: 256

reward:
  mode: "hybrid"
  placement_rewards: [1.0, 0.3, -0.3, -1.0]
  score_alpha: 0.5
  gamma: 0.99

agents:               # 4 个 agent 的算法 (self-play 或混合)
  - "dqn"
  - "dqn"
  - "dqn"
  - "dqn"

eval:
  mode: "vs_heuristic"
  n_games: 100
  eval_freq: 2000

data:                 # 真实数据预训练 (可选)
  kifu_path: null
  pretrain_epochs: 0
```

**CLI 调用**：
```bash
# 训练
python scripts/train.py --config configs/experiment/dqn_hanchan.yaml
# 断点恢复
python scripts/train.py --config configs/experiment/dqn_hanchan.yaml --resume runs/dqn_hanchan_v1/ckpt/latest.pt
# 评估
python scripts/evaluate.py --ckpt runs/dqn_hanchan_v1/ckpt/latest.pt --mode vs_heuristic --n_games 200
# 对局观察
python scripts/play.py --agent_ckpts runs/dqn_hanchan_v1/ckpt/latest.pt --human_seat 0
```

### config_loader 实现（补 §0 缺失项）
```python
# src/utils/config_loader.py
def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return validate_config(cfg)

def validate_config(cfg: dict) -> dict:
    # 校验必需 key + 一致性 (max_actions 等)
    # 兼容扁平/嵌套 config (旧代码读扁平 key)
    ...
```

---

## 10. 目录结构（RL 阶段）

```
src/
├── agent/
│   ├── base_agent.py        # BaseAgent 抽象接口
│   ├── registry.py          # 算法注册 + build_agent 工厂
│   ├── random_agent.py      # 随机基线
│   ├── heuristic_agent.py   # 启发式基线
│   ├── dqn_agent.py         # DQN (变长动作)
│   ├── ppo_agent.py         # PPO
│   └── models.py            # 神经网络 (共享 backbone)
├── trainning/               # 注意拼写(历史), 保持一致
│   ├── trainer.py           # 训练循环
│   ├── evaluator.py         # 评估
│   └── rollout.py           # 单局 rollout
├── data/
│   ├── kifu_loader.py       # 牌谱加载/重放/转 experience
│   └── experience.py        # experience buffer (replay/offline)
└── utils/
    ├── config_loader.py     # config 加载/校验
    └── logger.py            # 日志 (替代 print)

configs/experiment/          # 实验配置
├── dqn_hanchan.yaml
├── ppo_hanchan.yaml
└── bc_pretrain.yaml         # 模仿学习预训练

scripts/
├── train.py                 # 训练入口 (--config --resume)
├── evaluate.py              # 评估入口
├── play.py                  # 人机对局 (支持加载 agent)
└── convert_kifu.py          # 牌谱格式转换

runs/                        # 实验输出 (gitignore)
└── <exp_name>/{ckpt,metrics.csv,tensorboard,eval/}
```

---

## 11. 实施路线（分阶段）

### 阶段 A：闭环验证（2-3 天）
1. 补 `config_loader.py` + `logger.py`（替换 print）。
2. 实现 `random_agent` + `heuristic_agent` + `registry.py`。
3. 实现 `trainer.py` 最小版（4 随机 agent 跑 100 局，验证不崩 + 分数守恒 + 终局）。
4. 实现 reward 分配（hybrid mode）。
5. **验收**：100 局随机 baseline 跑完无异常，metrics.csv 生成。

### 阶段 B：DQN 训练（1 周）
1. 实现 `dqn_agent.py`（变长动作处理：action embedding + mask + Q 值）。
2. 实现 checkpoint/resume。
3. 实现 evaluator（vs 启发式）。
4. **验收**：DQN 10万局，vs 启发式平均顺位 < 2.5。

### 阶段 C：PPO + Self-play（2 周）
1. 实现 `ppo_agent.py`。
2. self-play 训练（4 个同版本 agent）。
3. league play 评估（防过拟合）。
4. **验收**：self-play agent vs 启发式 top-1 率 > 25%。

### 阶段 D：真实数据导入（1-2 周）
1. 实现 `kifu_loader.py` + `replay_kifu`（兼黄金牌谱回放，验证规则）。
2. 实现 BC 预训练（模仿学习初始化）。
3. 离线 RL（CQL/IQL）实验。
4. **验收**：BC 预训练后 agent vs 启发式顺位 < 2.3；规则牌谱回放一致率 > 95%。

### 阶段 E：长期训练 + 优化（持续）
1. observation 补全（opp_melds/riichi_flags 等）。
2. 分布式训练（多机 self-play）。
3. 策略蒸馏、对手池（league）。
4. 规则精度的黄金牌谱持续回归（CI 化）。

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 规则不准（计分/振听细节） | 训练出错误策略 | 牌谱回放校验 + seed 回归 CI |
| 训练卡死（如飞人 BLOCKER） | 浪费算力 | 已修；加 timeout + 异常捕获 |
| print 刷屏拖慢训练 | 性能 | logger 替换 + 可关 log |
| self-play 策略塌缩 | 学不到通用策略 | league play + 对手池 |
| 变长动作空间 | 标准 DQN 不适用 | action embedding + mask |
| 真实牌谱格式多样 | 转换复杂 | 自定义中间格式 + 转换器 |

---

## 13. 验收标准

1. ✅ `random_agent` 4 局对打跑完无异常，分数守恒。
2. ✅ DQN 训练 10 万局，checkpoint 保存/恢复正常。
3. ✅ resume 后续训指标与中断前连续（无跳变）。
4. ✅ evaluator 输出顺位/和牌率/放铳率等指标。
5. ✅ 至少 1 份牌谱回放成功，规则一致率 > 95%。
6. ✅ BC 预训练可初始化 agent（vs 启发式顺位优于随机）。
7. ✅ config 切换算法/ruleset 有效（DQN↔PPO、断幺开关等）。

---

## 14. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-02 | v1 初稿，定义 Agent 接口/训练循环/断点/评估/多算法/真实数据导入/实施路线 |
