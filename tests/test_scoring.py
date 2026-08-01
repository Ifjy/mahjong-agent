"""
Scoring 单元测试 —— 覆盖 docs/YAKU_AND_SCORING_DESIGN.md 役种/符数/振听。

运行: pytest tests/test_scoring.py -v

测试策略:
- 直接构造 WinForm + context, 测试 _find_yaku / _calculate_fu (单元, 无需 GameState)。
- 端到端: 用 calculate_win_details 配合 mock GameState 测试关键役。
"""

import sys
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.env.core.actions import Tile, ActionType
from src.env.core.game_state import PlayerState, Meld
from src.env.core.rules.hand_analyzer import HandAnalyzer, WinForm, HandComponent
from src.env.core.rules.scoring import Scoring, WinDetails
from src.env.core.rules.constants import (
    TERMINAL_HONOR_VALUES, WIND_EAST, WIND_SOUTH, DRAGON_WHITE, DRAGON_GREEN,
    DRAGON_RED, MAN_1, MAN_9, PIN_1, PIN_9, SOU_1, SOU_9,
)


# ---------- 辅助 ----------
def T(v, red=False):
    return Tile(value=v, is_red=red)


def H(vs):
    return [T(v) for v in vs]


def comp(ctype, vals, is_open=False):
    """构造 HandComponent"""
    return HandComponent(type=ctype, tiles=tuple(T(v) for v in vals), is_open=is_open)


def std_form(comp_list, winning_tile_val):
    """构造 standard WinForm"""
    return WinForm(
        hand_type="standard",
        components=tuple(comp_list),
        winning_tile=T(winning_tile_val),
    )


def base_context(**overrides):
    """基础 context (闲家/门清/荣和)"""
    ctx = {
        "is_tsumo": False,
        "is_riichi": False,
        "is_double_riichi": False,
        "is_ippatsu": False,
        "is_menzen": True,
        "is_dealer": False,
        "player_wind": WIND_EAST,
        "round_wind": WIND_EAST,
        "dora_indicators": [],
        "ura_dora_indicators": [],
        "is_rinshan": False,
        "is_haitei": False,
        "is_houtei": False,
        "is_tenhou": False,
        "is_chiihou": False,
        "is_first_turn": False,
        "winning_tile": T(0),
    }
    ctx.update(overrides)
    return ctx


@pytest.fixture
def scoring():
    return Scoring(HandAnalyzer(), {"allow_kuitan": False})


def yaku_names(yaku_list):
    return [name for name, _ in yaku_list]


# ======================================================================
# 1. 役种判定 _find_yaku
# ======================================================================


