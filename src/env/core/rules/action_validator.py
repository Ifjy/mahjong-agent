# 负责所有动作的合法性校验和响应优先级解决。
# 类/函数,函数头,职责描述
# Class,ActionValidator,"__init__(self, hand_analyzer, config)"
# Self-Turn,"get_legal_draw_actions(self, player: PlayerState, game_state: GameState) -> List[Action]","整合 Tsumo, Riichi, Closed/Added Kan 和 Discard 动作的生成。"
# Validation,"can_tsumo(self, player: PlayerState, game_state: GameState) -> bool",检查是否符合 Tsumo 的规则（如：是否无役、振听等）。
# Validation,"can_declare_kan(self, player: PlayerState, tile: Tile, kan_type: KanType, game_state: GameState) -> bool",检查暗杠、加杠、大明杠的合法性（牌数是否足够、是否在立直后）。
# Validation,"can_declare_riichi(self, player: PlayerState, game_state: GameState) -> bool",检查立直的合法性（门清、听牌、点数足够）。
# Response,"get_legal_response_actions(self, player: PlayerState, game_state: GameState) -> List[Action]","整合 Ron, Pon, Open Kan, Chi 的动作生成。"
# Priority,"resolve_response_priorities(self, declarations: Dict[int, Action], discarder_index: int) -> Tuple[Optional[Action], Optional[int]]",根据优先级 (Ron > Kan/Pon > Chi) 确定唯一的获胜响应动作和玩家。 （来自 temp_from_game state.py）
# action_validator.py

from typing import List, Dict, Optional, Tuple, Any, Set, Counter as TypingCounter
from collections import Counter

# 假设从 actions.py 和 game_state.py 导入
from src.env.core.actions import Action, ActionType, Tile, KanType
from src.env.core.game_state import GameState, PlayerState, Meld, GamePhase

# 假设从 hand_analyzer.py 和 scoring.py 导入
from src.env.core.rules.hand_analyzer import HandAnalyzer
from src.env.core.rules.scoring import Scoring

# 假设从 constants.py 导入
from src.env.core.rules.constants import TERMINAL_HONOR_VALUES, ACTION_PRIORITY

# HACK: 占位符
TERMINAL_HONOR_VALUES = set(range(27, 34)) | {0, 8, 9, 17, 18, 26}


