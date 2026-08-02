"""
随机基线 agent —— 从合法动作中均匀随机选择。

用途:
1. 验证环境闭环 (阶段 A 验收)。
2. 作为最弱基线对照评估。

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §2。
"""

from __future__ import annotations
from typing import Dict, List
import numpy as np

from src.agent.base_agent import BaseAgent
from src.agent.registry import register_agent


@register_agent("random")
class RandomAgent(BaseAgent):
    """均匀随机选择合法动作。"""

    def __init__(self, config: Dict, agent_id: int):
        super().__init__(config, agent_id)
        # 独立的 RNG (支持 seed 复现)
        seed = config.get("seed", None)
        self.rng = np.random.default_rng(
            None if seed is None else seed + agent_id
        )

    def select_action(
        self,
        observation: Dict,
        action_mask: np.ndarray,
        valid_actions: List,
        deterministic: bool = False,
    ) -> int:
        if not valid_actions:
            return 0
        # 从有效动作中随机选
        return int(self.rng.integers(0, len(valid_actions)))
