import sys
import os
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import numpy as np


# --- I. 用户提供的核心数据结构 ---


@dataclass(frozen=True)  # 使Tile不可变（更安全）
class Tile:
    """麻将牌表示（值0-33）"""

    value: int  # 0-8: 1-9万，9-17: 1-9筒，18-26: 1-9条，27-30: 东南西北，31-33: 白发中
    is_red: bool = False  # 是否是赤宝牌

    def __post_init__(self):
        # 添加对牌值的验证
        if not (0 <= self.value < 34):
            raise ValueError(f"无效的牌值: {self.value}")

    def __lt__(self, other):
        # 允许牌的排序
        if not isinstance(other, Tile):
            return NotImplemented
        return self.value < other.value

    def __hash__(self):
        # 使Tile可哈希，用于集合/字典
        return hash((self.value, self.is_red))

    def __str__(self):
        # 基本字符串表示（可增强）
        return f"T({self.value}{'r' if self.is_red else ''})"

    def __repr__(self):
        return self.__str__()


class ActionType(Enum):
    """麻将动作类型枚举 - 代表玩家可选择的动作"""

    DISCARD = auto()  # 打牌（必须）
    RIICHI = auto()  # 立直宣言（打牌的同时）
    CHI = auto()  # 吃（对上家弃牌）
    PON = auto()  # 碰（对任意玩家弃牌）
    KAN = auto()  # 杠（包括暗杠、加杠、大明杠）
    TSUMO = auto()  # 自摸和了（摸牌后）
    RON = auto()  # 荣和（对任意玩家弃牌）
    PASS = auto()  # 跳过（对弃牌不进行吃/碰/杠/荣和的选择）
    SPECIAL_DRAW = auto()  # 特殊流局宣告（例如九种九牌）- 作为一种可选动作
    # DRAW = auto()  # <-- 已移除，因为摸牌是一个流程控制，而非玩家选择的动作


class KanType(Enum):
    """杠的类型"""

    CLOSED = auto()  # 暗杠（Ankan）
    ADDED = auto()  # 加杠（Kakan/Shouminkan）- 在现有碰牌上加杠
    OPEN = auto()  # 大明杠（Daiminkan）- 对弃牌进行杠


