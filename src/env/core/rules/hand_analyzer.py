# hand_analyzer.py
"""
手牌分析器 (Hand Analyzer) —— 双阶段实现。

阶段 A (性能路径): shanten 预计算表 + find_wait_tiles/is_tenpai。
    用于 ActionValidator 立直判定、Scoring 振听/流局听牌判定。
阶段 B (正确性路径): 实例级回溯分解 find_all_winning_forms。
    仅在确认和牌后调用，供 Scoring 役种/符数使用，保留 Tile 实例信息。

设计见 docs/HAND_DECOMPOSITION_DESIGN.md。
"""

from typing import List, Set, Counter as TypingCounter, Dict, Optional, Any, Tuple, Iterator
from collections import Counter
from dataclasses import dataclass, field

from src.env.core.actions import Tile, ActionType, KanType
from src.env.core.game_state import Meld
from src.env.core.rules.constants import TERMINAL_HONOR_VALUES

# ======================================================================
# 1. 核心数据结构 (WinForm & HandComponent)
# ======================================================================


@dataclass(frozen=True)
class HandComponent:
    """
    表示一个已分解的面子（或雀头）—— HandAnalyzer 的内部解析结果。
    tiles 必须是 Tile 实例元组（保留赤宝牌/来源信息），按 value 排序。
    """

    type: str  # "shuntsu"(顺子) | "koutsu"(刻子) | "kantsu"(杠子) | "pair"(雀头) | "kokushi_single"
    tiles: Tuple[Tile, ...]
    is_open: bool = False  # True=来自副露(player.melds)，False=手牌内

    def __post_init__(self):
        # 保证 tiles 内部按 value 有序
        object.__setattr__(self, "tiles", tuple(sorted(self.tiles)))

    @property
    def value(self) -> int:
        """代表值（最小 value），用于排序与比较"""
        return self.tiles[0].value if self.tiles else -1


@dataclass(frozen=True)
class WinForm:
    """表示一个完整的和牌形式（一种分解方法）。"""

    hand_type: str  # "standard" | "chiitoitsu" | "kokushi"
    components: Tuple[HandComponent, ...]
    winning_tile: Tile

    @property
    def pair(self) -> Optional[HandComponent]:
        """返回这组分解中的雀头（仅 standard）"""
        if self.hand_type == "standard":
            for c in self.components:
                if c.type == "pair":
                    return c
        return None

    @property
    def all_tiles(self) -> List[Tile]:
        """返回这组分解中的所有 14 张牌"""
        return [tile for c in self.components for tile in c.tiles]


# ======================================================================
# 2. shanten 预计算表 (Performance-critical lookup tables)
# ======================================================================
#
# 经典做法：对单一数牌花色 (1-9, 每种 0-4 张)，预计算一个表
#   table[counts_tuple][mentsu_k][head_k]
# 其中 counts_tuple 是 (c0..c8) 的计数向量，mentsu_k 是该花色贡献的完整面子数，
# head_k 是该花色贡献的部分搭子(对子/边张/嵌张)数。
# 表存的是"该花色在取 (mentsu_k, head_k) 后剩余的无效牌数"。
#
# 全局 shanten 由 3 个花色 + 字牌组合得出。这里采用紧凑实现：直接对每个花色
# 穷举所有 (顺子/刻子/雀头/搭子) 取法，记录最优，构造一次性表。

_NUM_TILE_VALUES_PER_SUIT = 9  # 每花色 1-9


_SUIT_DECOMP_CACHE: Dict[Tuple[int, ...], List[Tuple[int, int, int]]] = {}
_HONOR_DECOMP_CACHE: Dict[Tuple[int, ...], List[Tuple[int, int, int]]] = {}


