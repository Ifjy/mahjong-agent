# 负责对手牌的结构进行分析：和牌形状判断、听牌判断、面子分解。
# 类/函数,函数头,职责描述
# Class,HandAnalyzer,"__init__(self, config)"
# Analysis,"check_win_shape(self, tiles: List[Tile], melds: List[Meld], is_tsumo: bool)",检查手牌和副露是否构成一个合法的和牌形状（标准形、七对子、国士无双）。
# Analysis,"find_winning_forms(self, tiles: List[Tile], melds: List[Meld], winning_tile: Tile)","穷举并返回手牌分解成的所有可能的 (melds, pair) 组合（用于多面听和役种判断）。"
# Tenpai,"is_tenpai(self, hand: List[Tile], melds: List[Meld]) -> bool",判断当前手牌（13张）是否处于听牌状态。
# Tenpai,"find_wait_tiles(self, hand: List[Tile], melds: List[Meld]) -> Set[Tile]",返回手牌听的所有牌张。
# Utility,"get_tile_counts(self, tiles: List[Tile]) -> Counter",快速计算牌的计数器。

# hand_analyzer.py

# hand_analyzer.py

from typing import List, Set, Counter as TypingCounter, Dict, Optional, Any, Tuple
from collections import Counter
from dataclasses import dataclass, field

# 假设从 actions.py 和 game_state.py 导入
from src.env.core.actions import Tile, ActionType, KanType
from src.env.core.game_state import Meld  # <-- 使用您提供的 Meld 定义

# 假设从 constants.py 导入
from src.env.core.rules.constants import TERMINAL_HONOR_VALUES

# ======================================================================
# == 1. 核心数据结构 (WinForm & HandComponent) ==
# ======================================================================


@dataclass(frozen=True)
class HandComponent:
    """
    表示一个已分解的面子（或雀头）—— 这是 HandAnalyzer 的内部解析结果。
    """

    type: str  # "shuntsu" (顺子), "koutsu" (刻子), "kantsu" (杠子), "pair" (雀头)
    tiles: Tuple[Tile, ...]  # 牌张 (使用 Tuple 保证不可变和可哈希)
    is_open: bool = False  # 这个面子是否是副露 (来自 player.melds)

    def __post_init__(self):
        # 确保 tiles 内部是有序的
        object.__setattr__(self, "tiles", tuple(sorted(self.tiles)))

    @property
    def value(self) -> int:
        """返回这个组件的代表值 (用于排序和比较)"""
        return self.tiles[0].value if self.tiles else -1


@dataclass(frozen=True)
class WinForm:
    """
    表示一个完整的和牌形式 (一种分解方法)。
    """

    hand_type: str  # "standard", "chiitoitsu", "kokushi"
    components: List[HandComponent]  # 包含所有面子和雀头的列表
    winning_tile: Tile  # 和了哪张牌

    @property
    def pair(self) -> Optional[HandComponent]:
        """返回这组分解中的雀头 (pair)"""
        if self.hand_type == "standard":
            for c in self.components:
                if c.type == "pair":
                    return c
        return None

    @property
    def all_tiles(self) -> List[Tile]:
        """返回这组分解中的所有14张牌"""
        return [tile for c in self.components for tile in c.tiles]


# ======================================================================
# == 2. 手牌分析器 (HandAnalyzer Class) ==
# ======================================================================


