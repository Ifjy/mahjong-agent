from __future__ import annotations
import random
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Set, TYPE_CHECKING  # 引入类型提示
from collections import Counter
from .actions import Action, ActionType, Tile, KanType


@dataclass(frozen=True)
class Meld:
    """表示一个副露 (吃, 碰, 杠)"""

    type: ActionType  # CHI, PON, KAN
    tiles: Tuple[Tile, ...]  # 组成该副露的牌 (对于KAN是4张，PON是3张，CHI是3张)
    from_player: int  # 该副露来自哪个玩家的弃牌 (-1 或自身索引表示暗杠/加杠)
    called_tile: Optional[Tile] = None  # 对于吃/碰/明杠，具体是哪张被叫的牌


class GamePhase(Enum):
    """枚举类型表示游戏当前阶段"""

    GAME_START = auto()  # 整场游戏开始前
    HAND_START = auto()  # 单局开始，准备发牌
    DEALING = auto()  # 发牌阶段 (内部状态)
    PLAYER_DRAW = auto()  # 轮到玩家摸牌 (等待环境触发摸牌)
    PLAYER_DISCARD = auto()  # 玩家摸牌后，等待其打牌或声明特殊动作(自摸/杠)
    WAITING_FOR_RESPONSE = auto()  # 玩家打牌后，等待其他玩家响应 (吃/碰/杠/荣和)
    ACTION_PROCESSING = auto()  # 正在处理一个动作 (例如鸣牌后等待该玩家打牌)
    HAND_OVER_SCORES = auto()  # 单局结束，结算分数
    GAME_OVER = auto()  # 整场游戏结束


class PlayerState:
    """表示单个玩家的状态"""

    def __init__(self, player_index: int, initial_score: int):
        self.player_index: int = player_index  # 玩家唯一标识 (0-3)
        self.score: int = initial_score  # 当前分数
        self.seat_wind: Optional[int] = (
            None  # 玩家座位风 (0=东, 1=南, 2=西, 3=北) - 每局开始时设置
        )

        # --- 手牌相关状态（每局重置）---
        self.hand: List[Tile] = []  # 玩家手牌列表 (暗牌)
        self.drawn_tile: Optional[Tile] = None  # 刚摸的牌 (若有)
        self.melds: List[Meld] = []  # 副露列表 (公开信息)
        self.discards: List[Tile] = []  # 弃牌列表 (公开信息，顺序重要)
        self.riichi_declared: bool = False  # 本局是否已立直
        self.riichi_turn: int = -1  # 立直在哪一巡声明 (-1表示未立直)
        self.ippatsu_chance: bool = False  # 当前是否有一发机会 (立直后一巡内)
        self.is_menzen: bool = True  # 是否门清 (无明示副露)
        # self.is_tenpai: bool = False           # 是否听牌 (可在 rules.py 中计算)
        # self.is_furiten: bool = False          # 是否振听 (可在 rules.py 中计算)
        self.has_won: bool = False  # 是否赢得比赛 (可在 rules.py 中计算)

    def reset_hand(self):
        """重置玩家状态以开始新局"""
        self.hand = []
        self.drawn_tile = None
        self.melds = []
        self.discards = []
        self.riichi_declared = False
        self.riichi_turn = -1
        self.ippatsu_chance = False
        self.is_menzen = True
        # seat_wind 会在 GameState.reset_new_hand 中重新分配


