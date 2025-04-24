import random
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple  # 引入类型提示
from src.env.core.rules import RulesEngine

# --- 从 actions.py 导入我们定义好的类 ---
# 假设 actions.py 与 game_state.py 在同一目录下或已正确配置路径
# 如果不在同一目录，需要调整 import 路径，例如 from src.env.core.actions import ...
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
