# 负责对手牌的结构进行分析：和牌形状判断、听牌判断、面子分解。
# 类/函数,函数头,职责描述
# Class,HandAnalyzer,"__init__(self, config)"
# Analysis,"check_win_shape(self, tiles: List[Tile], melds: List[Meld], is_tsumo: bool)",检查手牌和副露是否构成一个合法的和牌形状（标准形、七对子、国士无双）。
# Analysis,"find_winning_forms(self, tiles: List[Tile], melds: List[Meld], winning_tile: Tile)","穷举并返回手牌分解成的所有可能的 (melds, pair) 组合（用于多面听和役种判断）。"
# Tenpai,"is_tenpai(self, hand: List[Tile], melds: List[Meld]) -> bool",判断当前手牌（13张）是否处于听牌状态。
# Tenpai,"find_wait_tiles(self, hand: List[Tile], melds: List[Meld]) -> Set[Tile]",返回手牌听的所有牌张。
# Utility,"get_tile_counts(self, tiles: List[Tile]) -> Counter",快速计算牌的计数器。

# hand_analyzer.py

from typing import List, Set, Counter as TypingCounter
from collections import Counter

# 假设从 actions.py 和 game_state.py 导入
from src.env.core.actions import Tile
from src.env.core.game_state import Meld


# 假设从 constants.py 导入
from src.env.core.rules.constants import TERMINAL_HONOR_VALUES