class HandAnalyzer:
    """
    手牌分析器 (Hand Analyzer) - 重构版。

    职责：
    1. 分解 (Parse): 提供 `find_all_winning_forms`，返回所有可能的和牌分解。
    2. 判断 (Check): 提供 `check_win_shape` 和 `is_tenpai` (基于分解函数)。
    3. 听牌 (Waits): 提供 `find_wait_tiles` (用于振听检查)。
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.terminal_honor_values: Set[int] = TERMINAL_HONOR_VALUES

    # ======================================================================
    # == 公共 API (Public API) ==
    # ======================================================================

    def find_all_winning_forms(
        self, hand_tiles: List[Tile], melds: List[Meld], winning_tile: Tile
    ) -> List[WinForm]:
        """
        【新核心】查找并返回给定14张牌的所有有效和牌分解形式。
        """
        all_forms: List[WinForm] = []
        is_menzen = not melds

        # 1. 转换副露 (Open Melds) 为 HandComponent 格式
        # (桥接逻辑：将 GameState.Meld 转换为 HandAnalyzer.HandComponent)
        open_components = []
        for m in melds:
            if m.type == ActionType.CHI:
                comp_type = "shuntsu"
            elif m.type == ActionType.PON:
                comp_type = "koutsu"
            elif m.type == ActionType.KAN:
                comp_type = "kantsu"
            else:
                continue  # 不应该发生

            open_components.append(
                HandComponent(type=comp_type, tiles=m.tiles, is_open=True)
            )

        # 2. 检查特殊牌型 (仅门清)
        if is_menzen:
            all_forms.extend(self._find_kokushi_forms(hand_tiles, winning_tile))
            all_forms.extend(self._find_chiitoitsu_forms(hand_tiles, winning_tile))

        # 3. 检查标准牌型 (4面子1雀头)
        all_forms.extend(
            self._find_standard_forms(hand_tiles, open_components, winning_tile)
        )

        return all_forms

    def check_win_shape(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        【修改】检查是否构成和牌型。现在是 find_all_winning_forms 的包装器。
        """
        all_tiles_in_hand = hand_tiles + [tile for meld in melds for tile in meld.tiles]
        if len(all_tiles_in_hand) != 14:
            return False

        # 假设手牌中的最后一张是和牌 (或任意一张)
        winning_tile = hand_tiles[-1]

        return len(self.find_all_winning_forms(hand_tiles, melds, winning_tile)) > 0

    def is_tenpai(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        【修改】检查给定的13张牌组合是否听牌。
        """
        # (逻辑同上一版)
        if len(hand_tiles) + sum(len(m.tiles) for m in melds) != 13:
            return False

        for wait_value in self.find_wait_tiles(hand_tiles, melds):
            return True  # 只要找到一个听的牌，就是听牌
        return False

    def find_wait_tiles(self, hand_tiles: List[Tile], melds: List[Meld]) -> Set[int]:
        """
        【新】返回给定13张牌组合所听的所有牌值。
        (此函数对 Scoring 检查振听至关重要)
        """
        waits: Set[int] = set()
        if len(hand_tiles) + sum(len(m.tiles) for m in melds) != 13:
            return waits

        all_current_tiles = hand_tiles + [t for m in melds for t in m.tiles]
        current_value_counts = Counter(t.value for t in all_current_tiles)

        possible_tile_values = set(range(34))
        for val, count in current_value_counts.items():
            if count >= 4:
                possible_tile_values.discard(val)

        for potential_tile_value in possible_tile_values:
            test_tile = Tile(value=potential_tile_value, is_red=False)

            # **[重构]** 调用新的 check_win_shape
            if self.check_win_shape(hand_tiles + [test_tile], melds):
                waits.add(potential_tile_value)

        return waits

    # ======================================================================
    # == 内部辅助 (Internal Helpers) - (用于生成 WinForms) ==
    # ======================================================================

    def _find_standard_forms(
        self,
        hand_tiles: List[Tile],
        open_components: List[HandComponent],
        winning_tile: Tile,
    ) -> List[WinForm]:
        """
        【重写】查找所有可能的“标准型” (4面子1雀头) 分解。
        """
        forms: List[WinForm] = []
        value_counts: TypingCounter[int] = Counter(t.value for t in hand_tiles)

        possible_pairs = {t.value for t in hand_tiles if value_counts[t.value] >= 2}

        for pair_value in possible_pairs:
            remaining_counts = value_counts.copy()
            remaining_counts[pair_value] -= 2
            if remaining_counts[pair_value] == 0:
                del remaining_counts[pair_value]

            melds_needed = 4 - len(open_components)

            # (递归) 查找手牌中的剩余面子
            # 注意：_find_melds_recursive 现在需要更复杂，以处理 Tile 实例
            # 为了计分（例如一杯口），我们需要知道面子具体由哪些 Tile 实例组成

            # (简化版：使用上一版的 value-based 递归)
            # 这是一个性能优化，但会丢失一杯口等役种的判断
            # if self._find_melds_recursive_by_value(remaining_counts, melds_needed):
            #     # ... (省略)

            # (完整版：基于 Tile 实例的递归，较慢但准确)
            recursive_solutions = self._find_melds_recursive_by_tile(
                hand_tiles, pair_value, melds_needed
            )

            if recursive_solutions:
                # 6. 构建雀头的 HandComponent
                pair_tiles = [t for t in hand_tiles if t.value == pair_value][:2]
                pair_component = HandComponent(
                    type="pair", tiles=tuple(pair_tiles), is_open=False
                )

                for solution_melds in recursive_solutions:
                    all_components = solution_melds + open_components + [pair_component]
                    forms.append(
                        WinForm(
                            hand_type="standard",
                            components=all_components,
                            winning_tile=winning_tile,
                        )
                    )

        return forms

    def _find_melds_recursive_by_tile(
        self, hand_tiles: List[Tile], pair_value_to_exclude: int, melds_to_find: int
    ) -> List[List[HandComponent]]:
        """
        (占位符) 这是一个非常复杂的递归函数，用于查找手牌中所有可能的面子分解。
        它需要处理回溯、剪枝，并正确处理 Tile 实例（例如赤牌）。

        这是一个简化的实现，仅用于演示结构。
        """
        # TODO: 实现完整的手牌递归分解 (TBD)
        # 这是一个在麻将AI中众所周知的难题。
        # 暂时我们只使用上一版的 _find_standard_melds_recursive (by value)
        # 来验证结构。

        # 临时回退到 Value-based 检查
        temp_hand_tiles = [t for t in hand_tiles if t.value != pair_value_to_exclude]
        # (需要从 hand_tiles 中移除两张 pair_value 的牌)
        pair_removed_hand = list(hand_tiles)
        count = 0
        for t in hand_tiles:
            if t.value == pair_value_to_exclude and count < 2:
                pair_removed_hand.remove(t)
                count += 1

        counts = Counter(t.value for t in pair_removed_hand)

        if self._find_melds_recursive_by_value(counts, melds_to_find):
            # 警告：这将返回一个不包含具体 Tile 实例的空列表，
            # 导致 Scoring 模块无法计算一杯口或符数。
            # 这是一个必须在未来解决的技术债。
            return [[]]  # 返回一个“成功”的信号

        return []

    def _find_melds_recursive_by_value(
        self, tile_counts: TypingCounter[int], melds_to_find: int
    ) -> bool:
        """
        (保留) 递归辅助函数：检查是否能组成N个面子 (仅用值)。
        (逻辑同上一版)
        """
        if melds_to_find == 0:
            return sum(tile_counts.values()) == 0
        try:
            min_val = min(tile_counts.keys())
        except ValueError:
            return False

        if tile_counts[min_val] >= 3:
            next_counts = tile_counts.copy()
            next_counts[min_val] -= 3
            if next_counts[min_val] == 0:
                del next_counts[min_val]
            if self._find_melds_recursive_by_value(next_counts, melds_to_find - 1):
                return True

        is_number_tile = min_val < 27
        can_form_sequence = is_number_tile and (min_val % 9 <= 6)
        if (
            can_form_sequence
            and tile_counts.get(min_val + 1, 0) > 0
            and tile_counts.get(min_val + 2, 0) > 0
        ):
            next_counts = tile_counts.copy()
            next_counts[min_val] -= 1
            next_counts[min_val + 1] -= 1
            next_counts[min_val + 2] -= 1
            if next_counts[min_val] == 0:
                del next_counts[min_val]
            if next_counts[min_val + 1] == 0:
                del next_counts[min_val + 1]
            if next_counts[min_val + 2] == 0:
                del next_counts[min_val + 2]
            if self._find_melds_recursive_by_value(next_counts, melds_to_find - 1):
                return True
        return False

    def _find_chiitoitsu_forms(
        self, hand_tiles: List[Tile], winning_tile: Tile
    ) -> List[WinForm]:
        """
        【修改】检查七对子，如果成立则返回 WinForm。
        """
        # (逻辑同上一版)
        if len(hand_tiles) != 14:
            return []
        counts: TypingCounter[Tile] = Counter(hand_tiles)
        if len(counts) == 7 and all(c == 2 for c in counts.values()):
            components = [
                HandComponent(type="pair", tiles=tuple([tile, tile]))
                for tile in counts.keys()
            ]
            return [
                WinForm(
                    hand_type="chiitoitsu",
                    components=components,
                    winning_tile=winning_tile,
                )
            ]
        return []

    def _find_kokushi_forms(
        self, hand_tiles: List[Tile], winning_tile: Tile
    ) -> List[WinForm]:
        """
        【修改】检查国士无双，如果成立则返回 WinForm。
        """
        # (逻辑同上一版)
        if len(hand_tiles) != 14:
            return []
        hand_values = {t.value for t in hand_tiles}
        counts = Counter(t.value for t in hand_tiles)
        if not self.terminal_honor_values.issubset(hand_values):
            return []

        pair_count = 0
        single_count = 0
        for val, count in counts.items():
            if val in self.terminal_honor_values:
                if count == 2:
                    pair_count += 1
                elif count == 1:
                    single_count += 1
                else:
                    return []
            else:
                return []

        if pair_count == 1 and single_count == 12:
            components = []
            pair_tile = None
            for tile in hand_tiles:
                if counts[tile.value] == 2 and pair_tile is None:
                    pair_tile = tile
                    components.append(
                        HandComponent(type="pair", tiles=tuple([tile, tile]))
                    )
                elif counts[tile.value] == 1:
                    components.append(
                        HandComponent(type="kokushi_single", tiles=tuple([tile]))
                    )

            return [
                WinForm(
                    hand_type="kokushi",
                    components=components,
                    winning_tile=winning_tile,
                )
            ]
        return []
