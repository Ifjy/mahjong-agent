"""
DQN Agent —— 基于 Dueling DQN 的麻将 agent。

特性:
- Dueling DQN + Double DQN (用 target net 算 target Q)
- 变长动作空间处理 (action embedding + mask)
- Replay buffer (环形)
- epsilon-greedy 探索 (线性衰减)
- 支持批量推理 (BatchInferenceHelper)

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §2/§5。
"""

from __future__ import annotations
import random
from collections import deque
from typing import Dict, Any, Optional, List
import numpy as np

import torch
import torch.nn as nn
import torch.optim as optim

from src.agent.base_agent import BaseAgent
from src.agent.registry import register_agent
from src.agent.models import DuelingDQNNet


def _flatten_state(state: Dict) -> np.ndarray:
    """把 observation["state"] (dict of arrays) 展平为一维 float32 向量。"""
    parts = []
    for k in sorted(state.keys()):
        v = np.asarray(state[k], dtype=np.float32).ravel()
        parts.append(v)
    return np.concatenate(parts)


class ReplayBuffer:
    """环形 replay buffer。"""

    def __init__(self, capacity: int):
        self.buffer = deque(maxlen=capacity)

    def push(self, state, action_cands, action_mask, action_idx, reward,
             next_state, next_action_cands, next_action_mask, done):
        self.buffer.append((
            state, action_cands, action_mask, action_idx, reward,
            next_state, next_action_cands, next_action_mask, done,
        ))

    def sample(self, batch_size: int):
        batch = random.sample(self.buffer, min(batch_size, len(self.buffer)))
        return list(zip(*batch))

    def __len__(self):
        return len(self.buffer)


