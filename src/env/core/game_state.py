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


@dataclass
class PlayerState:
    """表示单个玩家的状态"""

    # --- 基础信息 ---
    player_index: int
    score: int
    seat_wind: int = 0  # 0-3: 东东南西 (初始化时应被覆盖)

    # --- 手牌与副露 (核心数据) ---
    hand: List[Tile] = field(default_factory=list)  # 手牌 (不含副露)
    melds: List[Meld] = field(default_factory=list)  # 副露 (吃碰杠)
    discards: List[Tile] = field(default_factory=list)  # 弃牌河
    drawn_tile: Optional[Tile] = None  # 当前摸到的牌 (尚未切出或加入手牌)

    # --- 立直相关状态 ---
    riichi_declared: bool = False  # 是否已立直 (持久状态)
    riichi_declared_this_turn: bool = (
        False  # [新增] 是否刚在这个动作中宣言立直 (临时状态)
    )
    riichi_turn: int = -1  # 立直发生的总巡目数 (用于判断双立直等)
    ippatsu_chance: bool = False  # 是否有一发机会

    # --- 规则校验缓存 (Caching) ---
    # 这些状态由 RulesEngine 计算后填入，用于加速 valid_actions 生成和 Observation
    is_menzen: bool = True  # 是否门清 (计算符数和役种必需)
    is_tenpai: bool = False  # 是否听牌 (用于流局罚符)
    is_furiten: bool = False  # [新增] 是否处于振听状态 (用于禁止荣和)

    # --- 统计/结算信息 ---
    has_won: bool = False  # 是否和牌

    def reset_hand(self):
        """重置玩家状态以开始新局"""
        self.hand.clear()
        self.melds.clear()
        self.discards.clear()
        self.drawn_tile = None

        self.riichi_declared = False
        self.riichi_declared_this_turn = False
        self.riichi_turn = -1
        self.ippatsu_chance = False

        self.is_menzen = True
        self.is_tenpai = False
        self.is_furiten = False
        self.has_won = False

    @property
    def is_dealer(self) -> bool:
        """
        (可选) 辅助属性，但需要访问 parent GameState。
        通常我们在外部判断： player.player_index == gamestate.dealer_index
        """
        return False  # 占位，实际逻辑在外部处理


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

    # game_state.py (部分)

    def apply_action(self, player_idx: int, action: "Action"):
        """
        [核心] 应用一个 *已验证为合法* 的动作，并 *只* 修改数据状态。
        不包含规则校验、流程控制或副作用（如摸岭上牌、翻宝牌）。
        """
        # 记录最后一次动作 (用于日志或回放)
        self.last_action_info = {
            "player": player_idx,
            "type": action.type.name,
            "action_obj": action,
        }

        try:
            player = self.players[player_idx]
        except IndexError:
            print(f"严重错误: apply_action 收到无效的 player_idx {player_idx}")
            return

        # ==================================================================
        # 1. 打牌 (DISCARD)
        # ==================================================================
        if action.type == ActionType.DISCARD:
            tile_to_discard = action.tile

            # 优先切出刚摸到的牌 (Tsumogiri)
            if player.drawn_tile and player.drawn_tile == tile_to_discard:
                player.drawn_tile = None
                # 记录切出的是摸到的牌 (用于UI显示灰色)
                # self.last_discard_was_tsumogiri = True
            else:
                # 切出手牌中的牌 (Te-dashi)
                # 如果有摸到的牌，先把它并入手牌 (理牌)
                if player.drawn_tile:
                    player.hand.append(player.drawn_tile)
                    player.drawn_tile = None
                    player.hand.sort()

                self._remove_tiles_from_hand(player, [tile_to_discard])
                # self.last_discard_was_tsumogiri = False

            # 添加到弃牌河
            player.discards.append(tile_to_discard)

            # 更新全局状态
            self.last_discarded_tile = tile_to_discard
            self.last_discard_player_index = player_idx

            # 如果这回合立直了，清理临时标记
            if player.riichi_declared_this_turn:
                player.riichi_declared_this_turn = False  # 立直成立
                # player.ippatsu_chance 保持为 True，直到下一次轮换

            # 更新振听状态 (舍牌振听)
            # (逻辑：如果听牌，且打出的牌是听的牌，则振听)
            # TODO: self._update_furiten_status(player)

        # ==================================================================
        # 2. 立直 (RIICHI)
        # ==================================================================
        elif action.type == ActionType.RIICHI:
            # 立直宣言 (扣分在下家打牌通过后才结算，但通常简化为立即扣分)
            # 这里我们立即执行状态变更
            player.riichi_declared = True
            player.riichi_declared_this_turn = True  # 标记为“刚立直”，用于一发判断
            player.ippatsu_chance = True

            player.score -= 1000
            self.riichi_sticks += 1

            # 立直必定伴随打牌 (Action.riichi_discard)
            tile_to_discard = action.riichi_discard

            # 执行打牌逻辑 (复制自 DISCARD，或递归调用)
            if player.drawn_tile and player.drawn_tile == tile_to_discard:
                player.drawn_tile = None
            else:
                if player.drawn_tile:
                    player.hand.append(player.drawn_tile)
                    player.drawn_tile = None
                    player.hand.sort()
                self._remove_tiles_from_hand(player, [tile_to_discard])

            player.discards.append(tile_to_discard)
            self.last_discarded_tile = tile_to_discard
            self.last_discard_player_index = player_idx

        # ==================================================================
        # 3. 鸣牌 (CHI / PON / OPEN KAN)
        # ==================================================================
        elif action.type in (ActionType.CHI, ActionType.PON) or (
            action.type == ActionType.KAN and action.kan_type == KanType.OPEN
        ):
            # 1. 从手牌移除用来鸣牌的搭子
            # (注意：Action 中包含的是 *全部* 组成副露的牌，还是只包含 *手牌中* 的牌？)
            # (根据您的 Action 定义：chi_tiles 是手牌中的两张; KAN/PON 的 tile 是目标牌)

            tiles_to_remove = []
            meld_tiles = []

            if action.type == ActionType.CHI:
                tiles_to_remove = list(action.chi_tiles)  # 手牌中的两张
                # 副露 = 吃的那张 + 手牌两张
                meld_tiles = [self.last_discarded_tile] + tiles_to_remove

            elif action.type == ActionType.PON:
                target_tile = action.tile  # 碰的牌 (类型)
                # 手牌中需要移除 2 张
                # 为了找到具体的 Tile 实例，我们需要在手牌里搜
                # 简化：假设 _remove_tiles_by_value 或调用者保证
                # 这里假设我们能找到 2 张匹配的牌
                found = [t for t in player.hand if t.value == target_tile.value][:2]
                tiles_to_remove = found
                meld_tiles = [self.last_discarded_tile] + tiles_to_remove

            elif action.type == ActionType.KAN:  # 明杠
                target_tile = action.tile
                # 手牌中移除 3 张
                found = [t for t in player.hand if t.value == target_tile.value][:3]
                tiles_to_remove = found
                meld_tiles = [self.last_discarded_tile] + tiles_to_remove

            self._remove_tiles_from_hand(player, tiles_to_remove)

            # 2. 创建副露对象
            new_meld = Meld(
                type=action.type,
                tiles=tuple(meld_tiles),  # 转为 tuple
                from_player=self.last_discard_player_index,
                called_tile=self.last_discarded_tile,
            )
            player.melds.append(new_meld)

            # 3. 更新状态
            player.is_menzen = False
            # 鸣牌者成为当前玩家
            self.current_player_index = player_idx

            # 4. 从上家弃牌河中“拿走”这张牌 (UI逻辑，通常不物理删除，而是标记为被鸣牌)
            # 但为了数据一致性，许多环境会选择保留在河里但标记，或者移出。
            # 这里我们选择保留引用，不做物理删除 (符合标准日麻记录，虽然显示上会移动)
            pass

            # 清除所有人的“一发”状态
            self._clear_ippatsu_for_all()

        # ==================================================================
        # 4. 暗杠 / 加杠 (CLOSED KAN / ADDED KAN)
        # ==================================================================
        elif action.type == ActionType.KAN and action.kan_type in (
            KanType.CLOSED,
            KanType.ADDED,
        ):

            if action.kan_type == KanType.CLOSED:
                # 暗杠：从手牌移除 4 张
                target_tile = action.tile
                # 包含摸到的牌
                full_hand = player.hand + (
                    [player.drawn_tile] if player.drawn_tile else []
                )
                found = [t for t in full_hand if t.value == target_tile.value][:4]

                # 移除 (需要处理 drawn_tile)
                if player.drawn_tile in found:
                    player.drawn_tile = None
                    found.remove(player.drawn_tile)  # 剩下的从 hand 移
                self._remove_tiles_from_hand(player, found)

                # 创建副露 (暗杠 from_player = 自己)
                new_meld = Meld(
                    type=ActionType.KAN,
                    tiles=tuple(
                        found + ([player.drawn_tile] if player.drawn_tile else [])
                    ),  # 4张
                    from_player=player_idx,
                    called_tile=None,
                )
                player.melds.append(new_meld)
                # 暗杠不破坏门清 (is_menzen 保持原样)

            elif action.kan_type == KanType.ADDED:
                # 加杠：从手牌移除 1 张，加到已有的 PON 上
                target_tile = action.tile

                # 移除
                if player.drawn_tile and player.drawn_tile.value == target_tile.value:
                    added_tile = player.drawn_tile
                    player.drawn_tile = None
                else:
                    # 从手牌找
                    added_tile = next(
                        (t for t in player.hand if t.value == target_tile.value), None
                    )
                    if added_tile:
                        self._remove_tiles_from_hand(player, [added_tile])

                # 更新副露
                for i, m in enumerate(player.melds):
                    if (
                        m.type == ActionType.PON
                        and m.tiles[0].value == target_tile.value
                    ):
                        # 替换旧的 PON 为新的 KAN
                        new_tiles = m.tiles + (added_tile,)
                        new_meld = Meld(
                            type=ActionType.KAN,
                            tiles=new_tiles,
                            from_player=m.from_player,
                            called_tile=m.called_tile,
                        )
                        player.melds[i] = new_meld
                        break

            self._clear_ippatsu_for_all()
            # 鸣牌也意味着上一家的“刚立直”状态结束（立直成立）
            # 虽然通常 riichi_declared_this_turn 主要用于判断是否刚刚打出了立直宣言牌
            # 在鸣牌后，就不再是“刚宣言”的状态了
            self.players[self.last_discard_player_index].riichi_declared_this_turn = (
                False
            )

        # ==================================================================
        # 5. 和牌 (TSUMO / RON)
        # ==================================================================
        elif action.type in (ActionType.TSUMO, ActionType.RON):
            # 仅设置标志位，具体的结算逻辑由 GameController 调用 RulesEngine 处理
            self._hand_over_flag = True
            # 注意：不在这里修改分数，分数修改由 RulesEngine 计算后回填

        # ==================================================================
        # 6. 流局 (SPECIAL DRAW)
        # ==================================================================
        elif action.type == ActionType.SPECIAL_DRAW:
            self._hand_over_flag = True

        elif action.type == ActionType.PASS:
            pass

    # --- 辅助方法 ---

    def _clear_ippatsu_for_all(self):
        """任何鸣牌都会消除所有人的“一发”机会"""
        for p in self.players:
            p.ippatsu_chance = False

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
        self, player: PlayerState, tiles_to_remove: List[Tile]
    ) -> bool:
        """从手牌中移除指定的牌实例"""
        for t in tiles_to_remove:
            if t in player.hand:
                player.hand.remove(t)
            else:
                return False  # 找不到牌
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
