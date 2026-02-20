from typing import Dict

import gymnasium as gym
from gymnasium import spaces
import numpy as np

from src.env.core.game_state import GameState
from src.env.core.GameController import GameController

# from src.env.core.actions import Action
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
        self.controller = GameController(config)

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
        super().reset(seed=seed)
        self.controller.reset(seed=seed)  # Controller 执行发牌等流程

        info = self._get_info()
        observation = self._get_observation()

        return observation, info

    def step(self, action_idx: int):
        """执行动作"""
        # 1. 获取当前行动的玩家 (Environment Knowledge)
        # 这是解决问题的关键：环境知道现在轮到谁
        current_player_idx = self.controller.gamestate.current_player_index

        # 2. 验证和获取动作对象
        if not (0 <= action_idx < len(self.current_candidates)):
            raise ValueError(f"Invalid action index {action_idx}")

        action = self.current_candidates[action_idx]  # Action 对象本身不含 player_index

        # 3. 动作执行前获取分数 (用于 Reward)
        # 使用 current_player_idx 而不是 action.player_index
        old_score = self.controller.gamestate.players[current_player_idx].score

        # 4. 核心：将动作交给 Controller
        # Controller 的 step 方法签名是 (player_idx, action)
        # 我们显式地传入 current_player_idx
        self.controller.step(current_player_idx, action)

        # 3. 检查状态、计算奖励
        state = self.controller.gamestate

        # 计算奖励：主要基于点数变化和即时惩罚
        reward = self._calculate_reward(old_score, state)

        # 终止判断：依赖 Controller 内部设置的标志
        terminated = state._game_over_flag  # 整场游戏结束

        # Truncated: 通常用于时间限制，这里我们简单设为 False
        truncated = False

        # 4. 获取新状态
        observation = self._get_observation()
        info = self._get_info()

        return observation, reward, terminated, truncated, info

    def _get_observation(self):
        """获取编码后的观察状态，包含候选动作信息"""
        return self.state_encoder.encode(
            game_state=self.controller.gamestate,
            player_index=self.controller.gamestate.current_player_index,
            candidate_actions=self.current_candidates,
        )

    def _get_info(self) -> Dict:
        """生成包含合法动作掩码的info字典"""
        state = self.controller.gamestate
        current_player_idx = state.current_player_index

        # --- 改进点 3: 通过 Controller 访问 RulesEngine ---
        self.current_candidates = (
            self.controller.rules_engine.generate_candidate_actions(
                game_state=state,
                player_index=current_player_idx,
            )
        )

        # 生成动作掩码 (不变)
        self.action_mask = np.zeros(self.max_candidates, dtype=np.int8)
        valid_count = min(len(self.current_candidates), self.max_candidates)
        self.action_mask[:valid_count] = 1

        return {
            "action_mask": self.action_mask,
            "valid_actions": self.current_candidates,
            "current_player": current_player_idx,
            "current_phase": state.game_phase.name,
        }

    def _calculate_reward(self, old_score: int, state: GameState) -> float:
        """
        计算奖励值。
        如果当前是局结束阶段 (HAND_OVER_SCORES)，则返回点数变化。
        否则返回小的负数作为步惩罚。
        """

        if state._hand_over_flag:
            # 局刚刚结束，计算当前玩家的点数变化作为奖励
            current_player_idx = state.current_player_index
            new_score = state.players[current_player_idx].score

            # (注意：这个实现可能过于简化，实际应考虑一局内其他玩家的奖励)
            return (new_score - old_score) / 1000.0  # 假设用千点作为奖励单位

        # 局中奖励 (可扩展，例如立直 +100，每步 -0.01)
        return -0.01

    def render(self, mode="human"):
        """渲染当前游戏状态"""
        if self.renderer:
            return self.renderer.render(self.controller.gamestate, mode=mode)
        elif mode == "text":
            print(f"Current Phase: {self.controller.gamestate.game_phase.name}")
            print(f"Current Player: {self.controller.gamestate.current_player_index}")
            print(f"Valid Actions: {len(self.current_candidates)} options")
        return None

    def close(self):
        """关闭环境"""
        if self.renderer:
            self.renderer.close()
