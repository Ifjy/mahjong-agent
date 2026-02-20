import random
import sys
from typing import List, Dict, Optional, Tuple

# --- 核心组件导入 ---
# 假设这些文件都在 'src/env/core' 或 'src/env/core/rules' 中
from src.env.core.game_state import GameState
from src.env.core.rules.rules_engine import RulesEngine
from src.env.core.game_state import Wall  # 假设 Wall 在 core 中
from src.env.core.game_state import PlayerState  # 假设 PlayerState 在 core 中
from src.env.core.actions import Action, ActionType, KanType
from src.env.core.game_state import GamePhase


class GameController:
    """
    麻将游戏控制器 (The Brain & State Machine).

    职责：
    1. 管理 GameState 和 Wall 的生命周期。
    2. 提供 step(player_idx, action) 接口供 Env 调用。
    3. 驱动游戏主循环 (摸牌 -> 打牌 -> 响应 -> 结算)。
    4. 调用 RulesEngine 进行规则判断和计分。
    """

    def __init__(self, config: Dict):
        self.config = config

        # 1. 核心组件
        self.wall = Wall()
        self.gamestate = GameState(config=self.config, wall=self.wall)
        self.rules_engine = RulesEngine(config=self.config)  # RulesEngine 无状态

        # 2. 响应管理 (Response Management)
        # 存储当前等待响应的玩家及其声明的动作 {player_idx: Action}
        self.pending_responses: Dict[int, Action] = {}

    def reset(self, seed: Optional[int] = None):
        """重置整个游戏 (由 Env.reset 调用)"""
        if seed is not None:
            random.seed(seed)
        self.gamestate.reset_game()
        self._start_new_hand()

    def _start_new_hand(self):
        """开始新的一局：洗牌、发牌、设置初始状态"""
        # 1. 重置数据
        self.gamestate.reset_new_hand()
        self.wall.shuffle_and_setup()
        self.pending_responses.clear()

        # 2. 配牌 (Deal Tiles)
        # 标准日麻：庄家14张，闲家13张
        # 我们模拟摸牌过程，直接操作 GameState
        for _ in range(3):  # 前三轮，每人拿4张
            for pid in range(self.gamestate.num_players):
                for _ in range(4):
                    self.gamestate.players[pid].hand.append(self.wall.draw_tile())

        for pid in range(self.gamestate.num_players):  # 第四轮，每人拿1张
            self.gamestate.players[pid].hand.append(self.wall.draw_tile())

        # 庄家多拿一张 (第14张)
        dealer_idx = self.gamestate.dealer_index
        dealer_tile = self.wall.draw_tile()
        self.gamestate.players[dealer_idx].hand.append(dealer_tile)
        self.gamestate.players[dealer_idx].drawn_tile = dealer_tile  # 标记为摸到的牌

        # 理牌
        for p in self.gamestate.players:
            p.hand.sort()

        # 3. 设置初始阶段
        self.gamestate.current_player_index = dealer_idx
        self.gamestate.game_phase = GamePhase.PLAYER_DISCARD  # 庄家已摸牌，等待出牌

        print(
            f"GameController: New hand started. Dealer: {dealer_idx}, Round: {self.gamestate.round_wind}-{self.gamestate.round_number}"
        )

    # ======================================================================
    # == 核心交互接口 (Step) ==
    # ======================================================================

    def step(
        self, player_idx: int, action: Action
    ) -> Tuple[GameState, int, bool, Dict]:
        """
        环境调用的主入口。应用动作并推演状态直到需要下一个 Agent 介入。

        Args:
            player_idx: 执行动作的玩家索引 (Env 必须保证这个 index 是正确的)
            action: 玩家选择的动作 (必须是合法的，建议 Env 先用 get_legal_actions 过滤)

        Returns:
            observation (GameState), reward, done, info
        """
        # 0. 安全检查 (防止非当前玩家乱动，除非是响应阶段)
        if self.gamestate.game_phase != GamePhase.WAITING_FOR_RESPONSE:
            if player_idx != self.gamestate.current_player_index:
                raise ValueError(f"Not player {player_idx}'s turn.")

        # 1. 应用动作 (根据当前阶段分发)
        if self.gamestate.game_phase == GamePhase.PLAYER_DISCARD:
            self._handle_player_discard_phase(player_idx, action)

        elif self.gamestate.game_phase == GamePhase.WAITING_FOR_RESPONSE:
            self._handle_response_phase(player_idx, action)

        else:
            raise RuntimeError(f"Invalid phase for step: {self.gamestate.game_phase}")

        # 2. 自动流程推演 (Auto-Flow)
        # 如果当前不需要玩家输入 (例如进入了处理阶段)，则自动推进直到需要输入或结束
        self._process_auto_flow()

        # 3. 返回结果
        done = self.gamestate.game_phase == GamePhase.GAME_OVER
        reward = 0  # TODO: 计算 reward
        return self.gamestate, reward, done, {}

    # ======================================================================
    # == 阶段处理逻辑 ==
    # ======================================================================

    def _handle_player_discard_phase(self, player_idx: int, action: Action):
        """
        处理 PLAYER_DISCARD 阶段的动作 (自摸 / 杠 / 立直 / 打牌)。
        """
        # 1. 验证动作合法性 (可选，但在 Env 中通常假设 Agent 的动作已 mask 过)
        # legal_actions = self.rules_engine.generate_candidate_actions(self.gamestate, player_idx)
        # if action not in legal_actions: raise ValueError("Illegal action")

        # 2. 应用动作到 GameState
        self.gamestate.apply_action(
            player_idx, action
        )  # 注意：GameState.apply_action 需要适配 player_idx

        # 3. 根据动作类型决定下一阶段
        next_phase = self.rules_engine.determine_next_phase(self.gamestate, action)

        if next_phase == GamePhase.HAND_OVER_SCORES:
            # 自摸 (TSUMO) -> 结算
            self._process_hand_outcome(
                end_reason="TSUMO", action=action, winner_idx=player_idx
            )

        elif next_phase == GamePhase.ACTION_PROCESSING:
            # 杠 (KAN) -> 自动流程处理 (摸岭上牌)
            self.gamestate.game_phase = GamePhase.ACTION_PROCESSING
            # _process_auto_flow 会接手

        elif next_phase == GamePhase.WAITING_FOR_RESPONSE:
            # 打牌 (DISCARD) -> 进入响应阶段
            self.gamestate.game_phase = GamePhase.WAITING_FOR_RESPONSE
            self.pending_responses.clear()  # 清空上一轮

        else:
            raise RuntimeError(
                f"Unexpected next phase from PLAYER_DISCARD: {next_phase}"
            )

    def _handle_response_phase(self, player_idx: int, action: Action):
        """
        处理 WAITING_FOR_RESPONSE 阶段的动作 (吃 / 碰 / 杠 / 荣 / 过)。
        """
        # 1. 记录响应
        self.pending_responses[player_idx] = action

        # 2. 检查是否所有人都响应了 (除了打牌者自己)
        # 需要响应的人数 = 总人数 - 1
        if len(self.pending_responses) < self.gamestate.num_players - 1:
            return  # 等待其他人

        # 3. 所有人都响应了 -> 解决优先级
        winning_action, winner_idx = self.rules_engine.resolve_response_priorities(
            self.pending_responses, self.gamestate
        )

        if winning_action and winning_action.type != ActionType.PASS:
            # 有人鸣牌或荣和
            self._execute_response(winner_idx, winning_action)
        else:
            # 所有人 PASS -> 流转到下家摸牌
            self._advance_to_next_turn()

    def _execute_response(self, player_idx: int, action: Action):
        """执行获胜的响应动作"""
        # 1. 应用动作
        self.gamestate.apply_action(player_idx, action)

        # 2. 决定下一阶段
        next_phase = self.rules_engine.determine_next_phase(self.gamestate, action)

        if next_phase == GamePhase.HAND_OVER_SCORES:
            # 荣和 (RON)
            self._process_hand_outcome(
                end_reason="RON",
                action=action,
                winner_idx=player_idx,
                loser_idx=self.gamestate.last_discard_player_index,
            )
        elif next_phase == GamePhase.PLAYER_DISCARD:
            # 吃/碰 (CHI/PON) -> 轮到该玩家打牌
            self.gamestate.current_player_index = player_idx
            self.gamestate.game_phase = GamePhase.PLAYER_DISCARD
            # 注意：鸣牌后不摸牌，drawn_tile 应为 None (由 apply_action 处理)
        elif next_phase == GamePhase.ACTION_PROCESSING:
            # 明杠 (OPEN KAN) -> 摸岭上牌
            self.gamestate.current_player_index = player_idx
            self.gamestate.game_phase = GamePhase.ACTION_PROCESSING

    # ======================================================================
    # == 自动流程 (Auto-Flow) ==
    # ======================================================================

    def _process_auto_flow(self):
        """
        处理不需要玩家输入的自动阶段。
        循环执行，直到游戏结束或进入需要玩家输入的阶段 (PLAYER_DISCARD / WAITING_FOR_RESPONSE)。
        """
        while True:
            phase = self.gamestate.game_phase

            # Case 1: 动作处理 (例如：杠后摸岭上牌)
            if phase == GamePhase.ACTION_PROCESSING:
                self._perform_rinshan_draw()
                # 摸完岭上牌后，自动切回 PLAYER_DISCARD，循环继续，等待玩家出牌
                continue

            # Case 2: 玩家摸牌 (PLAYER_DRAW) -> 这是一个瞬态，立即执行
            elif phase == GamePhase.PLAYER_DRAW:
                self._perform_regular_draw()
                # 摸完牌后，状态变为 PLAYER_DISCARD (或流局)，循环继续
                continue

            # Case 3: 局终结算 (HAND_OVER_SCORES)
            elif phase == GamePhase.HAND_OVER_SCORES:
                # 此时 RulesEngine.process_hand_outcome 已经计算完分数并存在 GameState 中
                # 或者是时候开始新的一局了
                # 检查是否整场游戏结束
                if self.rules_engine.is_game_over(self.gamestate):
                    self.gamestate.game_phase = GamePhase.GAME_OVER
                    break  # 退出循环
                else:
                    self._start_new_hand()
                    # 新局开始，状态变为 PLAYER_DISCARD，退出循环等待玩家
                    break

            # Case 4: 需要玩家输入 -> 退出自动循环
            elif phase in {
                GamePhase.PLAYER_DISCARD,
                GamePhase.WAITING_FOR_RESPONSE,
                GamePhase.GAME_OVER,
            }:
                break

            else:
                raise RuntimeError(f"Stuck in auto flow with phase: {phase}")

    def _perform_regular_draw(self):
        """执行常规摸牌"""
        tile = self.wall.draw_tile()

        if tile is None:
            # 荒牌流局
            self._process_hand_outcome(end_reason="EXHAUSTIVE_DRAW")
            return

        # 摸牌成功
        current_player = self.gamestate.players[self.gamestate.current_player_index]
        current_player.hand.append(tile)
        current_player.drawn_tile = tile
        self.gamestate.game_phase = GamePhase.PLAYER_DISCARD

    def _perform_rinshan_draw(self):
        """执行岭上摸牌 (杠后)"""
        tile = self.wall.draw_replacement_tile()

        if tile is None:
            # 理论上岭上牌不够是极其罕见的，视为流局或异常
            self._process_hand_outcome(end_reason="ABORTIVE_DRAW")
            return

        current_player = self.gamestate.players[self.gamestate.current_player_index]
        current_player.hand.append(tile)
        current_player.drawn_tile = tile
        # 标记为岭上开花上下文 (供 RulesEngine 使用)
        # self.gamestate.context.is_rinshan = True
        self.gamestate.game_phase = GamePhase.PLAYER_DISCARD

    def _advance_to_next_turn(self):
        """流转到下家摸牌"""
        next_pid = (
            self.gamestate.current_player_index + 1
        ) % self.gamestate.num_players
        self.gamestate.current_player_index = next_pid
        self.gamestate.game_phase = (
            GamePhase.PLAYER_DRAW
        )  # 设置为摸牌阶段，由 auto_flow 处理

    # ======================================================================
    # == 结算逻辑 ==
    # ======================================================================

    def _process_hand_outcome(
        self,
        end_reason: str,
        action: Optional[Action] = None,
        winner_idx: int = None,
        loser_idx: int = None,
    ):
        """调用 RulesEngine 结算本局，并更新 GameState"""

        # 1. 计算结果
        outcome = self.rules_engine.process_hand_outcome(
            self.gamestate, end_reason, action, winner_idx, loser_idx
        )

        # 2. 应用分数变化
        self.gamestate.update_scores(outcome["score_changes"])

        # 3. 计算下一局配置 (庄家, 场风, 本场)
        next_state_config = self.rules_engine.determine_next_hand_state(
            self.gamestate, outcome
        )
        self.gamestate.apply_next_hand_state(next_state_config)

        # 4. 设置阶段
        self.gamestate.game_phase = GamePhase.HAND_OVER_SCORES
        # 下一次 step 或 auto_flow 会处理新局开始或游戏结束
