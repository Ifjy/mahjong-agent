from __future__ import annotations
import random
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Set, TYPE_CHECKING  # 引入类型提示
from collections import Counter
from .actions import Action, ActionType, Tile, KanType

if TYPE_CHECKING:
    # 只有在 TYPE_CHECKING 为 True 时，这个导入才会被处理
    from rules import RulesEngine


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
    表示日本麻将游戏的完整状态。
    存储所有必要信息，并通过 apply_action 应用状态变更。
    不包含复杂的控制流或规则校验逻辑。
    """

    def __init__(self, config, wall: Wall, rules_engine: RulesEngine):
        """
        初始化游戏状态。
        Args:
            config (dict): 配置参数, 例如 {"num_players": 4, "initial_score": 25000, ...}
        """
        self.config = config or {}
        self.num_players = self.config.get("num_players", 4)
        initial_score = self.config.get("initial_score", 25000)

        # --- 玩家状态列表 ---
        self.players: List[PlayerState] = [
            PlayerState(i, initial_score) for i in range(self.num_players)
        ]

        # --- 牌墙状态 ---
        self.wall: Wall = wall
        self.rules_engine: RulesEngine = rules_engine
        # --- 游戏进程状态 ---
        self.round_wind: int = 0  # 当前场风 (0=东, 1=南, ...)
        self.round_number: int = 1  # 当前局数 (1-4)
        self.honba: int = 0  # 本场数
        self.riichi_sticks: int = 0  # 场上立直棒数
        self.dealer_index: int = 0  # 当前庄家索引
        self.current_player_index: int = 0  # 当前需要行动的玩家索引 (由 Env 控制)

        # --- 回合/动作相关状态 ---
        self.game_phase: GamePhase = GamePhase.GAME_START  # 当前游戏阶段 (由 Env 控制)
        self.last_discarded_tile: Optional[Tile] = None  # 最近一次打出的牌
        self.last_discard_player_index: int = -1  # 最近一次打牌的玩家索引
        self.last_action_info: Optional[Dict] = (
            None  # 关于上一个被应用动作的信息 (供调试/记录)
        )
        # --- 新增用于响应阶段的状态 ---
        # 存储玩家声明的响应动作 {player_index: Action}
        _response_declarations: Dict[int, Action] = field(default_factory=dict)
        # 需要声明响应的玩家索引列表，按声明顺序排列 (通常按逆时针顺位)
        _responders_to_prompt: List[int] = field(default_factory=list)
        # 记录已经完成响应声明的玩家索引集合
        _responded_to_current_discard: Set[int] = field(default_factory=set)
        # ---------------------------------------
        num_players: int = 4  # 玩家数量
        self._hand_over_flag: bool = False  # 内部标记: 当前局是否结束?
        self._game_over_flag: bool = False  # 内部标记: 整场游戏是否结束?
        self.turn_number: int = 0  # 当前局的巡目数

        # 注意: 移除了 response_action_cache 和 current_responders
        # 响应管理和轮转控制移至 mahjong_env.py

    def reset_game(self):
        """重置整场游戏状态 (例如半庄开始)"""
        initial_score = self.config.get("initial_score", 25000)
        for player in self.players:
            player.score = initial_score

        self.round_wind = 0
        self.round_number = 1
        self.honba = 0
        self.riichi_sticks = 0
        self.dealer_index = 0  # 初始庄家为 0
        self._game_over_flag = False
        self.game_phase = GamePhase.GAME_START  # 准备开始第一局
        print("游戏重置：东1局 0本场")
        # 不在此处调用 reset_new_hand，由环境在需要时调用

    def reset_new_hand(self):
        """重置状态以开始新的一局"""
        print(
            f"\n--- 新局开始: {['东','南','西','北'][self.round_wind]}{self.round_number}局 庄家: {self.dealer_index} 本场: {self.honba} ---"
        )

        # 1. 重置玩家手牌相关状态并分配座位风
        for i, player in enumerate(self.players):
            player.reset_hand()
            player.seat_wind = (
                i - self.dealer_index + self.num_players
            ) % self.num_players
            # print(f"玩家 {i} 座位风: {['东','南','西','北'][player.seat_wind]}") # 调试

        # 2. 设置牌墙
        self.wall.shuffle_and_setup()

        # 3. 发初始手牌 (每人13张)
        self.game_phase = GamePhase.DEALING  # 标记为发牌中 (内部状态)
        try:
            for i in range(self.num_players):
                player_hand = []
                for _ in range(13):
                    tile = self.wall.draw_tile()
                    if tile is None:
                        raise ValueError("发初始牌时牌墙不足！")
                    player_hand.append(tile)
                player_hand.sort()  # 排序手牌
                self.players[i].hand = player_hand
                # print(f"玩家 {i} 初始手牌: {[str(t) for t in player_hand]}") # 调试

            # 4. 庄家摸第14张牌
            dealer_player_index = self.dealer_index
            initial_draw = self.wall.draw_tile()
            if initial_draw is None:
                raise ValueError("庄家摸初始牌时牌墙不足！")
            self.players[dealer_player_index].drawn_tile = initial_draw
            # print(f"庄家 {dealer_player_index} 摸初始牌: {initial_draw}") # 调试

        except ValueError as e:
            print(f"错误: {e}")
            # 发生错误，标记牌局结束，可能需要特殊流局处理
            self._hand_over_flag = True
            self.game_phase = GamePhase.HAND_OVER_SCORES  # 进入结算
            return  # 提前退出

        # 5. 重置回合状态
        self.last_discarded_tile = None
        self.last_discard_player_index = -1
        self.last_action_info = {
            "type": ActionType.PASS.name,
            "info": "NEW_HAND_DEALT",
        }  # 标记新局开始
        self._hand_over_flag = False
        self.turn_number = 1  # 第一巡开始

        # 6. 设置初始游戏阶段和当前玩家 (由环境设置，这里只标记完成发牌)
        self.current_player_index = self.dealer_index  # 初始行动者是庄家
        self.game_phase = GamePhase.PLAYER_DISCARD  # 庄家需要打牌
        print(
            f"发牌完成，轮到庄家 {self.dealer_index} 行动，阶段: {self.game_phase.name}"
        )

    def _perform_discard_logic(
        self, player: "PlayerState", tile_to_discard: "Tile"
    ) -> bool:
        """
        执行从玩家手牌或摸牌区移除打出牌的逻辑。
        返回 True 表示成功移除，False 表示失败 (牌不在手牌/摸牌区)。
        不负责加入牌河，只负责从手牌/摸牌区移除。
        """
        is_discarding_drawn_tile = (
            player.drawn_tile is not None and tile_to_discard == player.drawn_tile
        )
        is_discarding_from_hand = tile_to_discard in player.hand

        if not (is_discarding_drawn_tile or is_discarding_from_hand):
            # 这个检查通常应在生成合法动作时完成，但在 apply_action 内部也作为安全检查
            print(
                f"错误: _perform_discard_logic 无法移除牌 {tile_to_discard} - 不在手牌或摸到的牌中。"
            )
            return False

        if is_discarding_drawn_tile:
            # 情况 1: 打出摸到的牌 (摸切)
            print(f"玩家 {player.player_index} 摸切 {tile_to_discard}")
            player.drawn_tile = None  # 清除摸到的牌槽位
            # 手牌 player.hand 保持不变 (13张)
            return True

        elif is_discarding_from_hand:
            # 情况 2: 打出手牌中的牌 (手切)
            print(f"玩家 {player.player_index} 手切 {tile_to_discard}")
            # 先将摸到的牌加入手牌（如果存在），然后清除摸到的牌槽位
            if player.drawn_tile is not None:
                player.hand.append(player.drawn_tile)
                player.drawn_tile = None

            # 从手牌中移除被打出的那张牌
            # 使用辅助方法 _remove_tiles_from_hand 来处理精确移除 (包括赤宝牌)
            if self._remove_tiles_from_hand(
                player, [tile_to_discard]
            ):  # _remove_tiles_from_hand 需要返回 bool 表示是否成功移除
                return True
            else:
                # _remove_tiles_from_hand 应该打印了更详细的错误信息
                print(
                    f"错误: _perform_discard_logic 调用 _remove_tiles_from_hand 失败。"
                )
                return False  # 移除失败

        # 不应该到达这里
        return False

    # --- 辅助方法：执行合法的杠动作的状态变更 ---
    def _perform_kan(
        self, player: "PlayerState", action: "Action"
    ) -> bool:  # 接收整个 Action 对象
        """
        执行合法的杠动作的状态变更 (移牌、摸岭上、翻宝牌等)。
        在 apply_action 中验证杠合法性后调用。

        Args:
            player: 执行杠动作的玩家状态对象。
            action: 玩家执行的 KAN 动作对象，应包含 kan_type 和 meld_tiles。

        Returns:
            True 如果杠操作成功完成 (包括成功摸到岭上牌)，
            False 如果因杠尾无牌等原因导致操作未能完全成功 (这通常会引发流局)。
        """
        kan_type = action.kan_type
        # 假设 Action 对象包含了构成杠的精确 Tile 对象列表
        tiles_to_meld = action.meld_tiles

        if kan_type is None or not tiles_to_meld:
            print(
                f"内部错误: 执行 KAN 操作时 Action 对象缺少 kan_type 或 meld_tiles。Action: {action}"
            )
            # 这表明动作生成或传递存在问题
            return False  # 动作信息不完整，杠操作失败

        print(
            f"执行杠操作: 玩家 {player.player_index}, 类型: {kan_type.name}, 构成牌: {tiles_to_meld}"
        )

        # --- 1. 从手牌中移除构成杠的牌 ---
        # 使用辅助方法 _remove_tiles_from_hand 来精确移除 tiles_to_meld 列表中的牌对象
        # _remove_tiles_from_hand 返回 True/False 表示是否成功移除
        if not self._remove_tiles_from_hand(player, tiles_to_meld):
            # 如果移除失败，_remove_tiles_from_hand 内部应该打印错误
            # 这不应该发生如果之前的验证 (RulesEngine) 是正确的
            print(
                f"内部错误: 玩家 {player.player_index} 执行 {kan_type.name} 杠时从手牌移除牌失败 {tiles_to_meld}。"
            )
            return False  # 移除失败，杠操作失败

        # --- 2. 将牌添加到副露 ---
        if kan_type == KanType.CLOSED:
            # 暗杠 (Ankan): 将移除的 4 张牌添加到副露列表
            if len(tiles_to_meld) != 4:
                print(
                    f"内部错误: CLOSED KAN 动作的 meld_tiles 数量不为 4。Tiles: {tiles_to_meld}"
                )
                return False  # 动作信息错误

            player.melds.append(
                {"type": KanType.CLOSED, "tiles": tiles_to_meld}
            )  # 使用移除的牌对象列表构建副露
            print(f"玩家 {player.player_index} 完成暗杠，副露: {player.melds[-1]}")

        elif kan_type == KanType.ADDED:
            # 加杠 (Kakan/Shouminkan): 将移除的 1 张牌添加到已有的碰副露上
            if len(tiles_to_meld) != 1:
                print(
                    f"内部错误: ADDED KAN 动作的 meld_tiles 数量不为 1。Tiles: {tiles_to_meld}"
                )
                return False  # 动作信息错误
            tile_to_add = tiles_to_meld[0]  # 加杠的那张牌对象

            # 找到可以进行加杠的现有碰副露
            target_meld = None
            # 遍历玩家的副露列表，查找匹配的碰牌副露
            for meld in player.melds:
                # 检查是否是碰牌副露 (ActionType.PON) 和牌数量为 3
                # 验证方法应该已经确保这个碰牌副露可以被 tile_to_add 加杠
                if meld["type"] == ActionType.PON and len(meld["tiles"]) == 3:
                    # 进一步检查：确认这个碰牌副露中的牌与要加杠的牌数值相同
                    # 虽然不是严格必须（规则允许同数值不同红宝牌），但通常如此
                    if meld["tiles"][0].value == tile_to_add.value:
                        target_meld = meld
                        break  # 找到第一个匹配的碰副露

            if not target_meld:
                # 这通常不应发生如果 RulesEngine 验证正确
                print(
                    f"内部错误: 玩家 {player.player_index} 尝试加杠 {tile_to_add} 但找不到匹配的碰副露。副露: {player.melds}"
                )
                return False  # 找不到目标副露，加杠失败

            # 更新目标副露：修改类型为 KanType.ADDED，并添加加杠的牌
            target_meld["type"] = KanType.ADDED
            target_meld["tiles"].append(tile_to_add)  # 将移除的那张牌添加到副露
            # Optional: Re-sort the tiles in the meld if you want consistent representation
            # target_meld["tiles"].sort(...)
            print(f"玩家 {player.player_index} 完成加杠，副露更新: {target_meld}")

        # Note: Open Kan (Daiminkan) is handled in _apply_winning_response, which would call a similar logic
        # to remove from discarder's discards, remove from player hand (usually 3 tiles),
        # and add to player melds as KanType.OPEN.

        # --- 3. 从杠尾 (死牌区) 摸一张岭上牌 (rinshan pai) ---
        replacement_tile = (
            self.wall.draw_replacement_tile()
        )  # 需要 Wall.draw_replacement_tile() -> Optional[Tile]

        if replacement_tile is None:
            # 杠尾无牌 -> 杠操作失败，导致流局
            print("杠尾无牌，无法摸取岭上牌！")
            # 返回 False 信号给 apply_action，由 apply_action 处理流局转换
            # 注意：此时牌已经从手牌移出并形成了副露。游戏会进入流局状态，状态会反映这个已完成的杠。
            return False  # 指示操作失败（无法摸到岭上牌）

        # 4. 将岭上牌赋值给 player.drawn_tile
        player.drawn_tile = replacement_tile
        print(f"玩家 {player.player_index} 摸得岭上牌: {replacement_tile}")

        # --- 5. 翻开新的杠宝牌指示牌 (Kan Dora) ---
        # Requires self.wall instance in GameState
        # 在摸取岭上牌后翻开
        self.wall.reveal_new_dora()  # 需要 Wall.reveal_new_dora()
        print("翻开新的杠宝牌指示牌。")

        # --- 6. 清除所有玩家的一发状态 ---
        # 杠牌会打破所有玩家的一发机会
        print("清除所有玩家的一发机会。")
        for p in self.players:  # 遍历 GameState 中的所有玩家
            p.ippatsu_chance = False

        # --- 7. 检查是否因四杠导致流局 (在 apply_action 中完成) ---
        # check_four_kan_abortive_draw 在 _perform_kan 返回成功后，在 apply_action 中调用。

        # 如果所有关键步骤都成功完成 (移除牌，更新副露，摸到岭上牌，翻开宝牌，清除一发)
        print(f"玩家 {player.player_index} 的杠操作成功执行。")
        return True

    # --- 新的辅助方法：验证响应阶段声明的动作 ---
    def _is_response_action_valid(
        self, game_state: "GameState", player_index: int, action: "Action"
    ) -> bool:
        """
        检查玩家在当前 WAITING_FOR_RESPONSE 阶段声明的动作是否合法。
        通过 RulesEngine 生成合法动作列表并检查。
        """
        if game_state.game_phase != GamePhase.WAITING_FOR_RESPONSE:
            print(
                f"错误: 在非响应阶段调用 _is_response_action_valid (阶段: {game_state.game_phase.name})"
            )
            return False  # 只能在响应阶段验证响应动作

        # 调用 RulesEngine 生成当前玩家在当前状态下所有合法的动作列表
        # RulesEngine 的 generate_candidate_actions 方法需要 GameState 实例和玩家索引
        all_valid_actions = self.rules_engine.generate_candidate_actions(
            game_state, player_index
        )

        # 检查玩家声明的动作是否在合法的动作列表中
        # 注意：比较 Action 对象需要确保 Action 类实现了 __eq__ 方法
        if action in all_valid_actions:
            return True
        else:
            print(
                f"错误: 玩家 {player_index} 声明的动作 {action} 在当前状态下不是合法动作。"
            )
            # Debug 辅助，打印合法动作列表
            # print(f"合法的动作列表为: {all_valid_actions}")
            return False

    def apply_action(self, action: Action, player_index: int):
        # todo 很多辅助函数还没有实现 另外 副露注意要修改门清状态哦 不然都可以立直、
        """
        应用玩家执行的动作并更新游戏状态。
        """
        # --- 1. 记录当前正在处理的动作信息 (放在最前面) ---
        print(
            f"玩家 {player_index} 尝试执行动作: {action.type.name} {action} 在阶段 {self.game_phase.name}"
        )
        self.last_action_info = {
            "type": action.type.name,
            "player": player_index,
            "action_obj": action,  # 存储完整的动作对象
            # TODO: 根据动作类型添加更多详情，例如 RON 需要放铳玩家索引
        }
        # ----------------------------------------------------

        # --- 2. 验证当前玩家回合 (通用验证) ---
        # 在响应阶段 (WAITING_FOR_RESPONSE)，current_player_index 表示轮到谁声明响应，这个检查是必要的
        # 在其他阶段，current_player_index 表示轮到谁行动，这个检查也是必要的
        if player_index != self.current_player_index:
            print(
                f"警告: 玩家 {player_index} 在非其回合/响应宣言回合尝试行动 ({self.game_phase.name})"
            )
            # TODO: 返回无效动作信号 / 抛出错误 / 记录无效动作
            return  # 无效行动，终止处理
        # --- 根据当前游戏阶段处理动作 ---

        if self.game_phase == GamePhase.PLAYER_DISCARD:
            # 在这个阶段预期的动作：DISCARD, TSUMO, KAN (Closed/Added), RIICHI, SPECIAL_DRAW
            if action.type == ActionType.DISCARD:
                # --- 处理打牌动作 ---
                player = self.players[player_index]
                tile_to_discard = action.tile

                # 验证并执行打牌移除手牌/摸牌逻辑 (使用新的辅助方法)
                if not self._perform_discard_logic(player, tile_to_discard):
                    print(
                        f"错误: 玩家 {player_index} 打出 {tile_to_discard} 失败 (移除手牌/摸牌区失败)。"
                    )
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 打牌移除失败，动作无效

                # 将打出的牌加入牌河，记录最后打牌信息 (这部分是通用的打牌 aftermath)
                player.discards.append(tile_to_discard)
                self.last_discarded_tile = tile_to_discard
                self.last_discard_player_index = player_index

                # 打牌后通常失去一发机会 (如果已立直)
                if (
                    player.riichi_declared
                ):  # Assumes riichi_declared is set when Riichi action is applied
                    player.ippatsu_chance = False  # Assumes ippatsu_chance is true after Riichi and before discard response

                # TODO: 检查振听 (furiten) 状态 after this discard

                # --- 阶段转换：进入响应阶段 ---
                self.game_phase = GamePhase.WAITING_FOR_RESPONSE
                print(f"玩家 {player_index} 打出 {tile_to_discard}，进入响应阶段。")

                # --- 初始化响应处理状态 ---
                self._response_declarations = {}
                self._responded_to_current_discard = set()
                # 构建需要声明响应的玩家队列，按逆时针顺序
                # _build_response_prompt_queue 需要访问 RulesEngine
                self._responders_to_prompt = self._build_response_prompt_queue(
                    self.last_discarded_tile
                )
                print(f"需要声明响应的玩家队列: {self._responders_to_prompt}")

                if self._responders_to_prompt:
                    self.current_player_index = self._responders_to_prompt[0]
                    print(f"首先轮到玩家 {self.current_player_index} 声明响应。")
                else:
                    print("没有玩家可以响应，直接进入下一摸牌阶段。")
                    self._transition_to_next_draw_phase()
            elif action.type == ActionType.TSUMO:
                # --- 处理 TSUMO (自摸) 动作 ---
                player = self.players[player_index]

                # --- 验证自摸合法性 (使用 RulesEngine) ---
                # 注意：check_win 方法不能修改玩家状态！应传入手牌+摸牌的临时组合

                # 尝试检查手牌本身是否构成国士无双十三面听之类的特殊自摸
                # 这是复杂情况，暂时简化处理：如果没有 drawn_tile 且手牌不构成和牌型，则视为无效
                combined_hand = list(player.hand)  # 创建手牌的副本
                if player.drawn_tile:
                    combined_hand.append(player.drawn_tile)  # 将摸到的牌添加到副本中
                if not self.rules_engine._check_basic_win_shape(
                    combined_hand, player.melds
                ):
                    print("错误: 手牌不构成和牌型，自摸无效。")
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 无效自摸

                # 调用 RulesEngine 检查是否满足和牌条件，win_info 包含和牌详情
                win_info = self.rules_engine._check_basic_win_shape(
                    combined_hand, player.melds
                )  # <--- 修复：传入组合手牌副本

                if win_info:
                    print(f"玩家 {player_index} 宣布 Tsumo! 和牌信息: {win_info}")
                    # --- 执行自摸和牌 (清理摸到的牌) ---
                    # 清除 drawn_tile 槽位 (如果和牌的是 drawn_tile)
                    # win_info.get("winning_tile") 应该提供实际和牌的牌
                    winning_tile_obj = win_info.get("winning_tile")
                    if player.drawn_tile == winning_tile_obj:  # 如果摸到的牌就是和牌牌
                        player.drawn_tile = None
                    # TODO: 如果和牌的是手牌中的牌 (如国士无双十三面自摸手牌中的一张)，则需要从手牌移除
                    # 这个逻辑通常包含在 _apply_winning_response 中处理，因为它涉及最终的手牌状态

                    # --- 更新游戏状态到计分阶段 ---
                    # _transition_to_scoring should handle setting game_phase to HAND_OVER_SCORES etc.
                    # 它也会传递和牌详情给 RulesEngine 进行分数计算
                    self._transition_to_scoring(
                        winner_index=player_index,
                        is_tsumo=True,
                        win_info=win_info,  # 传递和牌详情
                        ron_player_index=None,  # 自摸时没有放铳玩家
                    )
                else:
                    print(
                        f"错误: 玩家 {player_index} 尝试宣布 Tsumo 但手牌不满足和牌条件。"
                    )
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 无效自摸
            # 4. 处理 KAN (杠) 动作 (暗杠或加杠)
            elif action.type == ActionType.KAN:  # <--- 检查 ActionType.KAN
                player = self.players[player_index]
                tile_to_kan = action.tile  # 组成杠的牌 (暗杠是四张之一，加杠是第四张)
                kan_type = action.kan_type  # <--- 获取具体的 KanType 从 action 对象

                # 验证 KanType 是否存在且是 PLAYER_DISCARD 阶段允许的类型
                if kan_type is None or kan_type == KanType.OPEN:
                    print(
                        f"错误: 玩家 {player_index} 在 {self.game_phase.name} 阶段声明了无效的 KAN 类型 {kan_type}。"
                    )
                    # 大明杠 (OPEN) 是响应动作，不在此阶段处理
                    # 没有指定类型也是无效的
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 无效动作

                # --- 验证杠的合法性 (使用 RulesEngine) ---
                can_kan = False
                if kan_type == KanType.CLOSED:
                    # 验证暗杠合法性 (需要玩家手牌中有四张相同的牌)
                    can_kan = self.rules_engine.validate_closed_kan(
                        self, player_index, tile_to_kan
                    )  # 假设验证方法接收 game_state, player_index, tile
                elif kan_type == KanType.ADDED:
                    # 验证加杠合法性 (需要在玩家的碰牌副露上加一张手牌中的牌)
                    can_kan = self.rules_engine.validate_added_kan(
                        self, player_index, tile_to_kan
                    )  # 假设验证方法接收 game_state, player_index, tile

                if can_kan:
                    print(
                        f"玩家 {player_index} 宣布 {kan_type.name} 杠 使用 {tile_to_kan}"
                    )

                    # --- 执行杠操作 ---
                    # _perform_kan 辅助函数处理所有杠的 side effects (移牌, 摸岭上, 翻宝牌等)
                    # 它应该返回是否成功 (例如，岭上牌是否摸到)
                    # 将正确的 KanType 枚举值传递给 _perform_kan
                    kan_successful = self._perform_kan(
                        player, action
                    )  # <--- 传递 KanType 枚举值

                    if not kan_successful:
                        # 例如，杠尾没有牌了，或杠过程中出现其他错误
                        print(f"错误: 执行 {kan_type.name} 失败 (可能无岭上牌?)。")
                        # 杠失败可能导致流局 (如杠尾无牌) 或只是一个无效动作
                        # 如果是杠尾无牌流局，_perform_kan 内部可能会设置流局状态并返回 False
                        # 如果是其他原因失败，可能只返回 False 表示无效
                        # 假设返回 False 表示游戏继续，但此动作无效
                        # 如果失败一定导致流局，_perform_kan 内部应该调用 _transition_to_abortive_draw
                        # 暂时只打印错误，如果 _perform_kan 返回 False，认为动作失败
                        # TODO: 根据 _perform_kan 的返回值和规则细化错误处理
                        return  # 杠失败

                    # --- 状态更新 ---
                    # 杠成功并摸岭上牌后，玩家仍然处于需要行动的状态 (打牌或岭上开花)
                    # 阶段保持 PLAYER_DISCARD，当前玩家不变。
                    self.game_phase = (
                        GamePhase.PLAYER_DISCARD
                    )  # 或者如果需要，可以是一个特定的 KAN_DRAW_DISCARD 阶段
                    # self.current_player_index 保持 player_index (杠牌者)

                    # TODO: 如果规则允许，检查是否因不同玩家开了四杠导致流局
                    # check_four_kan_abortive_draw 需要访问 GameState 状态来计算杠的数量
                    if self.rules_engine.check_four_kan_abortive_draw(
                        self
                    ):  # 传递 game_state
                        print("检测到四杠散了流局。")
                        self._transition_to_abortive_draw("四杠散了 (Four Kan Abort)")
                        return  # 流局，终止当前 apply_action 流程

                else:
                    print(
                        f"错误: 玩家 {player_index} 尝试宣布无效的 {kan_type.name} 杠 使用 {tile_to_kan}。"
                    )
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 无效杠
            # 5. 处理 RIICHI (立直) 动作
            elif action.type == ActionType.RIICHI:
                # --- 处理 RIICHI (立直) 动作 ---
                # 立直是在打牌 *之前* 宣布的。这个动作包含了要打出的牌。
                player = self.players[player_index]
                tile_to_discard_for_riichi = action.tile  # 立直时要打出的牌

                # --- 验证立直合法性 (使用 RulesEngine) ---
                # validate_riichi 应该检查玩家是否门清、听牌、点数足够等，并可能需要打出的牌来检查振听
                #!! 这里的validate ricchi还没有实现
                can_declare_riichi = self.rules_engine.validate_riichi(
                    self, player_index, tile_to_discard_for_riichi
                )  # Assuming validate_riichi takes game_state, player_index, tile

                if can_declare_riichi:
                    print(
                        f"玩家 {player_index} 宣布 Riichi，准备打出 {tile_to_discard_for_riichi}"
                    )

                    # --- 执行立直 (状态更新) ---
                    player.riichi_declared = True  # 标记玩家已立直
                    # player.riichi_declared_this_turn = True # 标记本轮宣布立直 (用于管理立即打牌失去一发)，可以在 RulesEngine 中管理或单独处理
                    # self.current_round_turn needs to be tracked in GameState
                    # player.riichi_turn = self.current_round_turn # 记录立直的巡目
                    player.score -= 1000  # 扣除立直棒点数
                    self.riichi_sticks += 1  # 场上增加一支立直棒

                    # 获得一发机会 (直到下一玩家副露或自己摸牌)
                    player.ippatsu_chance = True

                    # --- 执行与立直相关的打牌 (使用辅助方法) ---
                    if not self._perform_discard_logic(
                        player, tile_to_discard_for_riichi
                    ):
                        print(
                            f"错误: 立直打出 {tile_to_discard_for_riichi} 失败 (应在验证中捕获)。"
                        )
                        # TODO: 严重错误状态，应该在验证时就避免
                        return  # 打牌失败

                    # 将打出的牌加入牌河，记录最后打牌信息 (通用的打牌 aftermath)
                    player.discards.append(tile_to_discard_for_riichi)
                    self.last_discarded_tile = tile_to_discard_for_riichi
                    self.last_discard_player_index = player_index

                    # 在宣布立直的这次打牌后立即失去一发机会
                    # player.ippatsu_chance = False # 如上所述，可能在后续响应处理或摸牌时清除

                    # TODO: 检查振听状态 after this discard

                    # TODO: 翻开新的宝牌指示牌 (如果立直时是牌墙上第 7 墩之前) - 这通常在 apply_action 完成后，由 MahjongEnv 或 RulesEngine 触发

                    # --- 阶段转换：进入响应阶段 ---
                    self.game_phase = GamePhase.WAITING_FOR_RESPONSE
                    print(
                        f"玩家 {player_index} 立直打出 {tile_to_discard_for_riichi}，进入响应阶段。"
                    )

                    # --- 初始化响应处理状态 (同普通打牌后) ---
                    self._response_declarations = {}
                    self._responded_to_current_discard = set()
                    self._responders_to_prompt = self._build_response_prompt_queue(
                        self.last_discarded_tile
                    )
                    print(f"需要声明响应的玩家队列: {self._responders_to_prompt}")

                    if self._responders_to_prompt:
                        self.current_player_index = self._responders_to_prompt[0]
                        print(f"首先轮到玩家 {self.current_player_index} 声明响应。")
                    else:
                        print(
                            "没有玩家可以响应，立直后无人响应，直接进入下一摸牌阶段。"
                        )
                        self._transition_to_next_draw_phase()

                else:
                    print(f"错误: 玩家 {player_index} 尝试宣布 Riichi 但不满足条件。")
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 无效立直

            elif action.type == ActionType.SPECIAL_DRAW:
                # --- 处理 SPECIAL_DRAW (特殊流局) 动作 (例如九种九牌 Kyuushu Kyuuhai) ---
                player = self.players[player_index]

                # 验证特殊流局合法性 (通常只在玩家的*第一巡*且没有任何副露/打牌干扰时允许)
                # validate_special_draw 应该返回流局类型字符串或 None/False
                special_draw_reason = self.rules_engine.validate_special_draw(
                    self, player_index
                )

                if special_draw_reason:
                    print(f"玩家 {player_index} 宣布特殊流局: {special_draw_reason}")
                    # --- 执行 ---
                    # 转换到流局状态并处理流局结算
                    self._transition_to_abortive_draw(special_draw_reason)
                else:
                    print(f"错误: 玩家 {player_index} 尝试宣布特殊流局但不满足条件。")
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 无效的特殊流局

            # 7. 处理此阶段其他意外的动作类型
            else:
                print(
                    f"警告: 在 {self.game_phase.name} 阶段收到意外的动作类型 {action.type.name}。"
                )
                # TODO: 返回无效动作信号 / 抛出错误
                return  # 意外的动作类型

        elif self.game_phase == GamePhase.WAITING_FOR_RESPONSE:
            # 在这个阶段预期的动作：PASS, CHI, PON, KAN (Open), RON
            # 这些是玩家对最后一张弃牌的响应声明

            # 玩家轮次检查 (在通用验证里处理了 player_index != self.current_player_index)

            # --- 验证声明的响应动作是否合法 ---
            # 使用 RulesEngine 检查这个玩家声明的这个动作是否真的合法
            if not self._is_response_action_valid(
                self, player_index, action
            ):  # 使用新的辅助方法进行验证
                print(f"错误: 玩家 {player_index} 声明的动作 {action.type.name} 无效。")
                # TODO: 返回无效动作信号 / 抛出错误
                return  # 无效的响应声明

            # --- 记录玩家的响应声明 ---
            # 如果动作合法，则记录
            self._response_declarations[player_index] = action
            self._responded_to_current_discard.add(player_index)  # 标记玩家已声明过
            print(f"玩家 {player_index} 声明了动作: {action.type.name}")

            # --- 从需要声明的队列中移除当前玩家 ---
            # 假设当前玩家总是队列的第一个
            if (
                self._responders_to_prompt
                and self._responders_to_prompt[0] == player_index
            ):
                self._responders_to_prompt.pop(0)
            else:
                # 这表示队列管理逻辑有误 - 当前玩家不在队列前端
                print(
                    f"内部错误: 玩家 {player_index} 不在响应声明队列的前端！队列: {self._responders_to_prompt}"
                )
                # TODO: Handle internal error - maybe terminate game or log critical error
                pass  # 继续处理，但状态可能已混乱

            # --- 检查是否所有需要声明的玩家都已声明 ---
            if not self._responders_to_prompt:
                # 所有潜在响应者都已声明了他们的动作 (PASS 或其他)
                print("所有需要声明的玩家已完成，开始解决响应优先级。")
                winning_action, winning_player_index = (
                    self._resolve_response_priorities()
                )  # 解决优先级

                if winning_action:
                    print(
                        f"响应解决结果: 玩家 {winning_player_index} 的 {winning_action.type.name} 动作获胜。"
                    )
                    # --- 应用获胜的响应动作并进行阶段转换 ---
                    # _apply_winning_response 需要处理 Ron, Pon, Kan(Open), Chi 的具体执行和状态转换
                    # 它也会清理响应阶段的状态
                    self._apply_winning_response(winning_action, winning_player_index)
                else:
                    # 没有非 PASS 动作声明，或者非 PASS 动作没有获胜 (所有人都 PASS 或优先级解决后无人胜出)
                    print("所有声明均为 PASS 或没有获胜的高优先级动作。")
                    # --- 过渡到下一摸牌阶段 ---
                    # 清理响应阶段状态，并进入下一轮摸牌
                    self._transition_to_next_draw_phase()

            else:
                # 还有其他玩家需要声明响应，将当前玩家切换到队列中的下一个玩家
                self.current_player_index = self._responders_to_prompt[0]
                print(f"转到下一个需要声明响应的玩家: {self.current_player_index}")

        # TODO: 处理其他阶段 PLAYER_DRAW, HAND_OVER_SCORES, ROUND_END, GAME_OVER 等的动作
        # 例如，在 HAND_OVER_SCORES 阶段，可能只有特殊动作如 READY_FOR_NEXT_ROUND 合法

        # 7. 处理所有其他意外的阶段/动作组合
        else:
            print(
                f"警告: 在意外的阶段 ({self.game_phase.name}) 收到动作类型 {action.type.name}。"
            )
            # TODO: 返回无效动作信号 / 抛出错误
            return  # 意外的阶段/动作组合
        # TODO: 处理其他阶段 PLAYER_DRAW, ACTION_PROCESSING, HAND_OVER_SCORES etc.
        # 在非预期阶段收到的动作通常是无效的，需要处理 (忽略，警告，或错误)
        # apply_action 完成后，env.step 方法会调用 _get_info() 和 _get_observation()
        # 它们将使用更新后的 game_state (包括新的 phase 和 current_player_index)
        # 来生成下一个 observation 和可行动作列表。

    # --- 辅助方法 (在 GameState 类内部实现) ---
    def _transition_to_scoring(
        self,
        winner_index: int,
        is_tsumo: bool,
        win_info: Dict[str, Any],
        ron_player_index: Optional[int] = None,
    ):
        """
        过渡游戏阶段到结算，存储本局和牌的关键信息。
        这个方法由 apply_action 调用，用于在和牌发生时更新状态并触发结算流程。
        实际的分数计算和应用在 MahjongEnv.step 中进行。

        Args:
            winner_index: 和牌玩家索引 (0-3)。
            is_tsumo: 是否为自摸和牌 (True) 或荣和 (False)。
            win_info: RulesEngine.check_win 或 check_ron 返回的详细和牌信息 (包含役、番、符、和牌牌等)。
            ron_player_index: 放铳玩家索引 (仅荣和时提供，自摸为 None)。
        """
        print(
            f"过渡到结算阶段 (HAND_OVER_SCORES)。获胜玩家: {winner_index}, 类型: {'自摸' if is_tsumo else '荣和'}"
        )
        self.game_phase = GamePhase.HAND_OVER_SCORES

        # --- 存储本局结束的关键信息 ---
        # 将 RulesEngine 验证和计算的原始 win_info 存储起来，供 RulesEngine.get_hand_outcome 在 step() 中读取
        # end_type 用于 RulesEngine.get_hand_outcome 识别结束类型
        self._hand_outcome_info_temp = {
            "end_type": "TSUMO" if is_tsumo else "RON",
            "winner_index": winner_index,
            "ron_player_index": ron_player_index,  # 荣和时是放铳者，自摸时是 None
            "is_tsumo": is_tsumo,
            "win_details": win_info,  # 存储从 check_win/check_ron 得到的详细信息 (包括 winning_tile)
            # TODO: 如果 RulesEngine.get_hand_outcome 需要，可以在这里存储当前局的 honba 和 riichi_sticks 数量
            # self.honba 和 self.riichi_sticks 已经在 GameState 中维护
            # 也可以让 get_hand_outcome 直接读取 self.honba 和 self.riichi_sticks
        }

        # --- 清理玩家状态 ---
        winning_player = self.players[winner_index]
        # 假设 win_info 字典中包含了 "winning_tile" 键，其值为实际和牌的 Tile 对象
        winning_tile_obj = win_info.get("winning_tile")

        # 1. 清理玩家摸到的牌槽位
        # 和牌后，玩家摸到的牌（如果存在）要么是和牌牌（自摸），要么已经被打出/杠掉。
        # 直接清空摸牌槽位是安全的。
        for p in self.players:
            p.drawn_tile = None  # 清空所有玩家的摸牌槽位

        # 2. 移除和牌牌 (如果它是从手牌中和的，例如国士无双十三面听自摸手牌中的一张)
        # 标准自摸和牌牌是 drawn_tile，上一步已经清空。
        # 标准荣和牌是被弃牌，不在手牌中。
        # 特殊和牌（如国士无双十三面听的自摸，和牌牌在手牌里）需要从手牌移除。
        # 我们检查和牌牌对象是否存在于当前玩家手牌中。
        if winning_tile_obj is not None and winning_tile_obj in winning_player.hand:
            print(
                f"移除和牌牌 {winning_tile_obj} 从玩家 {winner_index} 手牌 (例如国士无双特殊和牌)。"
            )
            # 使用 _remove_tiles_from_hand 辅助方法移除
            # _remove_tiles_from_hand 返回 True/False 表示成功/失败
            if not self._remove_tiles_from_hand(winning_player, [winning_tile_obj]):
                print(
                    "Internal error: Failed to remove winning tile from hand after Tsumo (special case). State inconsistent."
                )
                # TODO: 处理内部错误，游戏状态可能已损坏

        # 3. 清除所有玩家的一发状态
        # 和牌或副露会打破所有玩家的一发机会
        print("清除所有玩家的一发机会。")
        for p in self.players:
            p.ippatsu_chance = False

        # 4. TODO: 清除其他与本局进程相关的瞬时状态
        # 例如，一些与第一巡相关的标志、四风连打/四杠散了宣告后的状态等。
        # TODO: 立直棒的处理：立直棒通常归和牌者所有。点数的转移在 calculate_hand_scores 中计算。
        # GameState 的 riichi_sticks 数量在 apply_next_hand_state 中根据 determine_next_hand_state 的结果更新。

    # ... (其他方法，如 _remove_tiles_from_hand) ...
    # 确保 _remove_tiles_from_hand 方法已正确实现并返回 bool
    # 确保 PlayerState 类有 drawn_tile, hand, ippatsu_chance, player_index 属性
    # 确保 GamePhase 枚举包含 HAND_OVER_SCORES
    # 确保 Tile 对象是 hashable 和 comparable (value 和 is_red)
    # 确保 win_info 字典结构包括 "winning_tile" 键
    def _build_response_prompt_queue(self, discarded_tile: Tile) -> List[int]:
        """
        根据当前游戏状态和打出的牌，构建需要依次提示声明响应的玩家队列。
        这个队列按逆时针顺位排列。优先级解决在所有声明收集后进行。
        """
        queue = []
        discarder = self.last_discard_player_index
        if discarder is None:
            return queue  # 无弃牌者

        # 检查玩家是否“可能”响应，并按逆时针顺位添加到队列
        start_player = (discarder + 1) % self.num_players  # 从打牌者的下家开始检查
        for i in range(self.num_players - 1):  # 检查其他 3 个玩家
            player_index = (start_player + i) % self.num_players
            player = self.players[player_index]

            # 调用 RulesEngine 判断玩家是否有任何合法的响应动作 (Chi/Pon/Kan/Ron)
            # generate_candidate_actions 已经包含了这些判断，我们可以直接用它
            possible_actions = self.rules_engine.generate_candidate_actions(
                self, player_index
            )  # 需要传入 player_index
            # 过滤掉 PASS 动作，看看是否还有其他响应选项
            response_options = [
                a
                for a in possible_actions
                if a.type
                in {
                    ActionType.CHI,
                    ActionType.PON,
                    ActionType.KAN,
                    ActionType.RON,
                    ActionType.RON,
                }
            ]  # 重复 Ron 是为了确保包含

            if response_options:
                # 如果玩家有任何响应选项 (除了 PASS)，将其添加到需要声明的队列
                queue.append(player_index)

        # 队列现在按逆时针顺位包含所有有潜在响应选项的玩家
        # Ron 的高优先级意味着即使顺位靠后的玩家能 Ron，也可能优先于顺位靠前的 Chi/Pon/Kan
        # 但是在这个迭代声明模型中，我们按顺位询问，优先级在最后解决。
        # 这是一个可以工作的实现，但要注意它不是严格模拟同时宣言和优先级立即解决的过程。

        return queue

    def _resolve_response_priorities(self) -> Tuple[Optional[Action], Optional[int]]:
        """
        根据收集到的响应声明 (_response_declarations)，解决优先级冲突。
        Ron (any player) > Pon/Kan (any player) > Chi (next player)
        同优先级下，离打牌者逆时针方向最近的玩家优先 (即上家 > 对家 > 下家)。
        """
        # print(f"Debug Resolve: Resolving priorities for declarations: {self._response_declarations}")
        winning_action: Optional[Action] = None
        winning_player_index: Optional[int] = None
        winning_priority = -1  # 优先级值越高越优先

        # 定义优先级映射 (值越大优先级越高)
        priority_map = {
            ActionType.RON: 3,
            ActionType.KAN: 2,  # 大明杠声明
            ActionType.PON: 2,
            ActionType.CHI: 1,
            ActionType.PASS: 0,  # PASS 没有优先级，不会“获胜”
        }

        discarder = self.last_discard_player_index
        if discarder is None:
            # 如果没有最后打出的牌，说明不在响应阶段，不应该调用此方法
            print("警告: 在非响应阶段调用 _resolve_response_priorities")
            return None, None

        # --- 按优先级从高到低检查 ---

        # 1. 检查 Ron (最高优先级)
        ron_declarations = {
            idx: action
            for idx, action in self._response_declarations.items()
            if action.type == ActionType.RON
        }
        if ron_declarations:
            # 如果有 Ron 声明，按逆时针顺位离打牌者由近到远检查玩家
            # 逆时针顺序：上家 (discarder - 1), 对家 (discarder - 2), 下家 (discarder - 3)
            for i in range(1, self.num_players):  # i = 1, 2, 3 (对于 4 玩家)
                player_idx_reverse_turn = (discarder - i) % self.num_players

                # 检查这个玩家是否声明了 Ron
                if player_idx_reverse_turn in ron_declarations:
                    # 找到逆时针顺序中第一个声明 Ron 的玩家，他获胜
                    print(
                        f"Debug Resolve: Ron declared by player {player_idx_reverse_turn} wins."
                    )
                    return (
                        ron_declarations[player_idx_reverse_turn],
                        player_idx_reverse_turn,
                    )

        # 2. 检查 Pon/Kan (次高优先级)
        # Pon 和 Kan (大明杠) 优先级相同
        pon_kan_declarations = {
            idx: action
            for idx, action in self._response_declarations.items()
            if action.type in {ActionType.PON, ActionType.KAN}
        }
        if pon_kan_declarations:
            # 如果有 Pon 或 Kan 声明，按逆时针顺位离打牌者由近到远检查玩家
            for i in range(1, self.num_players):  # i = 1, 2, 3
                player_idx_reverse_turn = (discarder - i) % self.num_players

                # 检查这个玩家是否声明了 Pon 或 Kan
                if player_idx_reverse_turn in pon_kan_declarations:
                    # 找到逆时针顺序中第一个声明 Pon 或 Kan 的玩家，他获胜
                    print(
                        f"Debug Resolve: Pon/Kan declared by player {player_idx_reverse_turn} wins."
                    )
                    return (
                        pon_kan_declarations[player_idx_reverse_turn],
                        player_idx_reverse_turn,
                    )

        # 3. 检查 Chi (最低优先级)
        chi_declarations = {
            idx: action
            for idx, action in self._response_declarations.items()
            if action.type == ActionType.CHI
        }
        if chi_declarations:
            # Chi 只可能来自打牌者的下家 (discarder + 1) % N
            next_player_index = (discarder + 1) % self.num_players
            # 检查下家是否声明了 Chi
            if next_player_index in chi_declarations:
                # Chi 没有同优先级冲突 (因为只有下家能 Chi)，如果声明了且没有更高优先级动作，则 Chi 获胜
                print(
                    f"Debug Resolve: Chi declared by player {next_player_index} wins."
                )
                return chi_declarations[next_player_index], next_player_index

        # 如果以上所有优先级动作都没有获胜 (所有声明的动作都是 PASS)
        print("Debug Resolve: No winning non-PASS action declared.")
        return None, None  # 返回 None 表示没有获胜动作

    def _apply_winning_response(
        self, winning_action: Action, winning_player_index: int
    ):
        """
        应用在响应阶段解决优先级后获胜的非 PASS 动作。
        这会导致游戏状态根据该动作彻底转换，并清理响应阶段的状态。
        """
        player = self.players[winning_player_index]
        discarded_tile = self.last_discarded_tile  # 被宣言的牌

        if discarded_tile is None:
            print("内部错误: 尝试应用获胜响应动作但 last_discarded_tile 为 None")
            return  # 错误状态

        print(
            f"玩家 {winning_player_index} 执行获胜响应动作: {winning_action.type.name}"
        )

        # --- 应用获胜动作的具体逻辑 (根据不同的动作类型) ---
        if winning_action.type == ActionType.RON:
            print(f"玩家 {winning_player_index} 荣和！")
            # TODO: 实现荣和的细节：计算番数和得分，更新玩家分数
            # self.calculate_scores_ron(winning_player_index, self.last_discard_player_index, winning_action.winning_tile)
            self.game_phase = GamePhase.HAND_OVER_SCORES  # 转换阶段到计分
            self.current_player_index = winning_player_index  # 设置当前玩家为获胜者

        elif winning_action.type == ActionType.PON:
            print(f"玩家 {winning_player_index} 碰！")
            # 将弃牌加入副露，从手牌移除两张匹配的牌
            player.melds.append(
                {
                    "type": "pon",
                    "tiles": sorted(
                        [discarded_tile, winning_action.tile, winning_action.tile]
                    ),
                }
            )  # 副露通常排序
            self._remove_tiles_from_hand(
                player, [winning_action.tile, winning_action.tile]
            )  # 移除手牌中的两张碰牌 (需要处理赤宝牌和复数相同的普通牌)

            self.game_phase = GamePhase.PLAYER_DISCARD  # 碰牌者摸牌后打牌
            self.current_player_index = winning_player_index  # 轮到碰牌者行动

        elif winning_action.type == ActionType.KAN:  # Assuming Open Kan (Daiminkan)
            kan_type = winning_action.kan_type  # <--- 获取 KanType
            if kan_type == KanType.OPEN:
                # --- 处理大明杠 (Daiminkan) ---
                print(f"玩家 {winning_player_index} 大明杠！")
                tile_to_kan = winning_action.tile  # 被杠的弃牌
                discarder_index = self.last_discard_player_index  # 放杠者索引
                if discarder_index is None:
                    print("内部错误: 大明杠时无法确定放铳者。")
                    # TODO: 处理错误状态
                    return
                print(f"玩家 {winning_player_index} 大明杠！")
                # 将弃牌加入副露，从手牌移除三张匹配的牌
                player.melds.append(
                    {
                        "type": "kan_open",
                        "tiles": sorted(
                            [
                                discarded_tile,
                                winning_action.tile,
                                winning_action.tile,
                                winning_action.tile,
                            ]
                        ),
                    }
                )
                self._remove_tiles_from_hand(
                    player,
                    [winning_action.tile, winning_action.tile, winning_action.tile],
                )  # 移除手牌中的三张杠牌

                # TODO: 翻开新的宝牌指示牌 (需要 Wall 实例)
                rinshan_tile = self.wall.draw_replacement_tile()
                player.drawn_tile = rinshan_tile
                self.wall.reveal_new_dora()
                # 大明杠后通常立刻打牌，或者先检查岭上开花/杠后荣和
                # 简化处理：直接进入打牌阶段，但需要确保摸了岭上牌
                self.game_phase = GamePhase.PLAYER_DISCARD  # 杠牌者摸牌后打牌
                self.current_player_index = winning_player_index
                # 四杠散了应该在打牌后 无响应后结算 不然打牌后荣和并不是流局
                # TODO: 需要在 RulesEngine.generate_candidate_actions 中处理杠后可选项 (岭上开花，杠后打牌)
            elif kan_type in {KanType.CLOSED, KanType.ADDED}:
                #         #     # 暗杠和加杠不应该在响应阶段获胜，除非规则有特殊情况。
                #         #     # 通常优先级解决时，如果玩家声明了暗杠/加杠，但同时有别人声明了荣和，荣和会优先。
                #         #     # 如果没有荣和，暗杠/加杠会直接在 PLAYER_DISCARD 阶段执行，不会进入响应阶段获胜。
                #         #     # 所以这个分支理论上不应该被执行。
                print(f"内部错误: 在响应阶段获胜的 KAN 类型非 OPEN ({kan_type.name})。")
                #         #     # TODO: 处理错误状态
                return
        elif winning_action.type == ActionType.CHI:
            print(f"玩家 {winning_player_index} 吃！")
            # 将弃牌和手牌中的两张吃牌组合成副露
            meld_tiles = sorted([discarded_tile] + list(winning_action.chi_tiles))
            player.melds.append({"type": "chi", "tiles": meld_tiles})
            # 从手牌移除用于吃的两张牌
            self._remove_tiles_from_hand(
                player, list(winning_action.chi_tiles)
            )  # 移除手牌中的两张吃牌

            self.game_phase = GamePhase.PLAYER_DISCARD  # 吃牌者摸牌后打牌
            self.current_player_index = winning_player_index  # 轮到吃牌者行动

        # --- 清理响应阶段的状态 ---
        self.last_discarded_tile = None
        self.last_discard_player_index = None
        self._response_declarations = {}
        self._responders_to_prompt = []
        self._responded_to_current_discard = set()
        # 阶段和当前玩家已经在上面的具体动作处理中设置

    def _transition_to_next_draw_phase(self):
        """
        当响应阶段没有非 PASS 动作获胜时，过渡到下一玩家摸牌阶段。
        """
        print("过渡到下一摸牌阶段。")
        # 清理响应阶段的状态
        # todo 这里清晰last discard 正确吗？
        # self.last_discarded_tile = None
        # self.last_discard_player_index = None
        self._response_declarations = {}
        self._responders_to_prompt = []
        self._responded_to_current_discard = set()

        # 计算下一位摸牌玩家的索引 (打牌者的下家)
        # 原始打牌者索引保存在 self.last_action_info 中处理 DISCARD 时
        original_discarder_index = self.last_action_info.get("player")
        if original_discarder_index is None:
            print("内部错误: 无法确定原始打牌者以过渡到下一阶段。")
            # 回退方案：使用当前玩家的下家 (可能不准确)
            next_drawer_index = (self.current_player_index + 1) % self.num_players
        else:
            next_drawer_index = (original_discarder_index + 1) % self.num_players

        # 你需要在 GameState 中有 Wall 实例的引用，例如 self.wall
        # 并确保 Wall 类有 draw_tile() 和 get_remaining_live_tiles_count() 方法
        if self.wall.get_remaining_live_tiles_count() > 0:
            drawn_tile = self.wall.draw_tile()  # 从牌墙摸牌

            if drawn_tile:
                player = self.players[next_drawer_index]
                player.drawn_tile = drawn_tile  # 将摸到的牌赋给玩家的 drawn_tile 属性

                # TODO: 如果需要记录摸牌动作信息，可以在这里更新 self.last_action_info
                # 例如: self.last_action_info = {"type": "DRAW", "player": next_drawer_index, "tile": drawn_tile}

                # --- 设置游戏阶段为 PLAYER_DISCARD ---
                # 摸牌后直接进入打牌阶段
                self.game_phase = GamePhase.PLAYER_DISCARD
                self.current_player_index = next_drawer_index  # 当前玩家是摸牌者

                print(
                    f"玩家 {self.current_player_index} 摸到 {drawn_tile}，轮到其打牌。"
                )

                # TODO: 摸牌后立即检查自摸 (TSUMO) 或暗杠/加杠 (KAN) 的可能性。
                # RulesEngine 在 PLAYER_DISCARD 阶段生成动作时会包含这些选项。
                # 所以这里只需要设置好阶段，让 RulesEngine 去生成即可。

            else:
                # 从牌墙摸牌返回 None，表示牌墙已空 (理论上 get_remaining_live_tiles_count 应该先判断到)
                print("警告: 尝试摸牌但 Wall.draw_tile 返回 None。")
                # 转入流局处理
                self._handle_exhaustive_draw()  # Helper method for exhaustive draw

        else:
            # 牌墙已空，无法摸牌，进入流局
            print("牌墙已空，无法摸牌，流局！")
            self._handle_exhaustive_draw()  # Helper method for exhaustive draw

    # TODO: 添加一个辅助方法 _handle_exhaustive_draw 来处理牌墙摸完后的流局逻辑
    def _handle_exhaustive_draw(self):
        """处理牌墙摸完后的流局情况。"""
        print("牌墙摸完，流局！")
        self.game_phase = GamePhase.HAND_OVER_SCORES  # 或者一个专门表示流局的阶段
        self.current_player_index = -1  # 没有当前行动玩家
        # TODO: 实现流局时的得分计算和状态清理

    def _remove_tiles_from_hand(
        self, player: "PlayerState", tiles_to_remove: List["Tile"]
    ) -> bool:
        """
        尝试从玩家手牌中移除指定的牌列表。要求精确匹配 Tile 对象 (通过 __eq__ 判断 value 和 is_red)。
        返回 True 表示成功移除所有牌并更新玩家手牌，False 表示至少有一张牌无法找到并移除，不修改玩家手牌。
        """
        if not tiles_to_remove:
            # 如果列表为空，认为成功移除（什么都没做）
            return True

        # 创建手牌的副本进行操作
        temp_hand = list(player.hand)

        # 使用 Counter 来方便地检查手牌是否包含足够数量的特定牌（包括赤宝牌标识）
        # 需要确保 Tile 类正确实现了 __eq__ 和 __hash__ 方法，通常 dataclass(frozen=True) 会自动生成。
        hand_counts = Counter(temp_hand)
        tiles_needed_to_remove_counts = Counter(tiles_to_remove)

        # --- 检查手牌是否足够 ---
        # 遍历需要移除的每种特定牌及其数量
        for tile, count_needed in tiles_needed_to_remove_counts.items():
            # 检查手牌中这种特定牌的数量是否小于需要的数量
            if hand_counts[tile] < count_needed:
                print(
                    f"错误: 玩家 {player.player_index} 手牌不足以移除牌 {tile} (需要 {count_needed}, 手牌只有 {hand_counts[tile]})。"
                )
                # 打印当前手牌状态以供 Debug
                print(f"玩家 {player.player_index} 当前手牌: {player.hand}")
                return False  # 手牌不足，移除失败，立即返回 False

        # --- 执行移除操作 ---
        # 如果手牌足够，则从手牌副本中移除这些牌
        # 更安全且高效的方式是构建一个新的列表，只包含不需要移除的牌
        new_hand = []
        # 使用一个临时的 Counter 来追踪我们还需要移除的每种牌的数量
        temp_tiles_to_remove_counts = Counter(tiles_needed_to_remove_counts)

        for tile in temp_hand:
            # 如果当前遍历到的牌在需要移除的列表中，并且我们还需要移除这种牌
            if temp_tiles_to_remove_counts[tile] > 0:
                temp_tiles_to_remove_counts[
                    tile
                ] -= 1  # 则从需要移除的数量中减一（即“移除了”这张牌）
            else:
                new_hand.append(tile)  # 否则，这张牌保留在新手牌中

        # 经过循环，new_hand 应该就是移除指定牌后的手牌列表了
        # 所有在 tiles_to_remove 中的牌都应该被“移除”了 temp_tiles_to_remove_counts 应该都归零了

        # --- 更新玩家手牌 ---
        player.hand = new_hand

        # print(f"玩家 {player.player_index} 成功移除了牌 {tiles_to_remove}。") # Debug 辅助
        return True  # 成功移除所有指定的牌

    # TODO: 添加计算得分的方法 calculate_scores_ron 等

    # --- Getter 方法 ---
    def get_player_state(self, player_index: int) -> Optional[PlayerState]:
        """获取指定玩家的状态对象"""
        if 0 <= player_index < self.num_players:
            return self.players[player_index]
        return None

    # --- 更新分数和推进游戏的方法 (由环境调用) ---
    def update_scores(self, score_changes: Dict[int, int]):
        """根据计算结果更新玩家分数"""
        print(f"更新分数: {score_changes}")
        for player_index, change in score_changes.items():
            if 0 <= player_index < self.num_players:
                self.players[player_index].score += change
        print(f"更新后分数: {[(p.player_index, p.score) for p in self.players]}")
        # 可以在此检查是否有人被飞

    def apply_next_hand_state(self, next_hand_state_info: Dict[str, Any]):
        """
        根据 RulesEngine 计算的下一局状态信息更新 GameState 的相关属性。
        由环境调用。
        """
        print(f"应用下一局状态: {next_hand_state_info}")
        self.dealer_index = next_hand_state_info["next_dealer_index"]
        self.round_wind = next_hand_state_info["next_round_wind"]
        self.round_number = next_hand_state_info["next_round_number"]
        self.honba = next_hand_state_info["next_honba"]
        self.riichi_sticks = next_hand_state_info["next_riichi_sticks"]

        # 设置游戏阶段，表示本局结束，为下一局的 reset 做准备
        # 这个阶段标志应该被 env.reset() 检查到，从而触发新局的牌局设置
        self.game_phase = GamePhase.HAND_OVER_SCORES  # 例如，标记本局已结算完毕

    def get_info(self) -> Dict[str, Any]:
        """获取当前游戏状态的部分信息 (用于调试或记录)"""
        # 可以选择性地返回信息，避免过于庞大
        return {
            "round": f"{['东','南','西','北'][self.round_wind]}{self.round_number}",
            "honba": self.honba,
            "riichi_sticks": self.riichi_sticks,
            "dealer": self.dealer_index,
            "current_player": self.current_player_index,  # 注意这个是由 env 控制的
            "phase": self.game_phase.name,  # 这个也是由 env 控制更新的
            "scores": [p.score for p in self.players],
            "live_tiles_left": self.wall.get_remaining_live_tiles_count(),
            "dora_indicators": [
                str(t) for t in self.wall.dora_indicators
            ],  # 转为字符串方便查看
            "last_discard": (
                str(self.last_discarded_tile) if self.last_discarded_tile else None
            ),
            # 可以添加更多调试信息...
            # "player_hands": [[str(t) for t in p.hand] for p in self.players], # 可能非常庞大
        }


from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class WinDetails:
    """存储一次和牌的详细分析结果"""

    is_valid_win: bool = False  # 是否是规则上允许的和牌 (例如，不是振听荣和)
    winning_tile: Optional["Tile"] = None  # 和牌的那张牌对象
    is_tsumo: bool = False  # 是否是自摸
    yaku: List[str] = field(
        default_factory=list
    )  # 构成和牌的所有役种名称列表 (例如 ["Riichi", "Tsumo", "Pinfu"])
    han: int = 0  # 总番数 (不含宝牌)
    fu: int = 0  # 符数
    is_yakuman: bool = False  # 是否是役满
    yakuman_list: List[str] = field(
        default_factory=list
    )  # 役满名称列表 (例如 ["Kokushi Musou"])
    dora_count: int = 0  # 宝牌数 (Dora + Red Dora + Ura Dora)
    # TODO: 可能需要更多细节，例如宝牌指示牌、里宝牌指示牌、具体宝牌列表等

    # 可能需要根据游戏规则添加其他标志，例如：
    # is_menzen: bool = False # 是否门前清
    # is_ippatsu: bool = False # 是否一发
    # is_haitei: bool = False # 是否海底捞月
    # is_houtei: bool = False # 是否河底捞鱼
    # is_rinshan: bool = False # 是否岭上开花
    # is_chankan: bool = False # 是否抢杠
    # ...
