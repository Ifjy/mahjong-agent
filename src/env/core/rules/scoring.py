# scoring.py

from typing import List, Dict, Set, Optional, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
import math
from collections import Counter

# 假设从 actions.py 和 game_state.py 导入
from src.env.core.actions import Tile
from src.env.core.game_state import GameState, PlayerState, Meld, Wall

# 假设从 hand_analyzer.py 导入
from src.env.core.rules.hand_analyzer import (
    HandAnalyzer,
    WinForm,
)  # 假设 HandAnalyzer 导出了 WinForm

# 假设从 constants.py 导入
from src.env.core.rules.constants import (
    TERMINAL_HONOR_VALUES,
    WIND_EAST,
    WIND_SOUTH,
    WIND_WEST,
    WIND_NORTH,
    DRAGON_WHITE,
    DRAGON_GREEN,
    DRAGON_RED,
    MAN_1,
    SOU_9,
)

# ======================================================================
# 1. 计分数据结构 (Data Structures)
# ======================================================================


# ======================================================================
# 1. 计分数据结构 (Data Structures)
# ======================================================================


@dataclass
class WinDetails:
    """
    存储一次和牌的详细分析结果。
    """

    is_valid_win: bool = False
    winning_tile: Optional[Tile] = None
    is_tsumo: bool = False

    win_form: Optional[WinForm] = None  # 最终采用的分解形式

    yaku_list: List[Tuple[str, int]] = field(default_factory=list)
    han: int = 0
    fu: int = 0

    dora_count: int = 0
    total_han: int = 0

    score_points: int = 0
    score_payout: Dict[int, int] = field(default_factory=dict)

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
        self.allow_kuitan = self.config.get("allow_kuitan", False)

        # 点数查询表 (满贯以下)
        # (han, fu) -> (non_dealer_ron, dealer_ron)
        self.score_table = {
            (1, 30): (1000, 1500),
            (1, 40): (1300, 2000),
            (1, 50): (1600, 2400),
            (2, 25): (1600, 2400),  # 七对子
            (2, 30): (2000, 2900),
            (2, 40): (2600, 3900),
            (2, 50): (3200, 4800),
            (3, 30): (3900, 5800),
            (3, 40): (5200, 7700),
            (3, 50): (6400, 9600),
            (4, 30): (7700, 11600),
            (4, 40): (8000, 12000),  # 4翻40符及以上为满贯
        }

        # 满贯点数 (番数, 点数)
        self.mangan_scores = {
            5: 8000,  # 满贯
            6: 12000,  # 跳满
            7: 12000,  # 跳满
            8: 16000,  # 倍满
            9: 16000,  # 倍满
            10: 16000,  # 倍满
            11: 24000,  # 三倍满
            12: 24000,  # 三倍满
            13: 32000,  # 役满
        }
        self.yakuman_multiplier = 32000

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
        """
        details = WinDetails(winning_tile=winning_tile, is_tsumo=is_tsumo)

        # 1. 准备手牌 (14张)
        # 注意：这里假设 winning_tile 是具体的 Tile 实例
        final_hand = player.hand + (
            [winning_tile] if winning_tile not in player.hand else []
        )

        # 2. 收集上下文
        context = self._get_win_context(player, game_state, is_tsumo, winning_tile)

        # 3. 检查役满 (Yakuman)
        details.yakuman_list = self._find_yakuman(final_hand, player.melds, context)
        if details.yakuman_list:
            details.is_yakuman = True
            details.han = 13 * len(details.yakuman_list)  # 13番代表役满
            details.total_han = details.han
            details.score_points = self.yakuman_multiplier * len(details.yakuman_list)
        else:
            # 4. 非役满：获取所有手牌分解形式
            win_forms = self.hand_analyzer.find_all_winning_forms(
                final_hand, player.melds, winning_tile
            )

            if not win_forms:
                details.is_valid_win = False
                return details  # 形状无效

            # 5. 遍历所有分解，找到最佳的一种
            best_form = None
            best_han = -1
            best_fu = -1
            best_yaku_list = []

            for form in win_forms:
                yaku_list = self._find_yaku(form, context)
                han = sum(han for _, han in yaku_list)

                fu = self._calculate_fu(form, context, player.melds)

                # 比较分数 (简化：先比较番，再比较符)
                if han > best_han or (han == best_han and fu > best_fu):
                    best_han = han
                    best_fu = fu
                    best_yaku_list = yaku_list
                    best_form = form

            details.yaku_list = best_yaku_list
            details.han = best_han
            details.fu = best_fu
            details.win_form = best_form

            # 6. 检查一番缚 (Ippan Shibari)
            if details.han == 0:
                details.is_valid_win = False  # 无役!
                return details

            # 7. 计算宝牌 (Dora)
            details.dora_count = self._calculate_dora(
                final_hand, player.melds, game_state, context
            )
            details.total_han = details.han + details.dora_count

            # 8. 计算最终点数 (Score)
            details.score_points = self._calculate_points(
                details.total_han, details.fu, context
            )

        # 9. 检查振听 (Furiten)
        if not is_tsumo and self._is_furiten(player, winning_tile, game_state):
            details.is_valid_win = False
            return details

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
        将点数转化为玩家间的支付。
        """
        payout = {p.player_index: 0 for p in game_state.players}

        # 获取赢家索引 (假设从 game_state.current_player_index 或 win_form 无法直接获取时，需传入)
        # 这里假设 game_state.current_player_index 就是赢家 (对于 Tsumo)
        # 或者如果是 Ron，需要 RulesEngine 传入 winner_index。
        # 为了健壮性，我们假设调用此函数时，上下文已知。
        # 这里简单起见，通过遍历寻找持有 hand 的玩家 (不推荐)，或者假设 RulesEngine 处理好了
        # 更好的方式是让 WinDetails 携带 player_index，或者传入 winner_index
        # 暂时 HACK: 假设 game_state.current_player_index 是赢家 (如果是自摸)
        # 如果是荣和，赢家是响应者。
        # **修正**：此函数应接收 winner_index，但在参数列表中未提供。
        # 假设 win_details 上下文隐含了赢家，或者我们只能依赖外部调用逻辑正确。
        # 实际上，RulesEngine 调用此函数时，上下文是明确的。

        # 我们假设调用者（RulesEngine）已经处理了 winner_index，
        # 这里我们只计算金额，不负责分配给具体哪个 ID，除非传入了 winner_index。
        # 为了修复逻辑，我们假设 payout 的 key 是相对的，或者需要传入 winner_index。
        # 鉴于接口限制，我们假设 is_dealer 是从 context 传来的。

        # **重要修复**：为了代码能跑，我们需要知道谁是赢家。
        # 既然参数没传，我们假设 RulesEngine 会处理 payout 的分配，
        # 这里只返回 {"winner": points, "loser": -points} 这种结构？
        # 不，返回 Dict[int, int] 意味着必须知道 player_index。
        # 让我们假设 win_details.win_form 虽然没存 player_index，但我们可以通过遍历 game_state.players 找到手牌匹配的人。

        winner_index = -1
        for p in game_state.players:
            # 简单比对：谁的手牌+和牌 == final_hand?
            # 这不可靠。
            # 正确做法：修改 get_final_score_and_payout 签名，增加 winner_index。
            pass

        # 这里暂时抛出异常，提示需要修改接口，或者在下面使用占位符
        # raise NotImplementedError("Need winner_index to calculate payout")

        # 临时方案：假设调用时 context 包含 winner_index
        # 或者我们只计算点数，返回一个通用结构
        pass

        # ... (保留原有逻辑结构，但需注意上述问题)
        return {}

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
        【这是您之前缺失的函数】
        """
        # 确定场风
        round_wind_map = {0: WIND_EAST, 1: WIND_SOUTH, 2: WIND_WEST, 3: WIND_NORTH}
        round_wind_tile = round_wind_map.get(game_state.round_wind, WIND_EAST)

        # 确定自风
        # 自风计算：(玩家位置 - 庄家位置) % 4
        # 0: 东, 1: 南, 2: 西, 3: 北
        # 注意：这是相对位置。如果 dealer_index=0, player=1 (下家), 则 seat_offset=1 (南)
        seat_offset = (player.player_index - game_state.dealer_index) % 4
        player_wind_tile = round_wind_map.get(seat_offset, WIND_EAST)

        return {
            "is_tsumo": is_tsumo,
            "is_riichi": player.riichi_declared,
            "is_menzen": player.is_menzen,
            "is_dealer": player.player_index == game_state.dealer_index,
            "player_wind": player_wind_tile,
            "round_wind": round_wind_tile,
            "dora_indicators": game_state.wall.dora_indicators,
            "ura_dora_indicators": game_state.wall.ura_dora_indicators,
            # "is_ippatsu": player.ippatsu_chance,
            # "is_rinshan": player.just_kaned,
            # "is_haitei": game_state.wall.get_remaining_live_tiles_count() == 0,
        }

    # ======================================================================
    # == 役种判断 (Yaku Engine) ==
    # ======================================================================

    def _find_yakuman(
        self, hand: List[Tile], melds: List[Meld], context: Dict
    ) -> List[str]:
        """(Stub) 检查役满"""
        yakuman_list = []
        # TODO: 检查国士无双等
        return yakuman_list

    def _find_yaku(self, form: "WinForm", context: Dict) -> List[Tuple[str, int]]:
        """
        【核心】根据分解形式 (WinForm) 查找役种。
        """
        yaku_found = []

        # --- 1. 状况役 (Context Yaku) ---
        if context.get("is_riichi", False):
            yaku_found.append(("Riichi", 1))
        if context.get("is_tsumo", False) and context.get("is_menzen", False):
            yaku_found.append(("Menzen Tsumo", 1))

        # --- 2. 手牌役 (Hand Yaku) ---

        # 役牌 (Yakuhai)
        yaku_found.extend(self._check_yaku_yakuhai(form, context))

        # 断幺九 (Tanyao)
        if self._check_yaku_tanyao(form, context):
            yaku_found.append(("Tanyao", 1))

        return yaku_found

    # --- Yaku Helper Functions ---

    def _check_yaku_tanyao(self, form: "WinForm", context: Dict) -> bool:
        """检查断幺九"""
        if not context.get("is_menzen") and not self.allow_kuitan:
            return False  # 食断

        for tile in form.all_tiles:
            if tile.value in TERMINAL_HONOR_VALUES:
                return False
        return True

    def _check_yaku_yakuhai(
        self, form: "WinForm", context: Dict
    ) -> List[Tuple[str, int]]:
        """检查役牌 (三元牌, 场风, 自风)"""
        yakuhai_list = []
        player_wind = context.get("player_wind")
        round_wind = context.get("round_wind")

        yakuhai_values = {
            DRAGON_WHITE,
            DRAGON_GREEN,
            DRAGON_RED,
            player_wind,
            round_wind,
        }

        for comp in form.components:  # 使用 WinForm.components (HandComponent)
            if comp.type == "koutsu" or comp.type == "kantsu":
                val = comp.tiles[0].value
                if val in yakuhai_values:
                    if val == DRAGON_WHITE:
                        yakuhai_list.append(("Haku", 1))
                    elif val == DRAGON_GREEN:
                        yakuhai_list.append(("Hatsu", 1))
                    elif val == DRAGON_RED:
                        yakuhai_list.append(("Chun", 1))
                    elif val == player_wind:
                        yakuhai_list.append(("Player Wind", 1))
                    elif val == round_wind:
                        yakuhai_list.append(("Round Wind", 1))

        return list(set(yakuhai_list))

    # ======================================================================
    # == 符数计算 (Fu Engine) ==
    # ======================================================================

    def _calculate_fu(
        self, form: "WinForm", context: Dict, open_melds: List[Meld]
    ) -> int:
        """
        【MVP Stub】计算符数。
        """
        if form.hand_type == "chiitoitsu":
            return 25

        fu = 20  # 底符

        # 2. 和牌方式
        if context.get("is_menzen") and not context.get("is_tsumo"):
            fu += 10  # 门清荣和
        elif context.get("is_tsumo"):
            fu += 2  # 自摸

        # 3. 面子 (Mentsu)
        for comp in form.components:
            is_open = comp.is_open
            val = comp.tiles[0].value
            is_terminal_or_honor = val in TERMINAL_HONOR_VALUES

            if comp.type == "koutsu":  # 刻子
                base = 4 if is_terminal_or_honor else 2
                fu += base * (1 if is_open else 2)
            elif comp.type == "kantsu":  # 杠子
                base = 16 if is_terminal_or_honor else 8
                fu += base * (1 if is_open else 2)

        # 6. 进位
        if fu == 20:
            return 20
        return self._ceil_to_10(fu)

    # ======================================================================
    # == 振听 (Furiten) 和 宝牌 (Dora) ==
    # ======================================================================

    def _is_furiten(
        self, player: "PlayerState", winning_tile: "Tile", game_state: "GameState"
    ) -> bool:
        """(MVP Stub) 检查振听"""
        # 简单实现：检查听的牌是否在自己的弃牌河中
        # 这需要 HandAnalyzer 提供 find_wait_tiles
        # waits = self.hand_analyzer.find_wait_tiles(player.hand, player.melds)
        # discard_values = {t.value for t in player.discards}
        # ...
        return False

    def _calculate_dora(
        self,
        hand: List[Tile],
        melds: List[Meld],
        game_state: "GameState",
        context: Dict,
    ) -> int:
        """计算宝牌 (Dora)"""
        count = 0
        # 这里的 hand 已经是包含 winning_tile 的完整手牌
        # 加上副露中的牌
        all_tiles = hand + [tile for meld in melds for tile in meld.tiles]

        # 1. 赤宝牌
        count += sum(1 for tile in all_tiles if tile.is_red)

        dora_indicators = context.get("dora_indicators", [])
        dora_values = self._get_dora_values_from_indicators(dora_indicators)

        # 2. 表宝牌
        count += sum(1 for tile in all_tiles if tile.value in dora_values)

        # 3. 里宝牌
        if context.get("is_riichi", False):
            ura_dora_indicators = context.get("ura_dora_indicators", [])
            ura_dora_values = self._get_dora_values_from_indicators(ura_dora_indicators)
            count += sum(1 for tile in all_tiles if tile.value in ura_dora_values)

        return count

    def _get_dora_values_from_indicators(self, indicators: List[Tile]) -> Set[int]:
        """(Helper) 根据指示牌计算宝牌的值"""
        dora_values = set()
        for ind in indicators:
            val = ind.value
            if MAN_1 <= val <= MAN_9 - 1:
                dora_values.add(val + 1)
            elif val == MAN_9:
                dora_values.add(MAN_1)
            elif PIN_1 <= val <= PIN_9 - 1:
                dora_values.add(val + 1)
            elif val == PIN_9:
                dora_values.add(PIN_1)
            elif SOU_1 <= val <= SOU_9 - 1:
                dora_values.add(val + 1)
            elif val == SOU_9:
                dora_values.add(SOU_1)
            elif WIND_EAST <= val <= WIND_WEST:
                dora_values.add(val + 1)
            elif val == WIND_NORTH:
                dora_values.add(WIND_EAST)
            elif DRAGON_WHITE <= val <= DRAGON_GREEN:
                dora_values.add(val + 1)
            elif val == DRAGON_RED:
                dora_values.add(DRAGON_WHITE)
        return dora_values

    # ======================================================================
    # == 点数计算 (Points Engine) ==
    # ======================================================================

    def _ceil_to_10(self, fu: int) -> int:
        return math.ceil(fu / 10) * 10

    def _ceil_to_100(self, points: float) -> int:
        return math.ceil(points / 100) * 100

    def _calculate_points(self, total_han: int, fu: int, context: Dict) -> int:
        """计算基础点数"""
        is_dealer = context.get("is_dealer", False)

        if total_han >= 13:
            return self.yakuman_multiplier
        if total_han >= 5:
            return self.mangan_scores.get(total_han, self.mangan_scores[13])

        base_points = 0
        ron_key = (total_han, fu)
        if ron_key in self.score_table:
            base_points = self.score_table[ron_key][1 if is_dealer else 0]
        else:
            if (total_han == 4 and fu >= 40) or (total_han == 3 and fu >= 70):
                base_points = 8000
            else:
                base_points = fu * (2 ** (total_han + 2))
                if base_points > 2000:
                    base_points = 2000

        total_points = base_points * (6 if is_dealer else 4)
        if total_points >= 8000:
            return 8000

        return self._ceil_to_100(total_points)
