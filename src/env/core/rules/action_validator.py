# 负责所有动作的合法性校验和响应优先级解决。
# 类/函数,函数头,职责描述
# Class,ActionValidator,"__init__(self, hand_analyzer, config)"
# Self-Turn,"get_legal_draw_actions(self, player: PlayerState, game_state: GameState) -> List[Action]","整合 Tsumo, Riichi, Closed/Added Kan 和 Discard 动作的生成。"
# Validation,"can_tsumo(self, player: PlayerState, game_state: GameState) -> bool",检查是否符合 Tsumo 的规则（如：是否无役、振听等）。
# Validation,"can_declare_kan(self, player: PlayerState, tile: Tile, kan_type: KanType, game_state: GameState) -> bool",检查暗杠、加杠、大明杠的合法性（牌数是否足够、是否在立直后）。
# Validation,"can_declare_riichi(self, player: PlayerState, game_state: GameState) -> bool",检查立直的合法性（门清、听牌、点数足够）。
# Response,"get_legal_response_actions(self, player: PlayerState, game_state: GameState) -> List[Action]","整合 Ron, Pon, Open Kan, Chi 的动作生成。"
# Priority,"resolve_response_priorities(self, declarations: Dict[int, Action], discarder_index: int) -> Tuple[Optional[Action], Optional[int]]",根据优先级 (Ron > Kan/Pon > Chi) 确定唯一的获胜响应动作和玩家。 （来自 temp_from_game state.py）
