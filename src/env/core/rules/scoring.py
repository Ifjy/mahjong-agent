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
    MAN_9,
    PIN_1,
    PIN_9,
    SOU_1,
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

        # 1. 准备手牌 (14张, 含 winning_tile)
        # 手牌(不含副露)应为 13 张, 加 winning_tile 凑 14 张。
        # 注意: 不能用 `winning_tile in player.hand` 判断 (同 value 的牌会误判),
        # 而是按张数补足。
        meld_tile_count = sum(len(m.tiles) for m in player.melds)
        expected_hand_len = 14 - meld_tile_count
        if len(player.hand) == expected_hand_len - 1:
            # 手牌缺一张 (标准情况: 荣和/自摸前手牌 13 张)
            final_hand = player.hand + [winning_tile]
        elif len(player.hand) == expected_hand_len:
            # 手牌已 14 张 (winning_tile 可能已并入, 如自摸时 drawn_tile 已 append)
            final_hand = list(player.hand)
        else:
            # 异常张数, 兜底: 强制补 winning_tile
            final_hand = player.hand + [winning_tile]

        # 2. 收集上下文
        context = self._get_win_context(player, game_state, is_tsumo, winning_tile)

        # 3. 获取所有手牌分解形式 (役满和普通役都需要)
        win_forms = self.hand_analyzer.find_all_winning_forms(
            final_hand, player.melds, winning_tile
        )
        if not win_forms:
            details.is_valid_win = False
            return details  # 形状无效

        # 4. 先检查役满 (Yakuman): 状况役满 + 对每个 form 的结构性役满
        all_yakuman: List[str] = list(self._find_yakuman(final_hand, player.melds, context))
        if not all_yakuman:
            for form in win_forms:
                all_yakuman.extend(self._find_yakuman_for_form(form, context))
                if all_yakuman:
                    break  # 命中任一即役满

        if all_yakuman:
            details.yakuman_list = all_yakuman
            details.is_yakuman = True
            details.han = 13 * len(all_yakuman)
            details.total_han = details.han
            details.score_points = self.yakuman_multiplier * len(all_yakuman)
        else:
            # 5. 非役满: 遍历所有分解, 找 (番最大, 符最大) 的最优形
            best_form = None
            best_han = -1
            best_fu = -1
            best_yaku_list: List[Tuple[str, int]] = []

            for form in win_forms:
                yaku_list = self._find_yaku(form, context)
                han = sum(h for _, h in yaku_list)
                fu = self._calculate_fu(form, context, player.melds)

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
        winner_index: int,
        loser_index: Optional[int],
    ) -> Dict[int, int]:
        """
        【RulesEngine调用的结算函数】
        将点数转化为玩家间的支付变动。

        Args:
            win_details: 和牌详情。
            game_state: 当前游戏状态。
            winner_index: 赢家索引。
            loser_index: 放铳玩家索引（仅荣和有效）。
        """
        payout = {p.player_index: 0 for p in game_state.players}

        if winner_index not in payout:
            raise ValueError(f"Invalid winner_index: {winner_index}")

        if win_details.is_tsumo:
            # score_points 解释为总和牌点（已含子/亲差异），这里按基础点近似分摊
            is_dealer = winner_index == game_state.dealer_index
            if is_dealer:
                per_player = max(100, self._ceil_to_100(win_details.score_points / 3))
                for p in game_state.players:
                    if p.player_index == winner_index:
                        payout[p.player_index] += per_player * (game_state.num_players - 1)
                    else:
                        payout[p.player_index] -= per_player
            else:
                dealer_pay = max(100, self._ceil_to_100(win_details.score_points / 2))
                non_dealer_pay = max(100, self._ceil_to_100(win_details.score_points / 4))
                for p in game_state.players:
                    if p.player_index == winner_index:
                        payout[p.player_index] += dealer_pay + non_dealer_pay * (game_state.num_players - 2)
                    elif p.player_index == game_state.dealer_index:
                        payout[p.player_index] -= dealer_pay
                    else:
                        payout[p.player_index] -= non_dealer_pay
        else:
            if loser_index is None or loser_index not in payout:
                raise ValueError("RON settlement requires a valid loser_index.")
            payout[winner_index] += win_details.score_points
            payout[loser_index] -= win_details.score_points

        # 本场和立直棒处理（简化但可运行）
        honba_bonus = game_state.honba * 300
        if win_details.is_tsumo:
            for p in game_state.players:
                if p.player_index != winner_index:
                    payout[p.player_index] -= game_state.honba * 100
            payout[winner_index] += honba_bonus
        elif loser_index is not None:
            payout[loser_index] -= honba_bonus
            payout[winner_index] += honba_bonus

        if game_state.riichi_sticks > 0:
            payout[winner_index] += game_state.riichi_sticks * 1000

        return payout

    def calculate_ryuukyoku_penalty_tenpai(self, game_state: "GameState") -> Dict[int, int]:
        """
        荒牌流局罚符（3000点）分配。
        听牌玩家平分获得，未听牌玩家平分支付。
        """
        payout = {p.player_index: 0 for p in game_state.players}
        tenpai_players = [p for p in game_state.players if self.hand_analyzer.is_tenpai(p.hand, p.melds)]
        noten_players = [p for p in game_state.players if p not in tenpai_players]

        if not tenpai_players or not noten_players:
            return payout

        total_penalty = 3000
        gain_each = total_penalty // len(tenpai_players)
        lose_each = total_penalty // len(noten_players)

        for p in tenpai_players:
            payout[p.player_index] += gain_each
        for p in noten_players:
            payout[p.player_index] -= lose_each

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
        补全状况役所需的全部字段 (见 YAKU_AND_SCORING_DESIGN §2)。
        """
        # 确定场风/自风
        wind_map = {0: WIND_EAST, 1: WIND_SOUTH, 2: WIND_WEST, 3: WIND_NORTH}
        round_wind_tile = wind_map.get(game_state.round_wind, WIND_EAST)
        # 自风 = (玩家位置 - 庄家位置) % 4 -> 0东 1南 2西 3北
        seat_offset = (player.player_index - game_state.dealer_index) % 4
        player_wind_tile = wind_map.get(seat_offset, WIND_EAST)

        # —— 状况判定 ——
        is_first_turn = game_state.turn_number <= 1
        is_dealer = player.player_index == game_state.dealer_index

        # 岭上开花: 自摸 且 上一次摸牌是岭上摸牌
        is_rinshan = bool(is_tsumo and game_state.last_draw_was_rinshan)

        # 海底/河底: 活动牌墙摸完 (自摸=海底摸月, 荣和=河底捞鱼)
        wall_empty = game_state.wall.get_remaining_live_tiles_count() == 0
        is_haitei = bool(is_tsumo and wall_empty)
        is_houtei = bool((not is_tsumo) and wall_empty)

        # 天和/地和: 第一巡, 无副露 (门清), 庄家天和 / 闲家地和
        is_tenhou = bool(is_first_turn and is_dealer and player.is_menzen and is_tsumo)
        is_chiihou = bool(is_first_turn and (not is_dealer) and player.is_menzen and is_tsumo)

        # 双立直: 立直且发生在第1巡 (riichi_turn==0 表示宣言时巡目0)
        is_double_riichi = bool(player.riichi_declared and player.riichi_turn == 0)

        return {
            "is_tsumo": is_tsumo,
            "is_riichi": player.riichi_declared,
            "is_double_riichi": is_double_riichi,
            "is_ippatsu": bool(player.ippatsu_chance),
            "is_menzen": player.is_menzen,
            "is_dealer": is_dealer,
            "player_wind": player_wind_tile,
            "round_wind": round_wind_tile,
            "dora_indicators": game_state.wall.dora_indicators,
            "ura_dora_indicators": game_state.wall.ura_dora_indicators,
            "is_rinshan": is_rinshan,
            "is_haitei": is_haitei,
            "is_houtei": is_houtei,
            "is_tenhou": is_tenhou,
            "is_chiihou": is_chiihou,
            "is_first_turn": is_first_turn,
            "winning_tile": win_tile,
        }

    # ======================================================================
    # == 役种判断 (Yaku Engine) ==
    # ======================================================================

    def _find_yakuman(
        self, hand: List[Tile], melds: List[Meld], context: Dict
    ) -> List[str]:
        """
        检查役满 (Yakuman)。返回命中的役满名称列表。
        注意: 役满判定需要看具体分解, 这里接收 form 由 caller 遍历;
              但为简化, 此处用 hand+melds 直接判定结构性役满。
        """
        yakuman_list: List[str] = []
        all_tiles = hand + [t for m in melds for t in m.tiles]
        all_values = [t.value for t in all_tiles]
        value_set = set(all_values)
        value_counts: Counter = Counter(all_values)

        is_menzen = not melds

        # —— 天和 / 地和 (状况役满, 不依赖分解) ——
        if context.get("is_tenhou"):
            yakuman_list.append("Tenhou")
        if context.get("is_chiihou"):
            yakuman_list.append("Chiihou")

        # —— 国士无双 (13幺九字) ——
        terminal_honors_in_hand = {v for v in value_set if v in TERMINAL_HONOR_VALUES}
        if is_menzen and len(hand) == 14 and terminal_honors_in_hand == TERMINAL_HONOR_VALUES:
            # 13种齐全, 恰好1种2张
            th_counts = {v: value_counts[v] for v in TERMINAL_HONOR_VALUES}
            if all(c >= 1 for c in th_counts.values()) and sum(c == 2 for c in th_counts.values()) == 1:
                yakuman_list.append("Kokushi")

        # 以下役满基于"4面子1雀头"结构 (需 caller 传 form, 这里用近似: 统计刻子数)
        # 为准确判定, 役满结构性判定放到 _find_yakuman_for_form 中由 caller 对每个 form 调用。
        return yakuman_list

    def _find_yakuman_for_form(self, form: "WinForm", context: Dict) -> List[str]:
        """
        基于具体 WinForm 分解判定结构性役满 (四暗刻/大三元/字一色等)。
        caller (calculate_win_details) 会对每个 form 调用, 取命中即算。
        """
        yaku: List[str] = []
        comps = form.components
        is_menzen = context.get("is_menzen", True)

        # 提取所有面子 (排除雀头)
        melds_comps = [c for c in comps if c.type in ("shuntsu", "koutsu", "kantsu")]
        pair_comp = form.pair  # 雀头 (standard) 或 None

        all_tile_values = [t.value for c in comps for t in c.tiles]
        value_counts: Counter = Counter(all_tile_values)

        # —— 四暗刻 (4个暗刻, 门清) ——
        if is_menzen and form.hand_type == "standard":
            ankou_count = sum(
                1 for c in melds_comps
                if c.type in ("koutsu", "kantsu") and not c.is_open
            )
            if ankou_count == 4:
                # 单骑 (听雀头) vs 非单骑
                if pair_comp is not None and context.get("winning_tile") is not None:
                    wt = context["winning_tile"].value
                    if pair_comp.tiles[0].value == wt:
                        yaku.append("Suuankou Tanki")  # 四暗刻单骑
                    else:
                        yaku.append("Suuankou")
                else:
                    yaku.append("Suuankou")

        # —— 大三元 (白发中各一刻/杠) ——
        dragon_vals = {DRAGON_WHITE, DRAGON_GREEN, DRAGON_RED}
        dragon_mentsu = [
            c for c in melds_comps
            if c.type in ("koutsu", "kantsu") and c.tiles[0].value in dragon_vals
        ]
        if len(dragon_mentsu) == 3 and {c.tiles[0].value for c in dragon_mentsu} == dragon_vals:
            yaku.append("Daisangen")

        # —— 大四喜 / 小四喜 (风牌) ——
        wind_vals = {WIND_EAST, WIND_SOUTH, WIND_WEST, WIND_NORTH}
        wind_mentsu = [
            c for c in melds_comps
            if c.type in ("koutsu", "kantsu") and c.tiles[0].value in wind_vals
        ]
        wind_kinds = {c.tiles[0].value for c in wind_mentsu}
        if wind_kinds == wind_vals:
            yaku.append("Daisuushi")  # 大四喜: 4风刻
        elif len(wind_kinds) == 3 and pair_comp is not None and pair_comp.tiles[0].value in wind_vals:
            yaku.append("Shousuushi")  # 小四喜: 3风刻 + 1风雀头

        # —— 字一色 (全字牌) ——
        if all(v >= WIND_EAST for v in all_tile_values):
            yaku.append("Tsuuiisou")

        # —— 绿一色 (全绿: 23468索 + 发) ——
        green_vals = {SOU_1 + 1, SOU_1 + 2, SOU_1 + 3, SOU_1 + 5, SOU_1 + 7, DRAGON_GREEN}
        # SOU 基址=18: 2索=19,3索=20,4索=21,6索=23,8索=25, 发=32
        if all(v in green_vals for v in all_tile_values):
            yaku.append("Ryuuiisou")

        # —— 清老头 (全幺九数牌的刻子, 即 1m9m1p9p1s9s 的刻/杠) ——
        terminal_num = {MAN_1, MAN_9, PIN_1, PIN_9, SOU_1, SOU_9}
        if (
            form.hand_type == "standard"
            and all(c.type in ("koutsu", "kantsu") for c in melds_comps)
            and all(c.tiles[0].value in terminal_num for c in melds_comps)
            and pair_comp is not None
            and pair_comp.tiles[0].value in terminal_num
        ):
            yaku.append("Chinroutou")

        # —— 九莲宝灯 (门清, 同花 1112345678999 + 任1) ——
        if is_menzen and form.hand_type == "standard" and len(form.all_tiles) == 14:
            suit = self._tiles_single_suit(form.all_tiles)
            if suit is not None:
                base = [4, 1, 1, 1, 1, 1, 1, 1, 4]  # 1,9 各4; 2-8 各1 是九莲基本形
                counts = [0] * 9
                for t in form.all_tiles:
                    counts[t.value - suit] += 1
                if all(counts[i] >= base[i] for i in range(9)):
                    # 真九莲: 单骑和 (听的是补回基本形的那张, 即多出的那张)
                    wt = context.get("winning_tile")
                    # 简化: 若多出一张(总数14, 基本13+1), 视为真九莲
                    diff = sum(counts[i] - base[i] for i in range(9))
                    if diff >= 1 and wt is not None:
                        # 多出的那张在和牌位 -> 九莲
                        yaku.append("Chuuren Poutou")

        return yaku

    def _tiles_single_suit(self, tiles: List[Tile]) -> Optional[int]:
        """判断手牌是否单一数牌花色, 返回该花色基址(0/9/18); 含字牌或混花返回 None。"""
        suits = set()
        for t in tiles:
            v = t.value
            if v <= 8:
                suits.add(0)
            elif v <= 17:
                suits.add(9)
            elif v <= 26:
                suits.add(18)
            else:
                return None  # 字牌
        if len(suits) == 1:
            return next(iter(suits))
        return None

    def _find_yaku(self, form: "WinForm", context: Dict) -> List[Tuple[str, int]]:
        """
        【核心】根据分解形式 (WinForm) 查找普通役种 (非役满)。
        返回 [(役名, 番数), ...]。食下役在副露时减番。
        """
        yaku_found: List[Tuple[str, int]] = []
        is_menzen = context.get("is_menzen", True)

        # ===== 1. 状况役 (Context Yaku) =====
        if context.get("is_riichi"):
            if context.get("is_double_riichi"):
                yaku_found.append(("Double Riichi", 2))
            else:
                yaku_found.append(("Riichi", 1))
        if context.get("is_ippatsu"):
            yaku_found.append(("Ippatsu", 1))
        if context.get("is_tsumo"):
            if is_menzen:
                yaku_found.append(("Menzen Tsumo", 1))
        if context.get("is_rinshan"):
            yaku_found.append(("Rinshan Kaihou", 1))
        if context.get("is_haitei"):
            yaku_found.append(("Haitei Raoyue", 1))
        if context.get("is_houtei"):
            yaku_found.append(("Houtei Raoyui", 1))

        # ===== 2. 七对子 (仅 chiitoitsu 形) =====
        if form.hand_type == "chiitoitsu":
            yaku_found.append(("Chiitoitsu", 2))
            # 七对子不计其它手牌役 (无面子结构)
            return yaku_found

        # 国士在 _find_yakuman 处理, 此处 standard 分解继续

        # ===== 3. 手牌役 (Hand Yaku, standard 形) =====
        comps = form.components
        melds_comps = [c for c in comps if c.type in ("shuntsu", "koutsu", "kantsu")]
        pair_comp = form.pair
        all_tile_values = [t.value for t in form.all_tiles]
        all_values_set = set(all_tile_values)

        # 役牌 (Yakuhai): 三元/自风/场风的刻或杠
        yaku_found.extend(self._check_yaku_yakuhai(form, context))

        # 断幺九 (Tanyao)
        if self._check_yaku_tanyao(form, context):
            yaku_found.append(("Tanyao", 1))

        # 平和 (Pinfu): 门清, 全顺子, 雀头非役牌, 两面听
        if is_menzen and self._check_yaku_pinfu(form, context):
            yaku_found.append(("Pinfu", 1))

        # 一杯口 (Iipeikou): 门清, 两个完全相同的顺子
        if is_menzen and self._check_yaku_iipeikou(form):
            yaku_found.append(("Iipeikou", 1))

        # 对对和 (Toitoi): 无顺子, 全刻/杠
        if all(c.type in ("koutsu", "kantsu") for c in melds_comps):
            yaku_found.append(("Toitoi", 2))

        # 三暗刻 (Sanankou): 3个暗刻 (不含副露)
        ankou = sum(1 for c in melds_comps if c.type in ("koutsu", "kantsu") and not c.is_open)
        if ankou == 3:
            yaku_found.append(("Sanankou", 2))
        if ankou == 4 and not context.get("is_menzen"):
            # 非门清四暗刻不算役满, 但这里门清四暗刻已被役满处理
            pass

        # 三杠子 (Sankantsu): 3个杠
        kantsu = sum(1 for c in melds_comps if c.type == "kantsu")
        if kantsu == 3:
            yaku_found.append(("Sankantsu", 2))

        # 混老头 (Honroutou): 全幺九 (含字), 且全刻子 (与对对和复合)
        if all(v in TERMINAL_HONOR_VALUES for v in all_tile_values) and \
           all(c.type in ("koutsu", "kantsu") for c in melds_comps) and \
           pair_comp is not None:
            yaku_found.append(("Honroutou", 2))

        # 小三色 (Shousangen): 白发中两刻 + 雀头为第三种三元
        yaku_found.extend(self._check_yaku_shousangen(form))

        # —— 食下役 (副露减番) ——
        # 三色同顺 / 一气通贯 / 混全带 / 纯全带 / 混一色 / 清一色
        sanshoku = self._check_yaku_sanshoku(form)
        if sanshoku:
            yaku_found.append(("Sanshoku Doujun", 2 if is_menzen else 1))

        ikkitsuukan = self._check_yaku_ikkitsuukan(form)
        if ikkitsuukan:
            yaku_found.append(("Ikkitsuukan", 2 if is_menzen else 1))

        if self._check_yaku_chanta(form, context, pure=False):
            yaku_found.append(("Chanta", 2 if is_menzen else 1))  # 混全带幺九

        if self._check_yaku_chanta(form, context, pure=True):
            yaku_found.append(("Junchan", 3 if is_menzen else 2))  # 纯全带幺九

        honitsu = self._check_yaku_itsu(form, allow_honors=True)
        if honitsu:
            yaku_found.append(("Honiisou", 3 if is_menzen else 2))  # 混一色

        chinitsu = self._check_yaku_itsu(form, allow_honors=False)
        if chinitsu:
            yaku_found.append(("Chiniisou", 6 if is_menzen else 5))  # 清一色

        return yaku_found

    # ===== Yaku Helper Functions =====

    def _check_yaku_tanyao(self, form: "WinForm", context: Dict) -> bool:
        """断幺九: 全牌无幺九字"""
        if not context.get("is_menzen") and not self.allow_kuitan:
            return False  # 食断禁
        for tile in form.all_tiles:
            if tile.value in TERMINAL_HONOR_VALUES:
                return False
        return True

    def _check_yaku_yakuhai(
        self, form: "WinForm", context: Dict
    ) -> List[Tuple[str, int]]:
        """役牌: 三元/自风/场风的刻或杠 (每命中一个 +1 番)"""
        yakuhai_list = []
        player_wind = context.get("player_wind")
        round_wind = context.get("round_wind")

        for comp in form.components:
            if comp.type in ("koutsu", "kantsu"):
                val = comp.tiles[0].value
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
        return yakuhai_list

    def _check_yaku_pinfu(self, form: "WinForm", context: Dict) -> bool:
        """平和: 门清 + 全顺子 + 雀头非役牌 + 两面听"""
        comps = form.components
        melds_comps = [c for c in comps if c.type in ("shuntsu", "koutsu", "kantsu")]
        # 全顺子
        if not all(c.type == "shuntsu" for c in melds_comps):
            return False
        pair = form.pair
        if pair is None:
            return False
        # 雀头非役牌
        yakuhai_vals = {DRAGON_WHITE, DRAGON_GREEN, DRAGON_RED,
                        context.get("player_wind"), context.get("round_wind")}
        if pair.tiles[0].value in yakuhai_vals:
            return False
        # 两面听: winning_tile 完成顺子的两端 (非边张/嵌张)
        wt = context.get("winning_tile")
        if wt is None:
            return False
        wt_val = wt.value
        # 找到含 winning_tile 的那个顺子, 判断是否两面
        for c in melds_comps:
            cvals = [t.value for t in c.tiles]
            if wt_val in cvals:
                lo = min(cvals)
                hi = max(cvals)
                # 顺子 [lo, lo+1, lo+2], wt 在中间=嵌张, 在两端且顺子贴边=边张
                if wt_val == lo + 1:  # 中间 -> 嵌张
                    return False
                # wt 在两端: 若 lo==该花色1 (即 lo%9==0) 且 wt==lo -> 边张(听lo+2边)
                #   具体: 12 听3 -> lo=0(MAN_1), wt=MAN_1=0? 不, 顺子是123, wt=3即lo+2
                # 简化: 两面 = wt 在顺子两端 且 该端不是 1或9 的极限
                if wt_val == lo:
                    # wt 是顺子最小, 听的是 wt-1 和 wt+2 中的 wt? 实际两面听 wt-1 / wt+3?
                    # 正确: 两面听 = 顺子由 wt 在端, 且 wt 不是 1 或 9 边界
                    if lo % 9 == 0:  # 123 的 1 端 -> 边张
                        return False
                if wt_val == hi:
                    if hi % 9 == 8:  # 789 的 9 端 -> 边张
                        return False
                return True
        return False

    def _check_yaku_iipeikou(self, form: "WinForm") -> bool:
        """一杯口: 两个完全相同的顺子 (门清)"""
        shuntsu_keys = []
        for c in form.components:
            if c.type == "shuntsu":
                shuntsu_keys.append(tuple(sorted(t.value for t in c.tiles)))
        counts: Counter = Counter(shuntsu_keys)
        return any(v >= 2 for v in counts.values())

    def _check_yaku_shousangen(self, form: "WinForm") -> List[Tuple[str, int]]:
        """小三色: 白发中两刻 + 第三种作雀头"""
        result = []
        comps = form.components
        melds_comps = [c for c in comps if c.type in ("koutsu", "kantsu")]
        pair = form.pair
        if pair is None:
            return result
        dragon_vals = {DRAGON_WHITE, DRAGON_GREEN, DRAGON_RED}
        dragon_mentsu_kinds = {c.tiles[0].value for c in melds_comps
                               if c.tiles[0].value in dragon_vals}
        if len(dragon_mentsu_kinds) == 2 and pair.tiles[0].value in dragon_vals \
           and pair.tiles[0].value not in dragon_mentsu_kinds:
            result.append(("Shousangen", 2))
        return result

    def _check_yaku_sanshoku(self, form: "WinForm") -> bool:
        """三色同顺: 万筒索各一个同数顺子"""
        from collections import defaultdict
        suit_shuntsu = defaultdict(set)
        for c in form.components:
            if c.type == "shuntsu":
                cvals = sorted(t.value for t in c.tiles)
                lo = cvals[0]
                num = lo % 9  # 顺子的起始数 (0-6)
                if lo <= 8:
                    suit_shuntsu[0].add(num)
                elif lo <= 17:
                    suit_shuntsu[9].add(num)
                elif lo <= 26:
                    suit_shuntsu[18].add(num)
        # 三花色都有同一 num 的顺子
        if len(suit_shuntsu) < 3:
            return False
        common = suit_shuntsu[0] & suit_shuntsu[9] & suit_shuntsu[18]
        return len(common) >= 1

    def _check_yaku_ikkitsuukan(self, form: "WinForm") -> bool:
        """一气通贯: 同花 123/456/789 顺子"""
        from collections import defaultdict
        suit_sequences = defaultdict(set)
        for c in form.components:
            if c.type == "shuntsu":
                cvals = sorted(t.value for t in c.tiles)
                lo = cvals[0]
                start_num = lo % 9
                if start_num in (0, 3, 6):  # 123/456/789 的起始
                    suit = (lo // 9) * 9
                    suit_sequences[suit].add(start_num)
        for suit, starts in suit_sequences.items():
            if {0, 3, 6}.issubset(starts):
                return True
        return False

    def _check_yaku_chanta(self, form: "WinForm", context: Dict, pure: bool) -> bool:
        """
        混全带 (pure=False): 每个面子/雀头至少含一张幺九字
        纯全带 (pure=True): 每个面子/雀头至少含一张幺九 (不含字牌)
        """
        comps = form.components
        for c in comps:
            cvals = [t.value for t in c.tiles]
            if c.type == "pair":
                if pure:
                    if cvals[0] not in {MAN_1, MAN_9, PIN_1, PIN_9, SOU_1, SOU_9}:
                        return False
                else:
                    if cvals[0] not in TERMINAL_HONOR_VALUES:
                        return False
            else:
                # 面子: 至少含一张幺九 (pure 时仅数牌幺九)
                if pure:
                    target = {MAN_1, MAN_9, PIN_1, PIN_9, SOU_1, SOU_9}
                    if not any(v in target for v in cvals):
                        return False
                else:
                    if not any(v in TERMINAL_HONOR_VALUES for v in cvals):
                        return False
        return True

    def _check_yaku_itsu(self, form: "WinForm", allow_honors: bool) -> bool:
        """
        混一色 (allow_honors=True): 一种数牌花色 + 字牌
        清一色 (allow_honors=False): 仅一种数牌花色
        """
        suits = set()
        has_honor = False
        for t in form.all_tiles:
            v = t.value
            if v <= 8:
                suits.add(0)
            elif v <= 17:
                suits.add(9)
            elif v <= 26:
                suits.add(18)
            else:
                has_honor = True
        if len(suits) != 1:
            return False
        if allow_honors:  # 混一色: 允许字牌
            return True
        else:  # 清一色: 不允许字牌
            return not has_honor

    # ======================================================================
    # == 符数计算 (Fu Engine) ==
    # ======================================================================

    def _calculate_fu(
        self, form: "WinForm", context: Dict, open_melds: List[Meld]
    ) -> int:
        """
        计算符数 (标准型)。完整规则见 YAKU_AND_SCORING_DESIGN §4。
        七对子固定 25 符。
        """
        if form.hand_type == "chiitoitsu":
            return 25

        is_menzen = context.get("is_menzen", True)
        is_tsumo = context.get("is_tsumo", False)

        # —— 平和判定 (平和为特殊符数 20/30) ——
        is_pinfu = bool(is_menzen and self._check_yaku_pinfu(form, context))

        # 平和 + 门清荣和 = 30 符; 平和 + 自摸 = 20 符 (不加自摸2符)
        if is_pinfu:
            return 20 if is_tsumo else 30

        fu = 20  # 底符

        # 2. 和牌方式
        if is_menzen and not is_tsumo:
            fu += 10  # 门清荣和 +10
        elif is_tsumo:
            fu += 2  # 自摸 +2 (非平和时)

        # 3. 面子符 (刻子/杠子, 顺子无符)
        for comp in form.components:
            if comp.type not in ("koutsu", "kantsu"):
                continue
            val = comp.tiles[0].value
            is_yaochuu = val in TERMINAL_HONOR_VALUES
            is_open = comp.is_open
            if comp.type == "koutsu":
                base = 4 if is_yaochuu else 2
            else:  # kantsu
                base = 16 if is_yaochuu else 8
            fu += base * (1 if is_open else 2)  # 暗刻×2, 明刻×1

        # 4. 雀头符 (雀头是三元/自风/场风 -> +2 each)
        pair = form.pair
        if pair is not None:
            pair_val = pair.tiles[0].value
            yakuhai_vals = {DRAGON_WHITE, DRAGON_GREEN, DRAGON_RED,
                            context.get("player_wind"), context.get("round_wind")}
            if pair_val in yakuhai_vals:
                fu += 2

        # 5. 听牌符 (边张/嵌张/单骑 -> +2; 两面 -> 0)
        fu += self._calculate_wait_fu(form, context)

        # 6. 进位到 10 (底符 20 不进位; 七对已早返回)
        return self._ceil_to_10(fu)

    def _calculate_wait_fu(self, form: "WinForm", context: Dict) -> int:
        """听牌符: 边张/嵌张/单骑/边张单骑 +2; 两面 +0。"""
        wt = context.get("winning_tile")
        if wt is None:
            return 0
        wt_val = wt.value
        comps = form.components
        melds_comps = [c for c in comps if c.type in ("shuntsu", "koutsu", "kantsu")]

        # 单骑: winning_tile 在雀头 (雀头由 winning_tile 组成)
        pair = form.pair
        if pair is not None and pair.tiles[0].value == wt_val:
            return 2

        # 顺子听: 找含 winning_tile 的顺子
        for c in melds_comps:
            if c.type == "shuntsu":
                cvals = sorted(t.value for t in c.tiles)
                if wt_val in cvals:
                    lo, hi = cvals[0], cvals[2]
                    if wt_val == lo + 1:  # 中间 -> 嵌张
                        return 2
                    if wt_val == lo and lo % 9 == 0:  # 12 听3 边张
                        return 2
                    if wt_val == hi and hi % 9 == 8:  # 89 听7 边张
                        return 2
                    return 0  # 两面

        # 刻子听 (如 111 听第4张, 视为单骑类 +2, 实为边张单骑的变体)
        # 若 winning_tile 完成一个刻子, 通常算单骑/嵌张 -> +2
        for c in melds_comps:
            if c.type == "koutsu" and wt_val == c.tiles[0].value:
                return 2
        return 0

    # ======================================================================
    # == 振听 (Furiten) 和宝牌 (Dora) ==
    # ======================================================================

    def _is_furiten(
        self, player: "PlayerState", winning_tile: "Tile", game_state: "GameState"
    ) -> bool:
        """
        检查振听 (仅荣和时调用)。
        本实现覆盖舍牌振听 (永久): 听的牌任一在自己弃牌河中。
        同巡振听/立直振听需 GameState 额外字段 (阶段1后续补)。
        """
        # 舍牌振听: 听的牌 (去掉 winning_tile 后的 13 张) 任一在弃牌河
        # 注意: 荣和时 player.hand 不含 winning_tile, 直接用 player.hand
        try:
            waits = self.hand_analyzer.find_wait_tiles(player.hand, player.melds)
        except Exception:
            return False
        if not waits:
            return False
        discard_values = {t.value for t in player.discards}
        # 若任一听牌在弃牌河 -> 振听
        if waits & discard_values:
            return True
        # 立直振听: 立直后曾有机会荣和但 PASS (需 player.riichi_passed_ron 字段,
        # 当前 PlayerState 未实现, 暂跳过)
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
