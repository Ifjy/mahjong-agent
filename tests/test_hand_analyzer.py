"""
HandAnalyzer 单元测试 —— 覆盖 docs/HAND_DECOMPOSITION_DESIGN.md §9 全部用例。

运行: pytest tests/test_hand_analyzer.py -v
"""

import sys
import os
import time

import pytest

# 把项目根目录加入 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.env.core.actions import Tile, ActionType
from src.env.core.game_state import Meld
from src.env.core.rules.hand_analyzer import HandAnalyzer, WinForm, HandComponent


# ---------- 辅助构造 ----------
def H(values):
    """value 列表 -> Tile 列表"""
    return [Tile(value=v) for v in values]


def H_red(spec):
    """spec: [(value, is_red), ...] -> Tile 列表"""
    return [Tile(value=v, is_red=r) for v, r in spec]


@pytest.fixture
def ha():
    return HandAnalyzer()


# ======================================================================
# 1. 向听数 / 和牌判定
# ======================================================================


class TestShanten:
    def test_complete_standard_win(self, ha):
        # 123m 456p 789s 111z 22z 完整14张标准型
        win = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28, 28])
        assert ha.calculate_shanten(win, []) == -1

    def test_tenpai_standard(self, ha):
        # 去掉一张南(28)，应听牌 (shanten=0)
        tenpai = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28])
        assert ha.calculate_shanten(tenpai, []) == 0
        assert ha.is_tenpai(tenpai, []) is True

    def test_far_hand_positive_shanten(self, ha):
        # 全散牌，向听应为正数
        far = H([0, 2, 9, 11, 18, 20, 27, 28, 29, 30, 31, 32, 33])
        assert ha.calculate_shanten(far, []) > 0

    def test_chiitoitsu_win(self, ha):
        chiitoi = H([0, 0, 2, 2, 5, 5, 9, 9, 18, 18, 27, 27, 31, 31])
        assert ha.calculate_shanten(chiitoi, []) == -1

    def test_chiitoitsu_not_seven_pairs_when_quad(self, ha):
        # 4 张同 value 不能当两对 (七对子规则)
        bad = H([0, 0, 0, 0, 2, 2, 5, 5, 9, 9, 18, 18, 27, 27])
        # 这是 6 种 value，七对子不成立；向听 != -1 的七对子态
        assert ha._chiitoitsu_shanten(bad) != -1

    def test_kokushi_win(self, ha):
        kokushi = H([0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33, 0])
        assert ha.calculate_shanten(kokushi, []) == -1

    def test_kokushi_thirteen_sided_wait(self, ha):
        # 13 种幺九字各 1，听全部 13 种 (十三面)
        kokushi13 = H([0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33])
        waits = ha.find_wait_tiles(kokushi13, [])
        assert len(waits) == 13


# ======================================================================
# 2. 听牌 / 听牌枚举
# ======================================================================


class TestWaits:
    def test_simple_wait(self, ha):
        # 123m456p789s111z + 单骑听 28(南)
        tenpai = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28])
        waits = ha.find_wait_tiles(tenpai, [])
        assert 28 in waits

    def test_not_tenpai_returns_empty(self, ha):
        far = H([0, 2, 9, 11, 18, 20, 27, 28, 29, 30, 31, 32, 33])
        # 向听 > 0 时无听牌
        assert ha.is_tenpai(far, []) is False

    def test_wait_excludes_four_of_kind(self, ha):
        # 已有 4 张的 value 不可能是听的牌
        # 构造: 1111m 234p 567s 99z (13张) -> 听 1m? 不，4张1m已满
        hand = H([0, 0, 0, 0, 1, 2, 3, 13, 14, 15, 33, 33, 33])
        waits = ha.find_wait_tiles(hand, [])
        assert 0 not in waits  # 1m 已 4 张


# ======================================================================
# 3. 完整分解 find_all_winning_forms
# ======================================================================


