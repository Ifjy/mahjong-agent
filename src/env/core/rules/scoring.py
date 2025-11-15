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


@dataclass
class WinDetails:
    """
    存储一次和牌的详细分析结果。
    (基于提供的 scoring.py)
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
            # TODO: 填充完整的点数表
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
            # **[优化]** 调用 hand_analyzer 获取所有分解
            win_forms = self.hand_analyzer.find_all_winning_forms(
                final_hand, player.melds
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

                # TODO: Pinfu (平和) 役种会影响符数计算，需要在这里特殊处理
                # is_pinfu = any(y[0] == "Pinfu" for y in yaku_list)

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
        # 振听只在荣和 (Ron) 时检查
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
        winner_index = win_details.win_form.player_index  # 假设 WinForm 携带
        winner = game_state.players[winner_index]
        is_dealer = winner.is_dealer  # 假设 PlayerState 携带
        points = win_details.score_points

        # 添加本场 (Honba) 和 立直棒 (Riichi sticks)
        honba_points = game_state.honba * (300 if win_details.is_tsumo else 100)
        riichi_stick_points = game_state.riichi_sticks * 1000

        total_win_points = points + honba_points + riichi_stick_points

        if win_details.is_tsumo:
            # --- 自摸 (Tsumo) ---
            if is_dealer:
                # 庄家自摸，每家支付 1/3
                payment = self._ceil_to_100(points / 3) + (honba_points / 3)
                for p in game_state.players:
                    if p.player_index != winner_index:
                        payout[p.player_index] = -payment
            else:
                # 闲家自摸
                dealer_payment = self._ceil_to_100(points / 2) + (
                    honba_points / 3
                )  # 庄家付一半
                non_dealer_payment = self._ceil_to_100(points / 4) + (
                    honba_points / 3
                )  # 闲家付1/4
                for p in game_state.players:
                    if p.player_index == winner_index:
                        continue
                    if p.is_dealer:
                        payout[p.player_index] = -dealer_payment
                    else:
                        payout[p.player_index] = -non_dealer_payment
            payout[winner_index] = total_win_points

        else:
            # --- 荣和 (Ron) ---
            if loser_index is None:
                raise ValueError("Ron payout requires a loser_index.")
            payout[loser_index] = -(points + honba_points)
            payout[winner_index] = total_win_points

        win_details.score_payout = payout
        return payout

    # ======================================================================
    # == 役种判断 (Yaku Engine) ==
    # ======================================================================

    def _find_yakuman(
        self, hand: List[Tile], melds: List[Meld], context: Dict
    ) -> List[str]:
        """(Stub) 检查役满"""
        yakuman_list = []
        # TODO: 检查国士无双
        # if self.hand_analyzer._is_kokushi_raw(hand, melds):
        #     yakuman_list.append("Kokushi Musou")
        # TODO: 检查四暗刻, 大三元, 字一色, 绿一色, 清老头, 四杠子, 九莲宝灯, 天和, 地和
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
        # TODO: Ippatsu, Haitei, Houtei, Rinshan, Chankan

        # --- 2. 手牌役 (Hand Yaku) ---

        # 役牌 (Yakuhai)
        yaku_found.extend(self._check_yaku_yakuhai(form, context))

        # 断幺九 (Tanyao)
        if self._check_yaku_tanyao(form, context):
            yaku_found.append(("Tanyao", 1))

        # TODO: 平和 (Pinfu) - 复杂
        # if self._check_yaku_pinfu(form, context):
        #     yaku_found.append(("Pinfu", 1))

        # TODO: 一盃口 (Iipeikou) / 二盃口 (Ryanpeikou)
        # TODO: 三色同顺 (Sanshoku Doujun)
        # TODO: 一气贯通 (Ikkitsuukan)
        # TODO: 混全带幺九 (Chanta)
        # TODO: 对对和 (Toitoi)
        # TODO: 三暗刻 (San Ankou)
        # TODO: 三杠子 (San Kantsu)
        # TODO: 三色同刻 (Sanshoku Doukou)
        # TODO: 混老头 (Honroutou)
        # TODO: 小三元 (Shousangen)
        # TODO: 混一色 (Honitsu)
        # TODO: 清一色 (Chinitsu)

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

        for meld in form.melds:  # 遍历所有面子 (刻子/杠)
            if meld.type == "koutsu" or meld.type == "kantsu":
                val = meld.tiles[0].value
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

        # 检查雀头 (Pair)
        pair_val = form.pair.tiles[0].value
        if pair_val in yakuhai_values:
            # TODO: 连风牌 (Double Wind) 算 2 符, 但只算 1 役? (需要确认规则)
            # 这里我们只检查役
            pass

        return list(set(yakuhai_list))  # 去重 (例如东场东家)

    # ======================================================================
    # == 符数计算 (Fu Engine) ==
    # ======================================================================

    def _calculate_fu(
        self, form: "WinForm", context: Dict, open_melds: List[Meld]
    ) -> int:
        """
        【MVP Stub】计算符数。
        """
        # 1. 特殊情况
        if form.is_chiitoitsu:
            return 25

        # TODO: 检查平和 (Pinfu)
        # if is_pinfu:
        #    return 20 (自摸) or 30 (荣和)

        fu = 20  # 1. 底符 (Fuu)

        # 2. 和牌方式 (Agari)
        if context.get("is_menzen") and not context.get("is_tsumo"):
            fu += 10  # 门清荣和
        elif context.get("is_tsumo"):
            fu += 2  # 自摸 (平和除外)

        # 3. 听牌型 (Machi)
        # TODO: 依赖 WinForm 分析听型
        # if form.wait_type in ["kanchan", "penchan", "tanki"]:
        #    fu += 2

        # 4. 雀头 (Jantou)
        # TODO: 检查雀头是否为役牌
        # pair_val = form.pair.tiles[0].value
        # if pair_val in [DRAGONS, player_wind, round_wind]:
        #    fu += 2

        # 5. 面子 (Mentsu)
        for meld in form.melds:
            is_open = meld.id in [m.id for m in open_melds]  # 检查是否为副露
            val = meld.tiles[0].value
            is_terminal_or_honor = val in TERMINAL_HONOR_VALUES

            if meld.type == "koutsu":  # 刻子
                base = 4 if is_terminal_or_honor else 2
                fu += base * (1 if is_open else 2)  # 明刻 / 暗刻
            elif meld.type == "kantsu":  # 杠子
                base = 16 if is_terminal_or_honor else 8
                fu += base * (1 if is_open else 2)  # 明杠 / 暗杠

        # 6. 进位
        if fu == 20:
            return 20  # 平和自摸特例
        return self._ceil_to_10(fu)

    # ======================================================================
    # == 振听 (Furiten) 和 宝牌 (Dora) ==
    # ======================================================================

    def _is_furiten(
        self, player: "PlayerState", winning_tile: "Tile", game_state: "GameState"
    ) -> bool:
        """
        (MVP Stub) 检查振听 (仅荣和时)。
        """
        # TODO: 振听逻辑需要 HandAnalyzer.find_wait_tiles()
        # 1. 获取所有听牌
        # waits = self.hand_analyzer.find_wait_tiles(player.hand, player.melds)
        # if not waits: return False # 没听牌 (理论上不应发生)

        # 2. 检查听的牌是否在弃牌河
        # discard_values = {t.value for t in player.discard_pile}
        # if any(w.value in discard_values for w in waits):
        #    return True # 永久振听

        # 3. 检查同巡振听
        # TODO: 需要 GameState 跟踪同巡过手的牌

        return False  # 暂时假设不振听

    def _calculate_dora(
        self,
        hand: List[Tile],
        melds: List[Meld],
        game_state: "GameState",
        context: Dict,
    ) -> int:
        """
        计算宝牌 (Dora)。
        """
        count = 0
        all_tiles = hand + [tile for meld in melds for tile in meld.get("tiles", [])]

        # 1. 赤宝牌 (Red Dora)
        count += sum(1 for tile in all_tiles if tile.is_red)

        dora_indicators = game_state.wall.dora_indicators
        dora_values = self._get_dora_values_from_indicators(dora_indicators)

        # 2. 表宝牌 (Dora)
        count += sum(1 for tile in all_tiles if tile.value in dora_values)

        # 3. 里宝牌 (Ura Dora) - 仅立直时
        if context.get("is_riichi", False):
            ura_dora_indicators = game_state.wall.ura_dora_indicators
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
        """符数向上取整到10"""
        return math.ceil(fu / 10) * 10

    def _ceil_to_100(self, points: float) -> int:
        """点数向上取整到100"""
        return math.ceil(points / 100) * 100

    def _calculate_points(self, total_han: int, fu: int, context: Dict) -> int:
        """
        (MVP Stub) 根据番数和符数计算基础点数。
        """
        is_dealer = context.get("is_dealer", False)

        # 1. 查满贯表
        if total_han >= 13:
            return self.yakuman_multiplier  # 役满
        if total_han >= 5:
            return self.mangan_scores.get(total_han, self.mangan_scores[13])

        # 2. 查番数符数表
        base_points = 0
        ron_key = (total_han, fu)
        if ron_key in self.score_table:
            base_points = self.score_table[ron_key][1 if is_dealer else 0]
        else:
            # 4翻40符 或 3翻70符 以上为满贯
            if (total_han == 4 and fu >= 40) or (total_han == 3 and fu >= 70):
                base_points = 8000
            else:
                # 默认计算 (a = fu * 2^(han+2))
                base_points = fu * (2 ** (total_han + 2))
                if base_points > 2000:  # 满贯封顶
                    base_points = 2000

        # 基础点数 * 4 (闲家) 或 * 6 (庄家)
        total_points = base_points * (6 if is_dealer else 4)

        # 满贯封顶
        if total_points >= 8000:
            return 8000

        return self._ceil_to_100(total_points)
