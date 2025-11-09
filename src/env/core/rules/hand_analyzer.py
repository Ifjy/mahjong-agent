#负责对手牌的结构进行分析：和牌形状判断、听牌判断、面子分解。
    def _check_basic_win_shape(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """
        检查给定的手牌和副露组合是否构成和牌型 (标准型、七对子、国士无双)。

        Args:
            hand_tiles (List[Tile]): 玩家的隐藏手牌列表 (可能包含刚摸到/荣和的牌)。
                                      对于门清听牌检查，这里是13张。
                                      对于和牌检查，这里包含和牌的那张，总共应构成14张牌（加上副露）。
            melds (List[Meld]): 玩家已公开的副露列表。

        Returns:
            bool: 如果构成和牌型，返回 True，否则返回 False。
        """
        all_tiles_in_hand = hand_tiles + [
            tile for meld in melds for tile in meld["tiles"]
        ]
        total_tile_count = len(all_tiles_in_hand)

        # --- 基本检查：和牌必须是14张牌 ---
        if total_tile_count != 14:
            # print(f"Debug check_win: 牌数 {total_tile_count} 不等于 14，无法和牌。")
            return False

        # --- 特殊牌型检查 (仅在门清时可能) ---
        is_menzen = not melds  # 是否门清
        if is_menzen:
            # 检查国士无双 (需要 14 张手牌)
            if self._is_kokushi_raw(hand_tiles, melds):
                # print("Debug check_win: 检测到国士无双")
                return True
            # 检查七对子 (需要 14 张手牌，考虑赤牌)
            if self._is_seven_pairs_raw(hand_tiles, melds):
                # print("Debug check_win: 检测到七对子")
                return True

        # --- 标准牌型检查 (4面子 + 1雀头) ---
        # 使用 Counter 按牌值统计所有14张牌的数量 (忽略赤牌进行结构检查)
        # 注意：Meld 中的牌也需要包含进来统计
        all_tile_values = [tile.value for tile in all_tiles_in_hand]
        value_counts: TypingCounter[int] = Counter(all_tile_values)

        # 尝试将每个出现次数 >= 2 的牌值作为雀头 (对子)
        for pair_value in list(
            value_counts.keys()
        ):  # 使用 list 避免在迭代中修改 counter
            if value_counts[pair_value] >= 2:
                # 1. 复制当前的牌值计数
                remaining_counts = value_counts.copy()

                # 2. 从计数中移除雀头 (两张)
                remaining_counts[pair_value] -= 2
                if remaining_counts[pair_value] == 0:
                    del remaining_counts[pair_value]  # 移除数量为0的键

                # 3. 计算还需要组成多少个面子 (总共4个)
                melds_needed = 4

                # 4. 调用递归函数检查剩余的牌是否能组成所需数量的面子
                # print(f"Debug check_win: 尝试以 {pair_value} 作为雀头, 检查剩余牌: {remaining_counts}, 需要面子: {melds_needed}")
                if self._find_standard_melds(remaining_counts, melds_needed):
                    # print(f"Debug check_win: 成功找到以 {pair_value} 为雀头的标准和牌型")
                    return True  # 如果找到一种合法的分解方式，则手牌有效

        # 如果尝试了所有可能的雀头都无法构成 4 面子，则不是标准和牌型
        # print("Debug check_win: 未找到有效的标准和牌分解")
        return False

    def _is_standard_hand_recursive(
        self, hand_tiles: List[Tile], melds: List[Meld]
    ) -> bool:
        """(占位符) 递归检查标准型 4面子+1雀头"""
        # TBD: 实现复杂的递归分解算法
        # print("Debug: _is_standard_hand_recursive - 占位符返回 False")
        return False

    def _is_seven_pairs_raw(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """检查七对子 (考虑赤牌)"""
        if melds:
            return False
        if len(hand_tiles) != 14:
            return False
        counts: TypingCounter[Tile] = Counter(
            hand_tiles
        )  # 使用 Tile 对象计数，区分赤牌
        # 七对子必须正好有7种不同的牌，且每种牌恰好有2张
        return len(counts) == 7 and all(c == 2 for c in counts.values())

    def _is_kokushi_raw(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """检查国士无双 (十三幺)"""
        if melds:
            return False
        if len(hand_tiles) != 14:
            return False

        # 国士无双必须包含所有13种幺九牌，其中一种是 对子
        hand_values = {t.value for t in hand_tiles}
        counts = Counter(t.value for t in hand_tiles)  # 按牌值计数

        # 检查是否包含所有幺九牌
        if not self.terminal_honor_values.issubset(hand_values):
            return False

        # 检查是否只有一个对子，其他都是单张
        pair_count = 0
        single_count = 0
        has_non_terminal = False
        for val, count in counts.items():
            if val in self.terminal_honor_values:
                if count == 2:
                    pair_count += 1
                elif count == 1:
                    single_count += 1
                else:  # 幺九牌数量不能 > 2 或 < 1
                    return False
            else:  # 不能包含非幺九牌
                has_non_terminal = True
                break  # 提前退出

        if has_non_terminal:
            return False

        # 必须是1个对子 + 12个单张幺九牌
        return pair_count == 1 and single_count == 12

    def is_tenpai(self, hand_tiles: List[Tile], melds: List[Meld]) -> bool:
        """检查给定的13张牌组合是否听牌"""
        current_tiles_count = len(hand_tiles) + sum(len(m["tiles"]) for m in melds)
        if current_tiles_count != 13:
            # print(f"Debug is_tenpai: 牌数 {current_tiles_count} 不等于 13，无法判断听牌。")
            return False  # 听牌检查基于13张牌

        # 尝试加入所有可能的牌 (0-33)，看是否能和牌
        possible_tile_values = set(range(34))  # 所有基础牌值

        # 优化：如果手牌+副露中某种牌已经有4张，则不可能再摸到第5张来和牌
        all_current_tiles = hand_tiles + [t for m in melds for t in m["tiles"]]
        current_value_counts = Counter(t.value for t in all_current_tiles)
        for val, count in current_value_counts.items():
            if count >= 4:
                possible_tile_values.discard(val)  # 移除不可能摸到的牌

        for potential_tile_value in possible_tile_values:
            # 假设用非赤牌测试 (通常足够判断是否听牌结构)
            test_tile = Tile(value=potential_tile_value, is_red=False)
            # 使用 check_win 判断加入这张牌后是否和牌
            if self._check_basic_win_shape(hand_tiles + [test_tile], melds):
                # print(f"Debug is_tenpai: 加入 {test_tile} 可和牌，判定为听牌。")
                return True  # 只要有一种牌能和，就是听牌

        # print("Debug is_tenpai: 尝试所有可能牌后均无法和牌，判定为未听牌。")
        return False

    def is_hand_over(self, game_state: "GameState") -> bool:
        """
        判断当前游戏状态是否表示一局游戏已经结束 (有人和牌或流局)。
        这个方法由环境 (MahjongEnv.step) 调用。
        """
        # 一局游戏结束的标志通常是 GameState 的 game_phase 转换到了某个结束阶段。
        # GameState 的 apply_action 或其辅助方法 (如 _transition_to_scoring, _transition_to_abortive_draw)
        # 负责在和牌或流局发生时将 game_phase 设置为 GamePhase.HAND_OVER_SCORES。
        # 因此 RulesEngine 只需检查这个阶段标志即可。

        return game_state.game_phase == GamePhase.HAND_OVER_SCORES

        # 注意：如果你的 GameState 中有一个 _hand_over_flag，并且 apply_action 负责设置它，
        # 那么这里也可以检查 return game_state._hand_over_flag。
        # 推荐的做法是 game_phase 作为主要的状态标志。

    def is_game_over(self, game_state: "GameState") -> bool:
        """
        判断整场游戏是否已结束。包含各种游戏结束条件。
        这个方法由环境 (MahjongEnv.step) 调用。
        它在 determine_next_hand_state 和 apply_next_hand_state 之后被调用，
        所以 game_state 中的轮次、场风、庄家等已经是下一局的值了。
        """
        # 检查游戏结束条件基于 GameState 的当前状态 (注意，此时的状态是下一局的状态)

        # 1. 检查是否完成预定场数 (例如，假设打半庄，完成南4局)
        # 假设游戏在完成设定的最后一场风的最后一局，并且庄家轮换时结束。
        # 如果计算出的下一局状态的场风超过了游戏设定的最大场风，通常意味着游戏结束。
        # 假设游戏只打东风场和南风场 (半庄)， ROUND_WIND_SOUTH 是最后一个场风。
        max_game_round_wind = ROUND_WIND_SOUTH  # 半庄结束在南风场结束后

        # 如果下一局的场风超过了最大允许场风，并且是新场风的第一局 (next_round_number == 1)
        # 这通常意味着前一局是最后一个场风的最后一局，并且庄家轮换了。
        if game_state.round_wind > max_game_round_wind and game_state.round_number == 1:
            print("Debug Game Over: 完成最后一场风，游戏结束。")
            # TODO: 如果打全庄 (一庄)，这里需要检查西风场和北风场
            return True  # 完成最后一场风且庄家轮换

        # 2. 检查是否有人被飞 (分数<0)
        for player in game_state.players:
            # 确保 PlayerState 类有 score 属性
            if player.score < 0:
                print(
                    f"Debug Game Over: 玩家 {player.player_index} 分数飞了 ({player.score})，游戏结束。"
                )
                return True  # 有玩家分数低于0

        # 3. (可选) 检查是否达到设定的局数上限或时间上限
        # 如果你的游戏有固定的总局数限制，可以在这里检查
        # 例如: if game_state.total_hands_played >= self.config.get("max_hands", 8): return True

        # 4. (可选) 检查复杂的结束条件，例如南四局庄家点数等
        # 规则: 南四局庄家和牌不结束，闲家和牌或庄家流局未听牌结束
        # 如果你实现了 determines_next_hand_state 中的南四局庄家连庄逻辑，
        # 那么在南四局庄家连庄时，round_wind 和 round_number 都会保持不变。
        # is_game_over 此时不应返回 True。它应该在闲家和牌或庄家流局未听牌导致庄家轮换时，
        # 并且 round_wind 和 round_number 尝试前进时（即计算出的下一局是南5局或进入西1局时）返回 True。
        # 上面的 "完成最后一场风" 判断已经涵盖了庄家轮换导致进入下一场风时结束的逻辑。

        # 如果以上游戏结束条件都不满足
        return False
