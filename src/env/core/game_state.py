import random
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple, Set  # 引入类型提示


# --- 从 actions.py 导入我们定义好的类 ---
# 假设 actions.py 与 game_state.py 在同一目录下或已正确配置路径
# 如果不在同一目录，需要调整 import 路径，例如 from src.env.core.actions import ...
from .actions import Action, ActionType, Tile, KanType

# (我们也可以在这里定义 Meld 类)
import random
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple  # 引入类型提示

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


class GameState:
    """
    表示日本麻将游戏的完整状态。
    存储所有必要信息，并通过 apply_action 应用状态变更。
    不包含复杂的控制流或规则校验逻辑。
    """

    rules_engine: "RulesEngine" = field(init=False)

    def __init__(self, config, wall: Wall, rules_engine: "RulesEngine"):
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
        self.rules_engine: "RulesEngine" = rules_engine
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
                combined_hand = list(player.hand)  # 创建手牌的副本
                if player.drawn_tile:
                    combined_hand.append(player.drawn_tile)  # 将摸到的牌添加到副本中
                else:
                    # 没有摸牌就宣布自摸？通常是无效的，除非是特殊情况（如岭上开花时摸到的牌被移走）
                    print(
                        f"警告: 玩家 {player_index} 在没有 drawn_tile 时尝试宣布 Tsumo。"
                    )
                    # 尝试检查手牌本身是否构成国士无双十三面听之类的特殊自摸
                    # 这是复杂情况，暂时简化处理：如果没有 drawn_tile 且手牌不构成和牌型，则视为无效
                    if not self.rules_engine.check_win(combined_hand, player.melds):
                        print("错误: 手牌不构成和牌型，自摸无效。")
                        # TODO: 返回无效动作信号 / 抛出错误
                        return  # 无效自摸

                # 调用 RulesEngine 检查是否满足和牌条件，win_info 包含和牌详情
                win_info = self.rules_engine.check_win(
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
                        winning_tile=winning_tile_obj,  # 传递实际和牌的牌对象
                        is_tsumo=True,
                        win_info=win_info,  # 传递 RulesEngine 检查到的和牌详情
                    )
                else:
                    print(
                        f"错误: 玩家 {player_index} 尝试宣布 Tsumo 但手牌不满足和牌条件。"
                    )
                    # TODO: 返回无效动作信号 / 抛出错误
                    return  # 无效自摸
            # 4. 处理 KAN (杠) 动作 (暗杠或加杠)
            # todo kan 下分为 closed added open
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
                        player, tile_to_kan, kan_type
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

    def _remove_tiles_from_hand(self, player, tiles_to_remove: List[Tile]):
        """
        从玩家手牌中移除指定的牌列表。处理赤宝牌和多张相同普通牌的情况。
        """
        hand_tiles = list(player.hand)  # 创建手牌的副本进行操作
        for tile_to_remove in tiles_to_remove:
            removed = False
            # 尝试移除指定对象（包括赤宝牌标识）
            if tile_to_remove in hand_tiles:
                hand_tiles.remove(tile_to_remove)
                removed = True
            else:
                # 如果指定对象不在，尝试移除同类型的普通牌 (例如，要移除赤5，但手牌只有普通5)
                normal_tile_of_same_value = Tile(tile_to_remove.value, is_red=False)
                if normal_tile_of_same_value in hand_tiles:
                    hand_tiles.remove(normal_tile_of_same_value)
                    removed = True
                # TODO: 更复杂的赤宝牌移除逻辑，例如打出普通5，手牌有赤5和普通5，应该移除普通5

            if not removed:
                print(
                    f"内部错误: 无法从玩家 {player.index} 手牌中移除牌 {tile_to_remove}。手牌: {player.hand}"
                )
                # 这表示规则判断或动作生成有问题，玩家不应该选择这个动作

        player.hand = hand_tiles  # 更新玩家手牌

    # TODO: 添加计算得分的方法 calculate_scores_ron 等

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
