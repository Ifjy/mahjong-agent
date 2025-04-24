import random
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple  # 引入类型提示

# --- 从 actions.py 导入我们定义好的类 ---
# 假设 actions.py 与 game_state.py 在同一目录下或已正确配置路径
# 如果不在同一目录，需要调整 import 路径，例如 from src.env.core.actions import ...
from .actions import Action, ActionType, Tile, KanType


# (我们也可以在这里定义 Meld 类)
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

    def __init__(self, player_id: int, initial_score: int):
        self.player_id: int = player_id  # 玩家唯一标识 (0-3)
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


class GameState:
    """
    表示日本麻将游戏的完整状态。
    存储所有必要信息，并通过 apply_action 应用状态变更。
    不包含复杂的控制流或规则校验逻辑。
    """

    def __init__(self, config: Optional[Dict] = None):
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
        self.wall: Wall = Wall(self.config.get("game_rules"))

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

    def apply_action(self, action: Action, player_index: int) -> Dict[str, Any]:
        """
        将一个 *已验证* 的动作应用到游戏状态。
        只负责修改状态，不包含控制流或复杂校验。

        Args:
            action (Action): 来自 actions.py 的 Action 对象。
            player_index (int): 执行该动作的玩家索引。

        Returns:
            Dict[str, Any]: 描述动作执行结果的信息字典 (供环境使用)。
        """
        action_type = action.type
        player = self.players[player_index]
        action_result = {
            "type": action_type.name,
            "player": player_index,
        }  # 基本结果信息

        # print(f"应用动作: {action} by 玩家 {player_index}") # 调试

        # --- 根据动作类型修改状态 ---
        if action_type == ActionType.DISCARD:
            tile_to_discard = action.tile
            assert tile_to_discard is not None, "DISCARD 动作缺少 tile 参数"

            if player.drawn_tile == tile_to_discard:
                # 打出的是刚摸的牌
                player.drawn_tile = None
            elif tile_to_discard in player.hand:
                # 打出的是手牌中的一张
                player.hand.remove(tile_to_discard)
                # 如果之前有摸牌，现在正式加入手牌
                if player.drawn_tile:
                    player.hand.append(player.drawn_tile)
                    player.hand.sort()
                    player.drawn_tile = None
            else:
                # 理论上不应发生，因为动作应已验证
                raise ValueError(
                    f"玩家 {player_index} 尝试打出不在手中的牌: {tile_to_discard}"
                )

            player.discards.append(tile_to_discard)
            self.last_discarded_tile = tile_to_discard
            self.last_discard_player_index = player_index
            player.ippatsu_chance = False  # 打牌后失去一发机会
            self.game_phase = GamePhase.WAITING_FOR_RESPONSE  # 设置状态等待响应
            self.current_player_index = (player_index + 1) % self.num_players
            action_result.update({"tile": tile_to_discard})
            # print(f"玩家 {player_index} 打出 {tile_to_discard}, 手牌: {[str(t) for t in player.hand]}") # 调试
            self.game_phase = GamePhase.WAITING_FOR_RESPONSE
            # 在打牌后初始化响应队列 (这里简化，实际需要按Ron/Pon/Kan/Chi优先级构建)
            # 一个最简化的队列可能是打牌者下家、下下家、下下下家
            discarder = player_index
            self._response_queue = []
            for i in range(1, self.num_players):
                responder_index = (discarder + i) % self.num_players
                # 实际中需要检查该玩家当前状态下是否有合法的响应动作(Chi/Pon/Kan/Ron)
                # 如果 RulesEngine 有 is_response_possible(game_state, player_index, last_discarded_tile) 这样的方法会很有用
                # 这里假设所有非打牌者都需要被检查
                self._response_queue.append(responder_index)

            # 将当前玩家设置为响应队列的第一个玩家
            if self._response_queue:
                self.current_player_index = self._response_queue[0]
                print(f"进入响应阶段，首先检查玩家 {self.current_player_index}")
            else:
                # 如果响应队列为空 (例如只有1个玩家，或者规则不允许任何人响应)，直接进入下一摸牌阶段
                print("没有玩家需要响应，直接进入下一摸牌阶段")
                self.game_phase = GamePhase.PLAYER_DRAW  # 或HAND_OVER
                self.current_player_index = (
                    discarder + 1
                ) % self.num_players  # 下一位玩家摸牌
                self.last_discarded_tile = None  # 清理弃牌信息
                self.last_discard_player_index = None

        elif action_type == ActionType.RIICHI:
            discard_tile = action.riichi_discard
            assert discard_tile is not None, "RIICHI 动作缺少 riichi_discard 参数"

            # 1. 应用立直状态
            player.riichi_declared = True
            player.riichi_turn = self.turn_number  # 记录立直巡目
            player.ippatsu_chance = True  # 立直即获得一发机会
            player.score -= 1000  # 扣除供托
            self.riichi_sticks += 1

            # 2. 执行伴随的打牌动作 (逻辑同 DISCARD)
            if player.drawn_tile == discard_tile:
                player.drawn_tile = None
            elif discard_tile in player.hand:
                player.hand.remove(discard_tile)
                if player.drawn_tile:  # 如果有摸牌，加入手牌
                    player.hand.append(player.drawn_tile)
                    player.hand.sort()
                    player.drawn_tile = None
            else:
                raise ValueError(
                    f"玩家 {player_index} 立直时尝试打出不在手中的牌: {discard_tile}"
                )

            player.discards.append(discard_tile)
            self.last_discarded_tile = discard_tile
            self.last_discard_player_index = player_index
            self.game_phase = GamePhase.WAITING_FOR_RESPONSE  # 打牌后等待响应
            action_result.update(
                {"riichi_discard": discard_tile, "sticks": self.riichi_sticks}
            )
            print(
                f"玩家 {player_index} 立直宣言，打出 {discard_tile}。供托: {self.riichi_sticks}"
            )

        elif action_type == ActionType.CHI:
            chi_tiles_hand = action.chi_tiles  # 玩家手里的两张牌
            called_tile = self.last_discarded_tile  # 被吃的牌
            assert (
                chi_tiles_hand is not None and called_tile is not None
            ), "CHI 动作参数不完整"

            # 1. 从手牌移除
            try:
                player.hand.remove(chi_tiles_hand[0])
                player.hand.remove(chi_tiles_hand[1])
            except ValueError:
                raise ValueError(
                    f"玩家 {player_index} 吃牌时手牌 {player.hand} 中缺少 {chi_tiles_hand}"
                )

            # 2. 形成 Meld 对象 (包含三张牌，按顺序)
            meld_tiles = tuple(sorted(chi_tiles_hand + (called_tile,)))
            meld = Meld(
                type=ActionType.CHI,
                tiles=meld_tiles,
                from_player=self.last_discard_player_index,
                called_tile=called_tile,
            )
            player.melds.append(meld)
            player.is_menzen = False  # 破坏门清
            player.ippatsu_chance = False  # 鸣牌后失去一发

            # 3. 清除上一张弃牌信息 (因为它被鸣了)
            # TBD: 弃牌堆是否要标记被鸣？通常不需要显式移除
            self.last_discarded_tile = None
            # self.last_discard_player_index = -1 # 保持索引可能有用？

            # 4. 鸣牌后轮到鸣牌者打牌
            self.game_phase = GamePhase.PLAYER_DISCARD  # 等待鸣牌者打牌
            action_result.update({"meld": meld})
            print(
                f"玩家 {player_index} 吃 {called_tile} 使用 {chi_tiles_hand}，形成 {meld_tiles}"
            )

        elif action_type == ActionType.PON:
            pon_tile_type = action.tile  # 碰的牌 (类型)
            called_tile = self.last_discarded_tile  # 被碰的牌 (实例)
            assert (
                pon_tile_type is not None and called_tile is not None
            ), "PON 动作参数不完整"
            assert pon_tile_type.value == called_tile.value, "碰的牌类型与弃牌不符"

            # 1. 从手牌移除两张同种牌
            count = 0
            hand_copy = list(player.hand)  # 复制列表以安全移除
            removed_tiles = []
            for tile in hand_copy:
                if tile.value == pon_tile_type.value and count < 2:
                    player.hand.remove(tile)  # 从原列表移除
                    removed_tiles.append(tile)
                    count += 1
            if count != 2:
                raise ValueError(
                    f"玩家 {player_index} 碰牌 ({pon_tile_type}) 时手牌中不足两张"
                )

            # 2. 形成 Meld 对象
            meld_tiles = tuple(sorted(removed_tiles + [called_tile]))  # 三张牌
            meld = Meld(
                type=ActionType.PON,
                tiles=meld_tiles,
                from_player=self.last_discard_player_index,
                called_tile=called_tile,
            )
            player.melds.append(meld)
            player.is_menzen = False
            player.ippatsu_chance = False

            # 3. 清除弃牌信息
            self.last_discarded_tile = None

            # 4. 轮到鸣牌者打牌
            self.game_phase = GamePhase.PLAYER_DISCARD
            action_result.update({"meld": meld})
            print(
                f"玩家 {player_index} 碰 {called_tile} 使用 {removed_tiles}，形成 {meld_tiles}"
            )

        elif action_type == ActionType.KAN:
            kan_tile = action.tile  # 杠的牌 (类型或实例)
            kan_type = action.kan_type
            assert kan_tile is not None and kan_type is not None, "KAN 动作参数不完整"

            meld_tiles = []
            from_player = -1  # 默认为自己 (暗杠/加杠)
            called_tile_instance = None

            if kan_type == KanType.CLOSED:  # 暗杠
                # 从手牌移除四张
                count = 0
                hand_copy = list(player.hand)
                removed_tiles = []
                for tile in hand_copy:
                    if tile.value == kan_tile.value and count < 4:
                        player.hand.remove(tile)
                        removed_tiles.append(tile)
                        count += 1
                if count != 4:
                    raise ValueError(
                        f"玩家 {player_index} 暗杠 ({kan_tile}) 时手牌不足四张"
                    )
                # 如果有摸牌，需将其加入手牌
                if player.drawn_tile:
                    player.hand.append(player.drawn_tile)
                    player.hand.sort()
                    player.drawn_tile = None

                meld_tiles = tuple(sorted(removed_tiles))
                # player.is_menzen 保持不变
                from_player = player_index  # 标记为来自自己

            elif kan_type == KanType.ADDED:  # 加杠
                # 找到已有的碰副露
                existing_pon_index = -1
                for i, meld in enumerate(player.melds):
                    if (
                        meld.type == ActionType.PON
                        and meld.tiles[0].value == kan_tile.value
                    ):
                        existing_pon_index = i
                        break
                if existing_pon_index == -1:
                    raise ValueError(
                        f"玩家 {player_index} 尝试加杠 ({kan_tile}) 但没有找到对应的碰"
                    )

                # 从手牌或摸牌中移除第四张
                if player.drawn_tile and player.drawn_tile.value == kan_tile.value:
                    fourth_tile = player.drawn_tile
                    player.drawn_tile = None
                elif kan_tile in player.hand:
                    fourth_tile = kan_tile
                    player.hand.remove(kan_tile)
                    # 如果有摸牌，加入手牌
                    if player.drawn_tile:
                        player.hand.append(player.drawn_tile)
                        player.hand.sort()
                        player.drawn_tile = None
                else:
                    raise ValueError(
                        f"玩家 {player_index} 加杠 ({kan_tile}) 时缺少第四张牌"
                    )

                # 更新副露
                old_meld = player.melds.pop(existing_pon_index)
                meld_tiles = tuple(sorted(old_meld.tiles + (fourth_tile,)))
                from_player = old_meld.from_player  # 保持来源
                called_tile_instance = old_meld.called_tile
                # player.is_menzen 已是 False

            elif kan_type == KanType.OPEN:  # 大明杠
                called_tile_instance = self.last_discarded_tile
                assert (
                    called_tile_instance is not None
                    and called_tile_instance.value == kan_tile.value
                ), "大明杠的牌与弃牌不符"

                # 从手牌移除三张
                count = 0
                hand_copy = list(player.hand)
                removed_tiles = []
                for tile in hand_copy:
                    if tile.value == kan_tile.value and count < 3:
                        player.hand.remove(tile)
                        removed_tiles.append(tile)
                        count += 1
                if count != 3:
                    raise ValueError(
                        f"玩家 {player_index} 明杠 ({kan_tile}) 时手牌不足三张"
                    )

                meld_tiles = tuple(sorted(removed_tiles + [called_tile_instance]))
                player.is_menzen = False
                from_player = self.last_discard_player_index
                # 清除弃牌信息
                self.last_discarded_tile = None

            # 添加杠的 Meld
            meld = Meld(
                type=ActionType.KAN,
                tiles=meld_tiles,
                from_player=from_player,
                called_tile=called_tile_instance,
            )  # KanType 信息在 action 对象里，Meld 里只存 ActionType.KAN
            player.melds.append(meld)
            player.ippatsu_chance = False  # 杠后失去一发

            # 杠后摸岭上牌 & 开新宝牌
            replacement_tile = self.wall.draw_replacement_tile()
            new_dora_indicator = (
                self.wall.reveal_new_dora()
            )  # 即使没摸到牌也要尝试开宝牌

            if replacement_tile:
                player.drawn_tile = replacement_tile
                self.game_phase = GamePhase.PLAYER_DISCARD  # 杠完摸牌后需要打牌
                action_result.update({"replacement_drawn": replacement_tile})
            else:
                # 没有岭上牌可摸 (例如四杠散了前?), 行为可能依赖具体规则
                # 假设直接进入打牌阶段 (如果手牌足够)
                self.game_phase = GamePhase.PLAYER_DISCARD
                action_result.update({"replacement_drawn": None})

            action_result.update({"meld": meld, "new_dora": new_dora_indicator})
            print(
                f"玩家 {player_index} {kan_type.name} 使用 {meld_tiles}, 摸岭上: {replacement_tile}, 新宝牌指示: {new_dora_indicator}"
            )

        elif action_type == ActionType.TSUMO:
            # 自摸和牌
            self._hand_over_flag = True
            self.game_phase = GamePhase.HAND_OVER_SCORES
            winning_tile = (
                action.winning_tile or player.drawn_tile
            )  # 优先用action里的，否则用摸的牌
            action_result.update({"winning_tile": winning_tile, "hand_over": True})
            print(f"玩家 {player_index} 自摸和牌！和牌张: {winning_tile}")

        elif action_type == ActionType.RON:
            # 荣和
            self._hand_over_flag = True
            self.game_phase = GamePhase.HAND_OVER_SCORES
            winning_tile = (
                action.winning_tile or self.last_discarded_tile
            )  # 优先用action里的
            loser_index = self.last_discard_player_index
            action_result.update(
                {"winning_tile": winning_tile, "loser": loser_index, "hand_over": True}
            )
            print(
                f"玩家 {player_index} 荣和 玩家 {loser_index} 的弃牌 {winning_tile}！"
            )

        elif action_type == ActionType.PASS:
            if self.game_phase == GamePhase.WAITING_FOR_RESPONSE:
                print(f"玩家 {player_index} 在响应阶段 PASS 了。")
                # 从响应队列中移除当前玩家 (假设当前玩家就是 response_queue 的第一个)
                if self._response_queue and self._response_queue[0] == player_index:
                    self._response_queue.pop(0)  # 移除当前玩家

                # 检查是否还有需要响应的玩家
                if self._response_queue:
                    # 轮到响应队列中的下一个玩家
                    self.current_player_index = self._response_queue[0]
                    print(f"转到下一个潜在响应者: 玩家 {self.current_player_index}")
                else:
                    # 所有需要响应的玩家都已处理完毕 (要么 PASS 了，要么执行了动作并中断了响应流程)
                    print("所有相关玩家都已响应（PASS 或其他动作），响应阶段结束。")
                    # 响应阶段结束，进入下一摸牌阶段 (或流局)
                    self.game_phase = GamePhase.PLAYER_DRAW  # 或HAND_OVER
                    self.current_player_index = (
                        self.last_discard_player_index + 1
                    ) % self.num_players  # 打牌者的下家摸牌
                    print(f"下一玩家 {self.current_player_index} 摸牌。")
                    # 清理关于最后弃牌的临时状态
                    self.last_discarded_tile = None
                    self.last_discard_player_index = None
                    # 响应队列也已清空
            else:
                print(
                    f"警告: 在非响应阶段 ({self.game_phase.name}) 收到玩家 {player_index} 的 PASS 动作。忽略。"
                )
                # 在非响应阶段收到 PASS，通常是逻辑错误，可以忽略或抛出异常
                pass  # 忽略无效的PASS

        elif action_type == ActionType.SPECIAL_DRAW:
            # 特殊流局 (例如 九种九牌)
            self._hand_over_flag = True
            self.game_phase = GamePhase.HAND_OVER_SCORES
            action_result.update({"hand_over": True, "draw_type": "special"})
            print(f"玩家 {player_index} 宣告特殊流局。")

        else:
            print(f"警告: apply_action 收到未知动作类型: {action_type}")
            action_result.update({"error": f"Unknown action type: {action_type}"})

        # 记录最后应用的动作信息
        self.last_action_info = action_result

        # 返回结果信息给环境
        return action_result

    # --- Getter 方法 ---
    def get_player_state(self, player_index: int) -> Optional[PlayerState]:
        """获取指定玩家的状态对象"""
        if 0 <= player_index < self.num_players:
            return self.players[player_index]
        return None

    def is_hand_over(self) -> bool:
        """检查当前局是否已结束"""
        return self._hand_over_flag

    def is_game_over(self) -> bool:
        """检查整场游戏是否已结束"""
        # 可以添加更复杂的结束条件，如分数低于0 (飞了)
        if not self._game_over_flag:  # 避免重复检查
            # 检查是否完成预定场数 (例如，假设打南风场，南4局结束)
            if self.round_wind > 1:  # 假设 0=东, 1=南
                # 且当前不是刚开始南1局 (防止南1局直接结束)
                # 这个逻辑可以更精确，例如检查 self.round_number 是否完成
                self._game_over_flag = True
            # 检查是否有人被飞 (分数<0)
            # for p in self.players:
            #     if p.score < 0:
            #         self._game_over_flag = True
            #         break
        return self._game_over_flag

    # --- 更新分数和推进游戏的方法 (由环境调用) ---
    def update_scores(self, score_changes: Dict[int, int]):
        """根据计算结果更新玩家分数"""
        print(f"更新分数: {score_changes}")
        for player_id, change in score_changes.items():
            if 0 <= player_id < self.num_players:
                self.players[player_id].score += change
        print(f"更新后分数: {[(p.player_id, p.score) for p in self.players]}")
        # 可以在此检查是否有人被飞

    def advance_round(self, dealer_remains: bool):
        """
        推进到下一局或增加本场数。由环境在一局结束后调用。

        Args:
            dealer_remains (bool): 庄家是否连庄 (和牌或流局听牌)。
        """
        if self.is_game_over():
            print("游戏已结束，无法推进局数。")
            return False  # 返回 False 表示无法推进

        # 根据是否连庄更新本场数和庄家索引
        if dealer_remains:
            self.honba += 1
            # 庄家不变 (self.dealer_index 不变)
            print(f"庄家 ({self.dealer_index}) 连庄，本场增至 {self.honba}。")
        else:
            self.honba = 0  # 不连庄则本场清零
            self.dealer_index = (self.dealer_index + 1) % self.num_players
            print(f"下轮庄家变更为玩家 {self.dealer_index}，本场清零。")

            # 如果庄家轮了一圈，检查是否需要进位到下一风或结束游戏
            if self.dealer_index == 0:  # 庄家轮回到 P0
                self.round_number += 1
                print(f"局数推进至 {self.round_number} 局。")
                if self.round_number > self.num_players:  # 通常是4局
                    self.round_number = 1  # 重置局数
                    self.round_wind += 1  # 进入下一风
                    print(f"场风推进至 {['东','南','西','北'][self.round_wind]}。")
                    # 检查游戏是否结束 (例如打完南场)
                    # TODO: 使用更灵活的结束条件配置
                    if self.round_wind > 1:  # 假设只打东南两风 (半庄)
                        self._game_over_flag = True
                        self.game_phase = GamePhase.GAME_OVER
                        print("游戏结束 (完成南4局)。")
                        return False  # 游戏结束，不再开始新局

        # 准备开始新的一局 (重置牌局状态)
        # 注意：这里不直接调用 reset_new_hand，让环境决定何时调用
        self.game_phase = GamePhase.HAND_START  # 标记准备开始新局

        # 返回 True 表示可以开始新局
        return not self.is_game_over()

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
