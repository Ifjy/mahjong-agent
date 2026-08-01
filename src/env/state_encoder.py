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
        """编码基础游戏状态"""
        state_features = {}

        # 玩家私有信息
        player = game_state.players[player_index]
        state_features["hand"] = self._encode_tiles(player.hand)
        state_features["melds"] = self._encode_player_melds(player.melds)

        # 公共信息
        state_features["discards"] = np.stack(
            [self._encode_tiles(p.discards) for p in game_state.players]
        )
        state_features["dora"] = self._encode_tiles(game_state.wall.dora_indicators)

        # 游戏上下文
        state_features["wind"] = np.array(
            [
                game_state.round_wind,
                (
                    player.seat_wind
                    - game_state.players[game_state.dealer_index].seat_wind
                )
                % 4,
            ],
            dtype=np.uint8,
        )

        state_features["game_progress"] = np.array(
            [
                game_state.round_number,
                game_state.honba,
                game_state.riichi_sticks,
                game_state.wall.get_remaining_live_tiles_count(),  # <-- Correct method name
            ],
            dtype=np.uint16,
        )

        state_features["last_action"] = self._encode_last_action(game_state)
        state_features["scores"] = np.array(
            [p.score for p in game_state.players], dtype=np.int32
        )

        return state_features

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
        """定义观察空间"""
        return spaces.Dict(
            {
                "state": spaces.Dict(
                    {
                        "hand": spaces.Box(0, 4, (self.tile_types,), dtype=np.uint8),
                        "melds": spaces.Box(0, 4, (self.tile_types,), dtype=np.uint8),
                        "discards": spaces.Box(
                            0, 4, (4, self.tile_types), dtype=np.uint8
                        ),
                        "dora": spaces.Box(0, 4, (self.tile_types,), dtype=np.uint8),
                        "wind": spaces.Box(0, 3, (2,), dtype=np.uint8),
                        "game_progress": spaces.Box(0, 100, (4,), dtype=np.uint16),
                        "last_action": spaces.Box(
                            0, 3, (self.tile_types + 1,), dtype=np.uint8
                        ),
                        "scores": spaces.Box(-100000, 100000, (4,), dtype=np.int32),
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