class ActionValidator:
    """
    动作校验器 (Action Validator)。
    负责：
    1. 根据当前状态，生成所有合法的候选动作。
    2. 解决响应优先级冲突。

    依赖：
    - HandAnalyzer: 用于检查手牌结构 (听牌, 和牌形状)。
    - Scoring: 用于检查和牌合法性 (一番缚, 振听)。
    """

    def __init__(self, hand_analyzer: "HandAnalyzer", scoring: "Scoring", config: Dict):
        """
        构造函数：注入 HandAnalyzer 和 Scoring 依赖。
        """
        self.hand_analyzer = hand_analyzer
        self.scoring = scoring
        self.config = config or {}
        # TODO: 从 config 加载规则, 例如是否允许立直后暗杠改变听牌

    # ======================================================================
    # == 公共 API (Public API) - 供 RulesEngine 调用 ==
    # ======================================================================

    def get_legal_actions_on_draw(
        self, player: "PlayerState", game_state: "GameState"
    ) -> List["Action"]:
        """
        生成玩家在自摸牌后 (PLAYER_DISCARD 阶段) 可以进行的所有合法动作。
        (此逻辑移植自旧 rules_engine.py 的 PLAYER_DISCARD 分支)
        """
        candidates: List["Action"] = []

        # 1. 检查自摸 (TSUMO)
        # **[重构关键]**：调用 self.scoring 检查合法性
        if player.drawn_tile and self._can_tsumo(player, game_state):
            candidates.append(
                Action(type=ActionType.TSUMO, winning_tile=player.drawn_tile)
            )

        # 2. 检查杠 (KAN) - 暗杠和加杠
        possible_kans = self._find_self_kans(player, game_state)
        candidates.extend(possible_kans)

        # 3. 检查互斥逻辑 (和牌/杠/立直)
        can_tsumo = any(c.type == ActionType.TSUMO for c in candidates)
        can_kan = any(c.type == ActionType.KAN for c in candidates)

        if not can_tsumo and not can_kan:
            # 只有在不自摸也不杠的情况下才考虑立直和打牌

            # 3a. 检查立直 (RIICHI)
            possible_riichi_discards = self._find_riichi_discards(player, game_state)
            for discard_tile in possible_riichi_discards:
                candidates.append(
                    Action(type=ActionType.RIICHI, riichi_discard=discard_tile)
                )

            # 3b. 生成所有可能的打牌动作 (DISCARD)
            candidates.extend(self._generate_discard_actions(player))

        # 4. 检查特殊流局 (九种九牌)
        if self._can_declare_kyuushu_kyuuhai(player, game_state):
            candidates.append(Action(type=ActionType.SPECIAL_DRAW))

        return candidates

    def get_legal_actions_on_response(
        self, player: "PlayerState", game_state: "GameState"
    ) -> List["Action"]:
        """
        生成玩家在响应弃牌时 (WAITING_FOR_RESPONSE 阶段) 可以进行的所有合法动作。
        (此逻辑移植自旧 rules_engine.py 的 WAITING_FOR_RESPONSE 分支)
        """
        candidates: List["Action"] = []
        last_discard = game_state.last_discarded_tile

        if not last_discard:
            return [Action(type=ActionType.PASS)]  # 安全校验

        # 1. 检查荣和 (RON)
        # **[重构关键]**：调用 self.scoring 检查合法性
        if self._can_ron(player, last_discard, game_state):
            candidates.append(Action(type=ActionType.RON, winning_tile=last_discard))

        # 如果已立直，通常不能再进行碰/杠/吃
        if not player.riichi_declared:
            # 2. 检查碰 (PON)
            if self._can_pon(player, last_discard):
                pon_tile_type = Tile(value=last_discard.value, is_red=False)
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
            ) % game_state.num_players == player.player_index:
                candidates.extend(self._find_chi_actions(player, last_discard))

        # 5. 必须可以 PASS (不响应)
        candidates.append(Action(type=ActionType.PASS))

        return candidates

    def resolve_response_priorities(
        self, declarations: Dict[int, "Action"], discarder_index: int, num_players: int
    ) -> Tuple[Optional["Action"], Optional["int"]]:
        """
        根据收集到的响应声明，解决优先级冲突。
        (此逻辑移植自 temp_from_game state.py)

        规则: Ron (any) > Pon/Kan (any) > Chi (next player)
        """
        # HACK: 假设 ACTION_PRIORITY 已从 constants 导入
        priority_map = {
            ActionType.RON: 3,
            ActionType.KAN: 2,
            ActionType.PON: 2,
            ActionType.CHI: 1,
            ActionType.PASS: 0,
        }

        # 1. 检查 Ron (最高优先级)
        ron_declarations = {
            idx: action
            for idx, action in declarations.items()
            if action.type == ActionType.RON
        }
        if ron_declarations:
            # 检查上家 > 对家 > 下家 (逆时针最近)
            for i in range(1, num_players):
                player_idx_check = (discarder_index - i) % num_players
                if player_idx_check in ron_declarations:
                    return ron_declarations[player_idx_check], player_idx_check

        # 2. 检查 Pon/Kan (次高优先级)
        pon_kan_declarations = {
            idx: action
            for idx, action in declarations.items()
            if action.type in {ActionType.PON, ActionType.KAN}
        }
        if pon_kan_declarations:
            # 检查上家 > 对家 > 下家
            for i in range(1, num_players):
                player_idx_check = (discarder_index - i) % num_players
                if player_idx_check in pon_kan_declarations:
                    return pon_kan_declarations[player_idx_check], player_idx_check

        # 3. 检查 Chi (最低优先级)
        chi_declarations = {
            idx: action
            for idx, action in declarations.items()
            if action.type == ActionType.CHI
        }
        if chi_declarations:
            # 只有下家能 Chi
            next_player_index = (discarder_index + 1) % num_players
            if next_player_index in chi_declarations:
                return chi_declarations[next_player_index], next_player_index

        # 所有人Pass
        return None, None

    # ======================================================================
    # == 内部辅助 (Internal Helpers) - (移植自旧 rules_engine.py) ==
    # ======================================================================

    # --- 和牌检查 (重构关键) ---

    def _can_tsumo(self, player: "PlayerState", game_state: "GameState") -> bool:
        """
        检查玩家是否能自摸。
        **[重构]** 委托给 self.scoring 检查 (一番缚 + 振听)。
        """
        if not player.drawn_tile:
            return False

        # 委托 Scoring 模块进行完整检查 (形状, 役种, 振听)
        return self.scoring.is_valid_win(
            player, player.drawn_tile, is_tsumo=True, game_state=game_state
        )

    def _can_ron(
        self, player: "PlayerState", target_tile: "Tile", game_state: "GameState"
    ) -> bool:
        """
        检查玩家是否能荣和。
        **[重构]** 委托给 self.scoring 检查 (一番缚 + 振听)。
        """
        if not target_tile:
            return False

        # 委托 Scoring 模块进行完整检查 (形状, 役种, 振听)
        return self.scoring.is_valid_win(
            player, target_tile, is_tsumo=False, game_state=game_state
        )

    # --- 鸣牌检查 (简单移植) ---

    def _can_pon(self, player: "PlayerState", target_tile: "Tile") -> bool:
        """检查玩家是否能碰目标牌 (移植)"""
        if not target_tile or player.riichi_declared:
            return False
        # 手牌中至少有两张同种牌 (只比较 value)
        count = sum(1 for t in player.hand if t.value == target_tile.value)
        return count >= 2

    def _can_open_kan(self, player: "PlayerState", target_tile: "Tile") -> bool:
        """检查玩家是否能明杠目标牌 (移植)"""
        if not target_tile or player.riichi_declared:
            return False
        # 手牌中至少有三张同种牌 (只比较 value)
        count = sum(1 for t in player.hand if t.value == target_tile.value)
        return count >= 3

    def _find_chi_actions(
        self, player: "PlayerState", discarded_tile: "Tile"
    ) -> List["Action"]:
        """为响应阶段查找所有可能的吃牌动作 (移植)"""
        # (此逻辑从旧 rules_engine.py 完整迁移)
        chi_actions: List[Action] = []
        if discarded_tile.value >= 27:  # 字牌不能吃
            return []

        hand_tiles = player.hand
        target_value = discarded_tile.value

        # 模式 1: 需要 T-2, T-1 (例如，有 3m, 4m，吃 5m)
        if target_value % 9 >= 2:
            val1, val2 = target_value - 1, target_value - 2
            tile1_options = [t for t in hand_tiles if t.value == val1]
            tile2_options = [t for t in hand_tiles if t.value == val2]
            if tile1_options and tile2_options:
                chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                chi_actions.append(
                    Action(
                        type=ActionType.CHI, chi_tiles=chi_combo, tile=discarded_tile
                    )
                )

        # 模式 2: 需要 T-1, T+1 (例如，有 4m, 6m，吃 5m)
        if 1 <= target_value % 9 <= 7:
            val1, val2 = target_value - 1, target_value + 1
            tile1_options = [t for t in hand_tiles if t.value == val1]
            tile2_options = [t for t in hand_tiles if t.value == val2]
            if tile1_options and tile2_options:
                # (为简化，省略了旧代码中处理手牌多张同种牌的复杂逻辑，假设找到即可)
                chi_combo = tuple(sorted((tile1_options[0], tile2_options[0])))
                chi_actions.append(
                    Action(
                        type=ActionType.CHI, chi_tiles=chi_combo, tile=discarded_tile
                    )
                )

        # 模式 3: 需要 T+1, T+2 (例如，有 6m, 7m，吃 5m)
        if target_value % 9 <= 6:
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

        # 去重
        unique_chi_actions = []
        seen_chi_tiles = set()
        for action in chi_actions:
            if action.chi_tiles not in seen_chi_tiles:
                unique_chi_actions.append(action)
                seen_chi_tiles.add(action.chi_tiles)

        return unique_chi_actions

    # --- 自摸回合动作检查 (移植) ---

    def _find_self_kans(
        self, player: "PlayerState", game_state: "GameState"
    ) -> List["Action"]:
        """查找玩家在自己回合可以进行的杠 (暗杠, 加杠) (移植)"""
        kan_actions: List["Action"] = []
        full_hand_tiles = player.hand + (
            [player.drawn_tile] if player.drawn_tile else []
        )

        # 1. 查找暗杠 (Ankan)
        tile_counts: TypingCounter[Tile] = Counter(full_hand_tiles)
        for tile_object, count in tile_counts.items():
            if count == 4:
                # TODO: 检查立直后暗杠是否改变听牌
                # if player.riichi_declared and self._ankan_changes_wait(player, tile_object, game_state):
                #     continue
                kan_actions.append(
                    Action(
                        type=ActionType.KAN, kan_type=KanType.CLOSED, tile=tile_object
                    )  # 简化 tile 参数
                )

        # 2. 查找加杠 (Kakan)
        for meld in player.melds:
            if meld["type"] == ActionType.PON:
                pon_tile_value = meld["tiles"][0].value
                for tile in full_hand_tiles:
                    if tile.value == pon_tile_value:
                        # TODO: 检查立直后加杠是否改变听牌
                        kan_actions.append(
                            Action(
                                type=ActionType.KAN, kan_type=KanType.ADDED, tile=tile
                            )
                        )
                        break  # 一个碰只能加杠一次

        # TODO: 去重逻辑
        return kan_actions

    def _can_declare_riichi_basics(
        self, player: "PlayerState", game_state: "GameState"
    ) -> bool:
        """检查是否满足立直的基本条件 (门清、分数、剩余牌数、未立直) (移植)"""
        return (
            player.is_menzen
            and player.score >= 1000
            and game_state.wall.get_remaining_live_tiles_count() >= 4
            and not player.riichi_declared
        )

    def _find_riichi_discards(
        self, player: "PlayerState", game_state: "GameState"
    ) -> List["Tile"]:
        """查找宣告立直时可以打出的牌 (打了之后必须听牌) (移植)"""
        riichi_discards: List["Tile"] = []
        if not self._can_declare_riichi_basics(player, game_state):
            return []

        possible_discards = player.hand + (
            [player.drawn_tile] if player.drawn_tile else []
        )

        processed_tile_keys = set()
        for tile_to_discard in possible_discards:
            tile_key = (tile_to_discard.value, tile_to_discard.is_red)
            if tile_key in processed_tile_keys:
                continue
            processed_tile_keys.add(tile_key)

            # 模拟打出这张牌后的手牌
            temp_hand_after_discard = [
                t for t in possible_discards if t != tile_to_discard
            ]

            # **[重构关键]**：调用 self.hand_analyzer 检查听牌
            if self.hand_analyzer.is_tenpai(temp_hand_after_discard, player.melds):
                riichi_discards.append(tile_to_discard)

        return riichi_discards

    def _generate_discard_actions(self, player: "PlayerState") -> List["Action"]:
        """为打牌阶段生成所有可能的打牌动作 (移植)"""
        discard_actions: List["Action"] = []
        full_hand_tiles = player.hand + (
            [player.drawn_tile] if player.drawn_tile else []
        )

        # TODO: 考虑食替 (kuikae) 规则

        processed_tiles = set()
        for tile in full_hand_tiles:
            tile_key = (tile.value, tile.is_red)
            if tile_key not in processed_tiles:
                discard_actions.append(Action(type=ActionType.DISCARD, tile=tile))
                processed_tiles.add(tile_key)

        return discard_actions

    def _can_declare_kyuushu_kyuuhai(
        self, player: "PlayerState", game_state: "GameState"
    ) -> bool:
        """检查是否满足九种九牌流局条件 (移植)"""
        if game_state.turn_number != 1 or not player.is_menzen:
            return False

        full_hand = player.hand + ([player.drawn_tile] if player.drawn_tile else [])
        if len(full_hand) != 14:  # 必须是刚摸完牌
            return False

        unique_terminal_honors_count = len(
            {t.value for t in full_hand if t.value in TERMINAL_HONOR_VALUES}
        )
        return unique_terminal_honors_count >= 9