def _decompose_suit(counts: List[int]) -> List[Tuple[int, int, int]]:
    """
    动态分解单一数牌花色（懒计算，避免预生成 200 万状态全表）。
    counts: 长度 9 的列表，每个 0-4。
    返回该花色所有可达的 (mentsu, taatsu, pairs) 组合（由调用方取最优向听）。
    其中:
        mentsu = 完整面子(顺子/刻子)数
        taatsu = 部分搭子(边张/嵌张)数，不含对子
        pairs  = 对子数（对子既可作部分面子、也可作雀头）
    """
    results: List[Tuple[int, int, int]] = []
    work = list(counts)

    def dfs(idx: int, mentsu: int, taatsu: int, pairs: int):
        while idx < _NUM_TILE_VALUES_PER_SUIT and work[idx] == 0:
            idx += 1
        if idx == _NUM_TILE_VALUES_PER_SUIT:
            results.append((mentsu, taatsu, pairs))
            return

        c = work[idx]
        # 刻子
        if c >= 3:
            work[idx] -= 3
            dfs(idx, mentsu + 1, taatsu, pairs)
            work[idx] += 3
        # 顺子
        if idx <= 6 and c >= 1 and work[idx + 1] >= 1 and work[idx + 2] >= 1:
            work[idx] -= 1
            work[idx + 1] -= 1
            work[idx + 2] -= 1
            dfs(idx, mentsu + 1, taatsu, pairs)
            work[idx] += 1
            work[idx + 1] += 1
            work[idx + 2] += 1
        # 对子(单独记录，可作雀头或部分面子)
        if c >= 2:
            work[idx] -= 2
            dfs(idx, mentsu, taatsu, pairs + 1)
            work[idx] += 2
        # 边张/嵌张(部分搭子)
        if idx <= 7 and c >= 1 and work[idx + 1] >= 1:
            work[idx] -= 1
            work[idx + 1] -= 1
            dfs(idx, mentsu, taatsu + 1, pairs)
            work[idx] += 1
            work[idx + 1] += 1
        if idx <= 6 and c >= 1 and work[idx + 2] >= 1:
            work[idx] -= 1
            work[idx + 2] -= 1
            dfs(idx, mentsu, taatsu + 1, pairs)
            work[idx] += 1
            work[idx + 2] += 1
        # 孤张
        work[idx] -= 1
        dfs(idx, mentsu, taatsu, pairs)
        work[idx] += 1

    key = tuple(counts)
    cached = _SUIT_DECOMP_CACHE.get(key)
    if cached is not None:
        return cached
    dfs(0, 0, 0, 0)
    _SUIT_DECOMP_CACHE[key] = results
    return results


def _decompose_honors(counts: List[int]) -> List[Tuple[int, int, int]]:
    """
    字牌分解：字牌只能组刻子/对子/孤张（无顺子）。
    counts: 长度 7 的列表（27-33 各花色对应 value 的计数），每个 0-4。
    返回 (mentsu, taatsu, pairs) 组合（字牌无搭子，taatsu 恒为 0）。
    """
    results: List[Tuple[int, int, int]] = []
    work = list(counts)

    def dfs(idx: int, mentsu: int, pairs: int):
        while idx < len(work) and work[idx] == 0:
            idx += 1
        if idx == len(work):
            results.append((mentsu, 0, pairs))
            return
        c = work[idx]
        # 刻子
        if c >= 3:
            work[idx] -= 3
            dfs(idx, mentsu + 1, pairs)
            work[idx] += 3
        # 对子
        if c >= 2:
            work[idx] -= 2
            dfs(idx, mentsu, pairs + 1)
            work[idx] += 2
        # 孤张
        work[idx] -= 1
        dfs(idx, mentsu, pairs)
        work[idx] += 1

    key = tuple(counts)
    cached = _HONOR_DECOMP_CACHE.get(key)
    if cached is not None:
        return cached
    dfs(0, 0, 0)
    _HONOR_DECOMP_CACHE[key] = results
    return results


