import gym
from gym import spaces
import numpy as np
from typing import Dict, List, Optional

from src.env.core.game_state import GameState, Wall
from src.env.core.rules import RulesEngine
from src.env.core.actions import Action
from src.env.state_encoder import StateEncoder
from src.env.renderer import Renderer


class MahjongEnv(gym.Env):
    """基于扁平化候选动作空间的麻将环境"""

    metadata = {"render.modes": ["human", "text"]}

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.max_candidates = 100  # 最大候选动作数

        # 核心组件初始化
        self.wall = Wall()
        self.rules_engine = RulesEngine()
        self.game_state = GameState(
            config.get("game_rule_config", {}),
        )

        self.state_encoder = StateEncoder(config.get("state_encoder_config", {}))
        self.renderer = Renderer(config) if config.get("render", False) else None

        # 动作空间改为简单的离散空间
        self.action_space = spaces.Discrete(self.max_candidates)
        self.observation_space = self.state_encoder.get_observation_space()

        # 候选动作缓存
        self.current_candidates = []
        self.action_mask = np.zeros(self.max_candidates, dtype=np.int8)

    def reset(self, seed=None, options=None):
        """重置环境并返回初始观察"""
        # 如果你的gym版本需要，可以取消下面这行注释
        # super().reset(seed=seed)

        self.game_state.reset_new_hand()

        # --- 修改点：先生成初始状态的候选动作和掩码 ---
        info = (
            self._get_info()
        )  # 这个调用会填充 self.current_candidates 和 self.action_mask
        # --------------------------------------------

        observation = (
            self._get_observation()
        )  # 现在 _get_observation 可以使用已经填充好的 self.current_candidates

        # 根据新的gym版本API，reset 返回 (observation, info)
        return observation, info

    def step(self, action_idx: int):
        """
        执行动作
        Args:
            action_idx: 在候选动作列表中的索引
        """
        # 验证动作合法性
        if not (0 <= action_idx < len(self.current_candidates)):
            raise ValueError(f"Invalid action index {action_idx}")

        # 获取实际动作对象
        action = self.current_candidates[action_idx]

        # 应用动作
        self.game_state.apply_action(
            action, self.game_state.current_player_index
        )  # Pass the current player index

        # 获取新状态
        observation = self._get_observation()
        info = self._get_info()

        # 计算奖励
        reward = self._calculate_reward()

        # 终止判断
        terminated = self.rules_engine.is_hand_over(self.game_state)

        return observation, reward, terminated, False, info

    def _get_observation(self):
        """获取编码后的观察状态，包含候选动作信息"""
        return self.state_encoder.encode(
            game_state=self.game_state,
            player_index=self.game_state.current_player_index,
            candidate_actions=self.current_candidates,
        )

    def _get_info(self) -> Dict:
        """生成包含合法动作掩码的info字典"""
        # 生成候选动作列表
        # --- 修改点：传入当前玩家索引 ---
        self.current_candidates = self.rules_engine.generate_candidate_actions(
            game_state=self.game_state,
            player_index=self.game_state.current_player_index,  # <-- 添加了 player_index 参数
        )
        # -----------------------------

        # 生成动作掩码
        self.action_mask = np.zeros(self.max_candidates, dtype=np.int8)
        valid_count = min(len(self.current_candidates), self.max_candidates)
        self.action_mask[:valid_count] = 1

        return {
            "action_mask": self.action_mask,
            "valid_actions": self.current_candidates,
            "current_player": self.game_state.current_player_index,
            "current_phase": self.game_state.game_phase.name,
        }

    def _calculate_reward(self) -> float:
        """计算奖励值"""
        # 简化的奖励计算，可根据需要扩展
        if self.game_state.last_action_info:
            if self.game_state.last_action_info.get("type") in ["TSUMO", "RON"]:
                winner = self.game_state.last_action_info.get("winner")
                if winner == self.game_state.current_player_index:
                    return 1.0  # 获胜奖励
                else:
                    return -0.5  # 对手获胜惩罚
        return -0.01  # 每步小惩罚

    def render(self, mode="human"):
        """渲染当前游戏状态"""
        if self.renderer:
            return self.renderer.render(self.game_state, mode=mode)
        elif mode == "text":
            print(f"Current Phase: {self.game_state.game_phase.name}")
            print(f"Current Player: {self.game_state.current_player_index}")
            print(f"Valid Actions: {len(self.current_candidates)} options")
        return None

    def close(self):
        """关闭环境"""
        if self.renderer:
            self.renderer.close()
