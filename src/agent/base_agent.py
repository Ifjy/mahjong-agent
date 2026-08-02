"""
BaseAgent —— 所有 RL agent 的统一接口。

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §2。
Trainer 通过此接口统一调用各算法, 不感知具体实现。
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import numpy as np


class BaseAgent(ABC):
    """所有 RL agent / 基线 agent 的抽象基类。"""

    def __init__(self, config: Dict, agent_id: int):
        self.config = config
        self.agent_id = agent_id  # 玩家座位 (0-3)
        self.train_mode = True    # True=训练(探索), False=评估(贪婪)

    @abstractmethod
    def select_action(
        self,
        observation: Dict,
        action_mask: np.ndarray,
        valid_actions: List,
        deterministic: bool = False,
    ) -> int:
        """根据观察和动作掩码选择动作索引 (在 valid_actions 中的下标)。

        Args:
            observation: StateEncoder 编码的 dict。
            action_mask: (max_candidates,) 有效动作掩码。
            valid_actions: List[Action] 实际候选动作对象。
            deterministic: True=评估(贪婪), False=训练(探索)。
        Returns:
            action_idx: 选择的动作在 valid_actions 中的索引。
        """

    def store_transition(self, observation, action_idx, reward, next_observation, done, info):
        """存储一条转移 (s, a, r, s', done)。基线 agent 默认空。"""
        pass

    def update(self) -> Dict[str, float]:
        """从 buffer 采样训练, 返回指标 dict (如 loss)。基线 agent 默认空。"""
        return {}

    def assign_episode_reward(self, reward: float):
        """局结束后回填该 agent 的最终 reward (稀疏奖励)。基线默认空。"""
        pass

    def get_state(self) -> Dict[str, Any]:
        """返回可序列化状态 (权重/optimizer/步数/buffer), 用于 checkpoint。"""
        return {"agent_id": self.agent_id, "type": self.__class__.__name__}

    def load_state(self, state: Dict[str, Any]):
        """从 checkpoint 恢复状态。"""
        pass

    def store_offline_experience(self, trajectory: Dict):
        """注入真实玩家牌谱 experience (模仿学习/离线 RL)。默认空。"""
        pass