class HandAnalyzer:
    """
    手牌分析器 (Hand Analyzer)。
    负责所有与手牌“结构”相关的纯计算：
    1. 和牌形状判断 (标准型、七对子、国士无双)
    2. 听牌判断
    3. 面子分解
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        # 幺九牌集合 (从常量模块获取)
        self.terminal_honor_values: Set[int] = TERMINAL_HONOR_VALUES

    # ======================================================================
    # == 公共 API (Public API) ==
    # ======================================================================

    def check_win_shape(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        检查给定的手牌和副露组合是否构成和牌型 (标准型、七对子、国士无双)。

        Args:
            hand_tiles (List[Tile]): 玩家的隐藏手牌列表 (必须包含和牌的那张牌)。
            melds (List[Meld]): 玩家已公开的副露列表。

        Returns:
            bool: 如果构成和牌型，返回 True，否则返回 False。
        """
        #
        all_tiles_in_hand = hand_tiles + [
            tile for meld in melds for tile in meld.get("tiles", [])
        ]
        total_tile_count = len(all_tiles_in_hand)

        # --- 基本检查：和牌必须是14张牌 ---
        if total_tile_count != 14:
            # print(f"Debug check_win: 牌数 {total_tile_count} 不等于 14，无法和牌。")
            return False

        # --- 特殊牌型检查 (仅在门清时可能) ---
        is_menzen = not melds  # 是否门清
        if is_menzen:
            # 检查国士无双 (需要 14 张手牌)
            if self._is_kokushi_raw(hand_tiles, melds):
                # print("Debug check_win: 检测到国士无双")
                return True
            # 检查七对子 (需要 14 张手牌，考虑赤牌)
            if self._is_seven_pairs_raw(hand_tiles, melds):
                # print("Debug check_win: 检测到七对子")
                return True

        # --- 标准牌型检查 (4面子 + 1雀头) ---
        #
        all_tile_values = [tile.value for tile in all_tiles_in_hand]
        value_counts: TypingCounter[int] = Counter(all_tile_values)

        # 尝试将每个出现次数 >= 2 的牌值作为雀头 (对子)
        for pair_value in list(value_counts.keys()):
            if value_counts[pair_value] >= 2:
                remaining_counts = value_counts.copy()
                remaining_counts[pair_value] -= 2
                if remaining_counts[pair_value] == 0:
                    del remaining_counts[pair_value]

                # 3. 计算还需要组成多少个面子 (总共4个)
                # 注意：这里我们只检查手牌部分，副露(Meld)已经是面子了。
                # 我们的目标是把14张牌分解为 4面子+1雀头
                melds_needed = 4

                # 4. 调用递归函数检查剩余的牌是否能组成所需数量的面子
                if self._find_standard_melds_recursive(remaining_counts, melds_needed):
                    return True

        return False

    def is_tenpai(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        检查给定的13张牌组合是否听牌。
        (此逻辑直接迁移自 hand_parser.py)
        """
        current_tiles_count = len(hand_tiles) + sum(
            len(m.get("tiles", [])) for m in melds
        )
        if current_tiles_count != 13:
            # print(f"Debug is_tenpai: 牌数 {current_tiles_count} 不等于 13，无法判断听牌。")
            return False  # 听牌检查基于13张牌

        # 尝试加入所有可能的牌 (0-33)，看是否能和牌
        possible_tile_values = set(range(34))

        # 优化：如果手牌+副露中某种牌已经有4张，则不可能再摸到第5张来和牌
        all_current_tiles = hand_tiles + [t for m in melds for t in m.get("tiles", [])]
        current_value_counts = Counter(t.value for t in all_current_tiles)
        for val, count in current_value_counts.items():
            if count >= 4:
                possible_tile_values.discard(val)  # 移除不可能摸到的牌

        for potential_tile_value in possible_tile_values:
            # 假设用非赤牌测试 (通常足够判断是否听牌结构)
            test_tile = Tile(value=potential_tile_value, is_red=False)

            # 使用 check_win_shape 判断加入这张牌后是否和牌
            if self.check_win_shape(hand_tiles + [test_tile], melds):
                # print(f"Debug is_tenpai: 加入 {test_tile} 可和牌，判定为听牌。")
                return True  # 只要有一种牌能和，就是听牌

        return False

    # ======================================================================
    # == 内部辅助方法 (Internal Helpers) ==
    # ======================================================================

    def _find_standard_melds_recursive(
        self, tile_counts: TypingCounter[int], melds_to_find: int
    ) -> bool:
        """
        递归辅助函数：检查给定的牌值计数是否能组成指定数量的面子。
        (此逻辑基于 hand_parser.py 中的 _find_standard_melds，
         但 hand_parser.py 中该函数缺失，这里我根据上下文补充一个标准实现)
        """
        # 基本情况 1: 成功找到所有面子
        if melds_to_find == 0:
            return sum(tile_counts.values()) == 0  # 必须所有牌都用完

        # 找到最小的牌值进行尝试 (保证处理顺序)
        try:
            min_val = min(tile_counts.keys())
        except ValueError:
            return False  # 牌没了，但还需要面子

        # 尝试移除一个刻子 (三个 min_val)
        if tile_counts[min_val] >= 3:
            next_counts = tile_counts.copy()
            next_counts[min_val] -= 3
            if next_counts[min_val] == 0:
                del next_counts[min_val]
            if self._find_standard_melds_recursive(next_counts, melds_to_find - 1):
                return True

        # 尝试移除一个顺子 (min_val, min_val + 1, min_val + 2)
        is_number_tile = min_val < 27
        can_form_sequence = is_number_tile and (min_val % 9 <= 6)

        if (
            can_form_sequence
            and tile_counts[min_val + 1] > 0
            and tile_counts[min_val + 2] > 0
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

            if self._find_standard_melds_recursive(next_counts, melds_to_find - 1):
                return True

        # 如果以 min_val 开头的刻子和顺子都无法成功，则此路不通
        return False

    def _is_seven_pairs_raw(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        检查七对子 (考虑赤牌)
        (此逻辑直接迁移自 hand_parser.py)
        """
        if melds:
            return False
        if len(hand_tiles) != 14:
            return False
        counts: TypingCounter[Tile] = Counter(
            hand_tiles
        )  # 使用 Tile 对象计数，区分赤牌
        # 七对子必须正好有7种不同的牌，且每种牌恰好有2张
        return len(counts) == 7 and all(c == 2 for c in counts.values())

    def _is_kokushi_raw(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        检查国士无双 (十三幺)
        (此逻辑直接迁移自 hand_parser.py)
        """
        if melds:
            return False
        if len(hand_tiles) != 14:
            return False

        hand_values = {t.value for t in hand_tiles}
        counts = Counter(t.value for t in hand_tiles)

        # 检查是否包含所有幺九牌
        if not self.terminal_honor_values.issubset(hand_values):
            return False

        # 检查是否只有一个对子，其他都是单张
        pair_count = 0
        single_count = 0
        has_non_terminal = False
        for val, count in counts.items():
            if val in self.terminal_honor_values:
                if count == 2:
                    pair_count += 1
                elif count == 1:
                    single_count += 1
                else:
                    return False  # 幺九牌数量不能 > 2 或 < 1
            else:
                has_non_terminal = True
                break

        if has_non_terminal:
            return False

        # 必须是1个对子 + 12个单张幺九牌
        return pair_count == 1 and single_count == 12