def _count_tiles_by_value(tiles: List[Tile]) -> TypingCounter[int]:
    """按 value 统计张数（忽略 is_red）"""
    return Counter(t.value for t in tiles)


def _tiles_to_suit_honor_counts(tiles: List[Tile]) -> Tuple[List[int], List[int], List[int], List[int]]:
    """
    将 Tile 列表转为 3 个数牌花色 + 1 个字牌的计数向量。
    返回 (man_counts[9], pin_counts[9], sou_counts[9], honor_counts[7])。
    man: value 0-8, pin: 9-17, sou: 18-26, honor: 27-33。
    """
    man = [0] * 9
    pin = [0] * 9
    sou = [0] * 9
    honor = [0] * 7
    for t in tiles:
        v = t.value
        if v <= 8:
            man[v] += 1
        elif v <= 17:
            pin[v - 9] += 1
        elif v <= 26:
            sou[v - 18] += 1
        else:
            honor[v - 27] += 1
    return man, pin, sou, honor


# ======================================================================
# 3. 手牌分析器 (HandAnalyzer Class)
# ======================================================================


class HandAnalyzer:
    """
    手牌分析器 (双阶段)。
    职责：
    1. 阶段A: calculate_shanten / find_wait_tiles / is_tenpai (基于动态分解，O(快))。
    2. 阶段B: find_all_winning_forms (实例级回溯，仅和牌时)。
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.terminal_honor_values: Set[int] = set(TERMINAL_HONOR_VALUES)
        # 终端字牌 value 列表 (国士用)
        self._kokushi_values: List[int] = sorted(self.terminal_honor_values)

    # ==================================================================
    # == 阶段 A: shanten / tenpai / waits ==
    # ==================================================================

    def calculate_shanten(
        self, hand_tiles: List[Tile], melds: List[Meld], chiitoitsu_ok: bool = True
    ) -> int:
        """
        计算向听数。
        - -1 表示已和牌（完整 14 张）。
        - 0 表示听牌（13 张，差一张和）。
        - 正数表示距离听牌还需的"有效进牌"数。

        Args:
            hand_tiles: 手牌 Tile 列表（门清时含 winning_tile 为 14 张；
                        副露后手牌为 1/4/7/10/13 张）。
            melds: 副露列表（每个副露已是一个完整面子，向听计算时计入 mentsu）。
        """
        # —— 副露面子数（每个 meld 算 1 个完整 mentsu，杠也算 1 个）——
        open_mentsu = len(melds)

        # —— 标准型向听 ——
        # 副露的牌不参与手牌分解，但占用 mentsu 名额
        meld_tile_counts = _count_tiles_by_value(
            [t for m in melds for t in m.tiles]
        )
        # 手牌部分（含可能存在的副露折算）按花色拆分
        man, pin, sou, honor = _tiles_to_suit_honor_counts(hand_tiles)

        # 各花色分解的所有 (mentsu, partial, remain) 组合
        suit_opts = [
            _decompose_suit(man),
            _decompose_suit(pin),
            _decompose_suit(sou),
        ]
        honor_opts = [_decompose_honors(honor)]

        # 标准型需要 4 面子 + 1 雀头。副露已贡献 open_mentsu 个面子。
        # 手牌需贡献 (4 - open_mentsu) 个面子。
        mentsu_needed = 4 - open_mentsu

        best_standard = self._best_standard_shanten(
            suit_opts, honor_opts, mentsu_needed, open_mentsu
        )

        # —— 七对子向听（仅门清）——
        best_chiitoitsu = 99
        if chiitoitsu_ok and open_mentsu == 0:
            best_chiitoitsu = self._chiitoitsu_shanten(hand_tiles)

        # —— 国士向听（仅门清）——
        best_kokushi = 99
        if open_mentsu == 0:
            best_kokushi = self._kokushi_shanten(hand_tiles)

        return min(best_standard, best_chiitoitsu, best_kokushi)

    def _best_standard_shanten(
        self,
        suit_opts: List[List[Tuple[int, int, int]]],
        honor_opts: List[List[Tuple[int, int, int]]],
        mentsu_needed: int,
        open_mentsu: int,
    ) -> int:
        """
        组合 3 数牌花色 + 字牌的分解，取标准型最小向听（经典公式）。

        完整和牌 = 4 面子 + 1 雀头 = 5 块。
        副露贡献 open_mentsu 个完整面子。

        经典公式（对每个花色组合）:
            M = 手牌总面子, T = 总搭子, P = 总对子
            # 4 个面子位 (含副露)
            total_meld_slots = 4
            melds_filled = min(open_mentsu + M, total_meld_slots)
            meld_slots_left = total_meld_slots - melds_filled
            # 部分块(搭子+对子)用于补面子位
            partial_for_meld = min(T + P, meld_slots_left)
            pairs_for_meld = min(P, partial_for_meld)
            pairs_left = P - pairs_for_meld
            # 块数: 已补满 4 面子后, 若有剩余对子可作雀头(第5块)
            blocks = melds_filled + partial_for_meld
            has_pair = 1 if (meld_slots_left == 0 and pairs_left >= 1) else 0
            # 向听: 8 - 2*blocks - has_pair, 但 blocks 上限 5
            # 当 blocks < 4 时无雀头可言
            shanten = 8 - 2 * min(blocks, 5) - has_pair
        """
        all_opts = suit_opts + honor_opts  # 4 组

        best = 99
        total_meld_slots = 4
        for o0 in all_opts[0]:
            for o1 in all_opts[1]:
                for o2 in all_opts[2]:
                    for o3 in all_opts[3]:
                        m_total = o0[0] + o1[0] + o2[0] + o3[0]
                        t_total = o0[1] + o1[1] + o2[1] + o3[1]
                        p_total = o0[2] + o1[2] + o2[2] + o3[2]

                        melds_filled = min(open_mentsu + m_total, total_meld_slots)
                        meld_slots_left = total_meld_slots - melds_filled
                        partial_for_meld = min(t_total + p_total, meld_slots_left)
                        pairs_for_meld = min(p_total, partial_for_meld)
                        pairs_left = p_total - pairs_for_meld

                        blocks = melds_filled + partial_for_meld
                        # 雀头: 只有 4 面子位都填满后, 剩余对子才作雀头
                        has_pair = 1 if (meld_slots_left == 0 and pairs_left >= 1) else 0

                        shanten = 8 - 2 * blocks - has_pair
                        if shanten < best:
                            best = shanten
        return best

    def _chiitoitsu_shanten(self, hand_tiles: List[Tile]) -> int:
        """七对子向听（需门清，手牌 13 张听牌态 / 14 张和牌态）。"""
        counts = _count_tiles_by_value(hand_tiles)
        pairs = sum(1 for c in counts.values() if c >= 2)
        # 七对子需 7 对；多余张数不计
        kinds = len(counts)
        # 向听 = 6 - pairs（手牌 13 张时）；若手牌 14 张且 7 对则为 -1
        if len(hand_tiles) == 14:
            if pairs == 7 and kinds == 7:
                return -1
            return 6 - pairs + 1  # 还需调整
        # 13 张：shanten = 6 - pairs
        return 6 - pairs

    def _kokushi_shanten(self, hand_tiles: List[Tile]) -> int:
        """国士无双向听（需门清）。"""
        counts = _count_tiles_by_value(hand_tiles)
        term_honor_counts = {v: counts.get(v, 0) for v in self._kokushi_values}
        kinds = sum(1 for c in term_honor_counts.values() if c >= 1)
        has_pair = any(c >= 2 for c in term_honor_counts.values())
        if len(hand_tiles) == 14 and kinds == 13 and has_pair:
            return -1
        # 13 张：shanten = 13 - kinds - (1 if has_pair else 0)
        return 13 - kinds - (1 if has_pair else 0)

    def is_tenpai(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """13 张手牌是否听牌。"""
        total = len(hand_tiles) + sum(len(m.tiles) for m in melds)
        if total != 13:
            return False
        return self.calculate_shanten(hand_tiles, melds) == 0

    def find_wait_tiles(self, hand_tiles: List[Tile], melds: List[Meld]) -> Set[int]:
        """
        返回 13 张手牌所听的所有 value 集合（用于振听判定）。
        对每个候选 value，加入后若构成和牌形则该 value 是听的牌。

        优化: 用 check_win_shape (回溯判存在性) 替代 calculate_shanten==-1，
              因为"是否和牌"的回溯判定比算完整向听快得多。
        """
        waits: Set[int] = set()
        total = len(hand_tiles) + sum(len(m.tiles) for m in melds)
        if total != 13:
            return waits

        # 快速剪枝: 非听牌态 (shanten > 0) 不可能有听的牌
        if self.calculate_shanten(hand_tiles, melds) > 0:
            return waits

        cur_counts = _count_tiles_by_value(hand_tiles + [t for m in melds for t in m.tiles])
        is_menzen = not melds

        for v in range(34):
            # 已有 4 张的 value 不可能是听的牌
            if cur_counts.get(v, 0) >= 4:
                continue
            test_tile = Tile(value=v, is_red=False)
            # 用回溯判和牌形 (比完整 shanten 快)
            if self.check_win_shape(hand_tiles + [test_tile], melds, test_tile):
                waits.add(v)
        return waits

    # ==================================================================
    # == 阶段 B: 实例级回溯分解 ==
    # ==================================================================

    def find_all_winning_forms(
        self, hand_tiles: List[Tile], melds: List[Meld], winning_tile: Tile
    ) -> List[WinForm]:
        """
        查找并返回给定 14 张牌（手牌+winning_tile，副露已另算）的所有有效和牌分解。
        返回 Tile 实例级 WinForm 列表。
        """
        all_forms: List[WinForm] = []
        is_menzen = not melds

        # 1. 副露转 HandComponent（is_open=True）
        open_components = [self._meld_to_component(m) for m in melds]

        # 2. 特殊牌型（仅门清）
        if is_menzen:
            all_forms.extend(self._find_kokushi_forms(hand_tiles, winning_tile))
            all_forms.extend(self._find_chiitoitsu_forms(hand_tiles, winning_tile))

        # 3. 标准型
        all_forms.extend(
            self._find_standard_forms(hand_tiles, open_components, winning_tile)
        )
        return all_forms

    def check_win_shape(
        self, hand_tiles: List[Tile], melds: List[Meld], winning_tile: Tile
    ) -> bool:
        """
        检查 14 张牌（含 winning_tile）是否构成和牌形。
        性能优化: 用存在性判定 (找到第一个分解即返回)，不枚举全部。
        """
        is_menzen = not melds

        # 特殊牌型（仅门清，快速判定）
        if is_menzen:
            if self._find_chiitoitsu_forms(hand_tiles, winning_tile):
                return True
            if self._find_kokushi_forms(hand_tiles, winning_tile):
                return True

        # 标准型: 存在性判定
        open_components = [self._meld_to_component(m) for m in melds]
        return self._has_standard_form(hand_tiles, open_components)

    def _has_standard_form(
        self, hand_tiles: List[Tile], open_components: List[HandComponent]
    ) -> bool:
        """快速判断是否存在至少一种标准型分解（找到即返回，不枚举全部）。"""
        melds_needed = 4 - len(open_components)
        if melds_needed < 0:
            return False
        value_counts = _count_tiles_by_value(hand_tiles)
        possible_pairs = {v for v, c in value_counts.items() if c >= 2}
        for pair_value in possible_pairs:
            pair_tiles, remaining = self._take_n_tiles_by_value(hand_tiles, pair_value, 2)
            if pair_tiles is None:
                continue
            if self._has_melds_decomposition(remaining, melds_needed):
                return True
        return False

    def _has_melds_decomposition(self, tiles: List[Tile], k: int) -> bool:
        """快速判断 tiles 能否分解为 k 个面子（找到即返回 True）。"""
        if k == 0:
            return not tiles
        if not tiles:
            return False
        counts = _count_tiles_by_value(tiles)
        min_val = min(counts.keys())
        # 刻子
        if counts[min_val] >= 3:
            _, rest = self._take_n_tiles_by_value(tiles, min_val, 3)
            if self._has_melds_decomposition(rest, k - 1):
                return True
        # 顺子
        if min_val < 27 and min_val % 9 <= 6:
            if counts.get(min_val + 1, 0) >= 1 and counts.get(min_val + 2, 0) >= 1:
                seq, rest = self._take_sequence(tiles, min_val)
                if seq is not None and self._has_melds_decomposition(rest, k - 1):
                    return True
        return False

    # ==================================================================
    # == 内部: 副露转换 ==
    # ==================================================================

    def _meld_to_component(self, meld: Meld) -> HandComponent:
        """将 GameState.Meld 转为 HandComponent (is_open=True)。"""
        if meld.type == ActionType.CHI:
            comp_type = "shuntsu"
        elif meld.type == ActionType.PON:
            comp_type = "koutsu"
        elif meld.type == ActionType.KAN:
            comp_type = "kantsu"
        else:
            # 兜底：当作刻子
            comp_type = "koutsu"
        return HandComponent(type=comp_type, tiles=tuple(meld.tiles), is_open=True)

    # ==================================================================
    # == 内部: 标准型回溯 (Tile 实例级) ==
    # ==================================================================

    def _find_standard_forms(
        self,
        hand_tiles: List[Tile],
        open_components: List[HandComponent],
        winning_tile: Tile,
    ) -> List[WinForm]:
        """查找所有标准型 (4面子1雀头) 分解。"""
        forms: List[WinForm] = []
        melds_needed = 4 - len(open_components)
        if melds_needed < 0:
            return forms

        value_counts = _count_tiles_by_value(hand_tiles)

        # 枚举雀头候选
        possible_pairs = sorted({v for v, c in value_counts.items() if c >= 2})

        for pair_value in possible_pairs:
            # 从手牌移除 2 张 pair_value 的 Tile 实例
            pair_tiles, remaining = self._take_n_tiles_by_value(hand_tiles, pair_value, 2)
            if pair_tiles is None:
                continue
            pair_component = HandComponent(
                type="pair", tiles=tuple(pair_tiles), is_open=False
            )

            # 回溯找 melds_needed 个面子
            for meld_set in self._backtrack_melds(remaining, melds_needed):
                all_components = tuple(open_components) + tuple(meld_set) + (pair_component,)
                forms.append(
                    WinForm(
                        hand_type="standard",
                        components=all_components,
                        winning_tile=winning_tile,
                    )
                )
        return forms

    def _take_n_tiles_by_value(
        self, tiles: List[Tile], value: int, n: int
    ) -> Tuple[Optional[List[Tile]], List[Tile]]:
        """
        从 tiles 中取出 n 张指定 value 的 Tile 实例。
        返回 (取出的列表, 剩余列表)。若不足 n 张返回 (None, tiles)。
        """
        taken: List[Tile] = []
        remaining = list(tiles)
        for t in tiles:
            if t.value == value and len(taken) < n:
                taken.append(t)
                remaining.remove(t)
            if len(taken) == n:
                break
        if len(taken) < n:
            return None, tiles
        return taken, remaining

    def _backtrack_melds(
        self, tiles: List[Tile], k: int
    ) -> Iterator[List[HandComponent]]:
        """
        回溯枚举 tiles 中所有可能的 k 个面子分解（Tile 实例级）。
        剪枝：始终处理最小 value 的牌，保证不重复枚举。
        """
        if k == 0:
            if not tiles:
                yield []
            return
        if not tiles:
            return

        counts = _count_tiles_by_value(tiles)
        # 最小 value
        min_val = min(counts.keys())

        # 分支1: min_val 作刻子
        if counts[min_val] >= 3:
            triplet, rest = self._take_n_tiles_by_value(tiles, min_val, 3)
            if triplet is not None:
                comp = HandComponent(type="koutsu", tiles=tuple(triplet), is_open=False)
                for sub in self._backtrack_melds(rest, k - 1):
                    yield [comp] + sub

        # 分支2: min_val 作顺子（数牌且非 8/9 位）
        if min_val < 27 and min_val % 9 <= 6:
            if counts.get(min_val + 1, 0) >= 1 and counts.get(min_val + 2, 0) >= 1:
                seq, rest = self._take_sequence(tiles, min_val)
                if seq is not None:
                    comp = HandComponent(
                        type="shuntsu", tiles=tuple(seq), is_open=False
                    )
                    for sub in self._backtrack_melds(rest, k - 1):
                        yield [comp] + sub

    def _take_sequence(
        self, tiles: List[Tile], v: int
    ) -> Tuple[Optional[List[Tile]], List[Tile]]:
        """取 v, v+1, v+2 各一张（Tile 实例）。返回 (序列, 剩余)。"""
        remaining = list(tiles)
        seq: List[Tile] = []
        for need in (v, v + 1, v + 2):
            found = None
            for t in remaining:
                if t.value == need:
                    found = t
                    break
            if found is None:
                return None, tiles
            seq.append(found)
            remaining.remove(found)
        return seq, remaining

    # ==================================================================
    # == 内部: 七对子 / 国士 (Tile 实例级) ==
    # ==================================================================

    def _find_chiitoitsu_forms(
        self, hand_tiles: List[Tile], winning_tile: Tile
    ) -> List[WinForm]:
        """七对子判定（需门清，14 张，7 种不同 value 各 2 张）。"""
        if len(hand_tiles) != 14:
            return []
        counts = _count_tiles_by_value(hand_tiles)
        if len(counts) != 7:
            return []
        if not all(c == 2 for c in counts.values()):
            return []
        components = tuple(
            HandComponent(type="pair", tiles=tuple([t for t in hand_tiles if t.value == v][:2]))
            for v in sorted(counts.keys())
        )
        return [
            WinForm(
                hand_type="chiitoitsu",
                components=components,
                winning_tile=winning_tile,
            )
        ]

    def _find_kokushi_forms(
        self, hand_tiles: List[Tile], winning_tile: Tile
    ) -> List[WinForm]:
        """国士无双判定（需门清，13 种幺九字各 1 + 任 1 种成对）。"""
        if len(hand_tiles) != 14:
            return []
        counts = _count_tiles_by_value(hand_tiles)
        # 必须所有牌都是幺九字
        if any(v not in self.terminal_honor_values for v in counts.keys()):
            return []
        # 必须覆盖全部 13 种
        if set(counts.keys()) != self.terminal_honor_values:
            return []
        # 恰好 1 种 2 张，其余 1 张
        pair_val = [v for v, c in counts.items() if c == 2]
        if len(pair_val) != 1:
            return []

        components: List[HandComponent] = []
        for v in sorted(self._kokushi_values):
            tiles_v = [t for t in hand_tiles if t.value == v]
            if v == pair_val[0]:
                components.append(HandComponent(type="pair", tiles=tuple(tiles_v)))
            else:
                components.append(
                    HandComponent(type="kokushi_single", tiles=tuple(tiles_v))
                )
        return [
            WinForm(
                hand_type="kokushi",
                components=tuple(components),
                winning_tile=winning_tile,
            )
        ]