@register_agent("dqn")
class DQNAgent(BaseAgent):
    """DQN agent (Dueling + Double)。"""

    def __init__(self, config: Dict, agent_id: int):
        super().__init__(config, agent_id)

        ac = config.get("algo_config", {}) if "algo_config" in config else config
        dev = ac.get("device", "auto")
        if dev == "auto":
            dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(dev)
        self.hidden_dim = ac.get("hidden_dim", 256)
        self.lr = ac.get("learning_rate", ac.get("lr", 1e-4))
        self.gamma = ac.get("gamma", 0.99)
        self.batch_size = ac.get("batch_size", 256)
        self.buffer_size = ac.get("buffer_size", 200000)
        self.target_update_freq = ac.get("target_update_freq", 1000)

        # epsilon-greedy
        self.eps_start = ac.get("epsilon_start", 1.0)
        self.eps_end = ac.get("epsilon_end", 0.05)
        self.eps_decay_steps = ac.get("epsilon_decay_steps", 50000)
        self._train_steps = 0

        # 网络延迟初始化 (需要知道 state_dim/action_dim, 在第一次 select_action 时确定)
        self._initialized = False
        self._state_dim: Optional[int] = None
        self._action_dim: Optional[int] = None

        # 网络与优化器 (lazy init)
        self.q_net: Optional[DuelingDQNNet] = None
        self.target_net: Optional[DuelingDQNNet] = None
        self.optimizer: Optional[optim.Optimizer] = None
        self.buffer = ReplayBuffer(self.buffer_size)

        # 缓存上一步的 transition 片段, 供下一步 store
        self._last_obs = None  # (state_flat, action_cands, action_mask, action_idx)

        # 评估模式标志
        self.train_mode = True

    # —— 初始化 ——
    def _maybe_init(self, state_flat_dim: int, action_dim: int):
        if self._initialized and self._state_dim == state_flat_dim and self._action_dim == action_dim:
            return
        self._state_dim = state_flat_dim
        self._action_dim = action_dim
        self.q_net = DuelingDQNNet(state_flat_dim, action_dim, self.hidden_dim).to(self.device)
        self.target_net = DuelingDQNNet(state_flat_dim, action_dim, self.hidden_dim).to(self.device)
        self.target_net.load_state_dict(self.q_net.state_dict())
        self.target_net.eval()
        self.optimizer = optim.Adam(self.q_net.parameters(), lr=self.lr)
        self._initialized = True

    # —— BaseAgent 接口 ——
    def select_action(
        self,
        observation: Dict,
        action_mask: np.ndarray,
        valid_actions: List,
        deterministic: bool = False,
    ) -> int:
        if not valid_actions:
            return 0

        state_flat = _flatten_state(observation["state"])
        action_cands = np.asarray(observation["action_candidates"], dtype=np.float32)
        n_valid = len(valid_actions)
        # 只取前 n_valid 个候选 (padding 部分不在 valid_actions)
        self._maybe_init(state_flat.shape[0], action_cands.shape[1])

        # epsilon-greedy
        eps = self._current_epsilon()
        if (not deterministic) and self.train_mode and random.random() < eps:
            idx = random.randrange(n_valid)
        else:
            idx = self._greedy_select(state_flat, action_cands, action_mask, n_valid)
        return idx

    def _greedy_select(self, state_flat, action_cands, action_mask, n_valid) -> int:
        self.q_net.eval()
        with torch.no_grad():
            s = torch.from_numpy(state_flat).unsqueeze(0).to(self.device)
            a = torch.from_numpy(action_cands).unsqueeze(0).to(self.device)
            m = torch.from_numpy(action_mask.astype(np.float32)).unsqueeze(0).to(self.device)
            q = self.q_net(s, a, m).squeeze(0).cpu().numpy()
        # 仅在 n_valid 范围内 argmax
        return int(np.argmax(q[:n_valid]))

    @torch.no_grad()
    def batch_select(self, requests: List[Dict]) -> List[int]:
        """批量贪心选择。
        Args:
            requests: list of {state, action_candidates, action_mask, n_valid}
                      (来自多个 env 的决策请求)
        Returns:
            actions: list of action_idx (每个请求对应一个)
        批量 forward 大幅提升 GPU/CPU 利用率。
        """
        if not requests:
            return []
        self.q_net.eval()
        B = len(requests)
        states = np.stack([r["state_flat"] for r in requests])
        # 候选动作需对齐 (都取 max_actions 维)
        action_cands = np.stack([r["action_cands"] for r in requests])
        masks = np.stack([r["action_mask"].astype(np.float32) for r in requests])
        s = torch.from_numpy(states).to(self.device)
        a = torch.from_numpy(action_cands).to(self.device)
        m = torch.from_numpy(masks).to(self.device)
        q = self.q_net(s, a, m).cpu().numpy()  # (B, N)
        actions = []
        for i, r in enumerate(requests):
            n_valid = r["n_valid"]
            actions.append(int(np.argmax(q[i, :n_valid])))
        return actions

    def _current_epsilon(self) -> float:
        if self._train_steps >= self.eps_decay_steps:
            return self.eps_end
        frac = self._train_steps / max(1, self.eps_decay_steps)
        return self.eps_start + (self.eps_end - self.eps_start) * frac

    def store_transition(self, observation, action_idx, reward, next_observation, done, info):
        """把完整 (s,a,r,s',done) 存入 buffer。
        跳过无候选或 action_idx 指向非法位的 transition (防 Q 值污染)。"""
        if not self._initialized:
            return
        action_mask = np.asarray(observation["action_mask"], dtype=np.float32)
        if action_mask.sum() == 0:
            return  # 无候选
        if action_idx >= len(action_mask) or action_mask[action_idx] == 0:
            return  # action_idx 指向非法/padding 位, 跳过
        state_flat = _flatten_state(observation["state"])
        action_cands = np.asarray(observation["action_candidates"], dtype=np.float32)
        action_mask = np.asarray(observation["action_mask"], dtype=np.float32)
        next_state_flat = _flatten_state(next_observation["state"])
        next_action_cands = np.asarray(next_observation["action_candidates"], dtype=np.float32)
        next_action_mask = np.asarray(next_observation["action_mask"], dtype=np.float32)
        self.buffer.push(
            state_flat, action_cands, action_mask, action_idx, reward,
            next_state_flat, next_action_cands, next_action_mask, done,
        )

    def assign_episode_reward(self, reward: float):
        """局终回填 reward: 把缓存的 transition 与 reward 一起入 buffer。
        由 Trainer 在局结束时调用 (替代逐步 store_next)。
        """
        if self._last_obs is not None:
            # 没有显式 next, 用 done=True, next 用自身占位
            state_flat, action_cands, action_mask, action_idx = self._last_obs
            self.buffer.push(
                state_flat, action_cands, action_mask, action_idx, reward,
                state_flat, action_cands, action_mask, True,
            )
            self._last_obs = None

    def update(self) -> Dict[str, float]:
        """从 buffer采样训练一步, 返回 loss 指标。"""
        if len(self.buffer) < self.batch_size or not self.train_mode:
            return {}
        self.q_net.train()
        (states, action_cands, masks, action_idxs, rewards,
         next_states, next_action_cands, next_masks, dones) = self.buffer.sample(self.batch_size)

        # 转张量
        states_t = torch.from_numpy(np.stack(states)).to(self.device)
        action_cands_t = torch.from_numpy(np.stack(action_cands)).to(self.device)
        masks_t = torch.from_numpy(np.stack([m.astype(np.float32) for m in masks])).to(self.device)
        action_idxs_t = torch.tensor(action_idxs, dtype=torch.long, device=self.device)
        rewards_t = torch.tensor(rewards, dtype=torch.float32, device=self.device)
        next_states_t = torch.from_numpy(np.stack(next_states)).to(self.device)
        next_action_cands_t = torch.from_numpy(np.stack(next_action_cands)).to(self.device)
        next_masks_t = torch.from_numpy(np.stack([m.astype(np.float32) for m in next_masks])).to(self.device)
        dones_t = torch.tensor(dones, dtype=torch.float32, device=self.device)

        # 当前 Q (Double DQN: 用 q_net 选动作, target_net 估 Q)
        q_all = self.q_net(states_t, action_cands_t, masks_t)              # (B, N)
        q_sa = q_all.gather(1, action_idxs_t.unsqueeze(1)).squeeze(1)      # (B,)

        with torch.no_grad():
            # Double: q_net 选 next 动作
            next_q_main = self.q_net(next_states_t, next_action_cands_t, next_masks_t)
            next_act = next_q_main.argmax(dim=1, keepdim=True)
            # target_net 估该动作的 Q
            next_q_target = self.target_net(next_states_t, next_action_cands_t, next_masks_t)
            next_q = next_q_target.gather(1, next_act).squeeze(1)
            # 防御: 屏蔽 padding 产生的 -1e9 (next 动作选到非法位的极端情况)
            next_q = next_q.clamp(min=-100, max=100)
            target_q = rewards_t + self.gamma * next_q * (1.0 - dones_t)

        loss = nn.functional.smooth_l1_loss(q_sa, target_q)
        # reward clip 防止极端值 (已在 buffer 入库时归一化, 双保险)
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.q_net.parameters(), 10.0)
        self.optimizer.step()

        self._train_steps += 1
        if self._train_steps % self.target_update_freq == 0:
            self.target_net.load_state_dict(self.q_net.state_dict())

        return {"loss": loss.item(), "epsilon": self._current_epsilon()}

    # —— Checkpoint ——
    def get_state(self) -> Dict[str, Any]:
        if not self._initialized:
            return {"agent_id": self.agent_id, "initialized": False}
        return {
            "agent_id": self.agent_id,
            "initialized": True,
            "state_dim": self._state_dim,
            "action_dim": self._action_dim,
            "q_net": self.q_net.state_dict(),
            "target_net": self.target_net.state_dict(),
            "train_steps": self._train_steps,
        }

    def load_state(self, state: Dict[str, Any]):
        if not state.get("initialized", False):
            return
        self._maybe_init(state["state_dim"], state["action_dim"])
        self.q_net.load_state_dict(state["q_net"])
        self.target_net.load_state_dict(state["target_net"])
        self._train_steps = state.get("train_steps", 0)
