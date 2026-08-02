"""
启发式基线 agent —— 基于简单策略的对照基线。

策略优先级:
1. TSUMO/RON (能和就和)
2. RIICHI (能立直则立直)
3. 非摸切 DISCARD (手切优先, 保留摸到的牌)
4. 摸切 DISCARD
5. CHI/PON (鸣牌, 谨慎)
6. PASS

用途: 作为评估对照基线 (比随机强, 但远弱于训练好的 RL agent)。
设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §2。
"""

from __future__ import annotations
from typing import Dict, List
import numpy as np

from src.agent.base_agent import BaseAgent
from src.agent.registry import register_agent
from src.env.core.actions import ActionType


# 动作类型优先级 (数值越小越优先)
_PRIORITY = {
    ActionType.TSUMO: 0,
    ActionType.RON: 0,
    ActionType.RIICHI: 1,
    ActionType.DISCARD: 2,
    ActionType.CHI: 4,
    ActionType.PON: 4,
    ActionType.KAN: 5,      # 杠优先级低 (避免疯狂杠触发流局)
    ActionType.SPECIAL_DRAW: 6,
    ActionType.PASS: 7,
}


@register_agent("heuristic")
class HeuristicAgent(BaseAgent):
    """启发式策略基线。"""

    def __init__(self, config: Dict, agent_id: int):
        super().__init__(config, agent_id)
        seed = config.get("seed", None)
        self.rng = np.random.default_rng(
            None if seed is None else seed + agent_id + 1000
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

        best_prio = 999
        candidates_idx = []
        for i, action in enumerate(valid_actions):
            prio = _PRIORITY.get(action.type, 8)
            if prio < best_prio:
                best_prio = prio
                candidates_idx = [i]
            elif prio == best_prio:
                candidates_idx.append(i)

        # 同优先级内随机选 (打牌时多个候选)
        return int(self.rng.choice(candidates_idx))
