import numpy as np
from gymnasium import spaces
from typing import List
from src.env.core.actions import Action


class StateEncoder:
    """
    状态编码器类，支持候选动作特征编码
    """

    def __init__(self, config):
        self.config = config or {}
        self.tile_types = 34  # 基本牌型数量
        self.max_actions = self.config.get("max_actions", 100)  # 最大候选动作数
        # 动作特征维度: 至少需要 len(ActionType)+34+2*34+len(KanType) = 9+34+68+3 = 114
        self.action_feature_dim = self.config.get("action_feature_dim", 128)

    def encode(self, game_state, player_index, candidate_actions: List[Action] = None):
        """
        编码游戏状态和候选动作
        Args:
            candidate_actions: 当前可用的候选动作列表
        """
        observation = {}

        # 1. 编码游戏状态
        observation["state"] = self._encode_game_state(game_state, player_index)

        # 2. 编码候选动作
        if candidate_actions:
            observation["action_candidates"] = self._encode_actions(candidate_actions)
            observation["action_mask"] = self._create_action_mask(
                len(candidate_actions)
            )
        else:
            observation["action_candidates"] = np.zeros(
                (self.max_actions, self.action_feature_dim), dtype=np.float32
            )
            observation["action_mask"] = np.zeros(self.max_actions, dtype=np.int8)

        return observation

    def _encode_game_state(self, game_state, player_index):
        """
        编码基础游戏状态。所有字段统一输出 float32 并归一化到相近量级 (约 0~1),
        避免 scores(万级) 等大量级特征在首层 Linear 中淹没 hand/discards(0~4) 等小信号。

        归一化策略 (按字段语义):
          - 计数类 (hand/discards/dora/melds, 0~4):        /4  → 0~1
          - one-hot/flag (melds_full/riichi/menzen, 0/1):  原样
          - hand_counts (4~14):                            /13 → ~0.3~1
          - game_progress (round/honba/riichi/余牌):       各除合理上界 → 0~1
          - turn_number (1~70):                            /18 → 0~1
          - wind (0~3):                                    /4  → 0~0.75
          - scores:  (s - 初始点数) / 初始点数, 居中        → 约 -0.9~1.3
          - last_action (one-hot+player): tile 不动, player /3
        """
        state_features = {}
        initial_score = game_state.config.get("initial_score", 25000)

        # 玩家私有信息 (计数 /4)
        player = game_state.players[player_index]
        state_features["hand"] = (self._encode_tiles(player.hand).astype(np.float32) / 4.0)
        state_features["melds"] = (self._encode_player_melds(player.melds).astype(np.float32) / 4.0)

        # 公共信息 (计数 /4)
        state_features["discards"] = (
            np.stack([self._encode_tiles(p.discards) for p in game_state.players])
            .astype(np.float32) / 4.0
        )
        state_features["dora"] = (self._encode_tiles(game_state.wall.dora_indicators)
                                  .astype(np.float32) / 4.0)

        # --- 副露完整编码 (已是 0/1, 原样转 float32) ---
        state_features["melds_full"] = self._encode_all_melds(game_state, player_index).astype(np.float32)

        # --- 各家状态标志 (0/1, 原样转 float32) ---
        state_features["riichi_flags"] = np.array(
            [int(p.riichi_declared) for p in game_state.players],
            dtype=np.float32,
        )
        state_features["menzen_flags"] = np.array(
            [int(p.is_menzen) for p in game_state.players],
            dtype=np.float32,
        )
        state_features["hand_counts"] = np.array(
            [len(p.hand) + (1 if p.drawn_tile is not None else 0)
             for p in game_state.players],
            dtype=np.float32,
        ) / 13.0   # 手牌最多 13+1=14, /13 居中

        # 游戏上下文 (各自除合理上界)
        state_features["wind"] = np.array(
            [
                game_state.round_wind,
                (
                    player.seat_wind
                    - game_state.players[game_state.dealer_index].seat_wind
                )
                % 4,
            ],
            dtype=np.float32,
        ) / 4.0

        state_features["game_progress"] = np.array(
            [
                game_state.round_number / 8.0,                          # 局数 1~8
                min(game_state.honba, 10) / 10.0,                       # 本场 (截断到10)
                min(game_state.riichi_sticks, 8) / 8.0,                 # 立直棒 (截断到8)
                game_state.wall.get_remaining_live_tiles_count() / 122.0,  # 余牌 0~122
            ],
            dtype=np.float32,
        )
        # 当前巡目 (本局第几巡, 一局最多约 18 巡)
        state_features["turn_number"] = np.array(
            [min(game_state.turn_number, 18) / 18.0], dtype=np.float32,
        )

        # last_action: tile one-hot(34) 保持 0/1, 末位 player(0~3) /3
        last_act = self._encode_last_action(game_state).astype(np.float32)
        if last_act[-1] > 0:
            last_act[-1] = last_act[-1] / 3.0
        state_features["last_action"] = last_act

        # scores: 居中归一化 (s - 初始) / 初始 → 约 -0.9~1.3
        state_features["scores"] = np.array(
            [(p.score - initial_score) / initial_score for p in game_state.players],
            dtype=np.float32,
        )

        return state_features

    def _encode_all_melds(self, game_state, player_index) -> np.ndarray:
        """
        编码所有4家的副露 (信息无损)。
        shape: (4, 4, 38) = (4家, 每家最多4组, 每组= tiles_onehot(34)+type_onehot(4))
        type_onehot 4位: [吃CHI, 碰PON, 明杠KAN_open, 暗杠KAN_closed]
        """
        encoded = np.zeros((4, 4, 38), dtype=np.uint8)
        for i, p in enumerate(game_state.players):
            for j, meld in enumerate(p.melds[:4]):   # 最多4组
                slot = encoded[i, j]
                # tiles one-hot (34): 该组副露包含哪些牌型
                for tile in meld.tiles:
                    slot[tile.value] = 1
                # type one-hot (4)
                type_idx = self._meld_type_index(meld, i)
                slot[34 + type_idx] = 1
        return encoded

    @staticmethod
    def _meld_type_index(meld, owner_idx) -> int:
        """副露类型 -> one-hot 索引: 0=吃 1=碰 2=明杠 3=暗杠。"""
        from src.env.core.actions import ActionType
        if meld.type == ActionType.CHI:
            return 0
        if meld.type == ActionType.PON:
            return 1
        # KAN: from_player == 自己 -> 暗杠(加杠也算暗杠侧); 否则明杠(大明杠)
        if meld.from_player == owner_idx:
            return 3   # 暗杠
        return 2       # 明杠

    def _encode_actions(self, actions: List[Action]) -> np.ndarray:
        """
        编码候选动作列表，使用 Action 对象自带的 to_feature_vector 方法。
        """
        # 创建一个零填充的数组来存储所有动作的特征向量
        encoded = np.zeros(
            (self.max_actions, self.action_feature_dim), dtype=np.float32
        )

        # 遍历候选动作列表，最多取 max_actions 个
        for i, action in enumerate(actions[: self.max_actions]):
            try:
                # 调用 Action 对象自己的编码方法，并传入StateEncoder配置的特征维度
                # Action.to_feature_vector 应该确保其输出向量大小与 action_feature_dim 匹配
                action_vec = action.to_feature_vector(
                    feature_size=self.action_feature_dim
                )
                encoded[i, :] = action_vec
            except ValueError as e:
                print(f"警告: 编码动作 {action} 时发生错误: {e}")
                # 可以选择跳过此动作或将其编码为零向量

        return encoded

    def _create_action_mask(self, valid_action_count: int) -> np.ndarray:
        """创建动作掩码"""
        mask = np.zeros(self.max_actions, dtype=np.int8)
        mask[: min(valid_action_count, self.max_actions)] = 1
        return mask

    def _encode_tiles(self, tiles: List[int]) -> np.ndarray:
        """编码牌型集合"""
        counts = np.zeros(self.tile_types, dtype=np.uint8)
        for tile in tiles:
            counts[tile.value] += 1  # Access the integer value using .value
        return counts

    def _encode_player_melds(self, melds):
        """编码玩家副露"""
        encoded = np.zeros(self.tile_types, dtype=np.uint8)
        for meld in melds:
            for tile in meld.tiles:  # Meld 是 dataclass，用属性访问
                encoded[tile.value] += 1  # fix: use .value
        return encoded

    def _encode_last_action(self, game_state):
        """编码最后动作: [tile one-hot(34), player_idx]。
        last_action_info 结构: {"player": idx, "type": str, "action_obj": Action}
        tile 需从 action_obj 提取 (DISCARD.tile / RIICHI.riichi_discard / PON.tile / KAN.tile 等)。
        """
        encoded = np.zeros(self.tile_types + 1, dtype=np.uint8)
        info = game_state.last_action_info
        if not info:
            return encoded

        action_obj = info.get("action_obj")
        if action_obj is not None:
            # 从 Action 对象中提取关联的 tile (不同动作类型字段不同)
            tile = None
            if action_obj.type.name in ("DISCARD", "PON", "KAN"):
                tile = action_obj.tile
            elif action_obj.type.name == "RIICHI":
                tile = action_obj.riichi_discard
            elif action_obj.type.name in ("TSUMO", "RON"):
                tile = action_obj.winning_tile
            elif action_obj.type.name == "CHI":
                tile = action_obj.tile  # 被吃的牌 (若存在)

            if tile is not None and hasattr(tile, "value"):
                encoded[tile.value] = 1

        encoded[-1] = info.get("player", 0)
        return encoded

    def get_observation_space(self):
        """定义观察空间 (归一化后全部 float32)"""
        return spaces.Dict(
            {
                "state": spaces.Dict(
                    {
                        "hand": spaces.Box(0, 1, (self.tile_types,), dtype=np.float32),
                        "melds": spaces.Box(0, 1, (self.tile_types,), dtype=np.float32),
                        "melds_full": spaces.Box(
                            0, 1, (4, 4, 38), dtype=np.float32
                        ),
                        "discards": spaces.Box(
                            0, 1, (4, self.tile_types), dtype=np.float32
                        ),
                        "dora": spaces.Box(0, 1, (self.tile_types,), dtype=np.float32),
                        "riichi_flags": spaces.Box(0, 1, (4,), dtype=np.float32),
                        "menzen_flags": spaces.Box(0, 1, (4,), dtype=np.float32),
                        "hand_counts": spaces.Box(0, 1.1, (4,), dtype=np.float32),
                        "wind": spaces.Box(0, 1, (2,), dtype=np.float32),
                        "game_progress": spaces.Box(0, 1, (4,), dtype=np.float32),
                        "turn_number": spaces.Box(0, 1, (1,), dtype=np.float32),
                        "last_action": spaces.Box(
                            0, 1, (self.tile_types + 1,), dtype=np.float32
                        ),
                        "scores": spaces.Box(-1.5, 1.5, (4,), dtype=np.float32),
                    }
                ),
                "action_candidates": spaces.Box(
                    0,
                    1,
                    (self.max_actions, self.action_feature_dim),
                    dtype=np.float32,
                ),
                "action_mask": spaces.Box(0, 1, (self.max_actions,), dtype=np.int8),
            }
        )
