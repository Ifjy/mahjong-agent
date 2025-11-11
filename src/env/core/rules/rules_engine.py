# 类/函数,函数头,职责描述
# Class,RulesEngine,"__init__(self, config: Dict)"
# Coordination,"generate_candidate_actions(self, game_state: GameState, player_index: int) -> List[Action]",委托 ActionValidator，合并自摸动作和响应动作列表，返回当前玩家所有合法动作。
# Flow,"determine_next_phase(self, game_state: GameState, executed_action: Action) -> GamePhase","根据已执行的动作 (Discard, Pon, Kan, Pass 等)，确定游戏应进入的下一个阶段 (WAITING_FOR_RESPONSE, PLAYER_DRAW, HAND_OUTCOME 等)。"
# Coordination,"determine_hand_outcome(self, game_state: GameState, final_action: Action) -> Dict[str, Any]",委托 Scoring，处理和牌/流局后的结果结算，包括分数计算。
# Flow,"determine_next_hand_state(self, game_state: GameState, hand_outcome: Dict[str, Any]) -> Dict[str, Any]",根据本局结果（连庄、散家和、流局），计算下一局的场风、局数、本场数等。
# Flow,"is_game_over(self, game_state: GameState) -> bool",检查总游戏是否结束（例如：有人被飞，或达到最大局数）。
# Utility,"resolve_response_priorities(self, declarations: Dict[int, Action], game_state: GameState) -> Tuple[Optional[Action], Optional[int]]",委托 ActionValidator.resolve_response_priorities，解决多玩家响应时的冲突。
from __future__ import annotations
from typing import List, Dict, Optional, Tuple, Any, TYPE_CHECKING
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 假设的类型导入 (用于类型提示)
# 注意：RulesEngine 不直接使用它们的方法，只用于类型标注
# --------------------------------------------------------------------------

from src.env.core.game_state import GameState, PlayerState, GamePhase
from src.env.core.actions import Action, ActionType, Tile, KanType
from src.env.core.rules.action_validator import ActionValidator
from src.env.core.rules.scoring import Scoring
from src.env.core.rules.hand_analyzer import HandAnalyzer
from src.env.core.rules.constants import TERMINAL_HONOR_VALUES, ACTION_PRIORITY

# 假设常量定义在 constants.py
# from .constants import ROUND_WIND_SOUTH, GAME_LENGTH_MAX_WIND


# 假设的 WinDetails 数据结构 (可能移至 data.py 或 scoring.py)
@dataclass
class WinDetails:
    is_valid_win: bool = False
    winning_tile: Optional["Tile"] = None
    yaku: List[str] = field(default_factory=list)
    han: int = 0
    fu: int = 0
    score_points: int = 0
    is_yakuman: bool = False
    # ... 其他需要的结算细节


# --------------------------------------------------------------------------