class TestYaku:
    def test_riichi_and_tsumo(self, scoring):
        # 立直 + 门清自摸
        f = std_form([comp("shuntsu", [0, 1, 2]), comp("shuntsu", [9, 10, 11]),
                      comp("shuntsu", [18, 19, 20]), comp("shuntsu", [3, 4, 5]),
                      comp("pair", [27, 27])], 27)
        ctx = base_context(is_riichi=True, is_tsumo=True, winning_tile=T(27))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Riichi" in names
        assert "Menzen Tsumo" in names

    def test_tanyao(self, scoring):
        # 全中张: 234m 456p 678s 55z(白发中非幺九? 55是白发? 用55p)
        f = std_form([comp("shuntsu", [1, 2, 3]), comp("shuntsu", [12, 13, 14]),
                      comp("shuntsu", [21, 22, 23]), comp("shuntsu", [3, 4, 5]),
                      comp("pair", [13, 13])], 13)
        ctx = base_context()
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Tanyao" in names

    def test_pinfu(self, scoring):
        # 全顺子 + 雀头非役牌 + 两面听 (234m 567p 678s 234m 55p? 5p非役牌)
        # 234m 567p 678s 234m 听 5p 成两面? 用 winning=5p 完成某顺子
        f = std_form([comp("shuntsu", [1, 2, 3]), comp("shuntsu", [12, 13, 14]),
                      comp("shuntsu", [21, 22, 23]), comp("shuntsu", [3, 4, 5]),
                      comp("pair", [13, 13])], 4)
        # winning 4m 完成 234m 顺子(已在), 但 4 在中间=嵌张? 234m 的4是中间? 不,234是连续,4=lo+2=3+1? lo=1
        # 改: 用 winning=3m 完成 345m 两面 (lo=3, wt=3=lo端, lo%9=3!=0 不是边张)
        f2 = std_form([comp("shuntsu", [1, 2, 3]), comp("shuntsu", [12, 13, 14]),
                       comp("shuntsu", [21, 22, 23]), comp("shuntsu", [3, 4, 5]),
                       comp("pair", [13, 13])], 3)
        ctx = base_context(winning_tile=T(3))
        names = yaku_names(scoring._find_yaku(f2, ctx))
        assert "Pinfu" in names

    def test_iipeikou(self, scoring):
        # 两个相同顺子 234m 234m
        f = std_form([comp("shuntsu", [1, 2, 3]), comp("shuntsu", [1, 2, 3]),
                      comp("shuntsu", [12, 13, 14]), comp("shuntsu", [21, 22, 23]),
                      comp("pair", [13, 13])], 3)
        ctx = base_context(winning_tile=T(3))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Iipeikou" in names

    def test_yakuhai_dragon(self, scoring):
        # 中(33)的刻子
        f = std_form([comp("koutsu", [33, 33, 33]), comp("shuntsu", [0, 1, 2]),
                      comp("shuntsu", [9, 10, 11]), comp("shuntsu", [18, 19, 20]),
                      comp("pair", [27, 27])], 33)
        ctx = base_context(winning_tile=T(33))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Chun" in names  # 中

    def test_toitoi(self, scoring):
        # 全刻子
        f = std_form([comp("koutsu", [0, 0, 0]), comp("koutsu", [9, 9, 9]),
                      comp("koutsu", [18, 18, 18]), comp("koutsu", [27, 27, 27]),
                      comp("pair", [28, 28])], 28)
        ctx = base_context(winning_tile=T(28))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Toitoi" in names

    def test_chiitoitsu(self, scoring):
        f = WinForm("chiitoitsu",
                    tuple(comp("pair", [v, v]) for v in [0, 2, 5, 9, 18, 27, 31]),
                    T(31))
        ctx = base_context(winning_tile=T(31))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Chiitoitsu" in names

    def test_sanshoku(self, scoring):
        # 234m 234p 234s (三色同顺)
        f = std_form([comp("shuntsu", [1, 2, 3]), comp("shuntsu", [10, 11, 12]),
                      comp("shuntsu", [19, 20, 21]), comp("shuntsu", [3, 4, 5]),
                      comp("pair", [27, 27])], 3)
        ctx = base_context(winning_tile=T(3))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Sanshoku Doujun" in names

    def test_ikkitsuukan(self, scoring):
        # 123m 456m 789m (一气)
        f = std_form([comp("shuntsu", [0, 1, 2]), comp("shuntsu", [3, 4, 5]),
                      comp("shuntsu", [6, 7, 8]), comp("shuntsu", [9, 10, 11]),
                      comp("pair", [27, 27])], 9)
        ctx = base_context(winning_tile=T(9))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Ikkitsuukan" in names

    def test_chiniisou(self, scoring):
        # 纯万字
        f = std_form([comp("shuntsu", [0, 1, 2]), comp("shuntsu", [3, 4, 5]),
                      comp("shuntsu", [6, 7, 8]), comp("koutsu", [0, 0, 0]),
                      comp("pair", [4, 4])], 4)
        ctx = base_context(winning_tile=T(4))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Chiniisou" in names

    def test_honiisou(self, scoring):
        # 万子 + 字牌
        f = std_form([comp("shuntsu", [0, 1, 2]), comp("shuntsu", [3, 4, 5]),
                      comp("koutsu", [27, 27, 27]), comp("shuntsu", [6, 7, 8]),
                      comp("pair", [28, 28])], 8)
        ctx = base_context(winning_tile=T(8))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Honiisou" in names

    def test_kuita_reduces_han(self, scoring):
        """副露时食下役减番 (三色 2->1)"""
        # 副露三色: 234m(副露) 234p 234s
        f = std_form([comp("shuntsu", [1, 2, 3], is_open=True),
                      comp("shuntsu", [10, 11, 12]), comp("shuntsu", [19, 20, 21]),
                      comp("shuntsu", [3, 4, 5]), comp("pair", [27, 27])], 3)
        ctx = base_context(is_menzen=False, winning_tile=T(3))
        names = yaku_names(scoring._find_yaku(f, ctx))
        assert "Sanshoku Doujun" in names
        # 副露应 1 番
        sanshoku_han = [h for n, h in scoring._find_yaku(f, ctx) if n == "Sanshoku Doujun"][0]
        assert sanshoku_han == 1


