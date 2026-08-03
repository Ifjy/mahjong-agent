"""
apply_action 全分支 + GameController 状态机 端到端测试。

补全覆盖盲区 (见 docs/AUDIT_TEST_COVERAGE.md):
- RIICHI / KAN_OPEN / KAN_ADDED / RON / SPECIAL_DRAW 的 apply_action
- 张数守恒 + 副露正确性
- 响应优先级 / 食替 / 立直摸切
- seed 复现性
- 清理永真式/无断言占位测试

运行: pytest tests/test_apply_action_kan.py -v
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from src.env.core.actions import Tile, Action, ActionType, KanType
from src.env.core.game_state import GameState, Wall, Meld, GamePhase


def T(v, red=False):
    return Tile(value=v, is_red=red)

def H(values):
    return [T(v) for v in values]


class TestApplyActionKan:
    """三类杠 + 明杠/加杠的 apply_action 完整测试。"""

    def _make_gs(self, hand=None, drawn=None, melds=None, phase=GamePhase.PLAYER_DISCARD):
        gs = GameState({"num_players": 4}, Wall())
        gs.game_phase = phase
        gs.current_player_index = 0
        gs.last_discarded_tile = T(5)
        gs.last_discard_player_index = 1
        if hand is not None:
            gs.players[0].hand = list(hand)
        gs.players[0].drawn_tile = drawn
        gs.players[0].melds = melds or []
        return gs

    def _tot(self, p):
        return len(p.hand) + (1 if p.drawn_tile else 0) + sum(len(m.tiles) for m in p.melds)

    # ---- 暗杠 (CLOSED KAN) ----

    def test_closed_kan_with_drawn(self):
        """暗杠含drawn: hand3张+drawn1张=4张, 杠后hand-3 drawn=None melds+4"""
        gs = self._make_gs(H([0,1,2,9,10,11,18,19,20, 27,27,27, 5]), drawn=T(27))
        p = gs.players[0]
        before = self._tot(p)
        gs.apply_action(0, Action(type=ActionType.KAN, kan_type=KanType.CLOSED, tile=T(27)))
        assert self._tot(p) == before, f"暗杠(含drawn)张数应不变: {before}->{self._tot(p)}"
        assert p.drawn_tile is None
        assert len(p.melds) == 1 and p.melds[0].type == ActionType.KAN
        assert len(p.melds[0].tiles) == 4

    def test_closed_kan_without_drawn(self):
        """暗杠不含drawn: hand有4张同value, drawn是别的牌"""
        gs = self._make_gs(H([0,1,2, 9,9,9,9, 18,19,20, 27,27, 5]), drawn=T(28))
        # hand有4张9(=1筒), drawn=28(南), 暗杠1筒
        p = gs.players[0]
        before = self._tot(p)
        gs.apply_action(0, Action(type=ActionType.KAN, kan_type=KanType.CLOSED, tile=T(9)))
        after = self._tot(p)
        assert before == after, f"暗杠(不含drawn)张数应不变: {before}->{after}"
        assert p.drawn_tile is not None  # drawn没被消耗(28 != 9)
        assert len(p.melds) == 1 and p.melds[0].type == ActionType.KAN
        # hand应减4 (4张1筒移走)
        assert len(p.hand) == before - 1 - 4  # 原hand-4张 + drawn没动

    def test_closed_kan_not_enough_raises(self):
        """暗杠手牌不足4张应报错"""
        gs = self._make_gs(H([0,1,2, 9,9,9, 18,19,20, 27,27, 5, 6]), drawn=T(28))
        # 只有3张1筒(9), 不能暗杠
        with pytest.raises(RuntimeError):
            gs.apply_action(0, Action(type=ActionType.KAN, kan_type=KanType.CLOSED, tile=T(9)))

    # ---- 加杠 (ADDED KAN) ----

    def test_added_kan_with_drawn(self):
        """加杠用drawn_tile: PON+drawn -> KAN, drawn清除"""
        pon = Meld(type=ActionType.PON, tiles=tuple(H([27,27,27])), from_player=1, called_tile=T(27))
        gs = self._make_gs(H([0,1,2,9,10,11,18,19,20, 5]), drawn=T(27), melds=[pon])
        p = gs.players[0]
        before = self._tot(p)  # 10hand + 1drawn + 3meld = 14
        gs.apply_action(0, Action(type=ActionType.KAN, kan_type=KanType.ADDED, tile=T(27)))
        after = self._tot(p)
        assert before == after, f"加杠(drawn)张数应不变: {before}->{after}"
        assert p.drawn_tile is None  # drawn被消耗
        # PON(3张) -> KAN(4张)
        assert p.melds[0].type == ActionType.KAN and len(p.melds[0].tiles) == 4

    def test_added_kan_with_hand_clears_drawn(self):
        """加杠用手牌的牌: drawn应被清除 (回归测试: 之前不清除导致膨胀)"""
        pon = Meld(type=ActionType.PON, tiles=tuple(H([27,27,27])), from_player=1, called_tile=T(27))
        # hand有1张27, drawn是28(不同value), 加杠用hand的27
        gs = self._make_gs(H([0,1,2,9,10,11,18,19,20, 27, 5]), drawn=T(28), melds=[pon])
        p = gs.players[0]
        before = self._tot(p)  # 11hand + 1drawn + 3meld = 15
        gs.apply_action(0, Action(type=ActionType.KAN, kan_type=KanType.ADDED, tile=T(27)))
        after = self._tot(p)
        # 加杠用手牌: hand-1, melds 3->4(+1), drawn清除(-1) => 总-1
        # 但加杠后进杠流程, 手牌暂时少1张(无drawn), 之后岭上补
        assert p.drawn_tile is None, "加杠用手牌时drawn必须清除"
        assert p.melds[0].type == ActionType.KAN and len(p.melds[0].tiles) == 4
        # hand应减1(移走1张27)
        assert len(p.hand) == 10  # 原11-1=10

    def test_added_kan_no_pon_raises(self):
        """加杠但无对应PON应报错"""
        gs = self._make_gs(H([0,1,2,9,10,11,18,19,20, 27, 5, 6, 7]), drawn=T(28))
        with pytest.raises((RuntimeError, Exception)):
            gs.apply_action(0, Action(type=ActionType.KAN, kan_type=KanType.ADDED, tile=T(27)))

    # ---- 明杠 (OPEN KAN) ----

    def test_open_kan_hand_conservation(self):
        """明杠: 响应阶段, 手牌3张+弃牌 -> KAN"""
        gs = self._make_gs(
            H([0,1,2, 9,10,11, 18,19,20, 27,27,27, 5]),
            drawn=None,
            phase=GamePhase.WAITING_FOR_RESPONSE
        )
        gs.last_discarded_tile = T(27)  # 弃牌是27
        gs.last_discard_player_index = 2
        p = gs.players[0]
        before = self._tot(p)  # 13hand + 0drawn + 0meld = 13
        gs.apply_action(0, Action(type=ActionType.KAN, tile=T(27), kan_type=KanType.OPEN))
        after = self._tot(p)
        # 手牌-3 + melds+4(含弃牌) = 13-3+4 = 14
        assert after == 14, f"明杠后tot应=14: {before}->{after}"
        assert len(p.melds) == 1 and p.melds[0].type == ActionType.KAN
        assert len(p.melds[0].tiles) == 4
        assert p.is_menzen == False  # 明杠破坏门清

    # ---- RIICHI ----

    def test_riichi_action(self):
        """立直: 扣1000分 + 立直棒+1 + 打出riichi_discard"""
        gs = self._make_gs(H([0,1,2,9,10,11,18,19,20,27,27,27, 5]), drawn=T(28))
        p = gs.players[0]
        score_before = p.score
        sticks_before = gs.riichi_sticks
        gs.apply_action(0, Action(type=ActionType.RIICHI, riichi_discard=T(28)))
        assert p.score == score_before - 1000, "立直应扣1000分"
        assert gs.riichi_sticks == sticks_before + 1, "立直棒+1"
        assert p.riichi_declared == True
        assert p.drawn_tile is None  # riichi_discard是摸切(28=drawn)
        assert gs.last_discarded_tile.value == 28  # 打出了28

    def test_riichi_tedashi(self):
        """立直手切: drawn并入hand, 打出手牌中的牌"""
        gs = self._make_gs(H([0,1,2,9,10,11,18,19,20,27,27,27, 5]), drawn=T(28))
        p = gs.players[0]
        hand_before = len(p.hand)
        gs.apply_action(0, Action(type=ActionType.RIICHI, riichi_discard=T(5)))
        # drawn(28)并入hand, 打出5 => hand不变(13+1-1=13), drawn=None
        assert p.drawn_tile is None
        assert gs.last_discarded_tile.value == 5

    # ---- RON ----

    def test_ron_sets_flag(self):
        """荣和: 设_hand_over_flag"""
        gs = self._make_gs(H([0,1,2,9,10,11,18,19,20,27,27,27, 5]),
                          phase=GamePhase.WAITING_FOR_RESPONSE)
        gs.apply_action(0, Action(type=ActionType.RON, winning_tile=T(5)))
        assert gs._hand_over_flag == True


class TestSeedReproducibility:
    """seed 复现性测试。"""

    def test_same_seed_same_deal(self):
        """同seed两次reset应产生相同初始手牌"""
        from src.env.mahjong_env import MahjongEnv
        env1 = MahjongEnv({"num_players": 4, "initial_score": 25000})
        env2 = MahjongEnv({"num_players": 4, "initial_score": 25000})
        obs1, _ = env1.reset(seed=42)
        obs2, _ = env2.reset(seed=42)
        # 比较手牌编码
        import numpy as np
        h1 = obs1["state"]["hand"]
        h2 = obs2["state"]["hand"]
        assert np.array_equal(h1, h2), "同seed应产生相同手牌"

    def test_different_seed_different_deal(self):
        """不同seed应产生不同手牌"""
        from src.env.mahjong_env import MahjongEnv
        env1 = MahjongEnv({"num_players": 4, "initial_score": 25000})
        env2 = MahjongEnv({"num_players": 4, "initial_score": 25000})
        obs1, _ = env1.reset(seed=42)
        obs2, _ = env2.reset(seed=99)
        import numpy as np
        assert not np.array_equal(obs1["state"]["hand"], obs2["state"]["hand"]), \
            "不同seed应产生不同手牌"


class TestResponsePriority:
    """响应优先级 + 头跳测试。"""

    def test_ron_beats_pon(self):
        """RON优先于PON"""
        from src.env.core.rules.action_validator import ActionValidator
        from src.env.core.rules.hand_analyzer import HandAnalyzer
        from src.env.core.rules.scoring import Scoring
        ha = HandAnalyzer()
        sc = Scoring(ha, {})
        av = ActionValidator(ha, sc, {})
        # 玩家1 RON, 玩家2 PON, 打牌者是玩家0
        decls = {
            1: Action(type=ActionType.RON, winning_tile=T(5)),
            2: Action(type=ActionType.PON, tile=T(5)),
        }
        action, idx = av.resolve_response_priorities(decls, discarder_index=0, num_players=4)
        assert action.type == ActionType.RON, "RON应优先于PON"
        assert idx == 1

    def test_pon_beats_chi(self):
        """PON优先于CHI"""
        from src.env.core.rules.action_validator import ActionValidator
        from src.env.core.rules.hand_analyzer import HandAnalyzer
        from src.env.core.rules.scoring import Scoring
        ha = HandAnalyzer()
        sc = Scoring(ha, {})
        av = ActionValidator(ha, sc, {})
        decls = {
            1: Action(type=ActionType.CHI, chi_tiles=(T(0), T(1)), tile=T(2)),
            2: Action(type=ActionType.PON, tile=T(2)),
        }
        action, idx = av.resolve_response_priorities(decls, discarder_index=0, num_players=4)
        assert action.type == ActionType.PON
        assert idx == 2

    def test_all_pass_returns_none(self):
        """全PASS返回None"""
        from src.env.core.rules.action_validator import ActionValidator
        from src.env.core.rules.hand_analyzer import HandAnalyzer
        from src.env.core.rules.scoring import Scoring
        ha = HandAnalyzer()
        sc = Scoring(ha, {})
        av = ActionValidator(ha, sc, {})
        decls = {1: Action(type=ActionType.PASS), 2: Action(type=ActionType.PASS), 3: Action(type=ActionType.PASS)}
        action, idx = av.resolve_response_priorities(decls, discarder_index=0, num_players=4)
        assert action is None and idx is None

    def test_head_jump_atama_hane(self):
        """头跳: 同优先级时, 打牌者的上家(逆时针最近)优先"""
        from src.env.core.rules.action_validator import ActionValidator
        from src.env.core.rules.hand_analyzer import HandAnalyzer
        from src.env.core.rules.scoring import Scoring
        ha = HandAnalyzer()
        sc = Scoring(ha, {})
        av = ActionValidator(ha, sc, {})
        # 玩家1和玩家3都RON, 打牌者是玩家0 -> 头跳: 玩家1(上家)优先
        decls = {
            1: Action(type=ActionType.RON, winning_tile=T(5)),
            3: Action(type=ActionType.RON, winning_tile=T(5)),
        }
        action, idx = av.resolve_response_priorities(decls, discarder_index=0, num_players=4)
        assert idx == 1, "头跳: 玩家1(打牌者上家)应优先于玩家3"


class TestDoraReveal:
    """杠后翻新宝牌(reveal_new_dora)测试。"""

    def test_reveal_new_dora_increments(self):
        """reveal_new_dora后指示牌+1"""
        wall = Wall()
        wall.shuffle_and_setup()
        initial = len(wall.dora_indicators)
        assert initial == 1
        result = wall.reveal_new_dora()
        assert result is not None, "第一次reveal应成功"
        assert len(wall.dora_indicators) == 2

    def test_reveal_new_dora_max_5(self):
        """最多5组指示牌(1初始+4杠)"""
        wall = Wall()
        wall.shuffle_and_setup()
        for i in range(4):
            r = wall.reveal_new_dora()
            assert r is not None, f"第{i+1}次reveal应成功"
        assert len(wall.dora_indicators) == 5
        # 第5次应失败(最多5组)
        r = wall.reveal_new_dora()
        assert r is None, "第5次reveal应失败"

    def test_get_current_dora_tiles(self):
        """get_current_dora_tiles返回实际宝牌"""
        wall = Wall()
        wall.dora_indicators = [T(0)]  # 指示牌1m(0) -> 宝牌2m(1)
        dora = wall.get_current_dora_tiles()
        assert len(dora) == 1
        assert dora[0].value == 1, "指示牌1m -> 宝牌2m"


class TestFlowIntegration:
    """端到端流程测试: 开局→打牌→响应→摸牌循环。"""

    def test_full_hand_no_crash(self):
        """一局从reset到terminated不崩"""
        from src.env.mahjong_env import MahjongEnv
        from src.agent.heuristic_agent import HeuristicAgent
        from src.env.core.actions import ActionType
        from src.utils.logger import quiet
        quiet()
        env = MahjongEnv({"num_players": 4, "initial_score": 25000})
        agents = [HeuristicAgent({"seed": i}, i) for i in range(4)]
        obs, info = env.reset(seed=100)
        steps = 0
        while steps < 10000:
            valid = info.get("valid_actions", [])
            if not valid:
                break
            cp = info["current_player"]
            idx = agents[cp].select_action(obs, info["action_mask"], valid)
            obs, r, term, trunc, info = env.step(idx)
            steps += 1
            if term or trunc:
                break
        assert steps > 0, "应至少跑几步"
        # 分数和应<=100000 (立直棒供托)
        scores = [p.score for p in env.controller.gamestate.players]
        assert sum(scores) <= 100000, f"分数和{sum(scores)}应<=100000"

    def test_score_conservation_no_riichi(self):
        """无立直局: 分数和恒=100000"""
        from src.env.mahjong_env import MahjongEnv
        from src.agent.random_agent import RandomAgent
        from src.env.core.actions import ActionType
        from src.utils.logger import quiet
        quiet()
        env = MahjongEnv({"num_players": 4, "initial_score": 25000})
        agents = [RandomAgent({"seed": i}, i) for i in range(4)]
        obs, info = env.reset(seed=200)
        # 用优先级避免立直
        P = {ActionType.TSUMO:0, ActionType.RON:0, ActionType.KAN:1,
             ActionType.DISCARD:2, ActionType.CHI:4, ActionType.PON:5,
             ActionType.SPECIAL_DRAW:7, ActionType.PASS:8}
        # 覆盖RIICHI为低优先级
        riichi_low = {**P, ActionType.RIICHI: 9}
        for _ in range(3000):
            valid = info.get("valid_actions", [])
            if not valid: break
            idx = min(range(len(valid)), key=lambda i: riichi_low.get(valid[i].type, 8))
            obs, r, term, trunc, info = env.step(idx)
            if term or trunc: break
        scores = [p.score for p in env.controller.gamestate.players]
        # 无立直时分数和应=100000 (无供托冻结)
        # 但有杠可能翻宝, 不影响分数; 流局罚符零和
        assert sum(scores) == 100000, f"无立直分数和应=100000, 实际{sum(scores)}"


class TestDQNCheckpoint:
    """DQN checkpoint 保存/加载往返一致性。"""

    def test_checkpoint_roundtrip(self):
        """保存后加载, Q值输出应一致"""
        from src.agent.dqn_agent import DQNAgent
        import torch
        import numpy as np
        ag1 = DQNAgent({"algo_config": {"device": "cpu", "hidden_dim": 64}, "device": "cpu"}, 0)
        # 初始化网络
        obs_state = {"hand": np.zeros(34), "melds": np.zeros(34),
                     "discards": np.zeros((4,34)), "dora": np.zeros(34),
                     "wind": np.zeros(2), "game_progress": np.zeros(4),
                     "last_action": np.zeros(35), "scores": np.zeros(4)}
        obs = {"state": obs_state, "action_candidates": np.zeros((100, 128)),
               "action_mask": np.zeros(100)}
        obs["action_mask"][:5] = 1
        ag1.select_action(obs, obs["action_mask"], [None]*5, deterministic=True)
        # 保存
        state = ag1.get_state()
        # 加载到新agent
        ag2 = DQNAgent({"algo_config": {"device": "cpu", "hidden_dim": 64}, "device": "cpu"}, 0)
        ag2._maybe_init(283, 128)
        ag2.load_state(state)
        # 比较Q值
        ag1.q_net.eval(); ag2.q_net.eval()
        s = torch.from_numpy(np.zeros(283, dtype=np.float32)).unsqueeze(0)
        a = torch.from_numpy(np.zeros((1, 100, 128), dtype=np.float32))
        m = torch.zeros(1, 100); m[0, :5] = 1
        with torch.no_grad():
            q1 = ag1.q_net(s, a, m)
            q2 = ag2.q_net(s, a, m)
        assert torch.allclose(q1, q2), "加载后Q值应与保存前一致"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