@dataclass(frozen=True)
class Action:
    """完整的麻将动作表示"""

    type: ActionType

    # --- 参数 ---
    tile: Optional[Tile] = None
    chi_tiles: Optional[Tuple[Tile, Tile]] = field(default=None)
    kan_type: Optional[KanType] = None
    riichi_discard: Optional[Tile] = None
    winning_tile: Optional[Tile] = field(default=None)

    def __post_init__(self):
        """基本结构验证。"""
        if self.type == ActionType.DISCARD and self.tile is None:
            raise ValueError("DISCARD动作需要'tile'参数")
        elif self.type == ActionType.RIICHI and self.riichi_discard is None:
            raise ValueError("RIICHI动作需要'riichi_discard'参数")
        elif self.type == ActionType.CHI and (
            self.chi_tiles is None or len(self.chi_tiles) != 2
        ):
            raise ValueError("CHI动作需要'chi_tiles'参数（包含2张牌的元组）")
        elif self.type == ActionType.PON and self.tile is None:
            raise ValueError("PON动作需要'tile'参数（要碰的牌类型）")
        elif self.type == ActionType.KAN:
            if self.kan_type is None:
                raise ValueError("KAN动作需要'kan_type'参数")
            if self.tile is None:
                raise ValueError("KAN动作需要'tile'参数（要杠的牌类型）")

        # 移除了对 ActionType.DRAW 的验证

    def to_feature_vector(self, feature_size: int) -> np.ndarray:
        # to_feature_vector 必须根据新的 ActionType 数量进行调整
        required_size = len(ActionType) + 34 + (2 * 34) + len(KanType)
        if required_size > feature_size:
            raise ValueError(
                f"Action type {self.type.name} requires minimum feature size {required_size}, but provided size is {feature_size}"
            )

        feature_vector = np.zeros(feature_size, dtype=np.float32)

        # --- 编码逻辑 (与你代码中的一致，使用 offsets) ---
        type_offset = 0
        type_size = len(ActionType)  # 此处 type_size 会根据移除 DRAW 后的数量更新

        tile_offset = type_offset + type_size
        tile_size = 34

        chi_tiles_offset = tile_offset + tile_size
        chi_tile_size = 34
        chi_total_size = 2 * chi_tile_size

        kan_type_offset = chi_tiles_offset + chi_total_size
        kan_type_size = len(KanType)

        # 编码动作类型 (独热编码)
        if 0 <= self.type.value - 1 < type_size:
            feature_vector[type_offset + self.type.value - 1] = 1.0

        # 编码参数 (简化，仅包含与 Tile 相关的核心逻辑)
        primary_tile = None
        if self.type in [ActionType.DISCARD, ActionType.PON, ActionType.KAN]:
            primary_tile = self.tile
        elif self.type == ActionType.RIICHI:
            primary_tile = self.riichi_discard

        if primary_tile is not None:
            idx = tile_offset + primary_tile.value
            if 0 <= idx < tile_offset + tile_size:
                feature_vector[idx] = 1.0

        elif self.type == ActionType.KAN and self.kan_type is not None:
            if 0 <= self.kan_type.value - 1 < kan_type_size:
                feature_vector[kan_type_offset + self.kan_type.value - 1] = 1.0

        elif self.type == ActionType.CHI and self.chi_tiles:
            if len(self.chi_tiles) == 2:
                idx0 = chi_tiles_offset + self.chi_tiles[0].value
                if 0 <= idx0 < chi_tiles_offset + chi_tile_size:
                    feature_vector[idx0] = 1.0

                idx1 = chi_tiles_offset + chi_tile_size + self.chi_tiles[1].value
                if 0 <= idx1 < chi_tiles_offset + chi_total_size:
                    feature_vector[idx1] = 1.0

        return feature_vector

    def __str__(self):
        """可读的字符串表示"""
        parts = [f"type={self.type.name}"]
        if self.tile:
            parts.append(f"tile={self.tile}")
        if self.chi_tiles:
            parts.append(f"chi_tiles=({self.chi_tiles[0]}, {self.chi_tiles[1]})")
        if self.kan_type:
            parts.append(f"kan_type={self.kan_type.name}")
        if self.riichi_discard and self.type == ActionType.RIICHI:
            parts.append(f"riichi_discard={self.riichi_discard}")
        if self.winning_tile and self.type in [ActionType.TSUMO, ActionType.RON]:
            parts.append(f"winning_tile={self.winning_tile}")

        return f"Action({', '.join(parts)})"

    def __repr__(self):
        return self.__str__()


# 示例用法
if __name__ == "__main__":
    # 示例牌（假设Tile(0)是1万，Tile(1)是2万等）
    t1m = Tile(0)
    t2m = Tile(1)
    t3m = Tile(2)
    t5p = Tile(14, is_red=True)  # 赤5筒
    tew = Tile(27)  # 东风

    # 创建具体动作实例
    action_discard = Action(type=ActionType.DISCARD, tile=t5p)
    action_chi = Action(
        type=ActionType.CHI, chi_tiles=(t1m, t2m)
    )  # 假设对3万弃牌进行吃
    action_pon = Action(type=ActionType.PON, tile=tew)  # 假设对东风弃牌进行碰
    action_kan_closed = Action(type=ActionType.KAN, tile=t1m, kan_type=KanType.CLOSED)
    action_kan_added = Action(
        type=ActionType.KAN, tile=tew, kan_type=KanType.ADDED
    )  # 在现有东风碰上加杠
    action_riichi = Action(type=ActionType.RIICHI, riichi_discard=t3m)
    action_tsumo = Action(type=ActionType.TSUMO, winning_tile=t2m)
    action_ron = Action(type=ActionType.RON, winning_tile=tew)
    action_pass = Action(type=ActionType.PASS)

    print(action_discard)
    print(action_chi)
    print(action_pon)
    print(action_kan_closed)
    print(action_kan_added)
    print(action_riichi)
    print(action_tsumo)
    print(action_ron)
    print(action_pass)

    # 测试特征向量生成
    print("\n特征向量示例（KAN_CLOSED）:")
    feat_vec = action_kan_closed.to_feature_vector(feature_size=150)
    print(f"向量大小: {len(feat_vec)}")
    print(f"非零索引: {np.where(feat_vec != 0)[0]}")

    print("\n特征向量示例（CHI）:")
    feat_vec_chi = action_chi.to_feature_vector(feature_size=150)
    print(f"向量大小: {len(feat_vec_chi)}")
    print(f"非零索引: {np.where(feat_vec_chi != 0)[0]}")
