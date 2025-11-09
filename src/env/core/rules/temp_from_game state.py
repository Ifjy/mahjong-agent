# 1. 应该转移到 RulesEngine (规则/逻辑计算)

# 这些方法的核心是“判断”、“计算”或“生成”。它们不应该修改 GameState，而应该读取 GameState 并返回一个结果。

# _is_response_action_valid: (严重) 这是 RulesEngine 的核心职责。应变为 RulesEngine.is_action_valid(gamestate, action)。

# _build_response_prompt_queue: (严重) 这是 RulesEngine 的核心职责。应变为 RulesEngine.get_legal_actions(gamestate) 的一部分。

# _resolve_response_priorities: (严重) 这是 RulesEngine 的职责。应变为 RulesEngine.prioritize_actions(actions_list)。

# _perform_kan (及其子项): 它的校验逻辑（能否杠）和结果计算（杠了之后发生什么）属于 RulesEngine。GameState 的 apply_action 只应包含应用（例如，从手牌移除4张，增加一个明杠）。

# _perform_discard_logic: 同上，校验部分应移出。

# _apply_winning_response (及其子项): 它的分数计算、役种判断部分必须移到 RulesEngine (例如 RulesEngine.calculate_score(gamestate, win_info))。GameState 只负责应用（self.scores += ...）。

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