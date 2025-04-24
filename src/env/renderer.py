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
            tile_obj = game_state.last_action_info.get("tile")

            if action_type in {"DISCARD", "RIICHI", "DRAW"}:
                if tile_obj is not None:
                    tile_symbol = self._tile_symbols[tile_obj.value]
                    if action_type == "DISCARD":
                        print(f"玩家{player_idx} 打出 {tile_symbol}")
                    elif action_type == "RIICHI":
                        print(f"玩家{player_idx} 立直 打出 {tile_symbol}")
                    elif action_type == "DRAW":
                        print(f"玩家{player_idx} 摸牌 {tile_symbol}")
                elif action_type == "DRAW":
                    print(f"玩家{player_idx} 摸牌（流局）")
            elif action_type in {"TSUMO", "RON"}:
                print(f"玩家{player_idx} {action_type}")
            else:
                print(str(game_state.last_action_info))

        print("=" * 50 + "\n")

    def close(self):
        pass
