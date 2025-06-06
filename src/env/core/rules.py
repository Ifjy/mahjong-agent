from __future__ import annotations
from typing import (
    List,
    Dict,
    Optional,
    Set,
    Tuple,
    Any,
    Counter as TypingCounter,
)  # 引入类型提示
from collections import Counter, defaultdict  # 引入 Counter 和 defaultdict
import itertools  # 可能用于组合生成


# --- 从核心模块导入类 ---
# 假设这些文件在同一目录下或路径已配置
# from .actions import Action, ActionType, Tile, KanType, Meld  # ActionType, Tile, KanType 已在 actions.py 定义
# from .game_state import GamePhase, PlayerState, GameState, Meld # GamePhase, PlayerState, GameState, Meld 已在 game_state.py 定义
# 使用相对导入（如果文件结构支持）
from .actions import Action, ActionType, Tile, KanType
from .game_state import GamePhase, PlayerState, Meld, Wall, GameState

# 确保定义场风索引常量，这些常量本身可以是固定的
ROUND_WIND_EAST = 0
ROUND_WIND_SOUTH = 1
ROUND_WIND_WEST = 2
ROUND_WIND_NORTH = 3

# 定义游戏长度字符串到最大场风索引的映射
GAME_LENGTH_MAX_WIND = {
    "tonpuusen": ROUND_WIND_EAST,  # 东风场结束在东风场打完
    "hanchan": ROUND_WIND_SOUTH,  # 半庄结束在南风场打完
    "issousen": ROUND_WIND_NORTH,  # 一庄结束在北风场打完
    # 根据你需要支持的游戏长度调整
}


