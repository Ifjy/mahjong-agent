import sys
from typing import List, Dict, Optional

# --- 核心组件导入 ---
# 假设这些文件都在 'src/env/core' 或 'src/env/core/rules' 中
from src.env.core.game_state import GameState
from src.env.core.rules.rules_engine import RulesEngine
from src.env.core.game_state import Wall  # 假设 Wall 在 core 中
from src.env.core.game_state import PlayerState  # 假设 PlayerState 在 core 中
from src.env.core.actions import Action, ActionType, KanType
from src.env.core.game_state import GamePhase


class GameController:
    """
    (协调器/大脑)
    拥有并管理 GameState 和 RulesEngine。
    不直接与 AI/Agent 交互，而是由 MahjongEnv 来调用。
    负责执行动作、处理自动化的游戏流程（状态机）。
    """

    def __init__(self, config: Dict):
        """
        初始化控制器。

        Args:
            config (Dict): 游戏配置。
        """
        self.config = config

        # 1. 实例化无状态的规则引擎
        self.rules_engine = RulesEngine()

        # 2. 实例化数据容器 (Wall 和 GameState)
        self.wall = Wall()  # 假设 Wall 可以这样初始化
        self.gamestate = GameState(config=self.config, wall=self.wall)

        # 3. 存储响应阶段的临时状态 (从 GameState 移出)
        self._response_declarations: Dict[int, Action] = {}
        self._responders_to_prompt: List[int] = []

    def start_game(self):
        """
        重置并开始一场全新的游戏 (例如，东一局)。
        由 MahjongEnv.reset() 调用。
        """
        self.gamestate.reset_game()
        self._start_new_hand_deal()

    def _start_new_hand_deal(self):
        """
        (流程) 开始一个新局：重置数据、洗牌、发牌。
        修正：改为一张一张轮流发牌，并直接修改 GameState，不使用 DRAW Action。
        """
        # 1. 重置数据 (调用 GameState 的纯数据重置)
        self.gamestate.reset_new_hand()

        print("GameController: 正在发牌...")
        self.gamestate.game_phase = GamePhase.DEALING

        # --- 2. 修正后的发牌流程：一张一张轮流发牌 (13张/人) ---
        try:
            num_players = self.gamestate.num_players  # 通常为 4
            tiles_per_player = 13
            total_initial_tiles = num_players * tiles_per_player

            # 循环 52 次，每轮发一张牌
            for tile_index in range(total_initial_tiles):
                # 确定当前牌应该发给哪个玩家： 0, 1, 2, 3, 0, 1, ...
                current_player_idx = tile_index % num_players

                tile = self.wall.draw_tile()  # 返回 Tile 对象
                if tile is None:
                    raise ValueError(
                        f"发牌过程中（第 {tile_index + 1} 张牌）牌墙不足！"
                    )

                # 直接将牌（Tile 对象）加入到对应玩家的手牌中
                self.gamestate.players[current_player_idx].hand.append(tile)

            # 3. 初始发牌完成后，对手牌进行排序 (重要)
            for player in self.gamestate.players:
                player.hand.sort()

            # --- 4. 庄家摸第14张牌 (直接状态修改) ---
            dealer_idx = self.gamestate.dealer_index
            tile = self.wall.draw_tile()  # 返回 Tile 对象
            if tile is None:
                raise ValueError("庄家摸初始牌时牌墙不足！")

            # 直接将第14张牌添加到庄家手牌中
            self.gamestate.players[dealer_idx].hand.append(tile)
            self.gamestate.players[dealer_idx].hand.sort()

        except ValueError as e:
            print(f"发牌时出错: {e}")
            self.gamestate._hand_over_flag = True
            self.gamestate.game_phase = GamePhase.HAND_OVER_SCORES
            return

        # 5. 设置初始阶段和玩家
        self.gamestate.current_player_index = self.gamestate.dealer_index
        self.gamestate.game_phase = GamePhase.PLAYER_DISCARD

        print(
            f"GameController: 发牌完成，轮到玩家 {self.gamestate.current_player_index}。"
        )

    def apply_action(self, action: Action):
        """
        (核心入口) 应用一个玩家的决策动作。
        由 MahjongEnv.step() 调用。

        Args:
            action (Action): 玩家选择的 *合法* 动作。
        """
        if self.gamestate._game_over_flag:
            print("警告: 游戏已结束，无法应用动作。")
            return

        print(
            f"GameController: 正在应用动作 {action.type.name} (来自玩家 {action.player_index})"
        )

        # 1. 将动作应用到数据层
        self.gamestate.apply_action(action)

        # 2. (关键) 运行所有由此动作触发的 *自动* 阶段
        self._run_automatic_phases(applied_action=action)

    def _run_automatic_phases(self, applied_action: Action):
        """
        (核心流程) 处理一个动作后所有自动发生的连锁反应。
        循环运行，直到游戏再次需要玩家输入。
        """
        # 保护性循环，防止无限循环
        for _ in range(self.gamestate.num_players + 5):

            # A. 检查本局是否结束
            if self.gamestate._hand_over_flag:
                self._process_hand_end()

                # A.1 检查整场游戏是否结束
                if self.gamestate._game_over_flag:
                    self.gamestate.game_phase = GamePhase.GAME_OVER
                    print("GameController: 游戏结束。")
                    break  # 停止所有流程

                # A.2 自动开始新的一局
                print("GameController: 自动开始新的一局...")
                self._start_new_hand_deal()
                # 新局开始后，必定轮到玩家决策，所以可以 break
                break

            # B. 检查当前是否需要玩家输入
            current_phase = self.gamestate.game_phase
            if current_phase in (
                GamePhase.PLAYER_DISCARD,
                GamePhase.WAITING_FOR_RESPONSE,
            ):
                # 游戏进入等待玩家决策阶段，停止自动流程
                print(
                    f"GameController: 停止自动流程，等待玩家 {self.gamestate.current_player_index} 在 {current_phase.name} 阶段决策。"
                )
                break

            # C. 处理各种自动阶段

            # C.1 处理“刚刚弃牌” (或加杠) 后的响应检查
            # applied_action 是 *上一次* 刚应用的动作
            if (
                applied_action
                and applied_action.type in (ActionType.DISCARD, ActionType.RIICHI)
                or (
                    applied_action.type == ActionType.KAN
                    and applied_action.kan_type == KanType.ADDED
                )
            ):

                is_kan_response = applied_action.type == ActionType.KAN
                self._check_for_responses(is_kan_response)
                applied_action = None  # 清除 action，防止重复处理
                continue  # 立即开始下一次循环 (此时 phase 可能是 WAITING_FOR_RESPONSE 或 AUTO_DRAW)

            # C.2 处理“PASS”动作
            elif applied_action and applied_action.type == ActionType.PASS:
                self._handle_pass(applied_action)
                applied_action = None
                continue

            # C.3 处理自动摸牌阶段
            elif self.gamestate.game_phase == GamePhase.AUTO_DRAW:
                self._transition_to_next_draw()
                # _transition_to_next_draw 会应用一个 DRAW 动作并设置
                # game_phase = PLAYER_DISCARD，
                # 所以下一次循环 B 会
                continue

            # C.4 处理其他自动阶段 (例如刚吃碰杠后)
            # (这个逻辑已合并到 apply_action 和 _check_for_responses 中)
            elif applied_action:
                # 如果是一个非弃牌、非Pass的动作（如DRAW, PON, CHI, KAN）
                # 它们的结果是让当前玩家进入 PLAYER_DISCARD 阶段
                # 这个阶段会在循环 B 中被捕获并停止
                self.gamestate.game_phase = GamePhase.PLAYER_DISCARD
                applied_action = None
                continue

        else:
            print("严重错误: GameController._run_automatic_phases 循环次数过多！")
            # 强制设为错误状态
            self.gamestate.game_phase = GamePhase.GAME_OVER

    def _check_for_responses(self, is_kan_response: bool = False):
        """(流程) 检查对弃牌或加杠的响应。"""
        print("GameController: 检查响应...")

        # 1. 询问规则引擎
        responses = self.rules_engine.get_potential_responses(
            self.gamestate, is_kan_response
        )

        if responses:
            # 2. 有人可以响应
            print(f"GameController: 发现响应 {responses}")
            self.gamestate.game_phase = GamePhase.WAITING_FOR_RESPONSE

            # 建立响应队列 (这部分逻辑在 RulesEngine 中)
            self._responders_to_prompt = self.rules_engine.build_response_queue(
                self.gamestate, responses
            )
            self._response_declarations.clear()  # 清空上一轮的声明

            # 设置当前玩家为第一个需要响应的人
            self.gamestate.current_player_index = self._responders_to_prompt[0]

        else:
            # 3. 无人响应
            print("GameController: 无人响应。")
            if is_kan_response:
                # 如果是加杠无人抢，则杠牌者继续摸岭上牌后的弃牌
                self.gamestate.game_phase = GamePhase.PLAYER_DISCARD
                # current_player_index 已经在 apply_action(KAN) 时设为杠牌者
            else:
                # 如果是弃牌无人要，则进入自动摸牌阶段
                self.gamestate.game_phase = GamePhase.AUTO_DRAW

    def _handle_pass(self, pass_action: Action):
        """(流程) 处理一个 'PASS' 声明。"""
        responder_idx = pass_action.player_index
        print(f"GameController: 玩家 {responder_idx} 声明 PASS。")

        # 1. 记录声明
        self._response_declarations[responder_idx] = pass_action

        # 2. 从队列中移除
        if (
            self._responders_to_prompt
            and self._responders_to_prompt[0] == responder_idx
        ):
            self._responders_to_prompt.pop(0)
        else:
            print(f"警告: 响应队列管理出错, 玩家 {responder_idx} 不在队首。")

        # 3. 检查队列
        if self._responders_to_prompt:
            # 还有人需要响应，轮到下一个人
            next_responder = self._responders_to_prompt[0]
            self.gamestate.current_player_index = next_responder
            self.gamestate.game_phase = GamePhase.WAITING_FOR_RESPONSE  # 保持
        else:
            # 队列空了，解决所有响应
            print("GameController: 所有响应已收集，开始解决优先级...")
            self._resolve_responses()

    def _resolve_responses(self):
        """(流程) 在所有人都 PASS 或声明后，解决响应优先级。"""

        # 1. 询问规则引擎谁赢了
        winning_action = self.rules_engine.resolve_responses(
            self._response_declarations
        )

        # 2. 清理临时状态
        self._response_declarations.clear()
        self._responders_to_prompt.clear()

        if winning_action:
            # 3. 有人胜出 (RON, PON, KAN, CHI)
            print(
                f"GameController: 玩家 {winning_action.player_index} 的 {winning_action.type.name} 胜出。"
            )
            # 应用这个获胜的动作
            # 注意：这会触发 _hand_over_flag (如果是 RON)
            # 或者改变 current_player_index (如果是 MELD)
            self.gamestate.apply_action(winning_action)

            # 将阶段设置为 PLAYER_DISCARD (如果是 PON/CHI/KAN)
            # 或 HAND_OVER_SCORES (如果是 RON)
            # apply_action 已经设置了 _hand_over_flag，
            # 所以我们只需设置一个非等待状态，让主循环去处理
            if not self.gamestate._hand_over_flag:
                self.gamestate.game_phase = GamePhase.PLAYER_DISCARD

        else:
            # 4. 所有人都 PASS 了
            print("GameController: 所有玩家 PASS，进入自动摸牌。")
            self.gamestate.game_phase = GamePhase.AUTO_DRAW

    def _transition_to_next_draw(self):
        """(流程) 推进到下一个玩家摸牌。"""

        # 1. 检查流局 (牌山是否为空)
        if self.rules_engine.check_exhaustive_draw(self.gamestate):
            print("GameController: 牌山耗尽，荒牌流局。")
            # 设置流局状态
            self.gamestate._hand_over_flag = True
            self.gamestate.hand_outcome_info_temp = {
                "type": "EXHAUSTIVE_DRAW",
                "reason": "牌山耗尽",
            }
            self.gamestate.game_phase = GamePhase.HAND_OVER_SCORES
            return  # 停止摸牌

        # 2. 确定下一个摸牌的玩家
        # (注意：吃碰杠后，last_discard_player_index 不会变，所以+1是正确的)
        next_player_idx = (
            self.gamestate.last_discard_player_index + 1
        ) % self.gamestate.num_players

        # 3. 从牌山摸牌
        tile_to_draw = self.wall.draw_tile()
        if tile_to_draw is None:
            # 理论上不应发生，因为 check_exhaustive_draw 检查过了
            print("严重错误: _transition_to_next_draw 摸牌失败！")
            self.gamestate._hand_over_flag = True
            return

        print(f"GameController: 轮到玩家 {next_player_idx} 摸牌。")

        # 4. 创建并应用 DRAW 动作
        draw_action = Action(
            type=ActionType.DRAW, player_index=next_player_idx, tile=tile_to_draw
        )
        self.gamestate.apply_action(draw_action)

        # 5. 更新状态
        self.gamestate.current_player_index = next_player_idx
        self.gamestate.game_phase = GamePhase.PLAYER_DISCARD  # 摸牌后进入弃牌阶段

    def _process_hand_end(self):
        """(流程) 处理一局结束后的计分和状态推进。"""

        outcome = self.gamestate.hand_outcome_info_temp
        if not outcome:
            print("严重错误: _process_hand_end 被调用，但 outcome 为空。")
            self.gamestate._game_over_flag = True  # 避免死循环
            return

        print(f"GameController: 正在处理本局结束: {outcome['type']}")

        # 1. 计算分数变化
        # (RulesEngine 负责所有计分逻辑)
        score_results = self.rules_engine.calculate_final_scores(
            self.gamestate, outcome
        )

        # 2. 应用分数变化 (数据层)
        self.gamestate.update_scores(score_results["score_changes"])

        # 3. 计算下一局的状态 (场风、庄家、本场等)
        next_hand_info = self.rules_engine.determine_next_hand_state(
            self.gamestate, outcome, score_results
        )

        # 4. 应用下一局状态 (数据层)
        self.gamestate.apply_next_hand_state(next_hand_info)

        # 5. 清理本局结束标记 (允许 _start_new_hand_deal 运行)
        self.gamestate._hand_over_flag = False
        # game_phase 会在 apply_next_hand_state 中被设置为 HAND_OVER_SCORES
        # 或者 _game_over_flag 会被设置为 True