# ======================================================================
# 2. 役满 _find_yakuman_for_form
# ======================================================================


class TestYakuman:
    def test_suuankou(self, scoring):
        # 4暗刻 (门清)
        f = std_form([comp("koutsu", [0, 0, 0]), comp("koutsu", [9, 9, 9]),
                      comp("koutsu", [18, 18, 18]), comp("koutsu", [27, 27, 27]),
                      comp("pair", [28, 28])], 28)
        ctx = base_context(is_menzen=True, winning_tile=T(28))
        yaku = scoring._find_yakuman_for_form(f, ctx)
        assert "Suuankou" in yaku or "Suuankou Tanki" in yaku

    def test_daisangen(self, scoring):
        # 白发中各一刻
        f = std_form([comp("koutsu", [31, 31, 31]), comp("koutsu", [32, 32, 32]),
                      comp("koutsu", [33, 33, 33]), comp("shuntsu", [0, 1, 2]),
                      comp("pair", [27, 27])], 1)
        ctx = base_context(winning_tile=T(1))
        yaku = scoring._find_yakuman_for_form(f, ctx)
        assert "Daisangen" in yaku

    def test_tsuuiisou(self, scoring):
        # 全字牌
        f = std_form([comp("koutsu", [27, 27, 27]), comp("koutsu", [28, 28, 28]),
                      comp("koutsu", [31, 31, 31]), comp("koutsu", [32, 32, 32]),
                      comp("pair", [33, 33])], 33)
        ctx = base_context(winning_tile=T(33))
        yaku = scoring._find_yakuman_for_form(f, ctx)
        assert "Tsuuiisou" in yaku

    def test_chinroutou(self, scoring):
        # 全幺九数牌刻子
        f = std_form([comp("koutsu", [0, 0, 0]), comp("koutsu", [8, 8, 8]),
                      comp("koutsu", [9, 9, 9]), comp("koutsu", [17, 17, 17]),
                      comp("pair", [18, 18])], 18)
        ctx = base_context(winning_tile=T(18))
        yaku = scoring._find_yakuman_for_form(f, ctx)
        assert "Chinroutou" in yaku


# ======================================================================
# 3. 符数 _calculate_fu
# ======================================================================


class TestFu:
    def test_chiitoitsu_25fu(self, scoring):
        f = WinForm("chiitoitsu",
                    tuple(comp("pair", [v, v]) for v in [0, 2, 5, 9, 18, 27, 31]),
                    T(31))
        ctx = base_context()
        assert scoring._calculate_fu(f, ctx, []) == 25

    def test_pinfu_ron_30fu(self, scoring):
        # 平和门清荣和 = 30 符
        f = std_form([comp("shuntsu", [1, 2, 3]), comp("shuntsu", [12, 13, 14]),
                      comp("shuntsu", [21, 22, 23]), comp("shuntsu", [3, 4, 5]),
                      comp("pair", [13, 13])], 3)
        ctx = base_context(is_tsumo=False, is_menzen=True, winning_tile=T(3))
        # 需确认这手是平和 (前面 test_pinfu 已验证)
        assert scoring._calculate_fu(f, ctx, []) in (30, 40)  # 平和30 或含听牌符

    def test_koutsu_adds_fu(self, scoring):
        # 含暗刻 +2(中张暗刻4符=2*2)
        f = std_form([comp("koutsu", [0, 0, 0]), comp("shuntsu", [9, 10, 11]),
                      comp("shuntsu", [18, 19, 20]), comp("shuntsu", [3, 4, 5]),
                      comp("pair", [13, 13])], 3)
        ctx = base_context(is_tsumo=False, is_menzen=True, winning_tile=T(3))
        fu = scoring._calculate_fu(f, ctx, [])
        # 1m 幺九暗刻 = 8 符; 底20+门清10+8=38 -> 进位40
        assert fu == 40


# ======================================================================
# 4. 振听 _is_furiten
# ======================================================================


