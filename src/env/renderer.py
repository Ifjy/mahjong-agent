from src.env.core.actions import Action, ActionType, Tile, KanType


class Renderer:
    def __init__(self, config):
        self.config = config
        self._tile_symbols = self._init_tile_symbols()

    def _init_tile_symbols(self):
        symbols = []
        for i in range(9):
            symbols.append(f"{i+1}万")
        for i in range(9):
            symbols.append(f"{i+1}筒")
        for i in range(9):
            symbols.append(f"{i+1}索")
        symbols.extend(["东", "南", "西", "北", "白", "发", "中"])
        return symbols

    def _get_tile_string(self, tile: Tile) -> str:
        """将 Tile 对象渲染成字符串 (例如 '1m', '5pr')"""
        if tile is None:
            return "???"  # 或者其他表示空牌的符号
        # 确保 tile.value 在 _tile_symbols 中，或者提供一个回退
        base = self._tile_symbols.get(tile.value, f"T({tile.value})")
        # 假设 Tile 对象有 is_red 属性
        return base + ("r" if getattr(tile, "is_red", False) else "")

    def render_action_to_string(self, action: Action, player_idx: int = -1) -> str:
        """将 Action 对象渲染成人类可读的字符串"""
        # player_idx 是可选的，用于添加 "玩家X" 前缀
        player_prefix = f"玩家{player_idx} " if player_idx != -1 else ""

        action_type = action.type  # 假设 Action 对象有 type 属性

        if action_type == ActionType.DISCARD:
            # 假设 Action 对象有 tile 属性
            tile = action.tile
            return f"{player_prefix}打出 {self._get_tile_string(tile)}"

        elif action_type == ActionType.RIICHI:
            # 假设 Riichi Action 对象有 riichi_discard 属性
            riichi_tile = action.riichi_discard
            return f"{player_prefix}立直 打出 {self._get_tile_string(riichi_tile)}"

        elif action_type == ActionType.CHI:
            # 假设 Chi Action 对象有 chi_tiles 和 tile 属性
            chi_tiles = action.chi_tiles
            target_tile = action.tile  # 被吃的那张
            chi_str = " ".join(self._get_tile_string(t) for t in chi_tiles)
            return (
                f"{player_prefix}吃 {self._get_tile_string(target_tile)}，用 {chi_str}"
            )

        elif action_type == ActionType.PON:
            # 假设 Pon Action 对象有 tile 属性
            tile = action.tile
            return f"{player_prefix}碰 {self._get_tile_string(tile)}"

        elif action_type == ActionType.KAN:
            # 假设 Kan Action 对象有 tile 和 kan_type 属性
            tile = action.tile
            kan_type = action.kan_type
            if kan_type == KanType.CLOSED:
                kan_str = "暗杠"
            elif kan_type == KanType.OPEN:
                kan_str = "明杠"
            elif kan_type == KanType.ADDED:
                kan_str = "加杠"
            else:
                kan_str = "杠"  # 未知类型
            return f"{player_prefix}{kan_str} {self._get_tile_string(tile)}"

        elif action_type == ActionType.TSUMO:
            # 假设 Tsumo Action 对象有 winning_tile 属性
            winning_tile = action.winning_tile
            if winning_tile:
                return f"{player_prefix}自摸 {self._get_tile_string(winning_tile)}"
            else:
                return f"{player_prefix}自摸"

        elif action_type == ActionType.RON:
            # 假设 Ron Action 对象有 winning_tile 属性
            winning_tile = action.winning_tile
            if winning_tile:
                return f"{player_prefix}荣和 {self._get_tile_string(winning_tile)}"
            else:
                return f"{player_prefix}荣和"

        elif action_type == ActionType.PASS:
            return f"{player_prefix}选择跳过"

        elif action_type == ActionType.SPECIAL_DRAW:
            return f"{player_prefix}特殊流局宣告"

        # 可以添加对 DRAW 的处理（如果需要渲染摸牌动作）
        # elif action_type == ActionType.DRAW:
        #    tile = action.tile # 假设 Draw Action 有 tile 属性
        #    if tile is not None:
        #        return f"{player_prefix}摸牌 {self._get_tile_string(tile)}"
        #    else:
        #        return f"{player_prefix}摸牌（流局）"

        else:
            # 对于未明确处理的动作类型，返回其默认字符串表示
            return f"{player_prefix}{str(action)}"

    def render(self, game_state, mode="human"):
        if mode == "human":
            self._render_text(game_state)

    def _render_text(self, game_state):
        print("\n" + "=" * 50)
        wind_str = ["东", "南", "西", "北"][game_state.round_wind % 4]
        print(
            f"场风: {wind_str}{game_state.round_number}局  本场数: {game_state.honba}  立直棒: {game_state.riichi_sticks}"
        )
        print(
            f"剩余牌数: {game_state.wall.get_remaining_live_tiles_count()}"
        )  # ✅修正方法名

        dora_str = " ".join(
            [
                self._tile_symbols[dora_tile.value]
                for dora_tile in game_state.wall.dora_indicators
            ]  # ✅使用.value
        )
        print(f"宝牌指示牌: {dora_str}")

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

            if player.hand:
                hand_str = " ".join(
                    [self._tile_symbols[tile.value] for tile in sorted(player.hand)]
                )
                if player.drawn_tile is not None:
                    hand_str += f" + [{self._tile_symbols[player.drawn_tile.value]}]"
                print(f"手牌: {hand_str}")

            if player.melds:
                melds_str = []
                for meld in player.melds:
                    tiles_str = " ".join(
                        [self._tile_symbols[tile.value] for tile in meld["tiles"]]
                    )
                    melds_str.append(f"[{tiles_str}]")
                print(f"副露: {' '.join(melds_str)}")

            if player.discards:
                discard_str = " ".join(
                    [self._tile_symbols[tile.value] for tile in player.discards]
                )
                print(f"牌河: {discard_str}")

        if game_state.last_action_info:
            print("\n最后动作:", end=" ")

            action_type = game_state.last_action_info.get("type")
            player_idx = game_state.last_action_info.get("player")
            action_obj = game_state.last_action_info.get("action_obj")

            if action_obj is None:
                print(str(game_state.last_action_info))
                print("=" * 50 + "\n")
                return

            if action_obj is not None and player_idx is not None:
                # 直接调用渲染方法
                last_action_str = self.render_action_to_string(action_obj, player_idx)
                print(last_action_str)
            else:
                # 如果信息不全，打印原始信息作为回退
                print(str(game_state.last_action_info))

            print("=" * 50 + "\n")

    def close(self):
        pass
