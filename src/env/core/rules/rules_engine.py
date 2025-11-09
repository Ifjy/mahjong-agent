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
if TYPE_CHECKING:
    from ..game_state import GameState, PlayerState, GamePhase
    from ..actions import Action, ActionType, Tile, KanType
    from .action_validator import ActionValidator
    from .scoring import Scoring
    from .hand_analyzer import HandAnalyzer

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
        # self.hand_analyzer: HandAnalyzer = HandAnalyzer(self.config)

        # TODO: 实例化 Scoring，它依赖于 HandAnalyzer
        # self.scoring: Scoring = Scoring(self.hand_analyzer, self.config)

        # TODO: 实例化 ActionValidator，它依赖于 HandAnalyzer 和 Scoring
        # self.action_validator: ActionValidator = ActionValidator(
        #     self.hand_analyzer, self.scoring, self.config
        # )

        # 暂时使用 None 或 Placeholder
        self.hand_analyzer = None
        self.scoring = None
        self.action_validator = None

        print("RulesEngine initialized: Ready for delegation.")

    # ======================================================================
    # == 核心协调 I: 动作生成和优先级 (委托 ActionValidator) ==
    # ======================================================================

    def generate_candidate_actions(
        self, game_state: "GameState", player_index: int
    ) -> List["Action"]:
        """
        【委托】为指定玩家在当前游戏状态下生成所有合法的候选动作列表。

        Args:
            game_state: 当前游戏状态。
            player_index: 正在行动的玩家索引。

        Returns:
            List[Action]: 合法的动作列表。
        """
        if not self.action_validator:
            # 运行时检查：确保依赖组件已加载
            raise RuntimeError("ActionValidator not initialized.")

        phase = game_state.game_phase

        if phase == GamePhase.PLAYER_DISCARD:
            # 玩家摸牌后，生成自摸/杠/立直/打牌等动作
            return self.action_validator.get_legal_actions_on_draw(
                game_state.players[player_index], game_state
            )

        elif phase == GamePhase.WAITING_FOR_RESPONSE:
            # 玩家在其他玩家弃牌后，生成荣和/碰/杠/吃/过等动作
            return self.action_validator.get_legal_actions_on_discard(
                game_state.players[player_index],
                game_state,
                game_state.last_discarded_tile,
            )

        # TODO: 处理其他可能的 Phase (如特殊流局申报)
        return []

    def resolve_response_priorities(
        self, response_declarations: Dict[int, "Action"], game_state: "GameState"
    ) -> Tuple[Optional["Action"], Optional[int]]:
        """
        【委托】解决多玩家响应同一张弃牌时的优先级冲突。

        职责：将优先级逻辑完全委托给 ActionValidator。
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
        self, game_state: "GameState", winning_action: "Action"
    ) -> Dict[str, Any]:
        """
        【委托】处理和牌/流局后的结果结算流程，计算得分。

        Args:
            game_state: 游戏结束时的状态。
            winning_action: 导致和牌或流局的最终动作 (Tsumo/Ron/SpecialDraw)。

        Returns:
            Dict[str, Any]: 包含结算类型、赢家/输家索引、分数变化和 WinDetails 的报告。
        """
        if not self.scoring:
            raise RuntimeError("Scoring module not initialized.")

        outcome: Dict[str, Any] = {
            "end_type": winning_action.type.name,  # Tsumo, Ron, RYUUKYOKU (Special Draw)
            "winner_index": winning_action.player_index,
            "loser_index": None,
            "score_details": None,
            "score_changes": {},
        }

        if winning_action.type in {ActionType.TSUMO, ActionType.RON}:
            winner = game_state.players[winning_action.player_index]
            is_tsumo = winning_action.type == ActionType.TSUMO

            # 1. 识别放铳者 (如果是荣和)
            if winning_action.type == ActionType.RON:
                outcome["loser_index"] = game_state.last_discard_player_index

            # 2. 委托计算 WinDetails (役种、番、符)
            win_details: WinDetails = self.scoring.calculate_win_details(
                winner, winning_action.tile, is_tsumo, game_state
            )
            outcome["score_details"] = win_details

            # 3. 检查和牌是否合法 (例如：振听荣和、无役不和)
            if not self.scoring.is_valid_win(winner, win_details, game_state):
                # 理论上 ActionValidator 应该防止非法和牌，但作为最终检查是必要的
                outcome["end_type"] = "INVALID_WIN"
                print(f"Warning: Invalid win detected for {winner.player_index}")
                # TODO: 根据规则，可能需要判定为流局或罚符
                return outcome

            # 4. 委托计算最终得分和支付
            score_changes = self.scoring.get_final_score_and_payout(
                win_details, game_state, outcome["loser_index"]
            )
            outcome["score_changes"] = score_changes

        elif winning_action.type == ActionType.SPECIAL_DRAW:
            # TODO: 委托 Scoring 模块处理流局罚符 (例如九种九牌、四风连打等)
            # outcome["score_changes"] = self.scoring.calculate_ryuukyoku_penalty(game_state)
            pass

        elif winning_action.type == ActionType.DRAW_WALL_EMPTY:
            # TODO: 委托 Scoring 模块处理荒牌流局罚符 (Tenpai/Not Tenpai)
            # outcome["score_changes"] = self.scoring.calculate_ryuukyoku_penalty_tenpai(game_state)
            pass

        return outcome

    # ======================================================================
    # == 核心流程 III: 游戏状态转换 (高层流程控制) ==
    # ======================================================================

    def determine_next_phase(
        self, game_state: "GameState", executed_action: "Action"
    ) -> "GamePhase":
        """
        根据已执行的动作，确定游戏应进入的下一个阶段。
        这部分是 RulesEngine 的核心流程控制逻辑。

        Args:
            game_state: 执行动作前的状态。
            executed_action: 刚刚被执行的动作。

        Returns:
            GamePhase: 下一个游戏阶段。
        """
        if executed_action.type in {
            ActionType.TSUMO,
            ActionType.RON,
            ActionType.SPECIAL_DRAW,
            ActionType.DRAW_WALL_EMPTY,
        }:
            # 和牌或流局，进入结算阶段
            return GamePhase.HAND_OUTCOME

        elif executed_action.type == ActionType.DISCARD:
            # 打牌，游戏进入等待响应阶段 (除了立直后的暗杠可能绕过响应)
            # TODO: 需要判断是否是立直后的暗杠，如果是，直接进入摸牌/岭上开花

            # 检查牌山是否已经摸完
            if not game_state.wall.has_draw_tiles():
                return GamePhase.DRAW_WALL_EMPTY

            return GamePhase.WAITING_FOR_RESPONSE

        elif executed_action.type in {ActionType.PON, ActionType.CHI}:
            # 吃/碰，获得新牌后，进入打牌阶段
            return GamePhase.PLAYER_DISCARD

        elif executed_action.type == ActionType.KAN:
            # 杠，进入岭上牌阶段
            return GamePhase.RINSHAN_DRAW

        elif executed_action.type == ActionType.PASS:
            # 错过响应或无人响应
            # 流程将转移到下一家摸牌
            # TODO: GameController 应该根据当前玩家索引和 PASS 决定下一家是谁，这里只返回下一阶段
            return GamePhase.PLAYER_DRAW

        # TODO: 其他特殊动作的流程处理

        return GamePhase.UNKNOWN  # 默认或错误状态

    def determine_next_hand_state(
        self, game_state: "GameState", hand_outcome: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        根据本局结果，确定下一局的场风、局数、庄家、本场数、立直棒等。

        职责：主要处理连庄/庄家轮换逻辑。
        """

        current_dealer_index = game_state.dealer_index
        current_round_wind = game_state.round_wind
        current_round_number = game_state.round_number
        current_honba = game_state.honba

        end_type = hand_outcome["end_type"]
        winner_index = hand_outcome.get("winner_index")

        is_dealer_win = winner_index == current_dealer_index

        next_dealer_index = current_dealer_index
        next_round_wind = current_round_wind
        next_round_number = current_round_number
        next_honba = 0

        # 1. 计算下一局的本场数 (Honba)
        # 和牌或流局都导致本场数 +1，直到下一局有人和牌
        if end_type != "INVALID_WIN":  # 假设只有有效和牌才清零
            next_honba = current_honba + 1

        # 2. 确定庄家和局数 (Round Wind/Number)
        if end_type in {"TSUMO", "RON"} and is_dealer_win:
            # 庄家和牌：连庄 (Honba +1, 局数不变)
            # next_dealer_index = current_dealer_index (不变)
            # next_round_wind = current_round_wind (不变)
            # next_round_number = current_round_number (不变)
            pass
        elif end_type in {"TSUMO", "RON"} and not is_dealer_win:
            # 闲家和牌：庄家轮换
            next_dealer_index = (current_dealer_index + 1) % game_state.num_players

            # 检查是否需要进位到下一场风
            if next_dealer_index == game_state.initial_dealer_index:
                # 庄家轮回到初始庄家位置，表示该场风结束
                next_round_wind += 1  # 场风进位
                next_round_number = 1  # 局数重置为 1 (例如：东四局 -> 南一局)
            else:
                next_round_number += 1  # 局数递增

            next_honba = 0  # 闲家和牌清零本场数
        elif end_type in {"SPECIAL_DRAW", "DRAW_WALL_EMPTY"}:
            # 流局 (Ryuukyoku)：检查是否连庄 (通常是庄家听牌/流局满贯等)
            # TODO: 委托 ActionValidator 或 Scoring 判断是否应连庄 (通常是庄家是否听牌)
            should_dealer_stay = True  # 假设流局默认连庄，除非庄家不听牌

            if should_dealer_stay:
                # 庄家连庄 (Honba +1, 局数不变)
                pass  # 保持原样
            else:
                # 庄家不听牌，庄家轮换
                next_dealer_index = (current_dealer_index + 1) % game_state.num_players

                if next_dealer_index == game_state.initial_dealer_index:
                    next_round_wind += 1
                    next_round_number = 1
                else:
                    next_round_number += 1

                next_honba = 0  # 庄家轮换清零本场数

        # 3. 计算立直棒的转移 (胜利者获得所有立直棒，流局保留)
        current_riichi_sticks = game_state.riichi_sticks
        next_riichi_sticks = current_riichi_sticks
        if end_type in {"TSUMO", "RON"}:
            # 获胜者拿走所有立直棒
            next_riichi_sticks = 0
            # TODO: 将 current_riichi_sticks 的点数加到 winner_index 的最终得分中
        # else: 流局，立直棒保留到下一局

        # 4. 检查是否达到最大局数 (Game Over Check)
        # TODO: 检查 next_round_wind 是否超过 self.max_game_wind，
        # 如果超过，且当前是最后一局，则设置游戏结束标志

        return {
            "next_dealer_index": next_dealer_index,
            "next_round_wind": next_round_wind,
            "next_round_number": next_round_number,
            "next_honba": next_honba,
            "next_riichi_sticks": next_riichi_sticks,
            # TODO: "game_is_over": self.is_game_over(game_state, next_round_wind, next_round_number),
        }

    def is_game_over(self, game_state: "GameState") -> bool:
        """
        【流程】检查总游戏是否结束 (飞人、局数完成等)。

        职责：
        1. 检查是否有玩家分数低于 0 (飞人)。
        2. 检查是否达到了配置的最大场风和局数。
        """
        # TODO: 实现游戏结束的判定逻辑 (参考原 rules_engine.py 中的逻辑)
        # 1. 检查是否有人被飞 (PlayerState.score < 0)
        # 2. 检查当前场风和局数是否达到 self.max_game_wind 且完成了该场风的最后一局
        return False