class TestWinForms:
    def test_standard_single_decomposition(self, ha):
        win = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28, 28])
        forms = ha.find_all_winning_forms(win, [], Tile(28))
        assert len(forms) >= 1
        assert all(f.hand_type == "standard" for f in forms)

    def test_multiple_decompositions(self, ha):
        # 234m 234m 234m 234m 55p: 多种分解 (顺子可重复)
        multi = H([1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 13, 13])
        forms = ha.find_all_winning_forms(multi, [], Tile(13))
        assert len(forms) > 1, "应返回多个分解"

    def test_chiitoitsu_form(self, ha):
        chiitoi = H([0, 0, 2, 2, 5, 5, 9, 9, 18, 18, 27, 27, 31, 31])
        forms = ha.find_all_winning_forms(chiitoi, [], Tile(31))
        chiitoi_forms = [f for f in forms if f.hand_type == "chiitoitsu"]
        assert len(chiitoi_forms) == 1

    def test_kokushi_form(self, ha):
        kokushi = H([0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33, 0])
        forms = ha.find_all_winning_forms(kokushi, [], Tile(0))
        kokushi_forms = [f for f in forms if f.hand_type == "kokushi"]
        assert len(kokushi_forms) == 1

    def test_form_preserves_tile_instances(self, ha):
        """tiles 必须是 Tile 实例，保留 is_red 信息"""
        # 含赤5筒(13r) 的顺子 4r5p6p -> values 13,14,15
        win = H_red([(0, False), (1, False), (2, False),
                     (13, True), (14, False), (15, False),
                     (18, False), (19, False), (20, False),
                     (27, False), (27, False), (27, False),
                     (31, False), (31, False)])
        forms = ha.find_all_winning_forms(win, [], Tile(14))
        assert len(forms) >= 1
        # 检查赤5筒信息保留在某个 shuntsu 组件里
        red_preserved = any(
            t.is_red for f in forms for c in f.components for t in c.tiles
        )
        assert red_preserved, "赤宝牌 is_red 信息应被保留"

    def test_quad_not_two_pairs(self, ha):
        """4 张同 value 在七对子中不能当两对"""
        bad = H([0, 0, 0, 0, 2, 2, 5, 5, 9, 9, 18, 18, 27, 27])
        forms = ha.find_all_winning_forms(bad, [], Tile(0))
        chiitoi_forms = [f for f in forms if f.hand_type == "chiitoitsu"]
        assert len(chiitoi_forms) == 0


# ======================================================================
# 4. 副露
# ======================================================================


class TestMelds:
    def test_meld_win(self, ha):
        """副露 PON(中) + 手牌 123m456p789s白白"""
        meld = Meld(type=ActionType.PON, tiles=tuple(H([27, 27, 27])),
                    from_player=1, called_tile=Tile(27))
        win = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 31, 31])
        forms = ha.find_all_winning_forms(win, [meld], Tile(31))
        assert len(forms) >= 1
        # 副露面子应是 open 的 koutsu
        form = forms[0]
        open_comps = [c for c in form.components if c.is_open]
        assert len(open_comps) == 1
        assert open_comps[0].type == "koutsu"

    def test_meld_tenpai(self, ha):
        """副露后听牌"""
        meld = Meld(type=ActionType.PON, tiles=tuple(H([27, 27, 27])),
                    from_player=1, called_tile=Tile(27))
        hand = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 31])  # 听白成对
        assert ha.is_tenpai(hand, [meld]) is True
        assert 31 in ha.find_wait_tiles(hand, [meld])

    def test_chiitoitsu_not_with_meld(self, ha):
        """七对子必须门清，副露后不能七对子"""
        meld = Meld(type=ActionType.PON, tiles=tuple(H([27, 27, 27])),
                    from_player=1, called_tile=Tile(27))
        # 构造一个 11 张手牌 + 1 副露，若无副露像七对子
        hand = H([0, 0, 2, 2, 5, 5, 9, 9, 18, 18, 31])
        forms = ha.find_all_winning_forms(hand, [meld], Tile(31))
        assert all(f.hand_type != "chiitoitsu" for f in forms)


# ======================================================================
# 5. 性能
# ======================================================================


class TestPerformance:
    def test_is_tenpai_under_1ms(self, ha):
        tenpai = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28])
        # 预热
        ha.is_tenpai(tenpai, [])
        t0 = time.perf_counter()
        for _ in range(1000):
            ha.is_tenpai(tenpai, [])
        dt_ms = (time.perf_counter() - t0) / 1000 * 1000
        assert dt_ms < 1.0, f"is_tenpai 过慢: {dt_ms:.3f}ms"

    def test_find_wait_tiles_under_5ms(self, ha):
        tenpai = H([0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 28])
        ha.find_wait_tiles(tenpai, [])
        t0 = time.perf_counter()
        for _ in range(100):
            ha.find_wait_tiles(tenpai, [])
        dt_ms = (time.perf_counter() - t0) / 100 * 1000
        assert dt_ms < 5.0, f"find_wait_tiles 过慢: {dt_ms:.3f}ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
