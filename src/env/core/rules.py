from typing import (
    List,
    Dict,
    Optional,
    Set,
    Tuple,
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
        # 使用 Counter 来统计每种 Tile (考虑赤牌) 的数量
        tile_counts: TypingCounter[Tile] = Counter(full_hand_tiles)

        # 1. 查找暗杠 (Ankan)
        for tile, count in tile_counts.items():
            if count == 4:
                # 检查立直后暗杠是否改变听牌 (日麻规则通常允许不改变听牌的暗杠)
                # if player.riichi_declared and self._ankan_changes_wait(player, tile):
                #     continue # 如果改变听牌则不允许
                kan_actions.append(
                    Action(type=ActionType.KAN, tile=tile, kan_type=KanType.CLOSED)
                )

        # 2. 查找加杠 (Kakan)
        # 加杠必须使用 摸到的牌(drawn_tile) 或者 手牌中的一张(hand) 来加到已有的碰(meld)上
        tiles_available_for_kakan = (
            [player.drawn_tile] if player.drawn_tile else []
        ) + player.hand
        processed_kakan_values = set()  # 防止对同一种牌值的碰重复生成加杠动作

        for meld in player.melds:
            if meld.type == ActionType.PON:
                pon_tile_value = meld.tiles[0].value  # 碰的牌值
                if pon_tile_value in processed_kakan_values:
                    continue

                # 查找手牌中或摸到的牌是否有第四张
                fourth_tile_instance = None
                for tile in tiles_available_for_kakan:
                    if tile.value == pon_tile_value:
                        fourth_tile_instance = tile
                        break  # 找到一张即可

                if fourth_tile_instance:
                    # 检查立直后加杠是否改变听牌 (通常不允许)
                    # if player.riichi_declared and self._kakan_changes_wait(player, fourth_tile_instance):
                    #     continue

                    # 创建 Action, tile 参数使用碰的那种牌的代表 (例如非赤牌?)
                    # 或者就用找到的这张牌实例 fourth_tile_instance
                    kan_actions.append(
                        Action(
                            type=ActionType.KAN,
                            tile=fourth_tile_instance,
                            kan_type=KanType.ADDED,
                        )
                    )
                    processed_kakan_values.add(pon_tile_value)

        return kan_actions

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
                chi_actions.append(Action(type=ActionType.CHI, chi_tiles=chi_combo))

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
                    chi_actions.append(Action(type=ActionType.CHI, chi_tiles=chi_combo))
                elif val1 != val2:
                    chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                    chi_actions.append(Action(type=ActionType.CHI, chi_tiles=chi_combo))

        # 模式 3: 需要 T+1, T+2 (例如，有 6m, 7m，吃 5m)
        if target_value % 9 <= 6:  # 牌值需 <= 6 (索引)
            val1, val2 = target_value + 1, target_value + 2
            tile1_options = [t for t in hand_tiles if t.value == val1]
            tile2_options = [t for t in hand_tiles if t.value == val2]
            if tile1_options and tile2_options:
                chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                chi_actions.append(Action(type=ActionType.CHI, chi_tiles=chi_combo))

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
        if not self.check_win(full_hand, player.melds):
            return False

        # 2. 检查是否有役 (一番缚)
        # TBD: 实现役种检查
        context = self._get_win_context(
            player, game_state, is_tsumo=True, win_tile=player.drawn_tile
        )
        yaku_info = self.calculate_yaku_and_score(
            full_hand, player.melds, player.drawn_tile, context
        )
        if yaku_info.get("han", 0) < 1:
            # print(f"Debug: 玩家 {player.player_id} 自摸成型但无役")
            return False  # 无役不能和牌

        return True

    def _can_ron(
        self, player: PlayerState, target_tile: Tile, game_state: GameState
    ) -> bool:
        """检查玩家是否能荣和目标牌"""
        if not target_tile:
            return False

        # 组成模拟手牌 (手牌 + 目标牌)
        potential_hand = player.hand + [target_tile]

        # 1. 检查是否和牌型
        if not self.check_win(potential_hand, player.melds):
            return False

        # 2. 检查振听 (Furiten) - 复杂规则
        # TBD: 实现振听检查逻辑 _is_furiten
        # if self._is_furiten(player, target_tile, game_state):
        #     print(f"Debug: 玩家 {player.player_id} 荣和 {target_tile} 触发振听")
        #     return False

        # 3. 检查是否有役 (一番缚)
        # TBD: 实现役种检查
        context = self._get_win_context(
            player, game_state, is_tsumo=False, win_tile=target_tile
        )
        yaku_info = self.calculate_yaku_and_score(
            potential_hand, player.melds, target_tile, context
        )
        if yaku_info.get("han", 0) < 1:
            # print(f"Debug: 玩家 {player.player_id} 荣和成型但无役")
            return False  # 无役不能和牌

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

    def check_win(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        检查给定的手牌和副露组合是否构成和牌型。
        (占位符 - 这是最复杂的部分之一, 需要专门的算法)
        """
        # --- 必要检查 ---
        total_tiles = len(hand_tiles) + sum(len(m.tiles) for m in melds)
        if total_tiles != 14:
            return False  # 和牌必须是14张

        # --- 特殊牌型检查 ---
        if self._is_seven_pairs_raw(hand_tiles, melds):
            return True
        if self._is_kokushi_raw(hand_tiles, melds):
            return True

        # --- 标准牌型检查 (4面子 + 1雀头) ---
        # 需要一个高效的算法来分解手牌
        # TBD: 实现标准型检查 _is_standard_hand_recursive
        if self._is_standard_hand_recursive(hand_tiles, melds):
            return True

        return False  # 无法组成任何和牌型

    def _is_standard_hand_recursive(
        self, hand_tiles: List[Tile], melds: List[Meld]
    ) -> bool:
        """(占位符) 递归检查标准型 4面子+1雀头"""
        # TBD: 实现复杂的递归分解算法
        # print("Debug: _is_standard_hand_recursive - 占位符返回 False")
        return False

    def _is_seven_pairs_raw(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """(占位符) 检查七对子"""
        if melds:
            return False
        if len(hand_tiles) != 14:
            return False
        # 使用 Counter 统计 Tile 对象 (区分赤牌)
        counts: TypingCounter[Tile] = Counter(hand_tiles)
        # 检查是否有超过4张的牌 (理论上七对子不可能，但作为校验)
        if any(c > 2 for c in counts.values()):
            return False
        # 检查是否正好是7个对子
        return len(counts) == 7 and all(c == 2 for c in counts.values())

    def _is_kokushi_raw(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """(占位符) 检查国士无双"""
        if melds:
            return False
        if len(hand_tiles) != 14:
            return False

        hand_values = {t.value for t in hand_tiles}
        counts = Counter(t.value for t in hand_tiles)

        # 必须包含所有13种幺九牌
        if not self.terminal_honor_values.issubset(hand_values):
            return False
        # 必须有且仅有一个幺九牌是对子，其他都是单张
        pair_count = 0
        single_count = 0
        for term_val in self.terminal_honor_values:
            if counts[term_val] == 2:
                pair_count += 1
            elif counts[term_val] == 1:
                single_count += 1
            else:  # 必须是1或2张
                return False

        return pair_count == 1 and single_count == 12

    def is_tenpai(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """检查给定的13张牌组合是否听牌"""
        if len(hand_tiles) + sum(len(m.tiles) for m in melds) != 13:
            return False  # 听牌检查基于13张牌

        # 尝试加入所有可能的牌 (0-33)，看是否能和牌
        for potential_tile_value in range(34):
            # 假设用非赤牌测试 (通常足够判断是否听牌)
            test_tile = Tile(value=potential_tile_value, is_red=False)
            if self.check_win(hand_tiles + [test_tile], melds):
                return True  # 只要有一种牌能和，就是听牌

        return False

    def is_hand_over(self, game_state) -> bool:
        """
        判断当前牌局是否结束。
        通常包括以下几种情况：
        - 有玩家和牌（荣和或自摸）
        - 牌墙打完了（荒牌流局）
        """
        # 1. 任一玩家和牌
        for player in game_state.players:
            if player.has_won:  # 你需要在 player 中定义 has_won 标志位
                return True

        # 2. 牌墙耗尽
        if game_state.wall.get_remaining_live_tiles_count() == 0:
            return True

        return False

    # ======================================================================
    # == 计分、役种、符数相关 (占位符/简化实现) ==
    # ======================================================================

    def _get_win_context(
        self, player: PlayerState, game_state: GameState, is_tsumo: bool, win_tile: Tile
    ) -> Dict:
        """(辅助) 收集和牌时需要的上下文信息"""
        context = {
            "is_tsumo": is_tsumo,
            "is_ron": not is_tsumo,
            "is_riichi": player.riichi_declared,
            "is_ippatsu": player.ippatsu_chance,  # 需要在apply_action中正确维护
            "is_menzen": player.is_menzen,
            "player_wind": player.seat_wind,
            "round_wind": game_state.round_wind,
            "dora_indicators": list(game_state.wall.dora_indicators),  # 复制列表
            "ura_dora_indicators": (
                list(game_state.wall.ura_dora_indicators)
                if player.riichi_declared
                else []
            ),
            "is_dealer": game_state.dealer_index == player.player_id,
            "honba": game_state.honba,
            "riichi_sticks": game_state.riichi_sticks,
            "win_tile": win_tile,
            "turn_number": game_state.turn_number,
            "wall_remaining": game_state.wall.get_remaining_live_tiles_count(),
            # 可以添加更多信息，如 'last_action_type' (判断枪杠), 'is_haitei' (海底), 'is_houtei' (河底) 等
        }
        return context

    def calculate_yaku_and_score(
        self,
        hand_tiles_final: List[Tile],  # 和牌时的14张牌
        melds: List[Meld],
        win_tile: Tile,
        context: Dict,
    ) -> Dict:
        """
        计算和牌的役种、翻数、符数和最终得分。
        (占位符 - 需要非常详细的实现)
        """
        # TBD: 实现完整的役种判断、符数计算和得分计算逻辑

        yaku_list = []
        han = 0
        fu = 20  # 底符

        # --- 0. 役种判断 (最复杂部分) ---
        # TBD: 实现所有役种的判断逻辑
        # 例如: 断幺九, 平和, 一杯口, 混全带, 一气通贯, 三色同顺, 役牌, 对对和, 三暗刻, 小三元, 混老头, 七对子, 清一色, 国士无双, 四暗刻, 大三元, 字一色 ...
        # 需要考虑副露减翻、复合、食下等规则

        # --- 简化示例 ---
        if context.get("is_riichi"):
            yaku_list.append(("立直", 1))
            han += 1
        # if context.get('is_ippatsu'):
        #     yaku_list.append(("一发", 1)); han += 1
        if context.get("is_tsumo") and context.get("is_menzen"):
            yaku_list.append(("门前清自摸和", 1))
            han += 1
            fu += 2  # 自摸符 (门清时)

        # 检查宝牌
        dora_han = 0
        all_tiles_in_hand_and_melds = hand_tiles_final + [
            t for meld in melds for t in meld.tiles
        ]
        dora_indicators = context.get("dora_indicators", [])
        ura_indicators = context.get("ura_dora_indicators", [])  # ura只在立直时有效

        for tile in all_tiles_in_hand_and_melds:
            dora_han += self._get_dora_value_for_tile(tile, dora_indicators)
            if context.get("is_riichi"):  # 只有立直才看里宝
                dora_han += self._get_dora_value_for_tile(tile, ura_indicators)
            if tile.is_red:  # 赤宝牌
                dora_han += 1

        if dora_han > 0:
            yaku_list.append((f"宝牌 {dora_han}", dora_han))
            han += dora_han

        # --- 1. 最低一番检查 ---
        # 注意：宝牌不能作为唯一的一番来源 (除非有其他役)
        non_dora_han = sum(h for name, h in yaku_list if "宝牌" not in name)
        if non_dora_han < 1:
            # print("和牌无役 (仅宝牌不算)") # 或者宝牌可以单独算？查规则
            # 严格来说，如果只有宝牌，应该不能和。但有些规则允许？
            # 暂时认为无役 (需要确认规则)
            return {
                "yaku": yaku_list,
                "han": han,
                "fu": 0,
                "error": "和牌无役 (一番缚)",
            }

        # --- 2. 符数计算 ---
        # TBD: 实现复杂的符数计算 (副底, 和牌方式, 面子组成, 雀头, 听牌型)
        # 简化：
        if self._is_seven_pairs_raw(hand_tiles_final, melds):
            fu = 25  # 七对子固定25符
        else:
            # 其他牌型粗略估计 (例如平和自摸20符，其他门清荣和30符，非门清...)
            # 门清荣和: 30符起?
            # 门清自摸: 20符起? (因为有自摸2符)
            # 副露: 20符起?
            fu = 30  # 粗略估计
            if context.get("is_tsumo") and context.get("is_menzen"):
                fu = 20  # 平和自摸形？
            # 加上自摸/荣和符、面子符、雀头符、听牌符...
            # ... 复杂计算 ...
            # 符数计算后需要向上取整到10位 (切り上げ)
            fu = ((fu + 9) // 10) * 10

        # --- 3. 得分计算 ---
        # TBD: 实现基于翻数、符数、庄闲、本场、供托的完整点数计算

        # 简化：计算基本点 (A)
        if han >= 13:
            base_points = 8000  # 役满 (子)
        elif han >= 11:
            base_points = 6000  # 三倍满
        elif han >= 8:
            base_points = 4000  # 倍满
        elif han >= 6:
            base_points = 3000  # 跳满
        elif han == 5:
            base_points = 2000  # 满贯
        elif han <= 4 and fu > 0:  # 满贯以下
            base_points = fu * (2 ** (han + 2))
            if base_points > 2000:
                base_points = 2000  # 4翻40符/3翻70符 截断为满贯
        else:
            base_points = 0  # 无役或计算错误?

        # 向上取整到百位
        base_points = ((base_points + 99) // 100) * 100

        # 计算实际支付 (简化)
        score_payments = {}  # {player_index: change}
        is_dealer = context.get("is_dealer", False)
        honba_bonus = context.get("honba", 0) * 300  # 每本场 300 点
        riichi_sticks_bonus = (
            context.get("riichi_sticks", 0) * 1000
        )  # 每根立直棒 1000 点

        if context.get("is_tsumo"):
            # 自摸支付
            oya_pay = ((base_points * 2 + 99) // 100) * 100 + (
                honba_bonus // 3 * 100
            )  # 庄家支付的部分 (需要处理本场)
            ko_pay = ((base_points + 99) // 100) * 100 + (
                honba_bonus // 3 * 100
            )  # 子家支付的部分

            winner_gain = 0
            num_players = context.get("num_players", 4)  # 需要从context获取
            for i in range(num_players):
                if i == context.get("player_id"):
                    continue  # player_id 需要在 context 中
                payment = (
                    oya_pay if i == context.get("dealer_id") else ko_pay
                )  # dealer_id 需要在 context 中
                score_payments[i] = -payment
                winner_gain += payment
            score_payments[context.get("player_id")] = winner_gain + riichi_sticks_bonus
        else:  # 荣和支付
            loser_id = context.get("loser_id")  # loser_id 需要在 context 中
            payment = 0
            if is_dealer:
                payment = base_points * 6
            else:
                payment = base_points * 4
            payment = ((payment + 99) // 100) * 100 + honba_bonus

            score_payments[loser_id] = -payment
            score_payments[context.get("player_id")] = payment + riichi_sticks_bonus

        # 需要确保 context 包含 player_id, dealer_id, loser_id, num_players
        print(
            "警告: 分数支付计算依赖 context 中的 player_id, dealer_id, loser_id, num_players"
        )

        return {
            "yaku": yaku_list,
            "han": han,
            "fu": fu,
            "score_base": base_points,
            "score_payments": score_payments,  # 字典形式的分数变化
            "error": None,
        }

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

    # TODO: 实现 _is_furiten (振听检查)
    # def _is_furiten(...) -> bool: ...
