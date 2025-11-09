# 负责所有的计分逻辑：役种判断 (Yaku)、番数 (Han)、符数 (Fu) 计算、最终得分结算。
# 类/函数,函数头,职责描述
# Class,Scoring,"__init__(self, hand_analyzer, config)"
# Entry,"calculate_win_details(self, player: PlayerState, winning_tile: Tile, game_state: GameState) -> WinDetails",主入口：计算并返回完整的和牌详情 (Yaku、Han、Fu、Dora、是否有效和牌）。
# Yaku,"find_yaku(self, player: PlayerState, winning_tile: Tile, win_form: WinForm, game_state: GameState) -> List[YakuResult]",根据手牌的特定分解形式，判断所有役种并计算番数。
# Fu,"calculate_fu(self, player: PlayerState, winning_tile: Tile, win_form: WinForm, game_state: GameState) -> int",计算符数（基础符、面子符、对子符、边/坎/单骑听符）。
# Payout,"get_final_score_and_payout(self, win_details: WinDetails, game_state: GameState, loser_index: Optional[int]) -> Dict[str, Any]",将番数和符数转化为最终的点数，并计算各玩家的得分支付情况。
# scoring.py

from typing import List, Dict, Set, Optional, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
import math

# 假设从 actions.py 和 game_state.py 导入
from src.env.core.actions import Tile
from src.env.core.game_state import GameState, PlayerState, Meld

# 假设从 hand_analyzer.py 导入
# from .hand_analyzer import HandAnalyzer

# 假设从 constants.py 导入
# from .constants import TERMINAL_HONOR_VALUES, WIND_EAST, ...

if TYPE_CHECKING:
    # HACK: 用于类型提示
    from hand_analyzer import HandAnalyzer
    from game_state import GameState, PlayerState, Meld
    from actions import Tile

# ======================================================================
# 1. 计分数据结构 (Data Structures)
# ======================================================================


@dataclass
class WinDetails:
    """
    存储一次和牌的详细分析结果。
    (基于 rules_engine.py 的 WinDetails 结构)
    """

    is_valid_win: bool = False  # 是否是规则上允许的和牌 (有役, 非振听)
    winning_tile: Optional[Tile] = None
    is_tsumo: bool = False

    yaku_list: List[Tuple[str, int]] = field(
        default_factory=list
    )  # 役种列表 (名称, 番数)
    han: int = 0  # 总番数 (不含宝牌)
    fu: int = 0  # 符数

    dora_count: int = 0  # 宝牌数 (表/里/赤)
    total_han: int = 0  # 总番数 (役 + 宝牌)

    score_points: int = 0  # 最终点数 (例如 8000)
    score_payout: Dict[int, int] = field(default_factory=dict)  # 玩家的点数变化

    is_yakuman: bool = False
    yakuman_list: List[str] = field(default_factory=list)


# ======================================================================
# 2. 计分模块 (Scoring Class)
# ======================================================================