class RulesEngine:
    """
    麻将规则引擎。
    核心职责：根据游戏状态 (GameState) 为当前玩家生成所有合法的候选动作 (Action) 列表。
    也包含和牌检查、听牌检查、分数计算等辅助规则功能 (部分功能可能简化或需后续实现)。
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        初始化规则引擎。
        Args:
            config (dict, optional): 用于配置规则变体 (例如赤牌规则, 食断规则等)。
        """
        self.config = config or {}
        # 建议从一个嵌套的 game_rules 字典中获取规则参数，使配置结构清晰
        game_rules_config = self.config.get("game_rules", {})

        # --- 加载游戏流程和结束条件相关的配置 ---

        # 每个场风的局数上限
        self.max_hands_per_wind_cfg: int = game_rules_config.get(
            "max_hands_per_wind", 4
        )

        # 整场游戏结束的最小分数阈值 (飞了)
        self.min_game_end_score_cfg: int = game_rules_config.get(
            "min_game_end_score", 0
        )

        # 根据游戏长度字符串确定最大场风索引
        game_length_str = game_rules_config.get(
            "game_length", "hanchan"
        ).lower()  # 默认为半庄
        self.max_round_wind_index_cfg: int = GAME_LENGTH_MAX_WIND.get(
            game_length_str, GAME_LENGTH_MAX_WIND["hanchan"]
        )  # 如果配置中的字符串无效，也默认为半庄

        # 最大连庄数终止 (八连庄终止)
        self.max_consecutive_dealer_wins_cfg: Optional[int] = game_rules_config.get(
            "max_consecutive_dealer_wins", None
        )

        # 是否允许和了止め (南四局庄家点数第一且和牌/流局听牌时选择结束游戏)
        self.allow_agari_yame_cfg: bool = game_rules_config.get(
            "allow_agari_yame", False
        )

        # 终局点数上限 (达到此点数后游戏结束)
        self.score_threshold_game_end_cfg: Optional[int] = game_rules_config.get(
            "score_threshold_game_end", None
        )
        # 幺九牌集合 (Tile.value) - 用于国士无双等判断
        # 0-8: 万, 9-17: 筒, 18-26: 索, 27:东, 28:南, 29:西, 30:北, 31:白, 32:发, 33:中
        self.terminal_honor_values: Set[int] = {0, 8, 9, 17, 18, 26} | set(
            range(27, 34)
        )
        # TODO: 根据 config 初始化其他规则参数, 例如是否允许食断 (kuitan)

    # ======================================================================
    # == 主要入口：生成候选动作 ==
    # ======================================================================

    def generate_candidate_actions(
        self, game_state: GameState, player_index: int
    ) -> List[Action]:
        """
        为指定玩家在当前游戏状态下生成所有合法的候选动作列表。
        这是 "扁平化候选动作空间" 方法的核心。

        Args:
            game_state (GameState): 当前的游戏状态。
            player_index (int): 需要生成动作的玩家索引。

        Returns:
            List[Action]: 包含所有合法 Action 对象的列表。
        """
        candidates: List[Action] = []
        phase = game_state.game_phase
        player = game_state.players[player_index]

        # --- 根据不同游戏阶段生成动作 ---
        if phase == GamePhase.PLAYER_DISCARD:
            # 玩家摸牌后，轮到他/她打牌或声明特殊动作
            if player_index != game_state.current_player_index:
                print(
                    f"警告: 非当前玩家 {player_index} 在 PLAYER_DISCARD 阶段被请求生成动作 (当前玩家: {game_state.current_player_index})"
                )
                return []  # 非当前玩家在此阶段无动作

            # 1. 检查自摸 (TSUMO)
            # winning_tile 参数理论上是 player.drawn_tile，但为了明确可以传入
            if player.drawn_tile and self._can_tsumo(player, game_state):
                candidates.append(
                    Action(type=ActionType.TSUMO, winning_tile=player.drawn_tile)
                )

            # 2. 检查杠 (KAN) - 暗杠和加杠 (必须在打牌前)
            # 注意: 杠会消耗摸到的牌，与其他选项互斥 (除了特殊情况如杠上开花)
            possible_kans = self._find_self_kans(player, game_state)
            candidates.extend(possible_kans)

            # 3. 检查立直 (RIICHI) - 必须在打牌前宣告，且与杠/自摸通常互斥？
            # (立直时不能选择杠，但可以自摸)
            # 如果有自摸或杠的选项，通常不能同时立直。但如果选择不自摸/不杠，则可能可以立直。
            # 为简化，我们先生成所有可能，环境或Agent策略需要处理互斥性。
            # 如果已经选择了自摸，则不能再选择立直或打牌。
            # 如果已经选择了杠，则不能再选择立直或打牌。
            can_tsumo = any(c.type == ActionType.TSUMO for c in candidates)
            can_kan = any(c.type == ActionType.KAN for c in candidates)

            if (
                not can_tsumo and not can_kan
            ):  # 只有在不自摸也不杠的情况下才考虑立直和打牌
                possible_riichi_discards = self._find_riichi_discards(
                    player, game_state
                )
                for discard_tile in possible_riichi_discards:
                    candidates.append(
                        Action(type=ActionType.RIICHI, riichi_discard=discard_tile)
                    )

                # 4. 生成所有可能的打牌动作 (DISCARD)
                # 如果能立直，打出的牌必须是立直宣告牌；如果不能立直，则可以打任意牌。
                # 如果有立直选项，普通打牌动作是否还应该生成？取决于UI/Agent设计。
                # 方案A：如果能立直，只生成立直动作。
                # 方案B：生成所有打牌动作，Agent需要自己判断打这张牌是否是立直的一部分。
                # 采用方案B，生成所有可能的打牌动作。
                candidates.extend(self._generate_discard_actions(player))
            elif not can_tsumo and can_kan:  # 能杠不能自摸
                # 如果选择了杠，之后会进入打牌阶段，所以这里也需要生成杠之后的打牌选项？
                # 不对，杠是独立动作，选了杠就不能打牌了。这里不生成打牌动作。
                pass
            elif can_tsumo:  # 能自摸
                # 如果选了自摸，不能打牌或立直。
                pass

            # 5. 检查特殊流局 (九种九牌)
            # 只在第一巡且是庄家摸牌后，或非庄家第一次摸牌后，且无人鸣牌时检查
            if (
                game_state.turn_number == 1
                and player.is_menzen
                and self._can_declare_kyuushu_kyuuhai(player, game_state)
            ):
                candidates.append(Action(type=ActionType.SPECIAL_DRAW))

        elif phase == GamePhase.WAITING_FOR_RESPONSE:
            # 其他玩家打牌后，轮到当前玩家响应
            if player_index == game_state.last_discard_player_index:
                print(
                    f"警告: 打牌者 {player_index} 在 WAITING_FOR_RESPONSE 阶段被请求生成动作"
                )
                return []  # 打牌者自己不能响应

            last_discard = game_state.last_discarded_tile
            if not last_discard:
                print(f"警告: 在 WAITING_FOR_RESPONSE 阶段但 last_discarded_tile 为空")
                return [Action(type=ActionType.PASS)]  # 没有牌可响应，只能PASS

            # 1. 检查荣和 (RON)
            if self._can_ron(player, last_discard, game_state):
                candidates.append(
                    Action(type=ActionType.RON, winning_tile=last_discard)
                )

            # 如果已立直，通常不能再进行碰/杠/吃 (除非规则允许？日麻通常不允许)
            if not player.riichi_declared:
                # 2. 检查碰 (PON)
                if self._can_pon(player, last_discard):
                    # PON 动作只需指定牌类型即可
                    pon_tile_type = Tile(
                        value=last_discard.value, is_red=False
                    )  # 用非赤牌代表类型
                    candidates.append(Action(type=ActionType.PON, tile=pon_tile_type))

                # 3. 检查明杠 (KAN - OPEN / Daiminkan)
                if self._can_open_kan(player, last_discard):
                    kan_tile_type = Tile(value=last_discard.value, is_red=False)
                    candidates.append(
                        Action(
                            type=ActionType.KAN,
                            tile=kan_tile_type,
                            kan_type=KanType.OPEN,
                        )
                    )

                # 4. 检查吃 (CHI) - 仅限下家
                if (
                    game_state.last_discard_player_index + 1
                ) % game_state.num_players == player_index:
                    candidates.extend(self._find_chi_actions(player, last_discard))

            # 5. 必须可以 PASS (不响应)
            candidates.append(Action(type=ActionType.PASS))

        # 其他阶段 (如 HAND_START, ACTION_PROCESSING, HAND_OVER_SCORES)
        # 通常不由 Agent 主动选择动作，由环境驱动
        else:
            # print(f"信息: 在阶段 {phase.name} 不为玩家 {player_index} 生成动作。")
            # 如果环境意外地在这些阶段请求动作，返回空列表或仅含PASS可能更安全
            # if not candidates:
            #    candidates.append(Action(type=ActionType.PASS)) # 提供默认PASS避免卡死?
            pass

        return candidates

    # ======================================================================
    # == 辅助函数：生成具体类型的动作 ==
    # ======================================================================

    def _generate_discard_actions(self, player: PlayerState) -> List[Action]:
        """为打牌阶段生成所有可能的打牌动作 (每个 Tile 实例生成一个动作)"""
        discard_actions: List[Action] = []

        # 可以打出的牌包括手牌和刚摸到的牌
        full_hand_tiles = player.hand + (
            [player.drawn_tile] if player.drawn_tile else []
        )

        # TODO: 考虑食替 (kuikae) 规则 - 如果刚刚进行了吃/碰，不允许立即打出相关的牌
        # restricted_kuikae_tiles = self._get_kuikae_restrictions(player, game_state.last_action_info)

        processed_tiles = set()  # 对于值和赤牌状态都相同的牌，只生成一个代表性动作
        for tile in full_hand_tiles:
            # if tile in restricted_kuikae_tiles: continue # 跳过食替限制的牌

            # 使用 (value, is_red) 作为 key 来判断是否处理过同种牌
            tile_key = (tile.value, tile.is_red)
            if tile_key not in processed_tiles:
                discard_actions.append(Action(type=ActionType.DISCARD, tile=tile))
                processed_tiles.add(tile_key)

        return discard_actions

    def _find_self_kans(
        self, player: PlayerState, game_state: GameState
    ) -> List[Action]:
        """查找玩家在自己回合可以进行的杠 (暗杠, 加杠)"""
        kan_actions: List[Action] = []

        # 考虑手牌 + 摸到的牌
        full_hand_tiles = player.hand + (
            [player.drawn_tile] if player.drawn_tile else []
        )

        # --- 1. 查找暗杠 (Ankan) ---
        # 玩家手牌+摸牌中是否有 4 张相同的 Tile 对象
        # 使用 Counter 统计每种 Tile (考虑赤牌) 的数量
        tile_counts: TypingCounter[Tile] = Counter(full_hand_tiles)
        # todo 查看 红宝牌的影响
        for (
            tile_object,
            count,
        ) in tile_counts.items():  # 迭代的是具体的 Tile 对象和它的数量
            if count == 4:
                # 玩家有 4 张这个特定的 Tile 对象 (例如，4x Tile(5, False))

                # TODO: 检查立直后暗杠是否改变听牌 (Kan shite mo fuuri)
                # Rule: Cannot perform Ankan after Riichi if it changes the wait.
                # This needs a helper method that can analyze the hand BEFORE removing the 4 tiles
                # and AFTER removing the 4 tiles, then compare the waits.
                # if player.riichi_declared and self._ankan_changes_wait(player, tile_object, game_state):
                #     continue # 如果改变听牌则不允许生成此动作

                # 找到实际构成暗杠的 4 张 Tile 对象 (它们就是手牌中那 4 张匹配的 Tile 对象)
                ankan_meld_tiles = [
                    t for t in full_hand_tiles if t == tile_object
                ]  # 精确查找这 4 张牌对象

                # 创建 Action 对象，包含构成杠的具体 Tile 对象列表
                kan_actions.append(
                    Action(
                        type=ActionType.KAN,
                        kan_type=KanType.CLOSED,
                        # tile=tile_object # 可选，作为代表牌，但主要信息在 meld_tiles
                    )
                )

        # --- 2. 查找加杠 (Kakan) ---
        # 加杠必须使用 手牌中的一张 或 摸到的牌 来加到已有的碰上
        # 玩家的碰牌副露 player.melds
        # 可用于加杠的牌池 full_hand_tiles

        # 为了防止重复，我们需要记录哪些碰牌已经被考虑用于加杠
        processed_kakan_pon_melds_indices = set()  # 记录已处理的碰牌副露的索引

        for meld_index, meld in enumerate(player.melds):
            if meld["type"] == ActionType.PON:
                # 这个副露是一个碰牌 (3 张)
                # 查找手牌+摸牌区是否有可以加到这个碰上的牌
                pon_tile_value = meld["tiles"][0].value  # 碰牌的数值

                # 查找 full_hand_tiles 中是否有与 pon_tile_value 数值相同的牌
                # 注意：加杠允许混搭红宝牌和普通牌，所以这里只检查数值和花色
                matching_tile_in_hand_or_drawn: Optional[Tile] = None
                for tile in full_hand_tiles:
                    # 检查数值和花色是否匹配碰牌
                    if tile.value == pon_tile_value:
                        matching_tile_in_hand_or_drawn = (
                            tile  # 找到那张可以加杠的牌对象
                        )
                        break  # 找到一张即可（当前规则假设一个碰牌最多加杠一次）

                if matching_tile_in_hand_or_drawn:
                    # 找到了可以加杠的牌对象
                    # TODO: 检查立直后加杠是否允许 (Kan shite mo fuuri)
                    # Rule: Cannot perform Kakan after Riichi if it changes the wait (usually always changes wait).
                    # This needs a helper method.
                    # if player.riichi_declared and self._kakan_changes_wait(player, matching_tile_in_hand_or_drawn, meld_index, game_state):
                    #     continue # 如果改变听牌则不允许生成此动作

                    # 检查这个碰牌副露是否已经被考虑用于加杠（防止手牌有多张同值牌时重复生成）
                    # 我们只为这个特定的碰牌副露生成一个加杠动作，无论手牌有几张同值牌可以加。
                    if meld_index in processed_kakan_pon_melds_indices:
                        continue  # 已经生成过动作

                    # 创建 Action 对象，包含构成杠的具体 Tile 对象列表
                    # 加杠副露包含碰牌的 3 张 + 加杠的 1 张
                    kakan_meld_tiles = meld["tiles"] + [matching_tile_in_hand_or_drawn]

                    kan_actions.append(
                        Action(
                            type=ActionType.KAN,
                            kan_type=KanType.ADDED,
                            tile=matching_tile_in_hand_or_drawn,  # <-- 指定那张加杠的牌对象
                        )
                    )
                    processed_kakan_pon_melds_indices.add(
                        meld_index
                    )  # 标记此碰牌副露已处理

        return kan_actions

    # TODO: Implement helper methods for 立直后杠改变听牌检查 (非常复杂)
    # _ankan_changes_wait(self, player: PlayerState, kan_tile_object: Tile, game_state: GameState) -> bool
    # _kakan_changes_wait(self, player: PlayerState, kakan_tile_object: Tile, pon_meld_index: int, game_state: GameState) -> bool

    # TODO: Ensure Tile has a suit attribute (0=万, 1=筒, 2=索, 3=字) for suit matching in Kakan and Open Kan.
    # TODO: Ensure ActionType has PON.
    # TODO: Ensure KanType has CLOSED and ADDED.

    def _find_open_kans_response(
        self, player: PlayerState, game_state: GameState
    ) -> List[Action]:
        """查找玩家对最后一张弃牌可以进行的明杠 (Daiminkan)"""
        open_kan_actions: List[Action] = []

        # 明杠是对最后一张弃牌的响应动作
        last_discarded_tile = game_state.last_discarded_tile
        if last_discarded_tile is None:
            return open_kan_actions  # 没有弃牌，不能明杠

        discarder_index = game_state.last_discard_player_index
        if discarder_index is None or player.player_index == discarder_index:
            return open_kan_actions  # 玩家不能杠自己的弃牌 (除非特殊规则，通常不是明杠)

        # --- 检查玩家手牌是否满足明杠条件 ---
        # 玩家手牌中需要有 3 张牌，与被弃牌数值和花色相同（允许混合红/普通牌）
        # 这些牌将与被弃牌一起构成 4 张的明杠副露

        target_value = last_discarded_tile.value
        # 查找玩家 HAND (不是手牌+摸牌) 中与弃牌数值和花色匹配的牌
        matching_hand_tiles = [
            t for t in player.hand if t.value == target_value  # 检查数值和花色
        ]

        # 玩家需要至少 3 张这样的牌在手牌中
        if len(matching_hand_tiles) >= 3:
            # 可以进行明杠。需要选择 3 张具体的牌对象与弃牌构成副露。
            # 如果手牌中有超过 3 张匹配的牌（例如 4 张），也只需要用其中 3 张。
            # 规则通常不指定具体用哪 3 张，只要是符合数值花色的即可。
            # 简单起见，选择找到的前 3 张匹配的牌对象。
            tiles_from_hand_for_meld = matching_hand_tiles[:3]
            # 创建构成明杠副露的 Tile 对象列表：被弃牌 + 手牌中的 3 张
            open_kan_meld_tiles = [last_discarded_tile] + tiles_from_hand_for_meld
            # 创建 Action 对象，包含构成杠的具体 Tile 对象列表
            open_kan_actions.append(
                Action(
                    type=ActionType.KAN,
                    kan_type=KanType.OPEN,
                    tile=last_discarded_tile,  # 响应动作通常用 tile 参数表示目标牌
                )
            )

        return open_kan_actions

    # TODO: Ensure Tile has a suit attribute.
    # TODO: Ensure ActionType has KAN.
    # TODO: Ensure KanType has OPEN.
    # TODO: This method should be called in generate_candidate_actions during WAITING_FOR_RESPONSE phase.
    # TODO: Consider rules like Furiten (虽然主要影响 Ron，但理论上可能影响明杠的合法性，取决于具体规则实现)
    # TODO: 检查玩家是否在立直状态。立直后不可以明杠

    def _find_riichi_discards(
        self, player: PlayerState, game_state: GameState
    ) -> List[Tile]:
        """查找宣告立直时可以打出的牌 (打了之后必须听牌)"""
        riichi_discards: List[Tile] = []
        if not self._can_declare_riichi(player, game_state):
            return []  # 不满足立直基本条件

        possible_discards = player.hand + (
            [player.drawn_tile] if player.drawn_tile else []
        )

        processed_tile_keys = set()  # 处理同种牌 (value, is_red)
        for tile_to_discard in possible_discards:
            tile_key = (tile_to_discard.value, tile_to_discard.is_red)
            if tile_key in processed_tile_keys:
                continue
            processed_tile_keys.add(tile_key)

            # 模拟打出这张牌后的手牌 (需要复制列表)
            temp_full_hand = list(possible_discards)

            # 需要找到并移除正确的 Tile 实例
            found = False
            for i, t in enumerate(temp_full_hand):
                if (
                    t == tile_to_discard
                ):  # 使用 Tile 的 __eq__ (应该基于 value 和 is_red)
                    temp_hand_after_discard = (
                        temp_full_hand[:i] + temp_full_hand[i + 1 :]
                    )
                    found = True
                    break
            if not found:
                continue  # 理应找到

            # 检查剩余13张牌是否听牌
            if self.is_tenpai(temp_hand_after_discard, player.melds):
                riichi_discards.append(tile_to_discard)

        return riichi_discards

    def _find_chi_actions(
        self, player: PlayerState, discarded_tile: Tile
    ) -> List[Action]:
        """为响应阶段查找所有可能的吃牌动作"""
        chi_actions: List[Action] = []
        if discarded_tile.value >= 27:  # 字牌不能吃
            return []
        if player.riichi_declared:  # 立直后不能吃
            return []

        hand_tiles = player.hand  # 只用手牌吃
        # 使用 Counter 统计手牌中各种 Tile 的数量，方便查找
        hand_counts: TypingCounter[Tile] = Counter(hand_tiles)

        target_value = discarded_tile.value

        # --- 检查三种可能的顺子组合 ---
        # 模式 1: 需要 T-2, T-1 (例如，有 3m, 4m，吃 5m)
        if target_value % 9 >= 2:  # 牌值需 >= 2 (索引)
            val1, val2 = target_value - 1, target_value - 2
            # 查找手牌中是否有这两张牌 (需要具体 Tile 实例)
            tile1_options = [t for t in hand_tiles if t.value == val1]
            tile2_options = [t for t in hand_tiles if t.value == val2]
            # 如果同一种牌有多张 (例如多张赤牌)，理论上可以形成多种吃的组合
            # 为简化，我们只取第一种组合
            if tile1_options and tile2_options:
                # 取第一个找到的实例
                chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                chi_actions.append(
                    Action(
                        type=ActionType.CHI, chi_tiles=chi_combo, tile=discarded_tile
                    )
                )

        # 模式 2: 需要 T-1, T+1 (例如，有 4m, 6m，吃 5m)
        if 1 <= target_value % 9 <= 7:  # 牌值需 1-7 (索引)
            val1, val2 = target_value - 1, target_value + 1
            tile1_options = [t for t in hand_tiles if t.value == val1]
            tile2_options = [t for t in hand_tiles if t.value == val2]
            if tile1_options and tile2_options:
                # 处理 T-1 和 T+1 是同一张牌的情况 (例如手牌446，吃5)
                if val1 == val2 and hand_counts[tile1_options[0]] >= 2:
                    # 找到两张不同的实例 (如果赤牌不同) 或同一实例计数>=2
                    # 简化：如果计数>=2，就认为可以吃
                    chi_combo = tuple(
                        sorted((tile1_options[0], tile2_options[0]))
                    )  # 这样写可能有问题
                    # 正确做法：从 tile1_options 和 tile2_options 中各取一个
                    chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                    chi_actions.append(
                        Action(
                            type=ActionType.CHI,
                            chi_tiles=chi_combo,
                            tile=discarded_tile,
                        )
                    )
                elif val1 != val2:
                    chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                    chi_actions.append(
                        Action(
                            type=ActionType.CHI,
                            chi_tiles=chi_combo,
                            tile=discarded_tile,
                        )
                    )

        # 模式 3: 需要 T+1, T+2 (例如，有 6m, 7m，吃 5m)
        if target_value % 9 <= 6:  # 牌值需 <= 6 (索引)
            val1, val2 = target_value + 1, target_value + 2
            tile1_options = [t for t in hand_tiles if t.value == val1]
            tile2_options = [t for t in hand_tiles if t.value == val2]
            if tile1_options and tile2_options:
                chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                chi_actions.append(
                    Action(
                        type=ActionType.CHI, chi_tiles=chi_combo, tile=discarded_tile
                    )
                )

        # 去重 (可能因为赤牌导致生成了相同的逻辑吃法)
        unique_chi_actions = []
        seen_chi_tiles = set()
        for action in chi_actions:
            if action.chi_tiles not in seen_chi_tiles:
                unique_chi_actions.append(action)
                seen_chi_tiles.add(action.chi_tiles)

        return unique_chi_actions

    # ======================================================================
    # == 辅助函数：检查动作前提条件 ==
    # ======================================================================

    def _can_tsumo(self, player: PlayerState, game_state: GameState) -> bool:
        """检查玩家摸牌后是否能自摸"""
        if not player.drawn_tile:
            return False

        # 组成完整手牌 (手牌 + 摸的牌)
        # 注意：check_win 需要处理14张牌
        full_hand = player.hand + [player.drawn_tile]

        # 1. 检查是否和牌型
        if not self._check_basic_win_shape(full_hand, player.melds):
            return False

        # 2. 检查是否有役 (一番缚)
        # 调用专门检查是否有役的方法，传入必要的上下文信息
        # is_tsumo=True 表示这是自摸的情况
        # win_tile 对于自摸就是摸到的牌
        if not self.has_at_least_one_yaku(
            full_hand,
            player.melds,
            player.drawn_tile,
            player,
            game_state,
            is_tsumo=True,
        ):
            # print(f"Debug: 玩家 {player.player_id} 自摸 {player.drawn_tile} 满足形状但无役")
            return False

        return True

    

    # 检查振听的方法 (你需要实现它)
    def _is_furiten(
        self, player: PlayerState, win_tile: Tile, game_state: GameState
    ) -> bool:
        """检查玩家是否处于振听状态 (对目标牌不能荣和)"""
        # TODO: 实现振听检查逻辑
        # 1. 手牌听的牌是否在自己的牌河 (player.discards) 中？
        #    - 需要先计算 player.hand (不含摸牌) 和 player.melds 的听牌列表。
        #    - 遍历听牌列表，检查是否在 player.discards 中。
        # 2. 立直后过手？（对任意听牌，立直后如果对别家打出的牌没有荣和，则进入永久振听直到下次摸牌）
        # 3. 同巡振听？（对本次摸牌后，到自己下次摸牌前的任何其他玩家的弃牌，如果听的牌过手，则对本巡的任何听牌对象都振听）

        print("Warning: _is_furiten is a placeholder, implement actual furiten logic.")
        return False  # Placeholder: 暂时假设没有振听

    def _can_ron(
        self, player: PlayerState, target_tile: Tile, game_state: GameState
    ) -> bool:
        """检查玩家是否能荣和目标牌"""
        # 使用 'is None' 更清晰地检查 None
        if target_tile is None:
            # print("Debug: 目标牌为 None，不能荣和")
            return False

        # 组成模拟手牌 (手牌 + 目标牌)
        # 使用 list()[:] 或 copy.copy() 创建浅拷贝，避免修改原始 player.hand
        # 假设 player.hand 是列表
        potential_hand = list(player.hand) + [target_tile]
        # 为了 check_win 函数内部逻辑可能需要的排序，这里排序一下模拟手牌
        potential_hand.sort()

        # 1. 检查是否和牌型 (基本形状)
        # check_win 只需要手牌和副露来判断形状
        if not self._check_basic_win_shape(potential_hand, player.melds):
            # print(f"Debug: 玩家 {player.player_id} 荣和 {target_tile} 不满足和牌形状")
            return False

        # 2. 检查振听 (Furiten)
        # 需要实现 _is_furiten 方法
        # _is_furiten 需要玩家的原始手牌（不含 target_tile）、牌河、立直状态等信息
        # 这里传入 player 即可
        if self._is_furiten(player, target_tile, game_state):
            # print(f"Debug: 玩家 {player.player_id} 荣和 {target_tile} 触发振听")
            return False

        # 3. 检查是否有役 (一番缚)
        # 调用专门检查是否有役的方法，传入必要的上下文信息
        # is_tsumo=False 表示这是荣和的情况
        if not self.has_at_least_one_yaku(
            potential_hand,
            player.melds,
            target_tile,
            player,
            game_state,
            is_tsumo=False,
        ):
            # print(f"Debug: 玩家 {player.player_id} 荣和 {target_tile} 满足形状但无役")
            return False

        # 4. (可选) 其他特殊荣和限制，如头跳等。
        # 如果你实现了头跳规则，这里可能需要检查是否有其他玩家对同一张牌比当前玩家更早宣告了荣和。
        # 这需要 game_state 中记录当前弃牌被哪些玩家宣告了荣和，以及处理顺序。
        # if self._check_other_ron_restrictions(player, target_tile, game_state):
        #      return False # 因为规则禁止而不能荣和

        # 如果通过所有检查，则可以荣和
        # print(f"Debug: 玩家 {player.player_id} 可以荣和 {target_tile}")
        return True

    def _can_pon(self, player: PlayerState, target_tile: Tile) -> bool:
        """检查玩家是否能碰目标牌"""
        if not target_tile:
            return False
        if player.riichi_declared:
            return False  # 立直后不能碰

        # 手牌中至少有两张同种牌 (只比较 value)
        count = sum(1 for t in player.hand if t.value == target_tile.value)
        return count >= 2

    def _can_open_kan(self, player: PlayerState, target_tile: Tile) -> bool:
        """检查玩家是否能明杠目标牌"""
        if not target_tile:
            return False
        if player.riichi_declared:
            return False  # 立直后不能明杠

        # 手牌中至少有三张同种牌 (只比较 value)
        count = sum(1 for t in player.hand if t.value == target_tile.value)
        return count >= 3

    def _can_declare_riichi(self, player: PlayerState, game_state: GameState) -> bool:
        """检查是否满足立直的基本条件 (门清、分数、剩余牌数、未立直)"""
        return (
            player.is_menzen
            and player.score >= 1000
            and game_state.wall.get_remaining_live_tiles_count() >= 4
            and not player.riichi_declared
        )

    def _can_declare_kyuushu_kyuuhai(
        self, player: PlayerState, game_state: GameState
    ) -> bool:
        """检查是否满足九种九牌流局条件"""
        # 条件: 第一巡，无人鸣牌，手牌(含摸牌)中幺九牌种类>=9
        if game_state.turn_number != 1 or not player.is_menzen:
            return False
        # 检查是否轮到自己摸第一张牌（庄家是第0巡摸牌，子家是第1巡摸牌）
        # 这个逻辑可能需要更精确的巡目/摸牌次数判断

        # 检查手牌+摸牌
        full_hand = player.hand + ([player.drawn_tile] if player.drawn_tile else [])
        if len(full_hand) != 14:  # 必须是刚摸完牌
            return False

        unique_terminal_honors_count = len(
            {t.value for t in full_hand if t.value in self.terminal_honor_values}
        )
        return unique_terminal_honors_count >= 9

    # ======================================================================
    # == 核心规则：和牌检查与听牌检查 (占位符 - 需要完整实现!) ==
    # ======================================================================
    def _find_standard_melds(
        self, tile_counts: TypingCounter[int], melds_to_find: int
    ) -> bool:
        """
        递归辅助函数：检查给定的牌值计数是否能组成指定数量的面子 (刻子或顺子)。

        Args:
            tile_counts (TypingCounter[int]): 按牌值 (0-33) 统计的牌数量。
                                             注意：这里不区分赤牌，只关心牌的数值。
            melds_to_find (int): 需要找到的面子数量。

        Returns:
            bool: 如果可以组成指定数量的面子，则返回 True，否则返回 False。
        """
        # 基本情况 1: 成功找到所有面子
        if melds_to_find == 0:
            # 如果所有牌都用完了，说明成功分解
            return not tile_counts or sum(tile_counts.values()) == 0

        # 基本情况 2: 牌不够组成剩余面子 (剪枝优化)
        if sum(tile_counts.values()) < melds_to_find * 3:
            return False

        # 获取当前计数中最小的牌值进行尝试 (保证处理顺序，避免重复)
        # 如果 tile_counts 为空，min 会报错，但理论上会被 melds_to_find == 0 或 牌不够的检查拦截
        try:
            min_val = min(tile_counts.keys())
        except ValueError:
            # 如果 tile_counts 为空但 melds_to_find > 0, 意味着牌用完了但面子没找够
            return False

        # 尝试移除一个刻子 (三个 min_val)
        if tile_counts[min_val] >= 3:
            # 创建一个副本进行修改
            next_counts = tile_counts.copy()
            next_counts[min_val] -= 3
            # 如果该牌值数量变为0，从 Counter 中移除键
            if next_counts[min_val] == 0:
                del next_counts[min_val]
            # 递归调用，需要找的面子数减一
            if self._find_standard_melds(next_counts, melds_to_find - 1):
                return True  # 如果这条路成功了，直接返回 True

        # 尝试移除一个顺子 (min_val, min_val + 1, min_val + 2)
        # 检查条件：1. 不能是字牌 2. 不能是 8 或 9 开头 (会超出同花色范围)
        is_number_tile = min_val < 27
        can_form_sequence = is_number_tile and (min_val % 9 <= 6)

        if (
            can_form_sequence
            and tile_counts[min_val + 1] > 0
            and tile_counts[min_val + 2] > 0
        ):
            # 创建一个副本进行修改
            next_counts = tile_counts.copy()
            next_counts[min_val] -= 1
            next_counts[min_val + 1] -= 1
            next_counts[min_val + 2] -= 1

            # 如果牌值数量变为0，则移除键
            if next_counts[min_val] == 0:
                del next_counts[min_val]
            if next_counts[min_val + 1] == 0:
                del next_counts[min_val + 1]
            if next_counts[min_val + 2] == 0:
                del next_counts[min_val + 2]

            # 递归调用，需要找的面子数减一
            if self._find_standard_melds(next_counts, melds_to_find - 1):
                return True  # 如果这条路成功了，直接返回 True

        # 如果以 min_val 开头的刻子和顺子都无法成功构成剩余面子，则这条路失败
        return False

    def _check_basic_win_shape(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        检查给定的手牌和副露组合是否构成和牌型 (标准型、七对子、国士无双)。

        Args:
            hand_tiles (List[Tile]): 玩家的隐藏手牌列表 (可能包含刚摸到/荣和的牌)。
                                      对于门清听牌检查，这里是13张。
                                      对于和牌检查，这里包含和牌的那张，总共应构成14张牌（加上副露）。
            melds (List[Meld]): 玩家已公开的副露列表。

        Returns:
            bool: 如果构成和牌型，返回 True，否则返回 False。
        """
        all_tiles_in_hand = hand_tiles + [
            tile for meld in melds for tile in meld["tiles"]
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
        # 使用 Counter 按牌值统计所有14张牌的数量 (忽略赤牌进行结构检查)
        # 注意：Meld 中的牌也需要包含进来统计
        all_tile_values = [tile.value for tile in all_tiles_in_hand]
        value_counts: TypingCounter[int] = Counter(all_tile_values)

        # 尝试将每个出现次数 >= 2 的牌值作为雀头 (对子)
        for pair_value in list(
            value_counts.keys()
        ):  # 使用 list 避免在迭代中修改 counter
            if value_counts[pair_value] >= 2:
                # 1. 复制当前的牌值计数
                remaining_counts = value_counts.copy()

                # 2. 从计数中移除雀头 (两张)
                remaining_counts[pair_value] -= 2
                if remaining_counts[pair_value] == 0:
                    del remaining_counts[pair_value]  # 移除数量为0的键

                # 3. 计算还需要组成多少个面子 (总共4个)
                melds_needed = 4

                # 4. 调用递归函数检查剩余的牌是否能组成所需数量的面子
                # print(f"Debug check_win: 尝试以 {pair_value} 作为雀头, 检查剩余牌: {remaining_counts}, 需要面子: {melds_needed}")
                if self._find_standard_melds(remaining_counts, melds_needed):
                    # print(f"Debug check_win: 成功找到以 {pair_value} 为雀头的标准和牌型")
                    return True  # 如果找到一种合法的分解方式，则手牌有效

        # 如果尝试了所有可能的雀头都无法构成 4 面子，则不是标准和牌型
        # print("Debug check_win: 未找到有效的标准和牌分解")
        return False

    def _is_standard_hand_recursive(
        self, hand_tiles: List[Tile], melds: List[Meld]
    ) -> bool:
        """(占位符) 递归检查标准型 4面子+1雀头"""
        # TBD: 实现复杂的递归分解算法
        # print("Debug: _is_standard_hand_recursive - 占位符返回 False")
        return False

    def _is_seven_pairs_raw(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """检查七对子 (考虑赤牌)"""
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
        """检查国士无双 (十三幺)"""
        if melds:
            return False
        if len(hand_tiles) != 14:
            return False

        # 国士无双必须包含所有13种幺九牌，其中一种是 对子
        hand_values = {t.value for t in hand_tiles}
        counts = Counter(t.value for t in hand_tiles)  # 按牌值计数

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
                else:  # 幺九牌数量不能 > 2 或 < 1
                    return False
            else:  # 不能包含非幺九牌
                has_non_terminal = True
                break  # 提前退出

        if has_non_terminal:
            return False

        # 必须是1个对子 + 12个单张幺九牌
        return pair_count == 1 and single_count == 12

    def is_tenpai(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """检查给定的13张牌组合是否听牌"""
        current_tiles_count = len(hand_tiles) + sum(len(m["tiles"]) for m in melds)
        if current_tiles_count != 13:
            # print(f"Debug is_tenpai: 牌数 {current_tiles_count} 不等于 13，无法判断听牌。")
            return False  # 听牌检查基于13张牌

        # 尝试加入所有可能的牌 (0-33)，看是否能和牌
        possible_tile_values = set(range(34))  # 所有基础牌值

        # 优化：如果手牌+副露中某种牌已经有4张，则不可能再摸到第5张来和牌
        all_current_tiles = hand_tiles + [t for m in melds for t in m["tiles"]]
        current_value_counts = Counter(t.value for t in all_current_tiles)
        for val, count in current_value_counts.items():
            if count >= 4:
                possible_tile_values.discard(val)  # 移除不可能摸到的牌

        for potential_tile_value in possible_tile_values:
            # 假设用非赤牌测试 (通常足够判断是否听牌结构)
            test_tile = Tile(value=potential_tile_value, is_red=False)
            # 使用 check_win 判断加入这张牌后是否和牌
            if self._check_basic_win_shape(hand_tiles + [test_tile], melds):
                # print(f"Debug is_tenpai: 加入 {test_tile} 可和牌，判定为听牌。")
                return True  # 只要有一种牌能和，就是听牌

        # print("Debug is_tenpai: 尝试所有可能牌后均无法和牌，判定为未听牌。")
        return False

    def is_hand_over(self, game_state: "GameState") -> bool:
        """
        判断当前游戏状态是否表示一局游戏已经结束 (有人和牌或流局)。
        这个方法由环境 (MahjongEnv.step) 调用。
        """
        # 一局游戏结束的标志通常是 GameState 的 game_phase 转换到了某个结束阶段。
        # GameState 的 apply_action 或其辅助方法 (如 _transition_to_scoring, _transition_to_abortive_draw)
        # 负责在和牌或流局发生时将 game_phase 设置为 GamePhase.HAND_OVER_SCORES。
        # 因此 RulesEngine 只需检查这个阶段标志即可。

        return game_state.game_phase == GamePhase.HAND_OVER_SCORES

        # 注意：如果你的 GameState 中有一个 _hand_over_flag，并且 apply_action 负责设置它，
        # 那么这里也可以检查 return game_state._hand_over_flag。
        # 推荐的做法是 game_phase 作为主要的状态标志。

    def is_game_over(self, game_state: "GameState") -> bool:
        """
        判断整场游戏是否已结束。包含各种游戏结束条件。
        这个方法由环境 (MahjongEnv.step) 调用。
        它在 determine_next_hand_state 和 apply_next_hand_state 之后被调用，
        所以 game_state 中的轮次、场风、庄家等已经是下一局的值了。
        """
        # 检查游戏结束条件基于 GameState 的当前状态 (注意，此时的状态是下一局的状态)

        # 1. 检查是否完成预定场数 (例如，假设打半庄，完成南4局)
        # 假设游戏在完成设定的最后一场风的最后一局，并且庄家轮换时结束。
        # 如果计算出的下一局状态的场风超过了游戏设定的最大场风，通常意味着游戏结束。
        # 假设游戏只打东风场和南风场 (半庄)， ROUND_WIND_SOUTH 是最后一个场风。
        max_game_round_wind = ROUND_WIND_SOUTH  # 半庄结束在南风场结束后

        # 如果下一局的场风超过了最大允许场风，并且是新场风的第一局 (next_round_number == 1)
        # 这通常意味着前一局是最后一个场风的最后一局，并且庄家轮换了。
        if game_state.round_wind > max_game_round_wind and game_state.round_number == 1:
            print("Debug Game Over: 完成最后一场风，游戏结束。")
            # TODO: 如果打全庄 (一庄)，这里需要检查西风场和北风场
            return True  # 完成最后一场风且庄家轮换

        # 2. 检查是否有人被飞 (分数<0)
        for player in game_state.players:
            # 确保 PlayerState 类有 score 属性
            if player.score < 0:
                print(
                    f"Debug Game Over: 玩家 {player.player_index} 分数飞了 ({player.score})，游戏结束。"
                )
                return True  # 有玩家分数低于0

        # 3. (可选) 检查是否达到设定的局数上限或时间上限
        # 如果你的游戏有固定的总局数限制，可以在这里检查
        # 例如: if game_state.total_hands_played >= self.config.get("max_hands", 8): return True

        # 4. (可选) 检查复杂的结束条件，例如南四局庄家点数等
        # 规则: 南四局庄家和牌不结束，闲家和牌或庄家流局未听牌结束
        # 如果你实现了 determines_next_hand_state 中的南四局庄家连庄逻辑，
        # 那么在南四局庄家连庄时，round_wind 和 round_number 都会保持不变。
        # is_game_over 此时不应返回 True。它应该在闲家和牌或庄家流局未听牌导致庄家轮换时，
        # 并且 round_wind 和 round_number 尝试前进时（即计算出的下一局是南5局或进入西1局时）返回 True。
        # 上面的 "完成最后一场风" 判断已经涵盖了庄家轮换导致进入下一场风时结束的逻辑。

        # 如果以上游戏结束条件都不满足
        return False

    # TODO: 根据实际游戏规则定义和完善 is_game_over 的逻辑
    # 例如，半庄结束条件可能更复杂，涉及到是否西入、提前终止等
    # 例如，全庄结束条件涉及到四个场风的完成
    # 例如，是否设置点数上限，达到上限是否立即结束 (和了止め vs 终局)
    # 这些都需要根据你的游戏配置来决定。

    # ======================================================================
    # == 计分、役种、符数相关 (占位符/简化实现) ==
    # ======================================================================

    def _get_dora_value_for_tile(self, tile: Tile, indicators: List[Tile]) -> int:
        """计算一张牌相对于给定的宝牌指示牌列表的宝牌值"""
        count = 0
        for indicator in indicators:
            indicator_val = indicator.value
            tile_val = tile.value

            # 宝牌是指示牌的下一张
            dora_val = -1
            if 0 <= indicator_val <= 7:  # 1m-8m -> 2m-9m
                dora_val = indicator_val + 1
            elif indicator_val == 8:  # 9m -> 1m
                dora_val = 0
            elif 9 <= indicator_val <= 16:  # 1p-8p -> 2p-9p
                dora_val = indicator_val + 1
            elif indicator_val == 17:  # 9p -> 1p
                dora_val = 9
            elif 18 <= indicator_val <= 25:  # 1s-8s -> 2s-9s
                dora_val = indicator_val + 1
            elif indicator_val == 26:  # 9s -> 1s
                dora_val = 18
            elif 27 <= indicator_val <= 29:  # E S W -> S W N
                dora_val = indicator_val + 1
            elif indicator_val == 30:  # N -> E
                dora_val = 27
            elif 31 <= indicator_val <= 32:  # W R -> R G
                dora_val = indicator_val + 1
            elif indicator_val == 33:  # G -> W
                dora_val = 31

            if tile_val == dora_val:
                count += 1
        return count

    def _get_win_context(
        self,
        winner_player: PlayerState,
        game_state: GameState,
        is_tsumo: bool,
        win_tile: Tile,
        loser_index: Optional[int],
    ) -> Dict:
        """
        (辅助) 收集和牌时调用 calculate_yaku_and_score 所需的上下文信息。
        """
        # 假设 PlayerState 有 seat_wind, player_id, riichi_declared, ippatsu_chance, is_menzen 属性
        # 假设 GameState 有 round_wind, dealer_index, honba, riichi_sticks, wall, num_players 属性
        # 假设 Wall 有 dora_indicators, ura_dora_indicators, get_remaining_live_tiles_count 方法

        context = {
            "player_id": winner_player.player_id,  # 和牌者 ID
            "is_tsumo": is_tsumo,
            "is_ron": not is_tsumo,
            "is_riichi": winner_player.riichi_declared,
            "is_ippatsu": winner_player.ippatsu_chance,  # 需要环境在立直后的第一巡正确设置
            "is_menzen": winner_player.is_menzen,
            "player_wind": winner_player.seat_wind,  # 玩家的自风
            "round_wind": game_state.round_wind,  # 场风
            "dora_indicators": list(game_state.wall.dora_indicators),  # 当前宝牌指示牌
            "ura_dora_indicators": (  # 里宝牌指示牌 (仅立直和牌时有效)
                list(game_state.wall.ura_dora_indicators)
                if winner_player.riichi_declared
                else []
            ),
            "is_dealer": game_state.dealer_index == winner_player.player_id,  # 是否庄家
            "honba": game_state.honba,  # 本场数
            "riichi_sticks": game_state.riichi_sticks,  # 立直棒数量
            "win_tile": win_tile,  # 和了的牌
            "turn_number": game_state.turn_number,  # 当前巡目
            "wall_remaining": game_state.wall.get_remaining_live_tiles_count(),  # 剩余牌墙数
            "num_players": game_state.num_players,  # 玩家数量
            "dealer_id": game_state.dealer_index,  # 庄家 ID
            "loser_id": loser_index,  # 放铳者 ID (仅荣和时)
            # --- 以下是更详细的役种判断可能需要的上下文 ---
            # "is_haitei": game_state.wall.get_remaining_live_tiles_count() == 0 and is_tsumo, # 海底摸月
            # "is_houtei": game_state.wall.get_remaining_live_tiles_count() == 0 and not is_tsumo, # 河底捞鱼
            # "is_rinshan": winner_player.just_kaned_for_rinshan, # 玩家状态中需要标记是否刚杠完准备摸岭上牌
            # "is_chankan": game_state.last_action_info.get("type") == ActionType.KAN.name and game_state.last_action_info.get("kan_type") == KanType.ADDED, # 是否抢杠和 (需要环境在处理Ron时检查是否抢的加杠)
        }
        # 添加关于海底/河底/岭上/抢杠的更精确判断逻辑 (需要 GameState 或 last_action_info 提供更多信息)
        if game_state.wall.get_remaining_live_tiles_count() == 0:
            context["is_haitei"] = is_tsumo
            context["is_houtei"] = not is_tsumo
        # 'is_rinshan' 和 'is_chankan' 的判断比较复杂，需要依赖 GameState 中更详细的状态记录
        # 示例性添加，实际需要环境正确维护这些状态
        context.setdefault("is_rinshan", False)  # 假设 PlayerState 有标记
        context.setdefault(
            "is_chankan", False
        )  # 假设 GameState/last_action_info 能判断

        return context
    def has_at_least_one_yaku(
        self,
        hand: List[Tile],
        melds: List[Dict],
        win_tile: Tile,
        player: PlayerState,
        game_state: GameState,
        is_tsumo: bool,
    ) -> bool:
        """
        检查手牌是否包含至少一番的役种。
        这个方法不需要计算最终番数和点数，只需判断总役番数是否 >= 1。
        """
        # TODO: 实现役种判断的核心逻辑
        # 这可能是 RulesEngine 中最复杂的逻辑之一
        # 需要考虑的因素包括：
        # 1. 手牌组合（平和、断幺九、一盃口、三色同顺、一气贯通、混全带幺九等）
        # 2. 副露情况（门前清、对对和、三暗刻、三杠子、混老头、小三元、混一色、清一色等）
        # 3. 荣和/自摸区别（门前清自摸和）
        # 4. 荣和的牌（役牌、风牌）
        # 5. 游戏状态（立直、一发、海底/河底、岭上、抢杠、宝牌、里宝牌、红宝牌）
        # 6. 特殊役（七对子、国士无双）
        # 7. 役满判断（国士无双十三面、四暗刻、大三元、字一色、清老头、绿一色、九莲宝灯、四风连打、大四喜、小四喜、四杠子、地和、天和、纯正九莲宝灯、四暗刻单骑）

        # 一个常见的实现方式是：
        # 1. 判断所有可能的基础役种（不含番数增加因素，如宝牌）
        # 2. 计算基础役种的总 Han 数
        # 3. 根据游戏状态（立直、一发、宝牌等）增加 Han 数
        # 4. 判断最终总 Han 数是否 >= 1 (对于役满，通常直接返回 True)

        # 需要获取宝牌信息：
        # current_dora_tiles = game_state.wall.get_current_dora_tiles()
        # 需要获取玩家状态：
        # player.riichi_declared, player.ippatsu_chance, player.seat_wind
        # 需要获取场风：
        # game_state.round_wind

        print(
            "Warning: has_at_least_one_yaku is a placeholder, implement actual yaku logic."
        )
        # Placeholder: 假设永远有役 (错误!)
        # 实际需要根据手牌和规则判断
        # if self._calculate_total_han(hand, melds, win_tile, player, game_state, is_tsumo) >= 1:
        #     return True
        # return False
        return True  # Placeholder: 暂时返回 True 以便代码结构跑通
    
    def _analyze_win_for_scoring(self, game_state: "GameState", player_index: int, winning_tile: "Tile", is_tsumo: bool, ron_player_index: Optional[int] = None) -> Optional[WinDetails]:
    
    def calculate_yaku_and_score(
        self,
        hand_tiles_final: List[Tile],  # 和牌时的14张牌 (含和牌)
        melds: List[Dict],  # Assuming List[Dict] with "tiles" key, or List[MeldObject]
        win_tile: Tile,
        context: Dict,
    ) -> Dict:
        """
        计算和牌的役种、翻数、符数和最终得分。
        需要 context 字典包含和牌结算所需的所有相关信息。
        """

        # --- 0. 从 Context 提取必要信息 ---
        # 明确 context 字典需要包含哪些 key
        # 必须包含 (如果未自摸，还需要 loser_id):
        required_keys = [
            "winner_id",
            "is_tsumo",
            "is_menzen",
            "is_riichi",
            "is_dealer",
            "dealer_id",
            "honba",
            "riichi_sticks",
            "num_players",
            "actual_dora_tiles",
            "actual_ura_dora_tiles",  # 从 Wall 获取的实际宝牌/里宝牌牌型
            "round_wind_value",
            "player_seat_wind_value",  # 用于役牌、大三元等判断
            # ... 可能还需要更多用于役种判断的信息，例如：
            # "is_ippatsu", "is_haitei", "is_houtei", "is_rinshan", "is_chankan", etc.
        ]
        for key in required_keys:
            if key not in context:
                # print(f"Error: calculate_yaku_and_score context missing required key: {key}")
                # 可以返回错误信息或抛出异常
                return {
                    "yaku": [],
                    "han": 0,
                    "fu": 0,
                    "score_base": 0,
                    "score_payments": {},
                    "error": f"Context missing required key: {key}",
                }
        if not context["is_tsumo"] and "loser_id" not in context:
            # print("Error: calculate_yaku_and_score context missing loser_id for Ron win")
            return {
                "yaku": [],
                "han": 0,
                "fu": 0,
                "score_base": 0,
                "score_payments": {},
                "error": "Context missing loser_id for Ron win",
            }

        winner_id = context["winner_id"]
        loser_id = context.get("loser_id")  # 自摸时为 None
        is_tsumo = context["is_tsumo"]
        is_menzen = context["is_menzen"]
        is_riichi = context["is_riichi"]
        is_dealer_win = context["is_dealer"]  # 赢家是否是庄家
        dealer_id = context["dealer_id"]  # 这局的庄家 ID
        honba = context["honba"]
        riichi_sticks = context["riichi_sticks"]
        num_players = context["num_players"]
        actual_dora_tiles = context["actual_dora_tiles"]
        actual_ura_dora_tiles = context["actual_ura_dora_tiles"]
        # 提取其他可选 context 信息...

        yaku_list = []
        base_yaku_han = 0  # 非宝牌役种的翻数总和
        yakuman_list = []  # 役满列表
        yakuman_han = 0  # 役满翻数 (单倍役满算 13 翻或特定点数，多倍役满累加)

        # --- 1. 役种判断 (最复杂部分) ---
        # 需要根据 hand_tiles_final, melds, win_tile, context
        # 来判断所有适用的非役满役种和役满役种

        # 1.1 判断役满
        # TODO: 实现役满判断逻辑 (国士无双、四暗刻、大三元等)
        # diagnosed_yakuman = self._diagnose_yakuman(hand_tiles_final, melds, win_tile, context) # 需要实现这个 helper
        # if diagnosed_yakuman:
        #    yakuman_list.extend(diagnosed_yakuman)
        #    # 役满翻数累加 (双倍役满等)
        #    yakuman_han = sum(han for name, han in yakuman_list) # Assume helper returns list of (name, han) for yakuman
        #    # 如果是役满，通常不再计算非役满役种和符数，直接结算役满点数。

        # 1.2 判断非役满役种 (仅在没有役满时进行)
        # if not yakuman_list:
        # TODO: 实现所有非役满役种的判断逻辑 (断幺九, 平和, 一杯口, ... 清一色)
        # diagnosed_normal_yaku = self._diagnose_normal_yaku(hand_tiles_final, melds, win_tile, context) # 需要实现这个 helper
        # yaku_list.extend(diagnosed_normal_yaku)
        # base_yaku_han = sum(han for name, han in yaku_list)

        # --- Placeholder / Simplified Yaku ---
        # 将原有的简化役种判断整合进来
        # 注意：这里的判断是不完整的，仅为示例
        if is_riichi:
            yaku_list.append(("立直", 1))
            base_yaku_han += 1
        if is_tsumo and is_menzen:
            yaku_list.append(("门前清自摸和", 1))
            base_yaku_han += 1
        # TODO: 检查其他役牌、自风场风、断幺九（需要分析手牌构成）等基础役
        # Example: check for Yakuhai (役牌) based on context.round_wind_value, context.player_seat_wind_value and hand_tiles_final/melds
        # Add other simplified yaku checks here...

        # --- 2. 宝牌计算 ---
        dora_han = 0
        all_tiles_in_hand_and_melds = list(hand_tiles_final)  # 从 14 张和牌开始
        # 添加副露中的牌
        for meld in melds:
            if "tiles" in meld:  # 假设副露是字典列表，且包含 "tiles" 键
                all_tiles_in_hand_and_melds.extend(meld["tiles"])
            # TODO: 如果你的 meld 是自定义对象，需要调整这里来获取其中的 Tile 列表

        # 计算宝牌数 (表宝牌 + 里宝牌 + 赤宝牌)
        for tile in all_tiles_in_hand_and_melds:
            dora_han += self._count_dora_for_tile(tile, actual_dora_tiles)
            if is_riichi:  # 只有立直和牌才计算里宝牌
                dora_han += self._count_dora_for_tile(tile, actual_ura_dora_tiles)
            if tile.is_red:  # 赤宝牌
                dora_han += 1

        if dora_han > 0:
            # 将宝牌作为役种添加到列表中（方便显示），但它的翻数不计入一番缚的基础
            yaku_list.append((f"宝牌+", dora_han))
        # 总翻数 = 基本役翻数 + 宝牌翻数
        han = base_yaku_han + dora_han

        # --- 3. 和牌有效性检查 (一番缚 / 役满) ---
        # 如果有役满，则和牌有效，不再需要一番缚检查
        if not yakuman_list:
            # 如果没有役满，则检查是否有至少一番的非宝牌役种
            if base_yaku_han < 1:
                return {
                    "yaku": yaku_list,  # 返回发现的所有役种 (包括宝牌)
                    "han": han,  # 返回总翻数
                    "fu": 0,  # 无效和牌，符数无意义
                    "score_base": 0,  # 无效和牌，点数无意义
                    "score_payments": {},  # 无效和牌，无支付
                    "error": "和牌无役 (一番缚)",
                }
            # else: 有非宝牌役，可以继续计算符和点

        # --- 4. 符数计算 ---
        # TBD: 实现复杂的符数计算 (副底, 和牌方式, 面子组成, 雀头, 听牌型)
        # 这是另一个复杂的逻辑，需要详细分解手牌。
        # 简化 Placeholder
        fu = 20  # 底符 (Base Fu)
        if yakuman_list:  # 役满通常不计算符数
            fu = 0  # 或者根据特定役满规则设定符数，但通常不影响点数
        elif self._is_seven_pairs_raw(
            hand_tiles_final, melds
        ):  # 假设这个 helper 检查七对子形状
            fu = 25  # 七对子固定25符
        else:
            # 正常牌型的符数计算 (复杂!)
            # - 门清荣和：底符20 + 荣和符10 + 面子符 + 雀头符 + 听牌符 = 30符起
            # - 门清自摸：底符20 + 自摸符2 + 面子符 + 雀头符 + 听牌符 = 22符起 (平和自摸例外20符)
            # - 副露荣和/自摸：底符20 + 面子符 + 雀头符 + 听牌符 = 20符起
            # ... 复杂的面子符 (幺九牌刻子翻倍), 雀头符 (役牌雀头翻倍), 听牌符 (单骑、边张、坎张加符) 计算 ...

            # 示例：加上自摸/荣和的底符基础
            if is_menzen and not is_tsumo:  # 门清荣和
                fu += 10  # 荣和符 10
            # Note: 门清自摸没有额外的自摸符，但其底符是20，符数计算略有不同（平和自摸）。
            # 副露自摸有自摸符2符。

            # TODO: 根据手牌构成和和牌方式计算面子符、雀头符、听牌符并加到 fu 上

            # 符数计算后需要向上取整到10位 (切り上げ)
            # 例外：七对子25符，平和自摸20符。
            if fu > 20 and fu != 25:  # 七对子是25，平和自摸是20
                fu = ((fu + 9) // 10) * 10
            elif fu < 20 and not is_menzen:  # 副露最低20符（不含七对子）
                fu = 20

        # --- 5. 得分计算 ---
        # 实现基于总翻数、符数、庄闲、本场、供托的完整点数计算

        score_base = 0  # 基础点数 (不含本场和供托)

        # 5.1 役满点数
        if yakuman_list:
            # 役满根据翻数有不同的点数
            # 单倍役满 子家 8000点（庄家 12000点）
            # 双倍役满 子家 16000点（庄家 24000点）
            # 三倍役满 子家 24000点（庄家 36000点）等
            # base_points_per_yakuman = 8000 # 单倍役满基础点（子家）
            # score_base = yakuman_han * base_points_per_yakuman # 简单的役满点数计算
            # TODO: 根据役满类型和翻数计算准确的役满点数
            # Placeholder:
            score_base = 8000 * yakuman_han  # 粗略估算

        # 5.2 非役满点数 (翻数 >= 1 and < 13)
        elif han >= 1 and han < 13 and fu > 0:  # 需要有役且符数大于0 (役满已排除)
            # 计算基础点数 A = 符数 * 2^(翻数 + 2)
            raw_base_points = fu * (2 ** (han + 2))

            # 应用满贯及以上截断
            if han >= 6:  # 跳满 (6-7翻)
                score_base = 3000  # 子家跳满 base
            elif han >= 8:  # 倍满 (8-10翻)
                score_base = 4000  # 子家倍满 base
            elif han >= 11:  # 三倍满 (11-12翻)
                score_base = 6000  # 子家三倍满 base
            elif han == 5:  # 满贯 (5翻)
                score_base = 2000  # 子家满贯 base
            elif han <= 4:  # 满贯以下 (1-4翻)
                # 检查切り上げ満貫 (Kiraiage Mangan)
                if raw_base_points > 2000:
                    score_base = 2000  # 截断为满贯
                else:
                    score_base = raw_base_points  # 未满满贯的实际点数基础
            else:  # 理论上不应该到这里
                score_base = 0  # 计算错误？

        # 5.3 点数支付计算
        score_payments = defaultdict(int)  # 使用 defaultdict 方便累加分数变化
        total_gain_from_players = 0  # 从其他玩家获得的净点数（不含供托）

        honba_bonus_per_player_tsumo = honba * 100  # 自摸时每家支付的本场点数
        honba_bonus_total_ron = honba * 300  # 荣和时放铳者支付的总本场点数
        riichi_sticks_bonus = riichi_sticks * 1000  # 立直供托点数

        if is_tsumo:
            # 自摸点数计算和支付 (根据基础点数 A 和庄闲关系)
            # A = fu * 2^(han+2) (在应用满贯等截断之前)
            # Simplified calculation using the potentially truncated score_base
            # Need to use the correct payment formulas based on base_points and dealer status
            if is_dealer_win:  # 庄家自摸 (闲家每人支付 base_points * 2, 向上取整到百)
                payment_per_ko = (
                    (score_base * 2 + 99) // 100
                ) * 100 + honba_bonus_per_player_tsumo
                for pid in range(num_players):
                    if pid != winner_id:
                        score_payments[pid] -= payment_per_ko
                        total_gain_from_players += payment_per_ko
            else:  # 子家自摸 (庄家支付 base_points * 2, 子家支付 base_points * 1, 都向上取整到百)
                payment_oya = (
                    (score_base * 2 + 99) // 100
                ) * 100 + honba_bonus_per_player_tsumo
                payment_ko = (
                    (score_base + 99) // 100
                ) * 100 + honba_bonus_per_player_tsumo
                for pid in range(num_players):
                    if pid == winner_id:
                        continue
                    if pid == dealer_id:  # 如果对方是庄家
                        score_payments[pid] -= payment_oya
                        total_gain_from_players += payment_oya
                    else:  # 如果对方是子家
                        score_payments[pid] -= payment_ko
                        total_gain_from_players += (
                            payment_ko * 1
                        )  # *1 for clarity, two other children pay

            score_payments[winner_id] += (
                total_gain_from_players + riichi_sticks_bonus
            )  # 赢家收取点数和立直棒

        else:  # 荣和 (Ron)
            if loser_id is None:
                # 这个错误应该在 context 检查时捕获
                print("Error: Ron win calculation called without loser_id in context.")
                return {
                    "yaku": yaku_list,
                    "han": han,
                    "fu": fu,
                    "score_base": score_base,
                    "score_payments": {},
                    "error": "Internal Error: Ron win calculation missing loser_id",
                }

            # 荣和点数计算和支付 (放铳者支付全部点数)
            # 点数 = (基础点数 A * 荣和系数 + 99) // 100 * 100 + 本场*300 + 立直供托*1000
            # 荣和系数：庄家赢是 6，子家赢是 4
            ron_coefficient = 6 if is_dealer_win else 4
            payment_total = (
                ((score_base * ron_coefficient + 99) // 100) * 100
                + honba_bonus_total
                + riichi_sticks_bonus
            )

            score_payments[loser_id] -= payment_total
            score_payments[winner_id] += payment_total  # 赢家收取全部点数和立直棒

        # --- 返回结果 ---
        # 根据是否有役满或基本役判断最终是否是有效和牌
        final_error = None
        if not yakuman_list and base_yaku_han < 1:
            final_error = "和牌无役 (一番缚)"  # 除非是役满，否则无役是错误

        return {
            "yaku": yaku_list,  # 所有诊断出的役种 (包括宝牌和役满)
            "han": han,  # 总翻数 (包括宝牌和役满翻数)
            "fu": fu if not yakuman_list else 0,  # 役满通常符数记为0 (或者特定值)
            "score_base": score_base,  # 子家 base 点数 (未乘庄闲系数，已应用满贯截断)
            "score_payments": dict(score_payments),  # 字典形式 {玩家ID: 分数变化}
            "error": final_error,
            "yakuman": [name for name, han in yakuman_list],  # 单独列出役满名称列表
            "yakuman_han": yakuman_han,  # 役满总翻数 (对于多倍役满)
        }

    # 你需要实现的辅助方法示例：
    # def _diagnose_yakuman(self, hand_tiles_final, melds, win_tile, context) -> List[Tuple[str, int]]:
    #     """判断并返回役满列表及其翻数"""
    #     # TODO: 实现天和、地和、国士无双、四暗刻等判断
    #     pass

    # def _diagnose_normal_yaku(self, hand_tiles_final, melds, win_tile, context) -> List[Tuple[str, int]]:
    #     """判断并返回非役满役种列表及其翻数 (已考虑副露减翻)"""
    #     # TODO: 实现断幺九、平和、役牌、一盃口、混全带幺九等判断
    #     pass

    # def _calculate_mentsu_fu(self, mentsu, context):
    #     """计算面子（顺子、刻子、杠）的符数"""
    #     pass

    # def _calculate_jantou_fu(self, jantou, context):
    #     """计算雀头的符数"""
    #     pass

    # def _calculate_machi_fu(self, win_tile, hand_tiles_final, context):
    #     """计算听牌型的符数"""
    #     pass

    # 假设你已经有了 _is_seven_pairs_raw 方法来检查七对子形状

    def get_hand_outcome(self, game_state: "GameState") -> Dict[str, Any]:
        """
        确定并返回本局游戏的结果（和牌、流局等）的详细信息。
        这个方法在 is_hand_over 返回 True 后调用，GameState 应该处于本局结束时的状态。

        Args:
            game_state: 当前游戏状态 (处于一局结束的状态)。

        Returns:
            Dict[str, Any]: 包含本局结果信息的字典。
        """
        outcome: Dict[str, Any] = {
            "end_type": "UNKNOWN",  # 结束类型: "TSUMO", "RON", "EXHAUSTIVE_DRAW", "SPECIAL_DRAW", "SCORING_ERROR"
            "winner_index": None,  # 和牌玩家索引 (和牌时)
            "winners": [],  # 和牌玩家索引列表 (可能有多家和牌，例如多重荣和，但日麻通常只取一个)
            "loser_index": None,  # 放铳玩家索引 (仅 RON 时)
            "winning_tile": None,  # 和牌的牌 (和牌时)
            "draw_type": None,  # 流局类型 (流局时)
            "tenpai_players": [],  # 听牌玩家索引列表 (流局时)
            "noten_players": [],  # 未听牌玩家索引列表 (流局时)
            "score_details": None,  # 包含计算点数所需信息的字典 (番/符/役/支付等)
            "score_changes": defaultdict(int),  # 记录每个玩家最终分数变化的字典
            "error": None,  # 错误信息 (如果有)
        }

        # --- 检查是否由玩家动作导致结束 (和牌, 特殊流局声明) ---
        # **关键假设**: GameState.last_action_info 存储了导致结束的那个动作的信息
        # 例如: {'type': ActionType.TSUMO, 'player': 1, 'tile': Tile(...)}
        # 例如: {'type': ActionType.RON, 'player': 2, 'tile': Tile(...), 'loser_index': 0}
        # 例如: {'type': ActionType.SPECIAL_DRAW, 'player': 0, 'reason': '九种九牌'}
        last_action_info = game_state.last_action_info

        player_action_ended_hand = False
        if last_action_info:
            action_type_str = last_action_info.get(
                "type"
            )  # 假设类型存储为 ActionType 枚举或其 .name
            player_index = last_action_info.get("player")
            tile = last_action_info.get("tile")

            # 将 action_type_str 转回 ActionType 枚举比较 (如果需要)
            action_type = None
            if isinstance(action_type_str, ActionType):
                action_type = action_type_str
            elif isinstance(action_type_str, str):
                try:
                    action_type = ActionType[action_type_str]  # 尝试从名称恢复枚举
                except KeyError:
                    if action_type is None:
                        outcome["end_type"] = "UNKNOWN"
                        return outcome  # 无法识别的动作名称

            if action_type == ActionType.TSUMO:
                outcome["end_type"] = "TSUMO"
                outcome["winner_index"] = player_index
                outcome["winners"].append(player_index)
                outcome["winning_tile"] = tile
                player_action_ended_hand = True

            elif action_type == ActionType.RON:
                outcome["end_type"] = "RON"
                outcome["winner_index"] = player_index
                outcome["winners"].append(player_index)
                # **重要**: 必须能从 last_action_info 获取放铳者信息
                outcome["loser_index"] = last_action_info.get("loser_index")
                outcome["winning_tile"] = tile
                if outcome["loser_index"] is None:
                    print(
                        f"警告: Ron 动作信息缺少 loser_index for player {player_index}"
                    )
                    outcome["end_type"] = "SCORING_ERROR"  # 数据不完整，无法计分
                    outcome["error"] = "缺少放铳者索引 (loser_index)"
                player_action_ended_hand = True

            elif action_type == ActionType.SPECIAL_DRAW:
                outcome["end_type"] = "SPECIAL_DRAW"
                outcome["draw_type"] = last_action_info.get(
                    "reason", "特殊流局"
                )  # 例如 '九种九牌'
                # 通常特殊流局只增加本场数，无点数移动 (除非规则特殊)
                outcome["score_details"] = {
                    "yaku": [(outcome["draw_type"], 0)],
                    "han": 0,
                    "fu": 0,
                    "score_base": 0,
                    "score_payments": {},
                }
                player_action_ended_hand = True
                # 注意: 四杠散了, 四风连打, 四家立直 可能需要在这里或 RulesEngine 其他地方检查并设置

        # --- 如果不是玩家动作结束，检查是否牌墙摸完流局 ---
        if not player_action_ended_hand:
            # 假设牌墙摸完时，get_remaining_live_tiles_count() == 0
            # 并且没有发生和牌或特殊流局声明
            if game_state.wall.get_remaining_live_tiles_count() == 0:
                outcome["end_type"] = "EXHAUSTIVE_DRAW"
                outcome["draw_type"] = "荒牌流局 (牌墙摸完)"
            else:
                # 如果到这里，既没有玩家动作结束，牌墙也没完，说明状态可能不一致
                print(
                    f"警告: get_hand_outcome 无法确定结束类型。剩余牌: {game_state.wall.get_remaining_live_tiles_count()}, 最后动作: {last_action_info}"
                )
                outcome["end_type"] = "UNKNOWN"  # 保持未知状态
                return outcome  # 提前返回，信息不足

        # --- 计算得分和状态 ---
        if outcome["end_type"] in ["TSUMO", "RON"]:
            winner = game_state.players[outcome["winner_index"]]
            # 和牌时的最终手牌 (隐藏牌 + 和了的那张牌)
            # 假设和牌动作发生后，和的那张牌在 winner.hand 或 winner.drawn_tile 里
            # 这里需要明确：check_win/calculate_yaku 需要14张牌。
            # 假设在执行和牌动作时，环境已经将和牌加入winner.hand
            # 或者，如果自摸，和牌是 winner.drawn_tile
            final_hand_tiles = list(winner.hand)
            if (
                outcome["end_type"] == "TSUMO"
                and outcome["winning_tile"] not in final_hand_tiles
            ):
                # 如果自摸牌不在手牌里（例如刚摸到还没合并），加进去
                # 需要确保 winning_tile 是正确的 Tile 对象实例
                # 再次检查以避免重复添加
                if outcome["winning_tile"]:
                    # 检查是否真的需要添加 (可能环境逻辑已处理)
                    temp_hand_count = len(final_hand_tiles) + sum(
                        len(m["tiles"]) for m in winner.melds
                    )
                    if temp_hand_count == 13:  # 如果当前刚好13张，说明和牌需要加入
                        final_hand_tiles.append(outcome["winning_tile"])
                    elif temp_hand_count != 14:
                        print(
                            f"警告: 和牌时玩家 {winner.player_id} 牌数异常 ({temp_hand_count}张)"
                        )
                        outcome["end_type"] = "牌数异常"
                        return outcome

            elif (
                outcome["end_type"] == "RON"
                and outcome["winning_tile"] not in final_hand_tiles
            ):
                # 荣和牌理应在构成 check_win 通过的 14 张牌组合里
                # 可能需要从 game_state.last_discarded_tile 获取正确的实例?
                # 假设调用 get_hand_outcome 时 winner.hand 已经是和牌状态的14张(含副露)
                # 或者，如果环境没有更新手牌，我们手动添加
                temp_hand_count = len(final_hand_tiles) + sum(
                    len(m["tiles"]) for m in winner.melds
                )
                if temp_hand_count == 13 and outcome["winning_tile"]:
                    final_hand_tiles.append(outcome["winning_tile"])
                elif temp_hand_count != 14:
                    print(
                        f"警告: 和牌时玩家 {winner.player_id} 牌数异常 ({temp_hand_count}张)"
                    )

            # 准备上下文并计算得分
            win_context = self._get_win_context(
                winner_player=winner,
                game_state=game_state,
                is_tsumo=(outcome["end_type"] == "TSUMO"),
                win_tile=outcome["winning_tile"],
                loser_index=outcome["loser_index"],
            )
            try:
                # *** 调用计分函数 ***
                score_calc_result = self.calculate_yaku_and_score(
                    hand_tiles_final=final_hand_tiles,  # 传递最终的14张牌
                    melds=winner.melds,
                    win_tile=outcome["winning_tile"],
                    context=win_context,
                )
                if score_calc_result.get("error"):
                    print(f"计分错误: {score_calc_result['error']}")
                    outcome["end_type"] = "SCORING_ERROR"
                    outcome["score_details"] = score_calc_result
                else:
                    outcome["score_details"] = score_calc_result
                    # 将计算出的分数变化累加到 outcome["score_changes"]
                    payments = score_calc_result.get("score_payments", {})
                    for p_idx, change in payments.items():
                        outcome["score_changes"][p_idx] += change

            except Exception as e:
                print(f"严重错误: 调用 calculate_yaku_and_score 时发生异常: {e}")
                import traceback

                traceback.print_exc()
                outcome["end_type"] = "SCORING_ERROR"
                outcome["score_details"] = {"error": f"计算异常: {e}"}

        elif outcome["end_type"] == "EXHAUSTIVE_DRAW":
            # 荒牌流局，计算听牌罚点 (No-ten Bappu)
            tenpai_indices = []
            noten_indices = []
            for i, player in enumerate(game_state.players):
                # 调用 is_tenpai 检查听牌状态
                if self.is_tenpai(player.hand, player.melds):
                    tenpai_indices.append(i)
                else:
                    noten_indices.append(i)

            outcome["tenpai_players"] = tenpai_indices
            outcome["noten_players"] = noten_indices

            # 计算罚点支付 (标准规则: 共3000点)
            num_tenpai = len(tenpai_indices)
            num_noten = len(noten_indices)
            bappu_payments = defaultdict(int)

            if (
                0 < num_tenpai < game_state.num_players
            ):  # 只有在有人听牌且有人没听牌时才支付
                points_per_noten = 3000 // num_noten
                points_per_tenpai = 3000 // num_tenpai

                for idx in noten_indices:
                    bappu_payments[idx] -= points_per_noten
                for idx in tenpai_indices:
                    bappu_payments[idx] += points_per_tenpai

            # 将罚点累加到最终分数变化
            for p_idx, change in bappu_payments.items():
                outcome["score_changes"][p_idx] += change

            outcome["score_details"] = {
                "yaku": [("荒牌流局", 0)],
                "han": 0,
                "fu": 0,
                "score_base": 0,
                "score_payments": dict(bappu_payments),  # 记录罚点支付情况
            }

        elif outcome["end_type"] == "SPECIAL_DRAW":
            # 特殊流局通常无点数变化，只处理本场和立直棒（这部分应在更新GameState时处理）
            # score_changes 保持默认的 0
            if outcome["score_details"] is None:  # 如果前面没有设置
                outcome["score_details"] = {
                    "yaku": [(outcome["draw_type"], 0)],
                    "han": 0,
                    "fu": 0,
                    "score_base": 0,
                    "score_payments": {},
                }

        # print(f"RulesEngine Debug: Determined Hand Outcome: {outcome}")
        # 将 defaultdict 转为普通 dict，方便序列化或打印
        outcome["score_changes"] = dict(outcome["score_changes"])
        return outcome

    # TODO: 实现 _is_furiten (振听检查)
    # def _is_furiten(...) -> bool: ...

    def determine_next_hand_state(
        self, game_state: "GameState", hand_outcome_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        根据当前游戏状态和本局结果，确定下一局的场风、局数、本场数、立直棒和庄家。
        此函数计算理论上的下一局状态，不负责判断游戏是否实际结束。

        Args:
            game_state: 当前游戏状态 (一局刚结束)。
            hand_outcome_info: 本局结果详情。

        Returns:
            Dict[str, Any]: 包含下一局起始状态信息的字典。
        """
        current_dealer = game_state.dealer_index
        current_round_wind = game_state.round_wind
        current_round_number = game_state.round_number
        current_honba = game_state.honba
        current_riichi_sticks = game_state.riichi_sticks

        end_type = hand_outcome_info.get("end_type")
        winner_index: Optional[int] = hand_outcome_info.get("winner_index")
        tenpai_players: Optional[List[int]] = hand_outcome_info.get("tenpai_players")

        # 1. 判断庄家是否轮换 (亲流れ / Renchan)
        dealer_changes = False
        is_win = end_type in {"TSUMO", "RON"}
        is_exhaustive_draw = end_type == "EXHAUSTIVE_DRAW"
        is_special_draw = end_type == "SPECIAL_DRAW"  # 特殊流局通常连庄

        if is_win:
            if winner_index != current_dealer:
                dealer_changes = True
        elif is_exhaustive_draw:
            dealer_is_tenpai = (
                tenpai_players is not None and current_dealer in tenpai_players
            )
            if not dealer_is_tenpai:
                dealer_changes = True
        # elif is_special_draw:
        # 通常特殊流局是连庄，所以 dealer_changes 保持 False

        # 2. 计算下一局状态
        next_dealer_index: int
        next_round_wind: int
        next_round_number: int
        next_honba: int
        next_riichi_sticks: int

        if dealer_changes:
            # 庄家轮换 (亲流れ)
            next_dealer_index = (current_dealer + 1) % self.num_players
            next_honba = 0  # 本场数归零

            # 判断是否需要进风
            if current_round_number == self.max_rounds_per_wind:
                # 是本风最后一局，且庄家轮换 -> 进风
                next_round_wind = (
                    current_round_wind + 1
                )  # 暂时前进，后续可能需要检查是否超出总风数
                next_round_number = 1  # 新的风从第一局开始
                print(
                    f"Debug Next State: 场风 {current_round_wind} 第 {current_round_number} 局结束，庄家轮换，进入下一场风。"
                )
            else:
                # 不是本风最后一局 -> 局数前进，场风不变
                next_round_wind = current_round_wind
                next_round_number = current_round_number + 1
                print(
                    f"Debug Next State: 场风 {current_round_wind} 第 {current_round_number} 局结束，庄家轮换，进入第 {next_round_number} 局。"
                )

        else:
            # 庄家连庄 (Renchan)
            # (庄家和牌 / 荒牌流局且庄家听牌 / 特殊流局)
            next_dealer_index = current_dealer
            next_honba = current_honba + 1  # 本场数加 1
            next_round_wind = current_round_wind  # 场风不变
            next_round_number = current_round_number  # 局数不变
            print(
                f"Debug Next State: 场风 {current_round_wind} 第 {current_round_number} 局结束，庄家 {current_dealer} 连庄。"
            )

        # 3. 计算立直棒
        if is_win:
            next_riichi_sticks = 0  # 和牌者取走所有立直棒
        else:  # 所有流局 (荒牌流局, 特殊流局)
            next_riichi_sticks = current_riichi_sticks  # 立直棒保留在场上

        # 4. 组装结果
        # 注意: 这里计算出的 next_round_wind 可能大于等于 self.total_round_winds
        # (例如半庄南4结束后，计算出西1)。这表明游戏应该结束了。
        # 游戏是否结束的最终判断应该由调用此函数后的 is_game_over 逻辑处理。
        next_hand_state = {
            "next_dealer_index": next_dealer_index,
            "next_round_wind": next_round_wind,
            "next_round_number": next_round_number,
            "next_honba": next_honba,
            "next_riichi_sticks": next_riichi_sticks,
        }

        print(f"Debug Next State: Calculated next hand state: {next_hand_state}")
        return next_hand_state

    def validate_closed_kan(self, player: PlayerState, tile_to_kan: Tile) -> bool:
        """
        检查玩家是否可以进行暗杠 (Closed Kan)。

        Args:
            player: 玩家状态对象。
            tile: 需要检查的牌。
            melds: 玩家当前的副露列表。

        Returns:
            bool: 如果可以进行暗杠，返回 True；否则返回 False。
        """
        # 暗杠需要玩家手中有4张相同的牌
        combined_hand = player.hand + player.drawn_tile
        if combined_hand.count(tile_to_kan) == 4:
            return True
        return False

    def validate_added_kan(self, player: PlayerState, tile_to_kan: Tile) -> bool:
        """
        检查玩家是否可以进行加杠 (Added Kan)。

        Args:
            player: 玩家状态对象。
            tile_to_kan: 需要检查的牌。
            melds: 玩家当前的副露列表。

        Returns:
            bool: 如果可以进行加杠，返回 True；否则返回 False。
        """
        # 加杠需要玩家手中有3张相同的牌，并且有一个副露包含这张牌
        combined_hand = player.hand + player.drawn_tile
        if tile_to_kan in combined_hand:
            for meld in player.melds:
                if tile_to_kan in meld["tiles"] and meld["meld_type"] == "PON":
                    return True
        return False