class Wall:
    """表示牌墙，包含王牌和宝牌指示牌"""

    NUM_TILES_TOTAL = 136  # 不含赤宝牌的标准数量
    NUM_DEAD_WALL = 14  # 王牌区固定14张
    NUM_REPLACEMENT_TILES = 4  # 岭上牌数量

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.live_tiles: List[Tile] = []  # 可供正常摸牌的牌
        self.dead_wall_tiles: List[Tile] = []  # 王牌区的牌 (包含岭上牌和指示牌)
        self.dora_indicators: List[Tile] = []  # 当前已公开的宝牌指示牌
        self.ura_dora_indicators: List[Tile] = []  # 里宝牌指示牌 (立直和牌后才公开)
        self.replacement_tiles_drawn: int = 0  # 已摸取的岭上牌数量 (0-4)

        # TODO: 实现赤宝牌逻辑 (根据 config)

    def _generate_tiles(self) -> List[Tile]:
        """生成一副完整的麻将牌 (包括可能的赤宝牌)"""
        tiles = []
        # 万子, 筒子, 索子 (0-26)
        for suit_offset in range(0, 27, 9):
            for value in range(9):  # 1-9
                tile_val = suit_offset + value
                # 处理赤宝牌 (假设每种5各有一张赤牌)
                num_normal = 4
                is_red_possible = value == 4  # 值是5 (索引为4)
                # TODO: 从 config 读取赤宝牌配置
                use_red_fives = self.config.get("use_red_fives", True)
                if use_red_fives and is_red_possible:
                    tiles.append(Tile(value=tile_val, is_red=True))
                    num_normal = 3
                tiles.extend([Tile(value=tile_val, is_red=False)] * num_normal)
        # 字牌 (27-33)
        for value in range(27, 34):
            tiles.extend([Tile(value=value, is_red=False)] * 4)

        # 验证总数是否正确
        expected_total = 136 + (3 if use_red_fives else 0)  # 考虑赤宝牌的总数
        # assert len(tiles) == expected_total, f"生成的牌数 ({len(tiles)}) 与预期 ({expected_total}) 不符"
        # 暂时忽略总数断言，允许不含赤宝牌的标准136张
        assert len(tiles) == 136, "暂不支持赤宝牌，牌数应为136"  # 强制标准牌数

        return tiles

    def shuffle_and_setup(self):
        """洗牌并设置牌墙、宝牌指示牌"""
        all_tiles = self._generate_tiles()
        random.shuffle(all_tiles)

        num_live = len(all_tiles) - self.NUM_DEAD_WALL
        self.live_tiles = all_tiles[:num_live]
        self.dead_wall_tiles = all_tiles[num_live:]
        self.replacement_tiles_drawn = 0  # 重置已摸岭上牌计数

        # 设置初始宝牌指示牌 (标准日麻：从右数第3墩上层牌)
        # 在我们的列表表示中，这对应于 dead_wall_tiles 的索引 4 (第5张牌)
        dora_indicator_index = 4
        ura_dora_indicator_index = 5

        if dora_indicator_index >= len(
            self.dead_wall_tiles
        ) or ura_dora_indicator_index >= len(self.dead_wall_tiles):
            print(
                f"错误：王牌区牌数 ({len(self.dead_wall_tiles)}) 不足以设置初始宝牌指示牌！"
            )
            # 这里应该抛出错误或采取其他处理
            self.dora_indicators = []
            self.ura_dora_indicators = []
        else:
            self.dora_indicators = [self.dead_wall_tiles[dora_indicator_index]]
            self.ura_dora_indicators = [self.dead_wall_tiles[ura_dora_indicator_index]]

        # print(f"牌墙设置: {len(self.live_tiles)} 张活动牌, {len(self.dead_wall_tiles)} 张王牌.")
        print(f"初始宝牌指示牌: {self.dora_indicators[0]}")

    def draw_tile(self) -> Optional[Tile]:
        """从活动牌墙摸一张牌"""
        if not self.live_tiles:
            return None
        return self.live_tiles.pop(0)  # 从牌尾摸牌 (列表开头)

    def draw_replacement_tile(self) -> Optional[Tile]:
        """杠后从王牌区摸一张岭上牌"""
        if self.replacement_tiles_drawn < self.NUM_REPLACEMENT_TILES:
            # 岭上牌通常是王牌区最开始的几张牌
            if self.replacement_tiles_drawn < len(self.dead_wall_tiles):
                drawn_tile = self.dead_wall_tiles[self.replacement_tiles_drawn]
                self.replacement_tiles_drawn += 1
                return drawn_tile
            else:
                print(f"错误：尝试摸取岭上牌时王牌区索引越界!")
                return None  # 王牌区逻辑错误
        else:
            # 岭上牌已摸完 (理论上最多4次杠)
            print("警告：岭上牌已摸完！")
            return None

    def reveal_new_dora(self) -> Optional[Tile]:
        """杠后公开一个新的宝牌指示牌"""
        # 新宝牌指示牌的位置依赖于已公开的数量
        # 初始指示牌在索引4, 之后每次杠是索引 4+2, 4+4, 4+6
        num_revealed = len(self.dora_indicators)
        if num_revealed < 1 + self.NUM_REPLACEMENT_TILES:  # 最多公开 1 + 4 = 5 个宝牌
            new_dora_index = 4 + (num_revealed * 2)
            new_ura_dora_index = new_dora_index + 1

            # 检查索引是否在王牌区范围内
            if new_dora_index < len(self.dead_wall_tiles) and new_ura_dora_index < len(
                self.dead_wall_tiles
            ):
                new_dora = self.dead_wall_tiles[new_dora_index]
                new_ura = self.dead_wall_tiles[new_ura_dora_index]
                self.dora_indicators.append(new_dora)
                self.ura_dora_indicators.append(new_ura)
                print(f"杠后公开新宝牌指示牌: {new_dora}")
                return new_dora
            else:
                print(f"错误：尝试公开新宝牌时王牌区索引越界！")
                return None  # 王牌区逻辑或配置错误
        return None  # 已达到最大宝牌数量

    def get_remaining_live_tiles_count(self) -> int:
        """返回活动牌墙剩余牌数"""
        return len(self.live_tiles)

    def _calculate_next_tile_value(self, value: int) -> int:
        """内部方法：根据指示牌的value计算下一个宝牌的value"""
        # 万子/筒子/索子 (0-26)
        if 0 <= value <= 26:
            suit_offset = (value // 9) * 9  # 0, 9, 18
            number_in_suit = value % 9  # 0-8 (对应 1-9)
            # 宝牌是数字+1，但9的宝牌是1
            next_number_in_suit = (number_in_suit + 1) % 9  # 8(9) -> 0(1)
            return suit_offset + next_number_in_suit
        # 风牌 (27-30) 东南北西
        elif 27 <= value <= 30:
            # 东(27)->南(28)->西(29)->北(30)->东(27)
            return 27 + ((value - 27 + 1) % 4)
        # 三元牌 (31-33) 白发中
        elif 31 <= value <= 33:
            # 白(31)->发(32)->中(33)->白(31)
            return 31 + ((value - 31 + 1) % 3)
        else:
            # 理论上不应该有其他值
            raise ValueError(f"Invalid tile value for calculating Dora: {value}")

    def get_current_dora_tiles(self) -> List[Tile]:
        """
        返回当前所有宝牌指示牌指示的实际宝牌列表。
        这些是 Tile 实例，代表哪些牌是宝牌，但不包含其原本的 is_red 属性。
        是否是红宝牌是在渲染时判断原始牌实例的 is_red 属性。
        """
        current_dora_tiles: List[Tile] = []
        for indicator_tile in self.dora_indicators:
            try:
                # 根据指示牌的 value 计算出宝牌的 value
                dora_value = self._calculate_next_tile_value(indicator_tile.value)
                # 创建一个代表这个宝牌的 Tile 实例，is_red 设为 False，因为宝牌本身不带红色属性
                # 红宝牌是牌局中实际存在的红色的牌，如果它的 value 恰好是宝牌值，那它就是红宝牌。
                # 这里的 Tile 实例仅用于判断哪些 value 是宝牌。
                current_dora_tiles.append(
                    Tile(value=dora_value, is_red=False)
                )  # 注意这里 is_red=False
            except ValueError as e:
                print(
                    f"Warning: Could not calculate Dora for indicator {indicator_tile}: {e}"
                )
                # 可以在这里添加一个占位符牌或者忽略，取决于你希望如何处理错误
                pass  # 简单忽略错误指示牌
        return current_dora_tiles


@dataclass
class GameState:
    """
    [重构版]
    表示日本麻将游戏的完整状态。
    存储所有必要信息，并通过 apply_action 应用状态变更。
    不包含复杂的控制流、规则校验或临时流程状态。
    """

    def __init__(self, config, wall: "Wall"):
        """
        初始化游戏状态。
        """
        self.config = config or {}
        self.num_players = self.config.get("num_players", 4)
        initial_score = self.config.get("initial_score", 25000)

        # --- 玩家状态列表 ---
        self.players: List["PlayerState"] = [
            PlayerState(i, initial_score) for i in range(self.num_players)
        ]

        # --- 牌墙状态 ---
        self.wall: "Wall" = wall

        # --- 游戏进程状态 ---
        self.round_wind: int = 0  # 当前场风 (0=东, 1=南, ...)
        self.round_number: int = 1  # 当前局数 (1-4)
        self.honba: int = 0  # 本场数
        self.riichi_sticks: int = 0  # 场上立直棒数
        self.dealer_index: int = 0  # 当前庄家索引

        # --- 核心状态指针 (由 GameController 更新) ---
        self.current_player_index: int = 0
        self.game_phase: "GamePhase" = GamePhase.GAME_START

        # --- 回合/动作相关状态 ---
        self.last_discarded_tile: Optional["Tile"] = None  # 最近一次打出的牌
        self.last_discard_player_index: int = -1  # 最近一次打牌的玩家索引
        self.last_action_info: Optional[Dict] = None  # 上一个被应用动作的信息

        # --- 局/游戏结束标记 ---
        self._hand_over_flag: bool = False  # 内部标记: 当前局是否结束?
        self._game_over_flag: bool = False  # 内部标记: 整场游戏是否结束?
        self.turn_number: int = 0  # 当前局的巡目数

        # 存储本局结束时的临时信息 (供 Controller 计分)
        self.hand_outcome_info_temp: Optional[Dict] = None

    def reset_game(self):
        """[数据] 重置整场游戏状态 (例如半庄开始)"""
        initial_score = self.config.get("initial_score", 25000)
        for player in self.players:
            player.score = initial_score

        self.round_wind = 0
        self.round_number = 1
        self.honba = 0
        self.riichi_sticks = 0
        self.dealer_index = 0  # 初始庄家为 0
        self._game_over_flag = False
        # 注意：game_phase 由 GameController 在调用此方法后设置
        print("游戏重置：数据已清空。")

    def reset_new_hand(self):
        """
        [数据] 重置数据以准备新的一局。
        *不* 负责发牌或设置游戏阶段。
        """
        print(
            f"\n--- 新局数据重置: {['东','南','西','北'][self.round_wind]}{self.round_number}局 庄家: {self.dealer_index} 本场: {self.honba} ---"
        )

        # 1. 重置玩家手牌相关状态并分配座位风
        for i, player in enumerate(self.players):
            player.reset_hand()
            player.seat_wind = (
                i - self.dealer_index + self.num_players
            ) % self.num_players

        # 2. 重置牌墙 (洗牌并设置宝牌)
        self.wall.shuffle_and_setup()

        # 3. 重置回合状态
        self.last_discarded_tile = None
        self.last_discard_player_index = -1
        self.last_action_info = {
            "type": "PASS",  # 假设有 ActionType.PASS
            "info": "NEW_HAND_RESET",
        }
        self._hand_over_flag = False
        self.turn_number = 0  # 设为0，第一次摸牌时(DRAW动作)再+1

        # 4. 重置临时和牌信息
        self.hand_outcome_info_temp = None

        print("新局数据重置完毕。等待 GameController 发牌...")

    def apply_action(self, action: "Action"):
        """
        [核心]
        应用一个 *已验证为合法* 的动作，并 *只* 修改数据状态。
        不包含任何规则校验、阶段转换、或流程控制逻辑。
        """
        self.last_action_info = {"type": action.type.name, "action_obj": action}

        try:
            player = self.players[action.player_index]
        except (AttributeError, IndexError):
            print(
                f"严重错误: 传入 apply_action 的动作 {action} 缺少合法的 player_index。"
            )
            return

        # --- 根据动作类型 *机械地* 更新状态 ---

        if action.type == "ActionType.DRAW":  # 假设 ActionType 是枚举
            tile_to_draw = action.tile
            player.drawn_tile = tile_to_draw
            # self.wall.remove_tile(tile_to_draw) # 假设 draw_tile() 已经移除了
            self.turn_number += 1
            self._clear_ippatsu_for_all_others(action.player_index)

        elif action.type == "ActionType.DISCARD":
            tile_to_discard = action.tile

            if player.drawn_tile and player.drawn_tile == tile_to_discard:
                player.drawn_tile = None
            else:
                # 假设 _remove_tiles_from_hand 可以处理
                if not self._remove_tiles_from_hand(player, [tile_to_discard]):
                    print(
                        f"严重错误: apply_action(DISCARD) 无法从手牌 {player.hand} 移除 {tile_to_discard}"
                    )
                    # 即使失败，也继续执行，但记录错误

            player.discards.append(tile_to_discard)
            self.last_discarded_tile = tile_to_discard
            self.last_discard_player_index = action.player_index

            if player.riichi_declared_this_turn:
                player.ippatsu_chance = False
                player.riichi_declared_this_turn = False

            self._update_furiten_status(player)

        elif action.type == "ActionType.RIICHI":
            player.riichi_declared = True
            player.riichi_declared_this_turn = True
            player.ippatsu_chance = True
            player.score -= 1000
            self.riichi_sticks += 1

            # --- 内联 DISCARD 逻辑 ---
            tile_to_discard = action.tile
            if player.drawn_tile and player.drawn_tile == tile_to_discard:
                player.drawn_tile = None
            else:
                if not self._remove_tiles_from_hand(player, [tile_to_discard]):
                    print(
                        f"严重错误: apply_action(RIICHI) 无法从手牌 {player.hand} 移除 {tile_to_discard}"
                    )

            player.discards.append(tile_to_discard)
            self.last_discarded_tile = tile_to_discard
            self.last_discard_player_index = action.player_index
            self._update_furiten_status(player)

        elif action.type == "ActionType.KAN" and action.kan_type in (
            "KanType.CLOSED",
            "KanType.ADDED",
        ):
            # 暗杠或加杠
            self._apply_kan_tile_removal(player, action)  # 移牌

            # 添加副露 (假设 Meld 是一个类)
            new_meld = Meld(
                type=action.kan_type,
                tiles=action.tiles,
                from_player=action.player_index,
            )
            player.melds.append(new_meld)

            if action.kan_type == "KanType.ADDED":
                player.menzen = False  # 加杠时 menzen 状态不变 (因为之前碰过)

            # 摸岭上牌 (假设 action 已包含摸到的牌)
            rinshan_tile = action.rinshan_tile
            player.drawn_tile = rinshan_tile
            # self.wall.remove_rinshan_tile(rinshan_tile) # 假设 draw 已移除

            # 翻新宝牌 (假设 action 已包含新的宝牌)
            if action.new_dora_indicator:
                self.wall.dora_indicators.append(action.new_dora_indicator)

            self._clear_ippatsu_for_all_others(action.player_index)

        elif action.type in ("ActionType.PON", "ActionType.CHI") or (
            action.type == "ActionType.KAN" and action.kan_type == "KanType.OPEN"
        ):
            # 吃、碰、大明杠

            self._apply_meld_tile_removal(player, action)  # 移牌

            new_meld = Meld(
                type=action.type,
                tiles=action.tiles,
                from_player=self.last_discard_player_index,
            )
            player.melds.append(new_meld)
            player.menzen = False

            # 清理弃牌 (被吃/碰/杠的牌)
            if self.players[self.last_discard_player_index].discards:
                self.players[self.last_discard_player_index].discards.pop()

            self._clear_ippatsu_for_all_others(action.player_index)

            # 关键：更新当前玩家索引
            self.current_player_index = action.player_index

            if action.type == "ActionType.KAN":  # 大明杠
                rinshan_tile = action.rinshan_tile
                player.drawn_tile = rinshan_tile
                # self.wall.remove_rinshan_tile(rinshan_tile)
                if action.new_dora_indicator:
                    self.wall.dora_indicators.append(action.new_dora_indicator)

        elif action.type == "ActionType.TSUMO":
            self._hand_over_flag = True
            self.hand_outcome_info_temp = {
                "type": "TSUMO",
                "winner": action.player_index,
                "winning_tile": player.drawn_tile or action.tile,  # 确保和牌牌被记录
            }

        elif action.type == "ActionType.RON":
            self._hand_over_flag = True
            self.hand_outcome_info_temp = {
                "type": "RON",
                "winner": action.player_index,
                "loser": self.last_discard_player_index,
                "winning_tile": self.last_discarded_tile,
            }

        elif action.type == "ActionType.SPECIAL_DRAW":  # (九种九牌等)
            self._hand_over_flag = True
            self.hand_outcome_info_temp = {
                "type": "ABORTIVE_DRAW",
                "reason": action.reason,
                "declarer": action.player_index,
            }

        elif action.type == "ActionType.PASS":
            # Pass 动作不修改核心数据
            pass

    # --- 以下是纯数据操作的私有辅助方法 (应保留) ---

    def _apply_kan_tile_removal(self, player, action: "Action"):
        """[数据] 纯数据操作：根据 action.kan_type 从手牌或 melds 移除牌"""
        if action.kan_type == "KanType.CLOSED":
            # 从手牌移除4张 (假设 action.tiles 包含要移除的4张)
            if not self._remove_tiles_from_hand(player, action.tiles):
                print(f"严重错误: apply_action(CLOSED_KAN) 无法移除 {action.tiles}")
        elif action.kan_type == "KanType.ADDED":
            # 从手牌移除1张 (假设 action.tile 是要加的那张)
            if not self._remove_tiles_from_hand(player, [action.tile]):
                print(f"严重错误: apply_action(ADDED_KAN) 无法移除 {action.tile}")

            # 更新旧的 PONG meld
            for meld in player.melds:
                # 假设 meld 是对象，有 .type 和 .tiles 属性
                if (
                    meld.type == "ActionType.PON"
                    and meld.tiles[0].value == action.tile.value
                ):
                    meld.type = "ActionType.KAN"
                    meld.tiles.append(action.tile)
                    break

    def _apply_meld_tile_removal(self, player, action: "Action"):
        """[数据] 纯数据操作：为 CHI, PON, OPEN_KAN 移牌"""
        # 假设 action 知道要从手牌移哪些
        tiles_to_remove = action.get_tiles_from_hand()
        if not self._remove_tiles_from_hand(player, tiles_to_remove):
            print(f"严重错误: apply_action(MELD) 无法移除 {tiles_to_remove}")

    def _clear_ippatsu_for_all_others(self, current_player_idx):
        """[数据] 纯数据操作：清除所有*其他*玩家的一发机会"""
        for i, p in enumerate(self.players):
            if i != current_player_idx:
                p.ippatsu_chance = False

    def _update_furiten_status(self, player):
        """[数据] 纯数据操作：更新玩家的振听状态"""
        # TODO: 实现振听逻辑 (检查弃牌堆和听牌)
        pass

    def _remove_tiles_from_hand(
        self, player: "PlayerState", tiles_to_remove: List["Tile"]
    ) -> bool:
        """
        [数据] 尝试从玩家手牌中移除指定的牌列表。要求精确匹配 Tile 对象。
        """
        if not tiles_to_remove:
            return True

        temp_hand = list(player.hand)
        hand_counts = Counter(temp_hand)
        tiles_needed_to_remove_counts = Counter(tiles_to_remove)

        # 检查手牌是否足够
        for tile, count_needed in tiles_needed_to_remove_counts.items():
            if hand_counts[tile] < count_needed:
                print(
                    f"错误: 玩家 {player.player_index} 手牌不足以移除牌 {tile} (需要 {count_needed}, 手牌只有 {hand_counts[tile]})。"
                )
                print(f"玩家 {player.player_index} 当前手牌: {player.hand}")
                return False

        # 执行移除操作
        new_hand = []
        temp_tiles_to_remove_counts = Counter(tiles_needed_to_remove_counts)

        for tile in temp_hand:
            if temp_tiles_to_remove_counts[tile] > 0:
                temp_tiles_to_remove_counts[tile] -= 1
            else:
                new_hand.append(tile)

        player.hand = new_hand
        return True

    # --- Getter 方法 (应保留) ---
    def get_player_state(self, player_index: int) -> Optional["PlayerState"]:
        if 0 <= player_index < self.num_players:
            return self.players[player_index]
        return None

    # --- 更新分数和推进游戏的方法 (由 Controller 调用, 应保留) ---

    def update_scores(self, score_changes: Dict[int, int]):
        """[数据] 根据计算结果更新玩家分数"""
        print(f"更新分数: {score_changes}")
        for player_index, change in score_changes.items():
            if 0 <= player_index < self.num_players:
                self.players[player_index].score += change
        print(f"更新后分数: {[(p.player_index, p.score) for p in self.players]}")
        # TODO: 检查是否有人被飞 (tobi)，并设置 _game_over_flag

    def apply_next_hand_state(self, next_hand_state_info: Dict[str, Any]):
        """
        [数据] 根据 RulesEngine 计算的下一局状态信息更新 GameState。
        """
        print(f"应用下一局状态: {next_hand_state_info}")
        self.dealer_index = next_hand_state_info["next_dealer_index"]
        self.round_wind = next_hand_state_info["next_round_wind"]
        self.round_number = next_hand_state_info["next_round_number"]
        self.honba = next_hand_state_info["next_honba"]
        self.riichi_sticks = next_hand_state_info["next_riichi_sticks"]

        # 检查游戏是否结束
        if next_hand_state_info.get("game_over", False):
            self._game_over_flag = True

    def get_info(self) -> Dict[str, Any]:
        """[数据] 获取当前游戏状态的部分信息 (用于调试或记录)"""
        return {
            "round": f"{['东','南','西','北'][self.round_wind]}{self.round_number}",
            "honba": self.honba,
            "riichi_sticks": self.riichi_sticks,
            "dealer": self.dealer_index,
            "current_player": self.current_player_index,
            "phase": self.game_phase.name,
            "scores": [p.score for p in self.players],
            "live_tiles_left": self.wall.get_remaining_live_tiles_count(),
            "dora_indicators": [str(t) for t in self.wall.dora_indicators],
            "last_discard": (
                str(self.last_discarded_tile) if self.last_discarded_tile else None
            ),
        }
