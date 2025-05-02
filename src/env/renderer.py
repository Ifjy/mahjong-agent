from src.env.core.actions import Action, ActionType, Tile, KanType


class Renderer:
    def __init__(self, config):
        self.config = config
        self._tile_symbols = self._init_tile_symbols()

    def _init_tile_symbols(self):
        symbols = []
        # 万子 0-8
        for i in range(9):
            symbols.append(f"{i+1}万")
        # 筒子 9-17
        for i in range(9):
            symbols.append(f"{i+1}筒")
        # 索子 18-26
        for i in range(9):
            symbols.append(f"{i+1}索")
        # 字牌 27-33
        symbols.extend(["东", "南", "西", "北", "白", "发", "中"])
        return symbols  # 共 34 个符号，索引 0-33

    def _get_tile_string(self, tile: Tile) -> str:
        """
        将 Tile 对象渲染成字符串 (例如 '1万', '5筒r', '东')
        这个方法只负责根据 tile 的 value 和 is_red 属性生成基础字符串。
        是否是宝牌的标记 ('d') 将在渲染时根据当前宝牌列表额外添加。
        """
        if tile is None:
            return "???"

        if not hasattr(tile, "value"):
            return f"InvalidTile({type(tile).__name__})"

        tile_value = tile.value
        if (
            not isinstance(tile_value, int)
            or tile_value < 0
            or tile_value >= len(self._tile_symbols)
        ):
            return f"UnknownValue({tile_value})"

        base_symbol = self._tile_symbols[tile_value]
        # 假设 Tile 对象有 is_red 属性来表示是否是红宝牌实例
        is_red = getattr(tile, "is_red", False)
        # 只在这里添加 'r'，不添加 'd'
        return base_symbol + ("r" if is_red else "")

    def render_action_to_string(self, action: Action, player_idx: int = -1) -> str:
        """将 Action 对象渲染成人类可读的字符串"""
        # player_idx 是可选的，用于添加 "玩家X" 前缀
        player_prefix = f"玩家{player_idx} " if player_idx != -1 else ""

        # 这里使用 _get_tile_string，它不包含 'd' 标记，这是符合动作描述语境的
        # 比如 "玩家1 打出 5筒r" 比 "玩家1 打出 5筒rd" 更自然
        action_type = action.type

        if action_type == ActionType.DISCARD:
            tile = action.tile
            return f"{player_prefix}打出 {self._get_tile_string(tile)}"

        elif action_type == ActionType.RIICHI:
            riichi_tile = action.riichi_discard
            return f"{player_prefix}立直 打出 {self._get_tile_string(riichi_tile)}"

        elif action_type == ActionType.CHI:
            chi_tiles = action.chi_tiles
            target_tile = action.tile
            chi_str = " ".join(self._get_tile_string(t) for t in chi_tiles)
            return (
                f"{player_prefix}吃 {self._get_tile_string(target_tile)}，用 {chi_str}"
            )

        elif action_type == ActionType.PON:
            tile = action.tile
            return f"{player_prefix}碰 {self._get_tile_string(tile)}"

        elif action_type == ActionType.KAN:
            tile = action.tile
            kan_type = action.kan_type
            if kan_type == KanType.CLOSED:
                kan_str = "暗杠"
            elif kan_type == KanType.OPEN:
                kan_str = "明杠"
            elif kan_type == KanType.ADDED:
                kan_str = "加杠"
            else:
                kan_str = "杠"
            return f"{player_prefix}{kan_str} {self._get_tile_string(tile)}"

        elif action_type == ActionType.TSUMO:
            winning_tile = action.winning_tile
            if winning_tile:
                return f"{player_prefix}自摸 {self._get_tile_string(winning_tile)}"
            else:
                return f"{player_prefix}自摸"

        elif action_type == ActionType.RON:
            winning_tile = action.winning_tile
            if winning_tile:
                return f"{player_prefix}荣和 {self._get_tile_string(winning_tile)}"
            else:
                return f"{player_prefix}荣和"

        elif action_type == ActionType.PASS:
            return f"{player_prefix}选择跳过"

        elif action_type == ActionType.SPECIAL_DRAW:
            return f"{player_prefix}特殊流局宣告"

        # DRAW 动作通常只在内部处理，不作为玩家可见动作渲染
        # elif action_type == ActionType.DRAW:
        #     tile = action.tile
        #     if tile is not None:
        #         return f"{player_prefix}摸牌 {self._get_tile_string(tile)}"
        #     else:
        #         return f"{player_prefix}摸牌（流局）"

        else:
            return f"{player_prefix}{str(action)}"  # fallback

    def render(self, game_state, mode="human"):
        if mode == "human":
            self._render_text(game_state)

    def _render_text(self, game_state):
        print("\n" + "=" * 50)
        wind_str = ["东", "南", "西", "北"][game_state.round_wind % 4]
        print(
            f"场风: {wind_str}{game_state.round_number}局   本场数: {game_state.honba}   立直棒: {game_state.riichi_sticks}"
        )
        print(f"剩余牌数: {game_state.wall.get_remaining_live_tiles_count()}")

        # 渲染宝牌指示牌 (使用 _get_tile_string 处理红宝牌指示牌)
        indicator_str = " ".join(
            [
                self._get_tile_string(dora_tile)
                for dora_tile in game_state.wall.dora_indicators
            ]
        )
        print(f"宝牌指示牌: {indicator_str}")

        # 渲染实际宝牌，并获取实际宝牌的值集合以便后续标记
        actual_dora_tiles = game_state.wall.get_current_dora_tiles()
        dora_str = " ".join(
            [
                self._get_tile_string(dora_tile) for dora_tile in actual_dora_tiles
            ]  # 这里也使用 _get_tile_string
        )
        print(f"当前宝牌: {dora_str}")

        # 为了快速判断一张牌是否是宝牌，创建一个实际宝牌值的集合
        # 注意：这里只需要 value，因为宝牌是按值确定的，不区分红色普通
        dora_values = {tile.value for tile in actual_dora_tiles}

        current_player = game_state.current_player_index
        for i, player in enumerate(game_state.players):
            is_current = "-> " if i == current_player else "   "
            position = ["东家", "南家", "西家", "北家"][player.seat_wind % 4]
            status = []
            if player.riichi_declared:
                status.append("立直")
            if player.ippatsu_chance:
                status.append("一发")
            status_str = f"[{', '.join(status)}]" if status else ""

            print(f"\n{is_current}{position} {status_str} 得分: {player.score}")

            # --- 渲染手牌 ---
            if player.hand:
                hand_parts = []
                # 排序手牌并添加dora标记
                # 遍历 sorted(player.hand) 确保按规则排序
                for tile in sorted(player.hand):
                    tile_str = self._get_tile_string(
                        tile
                    )  # 获取基础字符串 (含 'r' 如果是红宝牌)
                    if tile.value in dora_values:
                        tile_str += "d"  # 如果是宝牌，添加 'd' 标记
                    hand_parts.append(tile_str)

                hand_str = " ".join(hand_parts)

                # 渲染摸牌并添加dora标记
                if player.drawn_tile is not None:
                    drawn_tile_str = self._get_tile_string(player.drawn_tile)
                    if player.drawn_tile.value in dora_values:
                        drawn_tile_str += "d"  # 如果是宝牌，添加 'd' 标记
                    hand_str += f" + [{drawn_tile_str}]"  # 将摸到的牌单独括起来

                print(f"手牌: {hand_str}")

            # --- 渲染副露 ---
            if player.melds:
                melds_str = []
                for meld in player.melds:
                    meld_parts = []
                    # 渲染副露中的牌并添加dora标记
                    for tile in meld["tiles"]:  # meld["tiles"] 应该是一个 Tile 列表
                        tile_str = self._get_tile_string(tile)
                        if tile.value in dora_values:
                            tile_str += "d"  # 如果是宝牌，添加 'd' 标记
                        meld_parts.append(tile_str)
                    melds_str.append(
                        f"[{' '.join(meld_parts)}]"
                    )  # 副露通常用方括号括起来
                print(f"副露: {' '.join(melds_str)}")

            # --- 渲染牌河 ---
            if player.discards:
                discard_parts = []
                # 渲染牌河中的牌并添加dora标记
                for tile in player.discards:  # player.discards 应该是一个 Tile 列表
                    tile_str = self._get_tile_string(tile)
                    if tile.value in dora_values:
                        tile_str += "d"  # 如果是宝牌，添加 'd' 标记
                    discard_parts.append(tile_str)
                discard_str = " ".join(discard_parts)
                print(f"牌河: {discard_str}")

            # 最后动作的渲染不加 'd' 标记，因为它描述的是动作本身，不是牌的状态
            # 比如 "玩家1 打出 5筒r" 清楚说明了打出的是红宝牌
            # 如果加上 'd' 会变成 "玩家1 打出 5筒rd" 语义上稍显重复
            # 如果需要，可以自行修改 render_action_to_string，但这里保持原样
            if game_state.last_action_info:
                print("\n最后动作:", end=" ")
                action_type = game_state.last_action_info.get("type")
                player_idx = game_state.last_action_info.get("player")
                action_obj = game_state.last_action_info.get("action_obj")

                if action_obj is None:
                    print(str(game_state.last_action_info))
                elif action_obj is not None and player_idx is not None:
                    # render_action_to_string 内部调用 _get_tile_string，不含 'd' 标记
                    last_action_str = self.render_action_to_string(
                        action_obj, player_idx
                    )
                    print(last_action_str)
                else:
                    print(str(game_state.last_action_info))

                print("=" * 50 + "\n")

    def close(self):
        pass
