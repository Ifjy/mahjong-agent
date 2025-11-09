# 定义所有麻将牌、场风、动作类型、优先级等不变的配置数据。
# constants.py
from typing import Set, Dict
from enum import Enum, auto

# 假设 actions.py 在同一模块级别或父级
from src.env.core.actions import ActionType

# ======================================================================
# 1. 牌值定义 (Tile Values)
# ======================================================================
# 0-8: 万 (Man) 1-9
# 9-17: 筒 (Pin) 1-9
# 18-26: 索 (Sou) 1-9
# 27: 东, 28: 南, 29: 西, 30: 北
# 31: 白, 32: 发, 33: 中

MAN_1, MAN_9 = 0, 8
PIN_1, PIN_9 = 9, 17
SOU_1, SOU_9 = 18, 26
WIND_EAST = 27
WIND_SOUTH = 28
WIND_WEST = 29
WIND_NORTH = 30
DRAGON_WHITE = 31
DRAGON_GREEN = 32
DRAGON_RED = 33

# 幺九牌 (Terminals and Honors)
# (基于 rules_engine.py 中的 self.terminal_honor_values)
TERMINAL_HONOR_VALUES: Set[int] = {
    MAN_1,
    MAN_9,
    PIN_1,
    PIN_9,
    SOU_1,
    SOU_9,
    WIND_EAST,
    WIND_SOUTH,
    WIND_WEST,
    WIND_NORTH,
    DRAGON_WHITE,
    DRAGON_GREEN,
    DRAGON_RED,
}

# ======================================================================
# 2. 游戏流程与规则 (Game Flow & Rules)
# ======================================================================


class Wind(Enum):
    """
    场风和自风的整数表示
    (基于 rules_engine.py 中的 ROUND_WIND_EAST = 0 等)
    """

    EAST = 0
    SOUTH = 1
    WEST = 2
    NORTH = 3


# 游戏长度到最大场风的映射
# (基于 rules_engine.py 中的 GAME_LENGTH_MAX_WIND)
GAME_LENGTH_MAX_WIND: Dict[str, Wind] = {
    "tonpuusen": Wind.EAST,  # 东风场
    "hanchan": Wind.SOUTH,  # 半庄 (东风 + 南风)
    "issousen": Wind.NORTH,  # 一庄 (全场)
}

# 响应动作的优先级
# (基于 temp_from_game state.py 中的 _resolve_response_priorities 逻辑)
# ***注意：这里我们从 actions.py 导入 ActionType***
ACTION_PRIORITY: Dict[ActionType, int] = {
    ActionType.RON: 3,
    ActionType.KAN: 2,  # 大明杠
    ActionType.PON: 2,
    ActionType.CHI: 1,
    ActionType.PASS: 0,
}