class RulesEngine:
    """
    麻将规则引擎 (高层协调器 / Facade)。

    核心职责：
    1. 初始化并持有所有低层规则组件的引用。
    2. 控制游戏流程和状态转换。
    3. 委托低层组件完成动作校验、手牌分析和分数计算。
    它自身不包含任何具体的规则判断逻辑 (如：能否Chi/Pon，如何算符)。
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        构造函数：初始化规则配置并实例化所有规则子模块。
        """
        self.config = config or {}

        # --- 游戏配置 (保留在此，用于高层流程控制) ---
        self.game_rules_config = self.config.get("game_rules", {})
        # self.max_game_wind = GAME_LENGTH_MAX_WIND.get(
        #     self.game_rules_config.get("game_length", "hanchan")
        # )

        # --- 实例化并依赖所有辅助组件 (依赖注入) ---
        # 必须确保 ActionValidator, Scoring, HandAnalyzer 已经被正确导入和实例化

        # TODO: 实例化 HandAnalyzer
        self.hand_analyzer: HandAnalyzer = HandAnalyzer(self.config)

        # TODO: 实例化 Scoring，它依赖于 HandAnalyzer
        self.scoring: Scoring = Scoring(self.hand_analyzer, self.config)

        # TODO: 实例化 ActionValidator，它依赖于 HandAnalyzer 和 Scoring
        self.action_validator: ActionValidator = ActionValidator(
            self.hand_analyzer, self.scoring, self.config
        )

        print("RulesEngine initialized: Ready for delegation.")

    # ======================================================================
    # == 核心协调 I: 动作生成和优先级 (委托 ActionValidator) ==
    # ======================================================================

    def generate_candidate_actions(
        self, game_state: "GameState", player_index: int
    ) -> List["Action"]:
        """
        【委托】为指定玩家在当前游戏状态下生成所有合法的候选动作列表。

        职责：
        1. 检查当前游戏阶段 (GamePhase)。
        2. 委托 ActionValidator 中对应的方法来生成实际的动作列表。
        """
        if not self.action_validator:
            raise RuntimeError("ActionValidator not initialized.")

        phase = game_state.game_phase
        player = game_state.players[player_index]

        # -----------------------------------------------------------------
        # 阶段 1: 玩家摸牌后 (轮到自己)
        # -----------------------------------------------------------------
        if phase == GamePhase.PLAYER_DISCARD:
            if player_index != game_state.current_player_index:
                return []

            # 委托 ActionValidator 生成 Tsumo/Kan/Riichi/Discard 动作
            return self.action_validator.get_legal_actions_on_draw(player, game_state)

        # -----------------------------------------------------------------
        # 阶段 2: 响应他人弃牌时
        # -----------------------------------------------------------------
        elif phase == GamePhase.WAITING_FOR_RESPONSE:
            if player_index == game_state.last_discard_player_index:
                return []

            # 委托 ActionValidator 生成 Ron/Pon/Kan/Chi/Pass 动作
            return self.action_validator.get_legal_actions_on_response(
                player, game_state
            )

        # -----------------------------------------------------------------
        # 阶段 3: 杠后摸岭上牌 (已在上一轮讨论中移除，合并到 ACTION_PROCESSING)
        # -----------------------------------------------------------------

        # -----------------------------------------------------------------
        # 其他非交互阶段
        # -----------------------------------------------------------------
        else:
            # 在 HAND_START, HAND_OVER_SCORES, GAME_OVER, PLAYER_DRAW,
            # ACTION_PROCESSING 等阶段，玩家不需要选择动作。
            return []

    def resolve_response_priorities(
        self, response_declarations: Dict[int, "Action"], game_state: "GameState"
    ) -> Tuple[Optional["Action"], Optional[int]]:
        """
        【委托】解决多玩家响应同一张弃牌时的优先级冲突。
        """
        if not self.action_validator:
            raise RuntimeError("ActionValidator not initialized.")

        return self.action_validator.resolve_response_priorities(
            response_declarations,
            game_state.last_discard_player_index,
            game_state.num_players,
        )

    # ======================================================================
    # == 核心协调 II: 局终结算 (委托 Scoring) ==
    # ======================================================================

    def process_hand_outcome(
        self,
        game_state: "GameState",
        end_reason: str,
        action: Optional["Action"] = None,
        player_index: Optional[int] = None,
        loser_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        【委托】处理和牌/流局后的结果结算流程，计算得分。

        **此函数是 GameController 在进入 HAND_OVER_SCORES 阶段后调用的。**

        Args:
            game_state: 游戏结束时的状态。
            end_reason: 结束原因 (e.g., "TSUMO", "RON", "SPECIAL_DRAW", "EXHAUSTIVE_DRAW")。
            action: 导致结束的玩家动作 (Tsumo/Ron/SpecialDraw)，流局时为 None。
            player_index: 执行动作的玩家 (赢家)，流局时为 None。
            loser_index: 放铳玩家 (仅 RON 时)，由 GameController 传入。

        Returns:
            Dict[str, Any]: 包含结算类型、分数变化和 WinDetails 的报告。
        """
        if not self.scoring:
            raise RuntimeError("Scoring module not initialized.")

        outcome: Dict[str, Any] = {
            "end_type": end_reason,
            "winner_index": player_index,
            "loser_index": loser_index,
            "score_details": None,
            "score_changes": {},
        }

        if end_reason in {"TSUMO", "RON"}:
            if action is None or player_index is None:
                raise ValueError(
                    "Winning action and player_index are required for TSUMO/RON."
                )

            winner = game_state.players[player_index]
            winning_tile = action.winning_tile  # 假设 Action 定义中有 winning_tile
            is_tsumo = end_reason == "TSUMO"

            # 1. 委托计算 WinDetails (役种、番、符)
            win_details: WinDetails = self.scoring.calculate_win_details(
                winner, winning_tile, is_tsumo, game_state
            )
            outcome["score_details"] = win_details

            # 2. 检查和牌是否合法 (由 Scoring 内部处理)
            if not win_details.is_valid_win:
                outcome["end_type"] = "INVALID_WIN"
                # TODO: 处理罚符 (Chombo) 逻辑
                return outcome

            # 3. 委托计算最终得分和支付
            score_changes = self.scoring.get_final_score_and_payout(
                win_details, game_state, loser_index
            )
            outcome["score_changes"] = score_changes

        elif end_reason == "EXHAUSTIVE_DRAW":
            # 荒牌流局 (牌山摸完)
            # 委托 Scoring 模块处理荒牌流局罚符 (Tenpai/Not Tenpai)
            outcome["score_changes"] = self.scoring.calculate_ryuukyoku_penalty_tenpai(
                game_state
            )

        elif end_reason == "SPECIAL_DRAW":
            # 特殊流局 (九种九牌等)
            # TODO: 委托 Scoring 模块处理特殊流局罚符 (通常为 0，但需处理本场和立直棒)
            # outcome["score_changes"] = self.scoring.calculate_ryuukyoku_penalty_special(game_state, action.type)
            pass

        return outcome

    # ======================================================================
    # == 核心流程 III: 游戏状态转换 (高层流程控制) ==
    # ======================================================================

    def determine_next_phase(
        self, game_state: "GameState", executed_action: "Action"
    ) -> "GamePhase":
        """
        【流程】根据已执行的动作，确定游戏应进入的下一个阶段。
        **此函数由 GameController 在成功执行一个动作后调用。**

        (纠正：移除了 RINSHAN_DRAW 和 DRAW_WALL_EMPTY 检查)
        """
        # 1. 检查导致本局结束的动作
        if executed_action.type in {
            ActionType.TSUMO,
            ActionType.RON,
            ActionType.SPECIAL_DRAW,
        }:
            # 和牌或流局，进入结算阶段
            return GamePhase.HAND_OVER_SCORES

        # 2. 检查鸣牌动作
        elif executed_action.type in {ActionType.PON, ActionType.CHI}:
            # 吃/碰，获得新牌后，进入打牌阶段
            # GameController 负责将弃牌加入副露并设置 player.drawn_tile = None
            return GamePhase.PLAYER_DISCARD

        elif executed_action.type == ActionType.KAN:
            # 杠，进入动作处理阶段
            # GameController 将在此阶段处理摸岭上牌，然后转回 PLAYER_DISCARD
            return GamePhase.ACTION_PROCESSING

        # 3. 检查常规流程动作
        elif executed_action.type == ActionType.DISCARD:
            # 打牌，游戏进入等待响应阶段
            # **注意：** 牌山是否摸完的检查
            # 已被移除，它属于 GameController 的职责。
            return GamePhase.WAITING_FOR_RESPONSE

        elif executed_action.type == ActionType.PASS:
            # 错过响应或无人响应
            # GameController 看到 PASS 后，会检查是否所有人都 PASS 了
            # 如果是，它将设置下一家摸牌，并转换到 PLAYER_DRAW
            return GamePhase.PLAYER_DRAW

        raise ValueError(f"无法确定 {executed_action.type} 后的下一阶段")

    def determine_next_hand_state(
        self, game_state: "GameState", hand_outcome: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        【流程】根据本局结果，确定下一局的场风、局数、庄家、本场数、立直棒。

        (此逻辑与上一版基本一致，仅依赖 hand_outcome 字典)
        """

        current_dealer_index = game_state.dealer_index
        current_round_wind = game_state.round_wind
        current_round_number = game_state.round_number
        current_honba = game_state.honba

        end_type = hand_outcome["end_type"]
        winner_index = hand_outcome.get("winner_index")  # 可能是 None

        is_dealer_win = winner_index == current_dealer_index

        next_dealer_index = current_dealer_index
        next_round_wind = current_round_wind
        next_round_number = current_round_number
        next_honba = current_honba + 1  # 默认连庄或流局，本场+1

        dealer_changes = False

        if end_type in {"TSUMO", "RON"}:
            if is_dealer_win:
                # 庄家和牌：连庄
                dealer_changes = False
            else:
                # 闲家和牌：庄家轮换
                dealer_changes = True
                next_honba = 0  # 闲家和牌清零本场数

        elif end_type in {"EXHAUSTIVE_DRAW", "SPECIAL_DRAW"}:
            # 流局
            # TODO: 委托 Scoring 或 ActionValidator 判断庄家是否听牌
            # 假设庄家听牌
            dealer_is_tenpai = True
            if dealer_is_tenpai:
                # 庄家听牌：连庄
                dealer_changes = False
            else:
                # 庄家未听牌：庄家轮换
                dealer_changes = True
                next_honba = 0  # 庄家轮换清零本场数

        elif end_type == "INVALID_WIN":
            # TODO: 处理罚符 (Chombo) 逻辑，通常不换庄家，本场不清零
            dealer_changes = False
            next_honba = current_honba  # 罚符不增加本场数

        # 处理庄家轮换
        if dealer_changes:
            next_dealer_index = (current_dealer_index + 1) % game_state.num_players

            # 检查是否需要进位到下一场风
            # 假设 game_state.initial_dealer_index 存储东1局的庄家索引
            if next_dealer_index == game_state.initial_dealer_index:
                next_round_wind += 1
                next_round_number = 1
            else:
                next_round_number += 1

        # 3. 计算立直棒的转移
        current_riichi_sticks = game_state.riichi_sticks
        next_riichi_sticks = current_riichi_sticks
        if end_type in {"TSUMO", "RON"}:
            next_riichi_sticks = 0  # 获胜者拿走
            # 注意：立直棒的点数应在 process_hand_outcome 中加给赢家

        return {
            "next_dealer_index": next_dealer_index,
            "next_round_wind": next_round_wind,
            "next_round_number": next_round_number,
            "next_honba": next_honba,
            "next_riichi_sticks": next_riichi_sticks,
        }

    def is_game_over(self, game_state: "GameState") -> bool:
        """
        【流程】检查总游戏是否结束 (飞人、局数完成等)。

        **此函数在 determine_next_hand_state *之后* 调用，**
        **检查的是 *下一局* 的状态是否超限。**
        """
        # 1. 检查是否有人被飞
        for player in game_state.players:
            if player.score < 0:
                # print(f"Debug Game Over: 玩家 {player.player_index} 分数飞了")
                return True

        # 2. 检查是否完成预定场数
        # (假设 game_state 已经是下一局的状态)
        # TODO: 从 config 加载 max_game_wind
        max_game_wind = 1  # 假设是半庄 (Wind.SOUTH = 1)

        if game_state.round_wind > max_game_wind:
            # print(f"Debug Game Over: 完成最后一场风 (南场)，游戏结束。")
            return True

        # TODO: 处理复杂的终局条件 (如南四局庄家和牌不结束等)

        return False