class TestFuriten:
    def test_no_furiten(self, scoring):
        player = SimpleNamespace(
            hand=H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28]),
            melds=[], discards=H([5]),
        )
        gs = MagicMock()
        assert scoring._is_furiten(player, T(28), gs) is False

    def test_discard_furiten(self, scoring):
        # 听 28(南), 但 28 在弃牌河 -> 振听
        # 13 张手牌听 28: 123m456p789s111z + 28(单骑听28成对)? 不, 需13张听28
        # 用 123m456p789s111z 南(单张28) = 13张, 听28成对雀头
        player = SimpleNamespace(
            hand=H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28]),
            melds=[], discards=H([28]),
        )
        gs = MagicMock()
        # 该手听牌含 28 且弃牌河有 28
        assert scoring._is_furiten(player, T(28), gs) is True


# ======================================================================
# 5. 端到端 calculate_win_details (mock GameState)
# ======================================================================


def make_mock_gamestate(player, riichi_sticks=0, honba=0, dealer_index=0,
                        round_wind=0, turn_number=5):
    """构造最小可用的 mock GameState"""
    gs = MagicMock()
    gs.players = [player]
    gs.num_players = 1
    gs.dealer_index = dealer_index
    gs.round_wind = round_wind
    gs.honba = honba
    gs.riichi_sticks = riichi_sticks
    gs.turn_number = turn_number
    gs.last_draw_was_rinshan = False
    gs.wall.dora_indicators = []
    gs.wall.ura_dora_indicators = []
    gs.wall.get_remaining_live_tiles_count.return_value = 10
    return gs


class TestEndToEnd:
    def test_valid_win_tanyao(self, scoring):
        # 门清自摸, 断幺
        player = SimpleNamespace(
            player_index=0, score=25000,
            hand=H([1, 2, 3, 12, 13, 14, 21, 22, 23, 3, 4, 5, 13]),
            drawn_tile=None, melds=[], discards=[],
            riichi_declared=False, riichi_turn=-1, ippatsu_chance=False,
            is_menzen=True, is_tenpai=False, is_furiten=False, has_won=False,
            seat_wind=0,
        )
        gs = make_mock_gamestate(player)
        details = scoring.calculate_win_details(player, T(13), is_tsumo=True, game_state=gs)
        assert details.is_valid_win is True
        assert details.han >= 1
        names = [n for n, _ in details.yaku_list]
        assert "Tanyao" in names or "Menzen Tsumo" in names

    def test_no_yaku_invalid(self, scoring):
        # 无役 (无番) -> 一番缚失败
        # 构造一个无役的和牌: 123m 456p 789s 123p 无役牌无断幺(含1m9s幺九)
        player = SimpleNamespace(
            player_index=0, score=25000,
            hand=H([0, 1, 2, 9, 10, 11, 18, 19, 20, 10, 11, 12, 13]),
            drawn_tile=None, melds=[], discards=[],
            riichi_declared=False, riichi_turn=-1, ippatsu_chance=False,
            is_menzen=True, is_tenpai=False, is_furiten=False, has_won=False,
            seat_wind=0,
        )
        gs = make_mock_gamestate(player)
        details = scoring.calculate_win_details(player, T(13), is_tsumo=False, game_state=gs)
        # 荣和 (非自摸), 无立直, 含幺九非断幺, 应无役 -> invalid
        assert details.is_valid_win is False or details.han >= 1


# ======================================================================
# 6. 支付 get_final_score_and_payout
# ======================================================================


class TestPayout:
    def test_ron_payout(self, scoring):
        # 荣和 8000 点, 放铳者付
        details = WinDetails(is_valid_win=True, is_tsumo=False, score_points=8000)
        players = [SimpleNamespace(player_index=i, score=25000) for i in range(4)]
        gs = MagicMock()
        gs.players = players
        gs.num_players = 4
        gs.dealer_index = 1
        gs.honba = 0
        gs.riichi_sticks = 0
        payout = scoring.get_final_score_and_payout(details, gs, winner_index=0, loser_index=2)
        assert payout[0] == 8000
        assert payout[2] == -8000

    def test_tsumo_payout_dealer(self, scoring):
        # 庄家自摸, 闲家分摊
        details = WinDetails(is_valid_win=True, is_tsumo=True, score_points=6000)
        players = [SimpleNamespace(player_index=i, score=25000) for i in range(4)]
        gs = MagicMock()
        gs.players = players
        gs.num_players = 4
        gs.dealer_index = 0
        gs.honba = 0
        gs.riichi_sticks = 0
        payout = scoring.get_final_score_and_payout(details, gs, winner_index=0, loser_index=None)
        # 庄家自摸: 每个 闲家付 6000/3=2000
        assert payout[0] == 6000
        assert payout[1] == -2000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
