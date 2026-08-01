from typing import Dict, Optional

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

    def __init__(self, config=None):
        super().__init__()
        self.config = config or {}
        # 最大候选动作数，须与 StateEncoder.max_actions 保持一致
        encoder_config = self.config.get("state_encoder_config", {})
        self.max_candidates = encoder_config.get("max_actions", 100)

        # 核心组件初始化
        self.controller = GameController(self.config)

        self.state_encoder = StateEncoder(encoder_config)
        self.renderer = Renderer(self.config) if self.config.get("render", False) else None

        # 动作空间改为简单的离散空间
        self.action_space = spaces.Discrete(self.max_candidates)
        self.observation_space = self.state_encoder.get_observation_space()

        # 候选动作缓存
        self.current_candidates = []
        self.action_mask = np.zeros(self.max_candidates, dtype=np.int8)

        # 当前行动玩家指针 (多智能体调度, 由 _get_info 维护, 不写 GameState)
        self._acting_player_idx = 0

    def reset(self, seed=None, options=None):
        """重置环境并返回初始观察"""
        # --- 改进点 2: reset 流程由 Controller 接管 ---
        if seed is not None:
            # 处理 seed (如果 Controller 支持)
            pass

        self.controller.reset()  # Controller 执行发牌等流程

        info = self._get_info()
        observation = self._get_observation()

        return observation, info

    def step(self, action_idx: int):
        """执行动作。

        多智能体调度: 用 self._acting_player_idx (由上次 _get_info 计算) 决定
        动作归属, 不读/写 GameState.current_player_index (那是 Controller 的职责)。
        """
        # 1. 当前行动玩家 (由上次 _get_info 算出, 外层据此选 action_idx)
        current_player_idx = self._acting_player_idx

        # 2. 验证和获取动作对象
        if not (0 <= action_idx < len(self.current_candidates)):
            raise ValueError(f"Invalid action index {action_idx}")

        action = self.current_candidates[action_idx]

        # 3. 动作执行前获取分数 (用于 Reward)
        old_score = self.controller.gamestate.players[current_player_idx].score

        # 4. 将动作交给 Controller (Controller 内部维护 current_player_index)
        self.controller.step(current_player_idx, action)

        # 5. 计算奖励
        state = self.controller.gamestate
        reward = self._calculate_reward(old_score, state, current_player_idx)

        terminated = state._game_over_flag
        truncated = False

        # 6. 获取新状态 (会更新 self._acting_player_idx)
        observation = self._get_observation()
        info = self._get_info()

        return observation, reward, terminated, truncated, info

    def _get_observation(self):
        """获取编码后的观察状态，包含候选动作信息"""
        return self.state_encoder.encode(
            game_state=self.controller.gamestate,
            player_index=self._acting_player_idx,
            candidate_actions=self.current_candidates,
        )

    def _get_info(self) -> Dict:
        """生成包含合法动作掩码的info字典。

        多智能体调度 (不写 GameState):
        - PLAYER_DISCARD 阶段: acting player = Controller 的 current_player_index。
        - WAITING_FOR_RESPONSE 阶段: acting player = 下一个尚未响应的响应者
          (由 _next_responder 计算, 仅写入 self._acting_player_idx 和 info,
          绝不写 GameState.current_player_index, 避免干扰 Controller 状态机)。
        """
        from src.env.core.game_state import GamePhase

        state = self.controller.gamestate

        if state.game_phase == GamePhase.WAITING_FOR_RESPONSE:
            current_player_idx = self._next_responder(state)
            if current_player_idx is None:
                # 所有响应者都已表态 (Controller 理论上已收齐推进; 兜底用打牌者)
                current_player_idx = state.last_discard_player_index
        else:
            current_player_idx = state.current_player_index

        # 记录当前行动玩家, 供下次 step() 使用 (不写 GameState!)
        self._acting_player_idx = current_player_idx

        # 通过 Controller 访问 RulesEngine 生成候选动作
        self.current_candidates = (
            self.controller.rules_engine.generate_candidate_actions(
                game_state=state,
                player_index=current_player_idx,
            )
        )

        # 生成动作掩码
        self.action_mask = np.zeros(self.max_candidates, dtype=np.int8)
        valid_count = min(len(self.current_candidates), self.max_candidates)
        self.action_mask[:valid_count] = 1

        return {
            "action_mask": self.action_mask,
            "valid_actions": self.current_candidates,
            "current_player": current_player_idx,
            "current_phase": state.game_phase.name,
        }

    def _next_responder(self, state) -> Optional[int]:
        """在 WAITING_FOR_RESPONSE 阶段，找出第一个尚未响应（不在
        controller.pending_responses 中）且非打牌者的玩家索引。
        返回 None 表示所有响应者都已表态。"""
        discarder = state.last_discard_player_index
        pending = self.controller.pending_responses
        for offset in range(1, state.num_players):
            cand_idx = (discarder + offset) % state.num_players
            if cand_idx == discarder:
                continue
            if cand_idx in pending:
                continue  # 该玩家已响应
            return cand_idx
        return None

    def _calculate_reward(self, old_score: int, state: GameState, player_idx: int) -> float:
        """
        计算奖励值。
        如果当前是局结束阶段 (HAND_OVER_SCORES)，则返回指定玩家的点数变化。
        否则返回小的负数作为步惩罚。
        """

        if state._hand_over_flag:
            # 局刚刚结束，计算该动作玩家的点数变化作为奖励
            new_score = state.players[player_idx].score

            # (注意：这个实现可能过于简化，实际应考虑一局内其他玩家的奖励)
            return (new_score - old_score) / 1000.0  # 假设用千点作为奖励单位

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
