from typing import Dict

import gym
from gym import spaces
import numpy as np

from src.env.core.game_state import GameState, Wall, GamePhase
from src.env.core.rules import RulesEngine

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
        self.wall = Wall()
        self.rules_engine = RulesEngine()
        self.game_state = GameState(
            config.get("game_rule_config", {}), self.wall, self.rules_engine
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
        hand_is_over = self.rules_engine.is_hand_over(self.game_state)
        terminated = hand_is_over  # Episode 在一局游戏结束时终止

        # 整场游戏是否结束的标志，默认不是
        game_is_over = False

        if hand_is_over:
            # --- 处理一局游戏结束后的结算流程 ---
            print("\n--- 一局游戏结束 ---")
            self.game_state.game_phase = GamePhase.HAND_OVER_SCORES  # 切换阶段到结算

            # TODO: 获取本局游戏结果的详细信息 (和牌类型、番数、符、点数变动、流局类型等)
            hand_outcome_info = self.rules_engine.get_hand_outcome(self.game_state)

            # # TODO: 根据本局结果计算玩家点数变动
            # get hand outcome 中会调用函数calculate_yaku_and_score计算。 同时中间会有字段 score_changes 记录点数变动。
            # TODO: 应用点数变动到玩家分数
            # 需要 GameState 提供方法更新玩家分数
            self.game_state.update_scores(
                hand_outcome_info["score_changes"]
            )  # 假设 GameState 有此方法

            # TODO: 根据本局结果确定下一局的场风、局数、本场数、立直棒和庄家
            # 需要 RulesEngine 提供方法确定下一局的状态信息
            next_hand_state_info = self.rules_engine.determine_next_hand_state(
                self.game_state, hand_outcome_info
            )  # 假设 RulesEngine 有此方法

            # TODO: 在 GameState 中应用下一局的状态
            # 需要 GameState 提供方法更新 round_wind, round_number, honba, riichi_sticks, dealer_index
            self.game_state.apply_next_hand_state(
                next_hand_state_info
            )  # 假设 GameState 有此方法
            self.game_state.game_phase = (
                GamePhase.HAND_OVER_SCORES
            )  # 标记本局结束，准备进入下一局设置

            # --- 检查整场游戏是否结束 ---
            # 根据游戏规则判断是否达到整场游戏结束条件 (例如打完南四局，点数飞了等)
            game_is_over = self.rules_engine.is_game_over(
                self.game_state
            )  # 假设 RulesEngine 有此方法判断整场游戏结束

            terminated = game_is_over  # Episode 在整场游戏结束时终止

            # 返回的状态信息反映的是局刚结束结算后的状态
            # 训练循环在 terminated 为 False 时会调用 env.reset() 来开始下一局

            print(f"--- 本局结束。整场游戏是否结束: {game_is_over} ---")
            # 返回值中的 terminated 标志现在表示的是整场游戏的结束

            return observation, reward, terminated, False, info

        else:  # 一局游戏未结束，继续进行
            terminated = False  # 整场游戏当然也未结束
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
