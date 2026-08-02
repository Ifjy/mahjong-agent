"""
ParallelCollector —— 单进程多 env 并行采集 + 批量推理。

提速原理:
- 维持 N 个独立 env 同时推进 (self-play 数据采集)。
- 当多个 env 都需要决策时, 把决策请求攒成一批, 一次 GPU/CPU forward
  (batch_select), 大幅提升硬件利用率 (相比每步 B=1 forward)。
- 4 人麻将每 env 内部仍串行, 但 N 个 env 间可批量。

注意: 4 个 agent 共享同一套网络权重 (self-play), 采集到的经验汇入共享 buffer。
设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §3。
"""

from __future__ import annotations
import numpy as np
from typing import Dict, List, Optional, Any

from src.env.mahjong_env import MahjongEnv
from src.agent.dqn_agent import DQNAgent, _flatten_state


class ParallelCollector:
    """多 env 并行采集器 (单进程)。"""

    def __init__(self, env_config: Dict, agent: DQNAgent, num_envs: int = 8,
                 base_seed: int = 0):
        self.agent = agent
        self.num_envs = num_envs
        self.envs = [MahjongEnv(env_config) for _ in range(num_envs)]
        # 每个 env 当前 obs/info, 及 4 个 agent 的 pending transition
        self.obs_list: List[Optional[Dict]] = [None] * num_envs
        self.info_list: List[Dict] = [{}] * num_envs
        self.pending = [  # [env_idx] -> {player: (obs, action_idx, accrue)}
            {i: None for i in range(4)} for _ in range(num_envs)
        ]
        self.accrue = [[0.0] * 4 for _ in range(num_envs)]
        self.episodes_completed = 0
        self.total_steps = 0

        # 初始化所有 env
        for i, env in enumerate(self.envs):
            obs, info = env.reset(seed=base_seed + i)
            self.obs_list[i] = obs
            self.info_list[i] = info

        # 触发 DQN 网络初始化 (需要 state_dim/action_dim)
        first_obs = self.obs_list[0]
        if "state" in first_obs and "action_candidates" in first_obs:
            sf = _flatten_state(first_obs["state"])
            agent._maybe_init(sf.shape[0], first_obs["action_candidates"].shape[1])

    def collect(self, num_steps: int, deterministic: bool = False) -> Dict[str, Any]:
        """采集 num_steps 步, 返回统计信息。
        每次: 把所有 env 的当前决策请求攒批 → batch_select → 各 env 推进。
        """
        steps_done = 0
        # epsilon 在批量选择时统一用一个值 (基于 agent 当前步数)
        eps = self.agent._current_epsilon() if (self.agent.train_mode and not deterministic) else 0.0

        while steps_done < num_steps:
            # 1. 收集所有 env 的当前决策请求 (排除已终止的)
            requests = []
            env_indices = []
            for i in range(self.num_envs):
                info = self.info_list[i]
                valid = info.get("valid_actions", [])
                if not valid:
                    # env 终止, 重置
                    self._reset_env(i)
                    continue
                cp = info.get("current_player", 0)
                obs = self.obs_list[i]
                # epsilon-greedy: 部分随机
                if not deterministic and self.agent.train_mode and np.random.random() < eps:
                    # 随机选, 不进批量
                    idx = np.random.randint(0, len(valid))
                    self._advance_env(i, cp, idx)
                    steps_done += 1
                    continue
                # 构造批量请求
                state_flat = _flatten_state(obs["state"])
                self.agent._maybe_init(state_flat.shape[0], obs["action_candidates"].shape[1])
                requests.append({
                    "state_flat": state_flat,
                    "action_cands": np.asarray(obs["action_candidates"], dtype=np.float32),
                    "action_mask": np.asarray(obs["action_mask"], dtype=np.float32),
                    "n_valid": len(valid),
                })
                env_indices.append((i, cp))

            # 2. 批量 forward 选择
            if requests:
                actions = self.agent.batch_select(requests)
                for (i, cp), idx in zip(env_indices, actions):
                    self._advance_env(i, cp, idx)
                    steps_done += 1
                    if steps_done >= num_steps:
                        break

        return {"steps": self.total_steps, "episodes": self.episodes_completed}

    def _advance_env(self, env_idx: int, player: int, action_idx: int):
        """推进一个 env 一步, 处理 transition 采集。"""
        env = self.envs[env_idx]
        obs = self.obs_list[env_idx]
        info = self.info_list[env_idx]
        agent = self.agent

        # 该 player 有 pending -> 入 buffer
        if self.pending[env_idx][player] is not None:
            p_obs, p_act = self.pending[env_idx][player]
            agent.store_transition(
                p_obs, p_act, self.accrue[env_idx][player], obs, False, info
            )
            self.accrue[env_idx][player] = 0.0
            self.pending[env_idx][player] = None

        self.pending[env_idx][player] = (obs, action_idx)
        next_obs, reward, terminated, truncated, next_info = env.step(action_idx)
        self.accrue[env_idx][player] += reward
        self.total_steps += 1

        self.obs_list[env_idx] = next_obs
        self.info_list[env_idx] = next_info

        if terminated or truncated:
            # 终局: 回填所有 player 的 pending
            per_player_rewards = next_info.get("rewards", {i: 0.0 for i in range(4)})
            for p in range(4):
                if self.pending[env_idx][p] is not None:
                    p_obs, p_act = self.pending[env_idx][p]
                    final_r = self.accrue[env_idx][p] + per_player_rewards.get(p, 0.0)
                    agent.store_transition(p_obs, p_act, final_r, next_obs, True, next_info)
                    self.pending[env_idx][p] = None
                    self.accrue[env_idx][p] = 0.0
            self.episodes_completed += 1
            self._reset_env(env_idx)

    def _reset_env(self, env_idx: int, seed: Optional[int] = None):
        """重置指定 env。"""
        if seed is None:
            seed = self.episodes_completed * 1000 + env_idx
        obs, info = self.envs[env_idx].reset(seed=seed)
        self.obs_list[env_idx] = obs
        self.info_list[env_idx] = info
        self.pending[env_idx] = {i: None for i in range(4)}
        self.accrue[env_idx] = [0.0] * 4