class Scoring:
    """
    负责役种判断 (Yaku)、番数 (Han)、符数 (Fu) 和最终点数计算。
    """

    def __init__(self, hand_analyzer: "HandAnalyzer", config: Dict):
        """
        构造函数：依赖 HandAnalyzer (用于获取手牌分解形式)。
        """
        self.hand_analyzer = hand_analyzer
        self.config = config or {}

        # TODO: 从 config 中加载规则 (例如 kuitan: 是否食断)
        self.allow_kuitan = self.config.get("allow_kuitan", False)

    # ======================================================================
    # == 公共 API (Public API) ==
    # ======================================================================

    def calculate_win_details(
        self,
        player: "PlayerState",
        winning_tile: "Tile",
        is_tsumo: bool,
        game_state: "GameState",
    ) -> "WinDetails":
        """
        【主入口】计算完整的和牌详情。
        这是 ActionValidator 判断“有役”时调用的核心。
        """
        details = WinDetails(winning_tile=winning_tile, is_tsumo=is_tsumo)

        # 1. 准备手牌 (14张)
        final_hand = player.hand + (
            [winning_tile] if winning_tile not in player.hand else []
        )

        # 2. 检查基本形状 (如果 HandAnalyzer 说不行，直接返回无效)
        if not self.hand_analyzer.check_win_shape(final_hand, player.melds):
            details.is_valid_win = False
            return details  # 形状无效

        # 3. 收集上下文 (用于役种判断)
        context = self._get_win_context(player, game_state, is_tsumo, winning_tile)

        # 4. TODO: 检查役满 (Yakuman)
        # details.yakuman_list = self._find_yakuman(final_hand, player.melds, context)
        if details.yakuman_list:
            # (役满逻辑暂不实现)
            pass

        # 5. 查找常规役种 (Yaku)
        # TODO: 优化 - 需要 HandAnalyzer 返回最佳分解形式 (WinForm)
        details.yaku_list = self._find_yaku(final_hand, player.melds, context)

        # 6. 计算番数 (Han)
        details.han = sum(han for _, han in details.yaku_list)

        # 7. 检查一番缚 (Ippan Shibari) - 核心!
        if details.han == 0:
            details.is_valid_win = False  # 无役!
            return details  # ActionValidator 将据此拒绝和牌

        # 8. 计算符数 (Fu) - (MVP Stub)
        details.fu = self._calculate_fu(final_hand, player.melds, context)

        # 9. 计算宝牌 (Dora)
        details.dora_count = self._calculate_dora(
            final_hand, player.melds, game_state, context
        )
        details.total_han = details.han + details.dora_count

        # 10. TODO: 计算最终点数 (Score)
        # details.score_points = self._calculate_points(details.total_han, details.fu, context)

        # 11. TODO: 检查振听 (Furiten)
        # if self.is_furiten(player, winning_tile, game_state):
        #    details.is_valid_win = False
        #    return details

        details.is_valid_win = True
        return details

    def is_valid_win(
        self,
        player: "PlayerState",
        winning_tile: "Tile",
        is_tsumo: bool,
        game_state: "GameState",
    ) -> bool:
        """
        【ActionValidator调用的辅助函数】
        检查和牌是否合法 (有役 + 非振听)。
        """
        details = self.calculate_win_details(player, winning_tile, is_tsumo, game_state)
        return details.is_valid_win

    def get_final_score_and_payout(
        self,
        win_details: "WinDetails",
        game_state: "GameState",
        loser_index: Optional[int],
    ) -> Dict[int, int]:
        """
        【RulesEngine调用的结算函数】
        (MVP Stub) 暂不实现详细结算，这对 ActionValidator 不是必需的。
        """
        # TODO: 实现点数表查找和支付计算逻辑
        payout = {}
        return payout

    # ======================================================================
    # == 内部辅助 (Internal Helpers) ==
    # ======================================================================

    def _get_win_context(
        self,
        player: "PlayerState",
        game_state: "GameState",
        is_tsumo: bool,
        win_tile: "Tile",
    ) -> Dict:
        """
        (辅助) 收集所有役种判断所需的上下文信息。
        (基于 rules_engine.py 的 _get_win_context)
        """
        return {
            "is_tsumo": is_tsumo,
            "is_riichi": player.riichi_declared,
            "is_menzen": player.is_menzen,
            "player_wind": player.seat_wind,
            "round_wind": game_state.round_wind,
            # "is_ippatsu": player.ippatsu_chance, # (需要 PlayerState 支持)
            # "is_rinshan": player.just_kaned, # (需要 PlayerState 支持)
            # "is_haitei": game_state.wall.get_remaining_live_tiles_count() == 0, # (需要 Wall 支持)
        }

    def _find_yaku(
        self, hand: List[Tile], melds: List[Meld], context: Dict
    ) -> List[Tuple[str, int]]:
        """
        【MVP 核心】查找役种。我们只实现最简单的几个。
        """
        yaku_found = []

        # --- MVP Yaku 1: 立直 (Riichi) ---
        if context.get("is_riichi", False):
            yaku_found.append(("Riichi", 1))

        # --- MVP Yaku 2: 门前清自摸和 (Menzen Tsumo) ---
        if context.get("is_tsumo", False) and context.get("is_menzen", False):
            yaku_found.append(("Menzen Tsumo", 1))

        # --- TODO: 添加所有其他役种 ---
        # TODO: 断幺九 (Tanyao) - (需要检查 self.allow_kuitan)
        # TODO: 役牌 (Yakuhai) - (白、发、中、场风、自风)
        # TODO: 平和 (Pinfu)
        # TODO: 一盃口 (Iipeikou)
        # TODO: 三色同顺 (Sanshoku Doujun)
        # ... (等等)

        return yaku_found

    def _calculate_fu(self, hand: List[Tile], melds: List[Meld], context: Dict) -> int:
        """
        【MVP Stub】计算符数。
        暂不实现复杂计算，返回一个合理的默认值。
        (七对子 25, 平和 20/30, 其他 30+)
        """
        # TODO: 完整实现符数计算 (底符, 面子, 雀头, 听型, 自摸/荣和)

        # 临时规则:
        if self.hand_analyzer._is_seven_pairs_raw(hand, melds):
            return 25

        # TODO: 检查是否平和 (Pinfu)

        return 30  # 默认 30 符 (最常见的基础)

    def _calculate_dora(
        self,
        hand: List[Tile],
        melds: List[Meld],
        game_state: "GameState",
        context: Dict,
    ) -> int:
        """
        【MVP 实现】计算宝牌 (Dora)。这个逻辑是独立的，可以实现。
        """
        count = 0
        all_tiles = hand + [tile for meld in melds for tile in meld.get("tiles", [])]

        # 1. 赤宝牌 (Red Dora)
        count += sum(1 for tile in all_tiles if tile.is_red)

        # 2. 表宝牌 (Dora)
        # (基于 rules_engine.py 的 _get_dora_value_for_tile 逻辑)
        dora_indicators = game_state.wall.dora_indicators
        # TODO: 实现 _get_dora_value_for_tile 逻辑 (计算指示牌的下一张)
        # count += sum(self._get_dora_value_for_tile(tile, dora_indicators) for tile in all_tiles)

        # 3. 里宝牌 (Ura Dora) - 仅立直时
        if context.get("is_riichi", False):
            ura_dora_indicators = game_state.wall.ura_dora_indicators
            # TODO: 实现 _get_dora_value_for_tile 逻辑
            # count += sum(self._get_dora_value_for_tile(tile, ura_dora_indicators) for tile in all_tiles)

        return count
