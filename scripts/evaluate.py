"""
评估脚本: 加载训练好的 DQN, 与基线 agent 对打, 统计顺位/胜率。

用法:
  python scripts/evaluate.py --ckpt runs/dqn_hanchan/ckpt/ep_200.pt --opponent heuristic --games 20
  python scripts/evaluate.py --ckpt ... --opponent random --games 50
"""

from __future__ import annotations
import os
import sys
import argparse
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import quiet
from src.utils.config_loader import get_default_config
from src.env.mahjong_env import MahjongEnv
from src.agent.registry import build_agent
from src.agent.dqn_agent import DQNAgent
from src.env.core.actions import ActionType

# 启发式/随机的动作优先级
_P = {
    ActionType.TSUMO: 0, ActionType.RON: 0, ActionType.KAN: 1,
    ActionType.DISCARD: 2, ActionType.RIICHI: 3, ActionType.CHI: 4,
    ActionType.PON: 5, ActionType.SPECIAL_DRAW: 7, ActionType.PASS: 8,
}


def evaluate(ckpt_path: str, opponent: str, games: int, seat: int = 0):
    """DQN (座位 seat) vs 3 个 opponent。"""
    quiet()
    env = MahjongEnv(get_default_config())
    # 加载 DQN (兼容 train.py 的 agents[] 和 bc_to_rl.py 的 agents[] 格式,
    # 以及旧版 agent 单字段格式)
    import torch
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    if "agent" in ckpt:
        agent_state = ckpt["agent"]
    elif "agents" in ckpt:
        agent_state = ckpt["agents"][0]
    elif "model_state_dict" in ckpt:   # BC 原始格式 (bc_best.pt)
        agent_state = {
            "initialized": True,
            "state_dim": ckpt["state_dim"],
            "action_dim": ckpt["action_dim"],
            "q_net": ckpt["model_state_dict"],
            "target_net": ckpt["model_state_dict"],
            "train_steps": 0,
        }
    else:
        raise ValueError(f"checkpoint {ckpt_path} 格式未知: {list(ckpt.keys())}")
    dqn = DQNAgent({"algo_config": {"device": "cpu", "hidden_dim": 256}, "device": "cpu"}, seat)
    dqn.load_state(agent_state)
    dqn.train_mode = False

    # 3 个对手 (用真正的 agent, 不是固定优先级)
    opps = [build_agent(opponent, {"seed": 100 + i}, i) for i in range(1, 4)]
    # agents[seat] = dqn, 其它 = opponent
    agents = [None] * 4
    agents[seat] = dqn
    oi = 0
    for i in range(4):
        if i != seat:
            agents[i] = opps[oi]; oi += 1

    ranks = []        # DQN 的顺位 (1=头名)
    dqn_scores = []
    for g in range(games):
        obs, info = env.reset(seed=50000 + g)
        while True:
            cp = info.get("current_player", 0)
            valid = info.get("valid_actions", [])
            if not valid:
                break
            ag = agents[cp]
            if cp == seat:
                idx = ag.select_action(obs, info["action_mask"], valid, deterministic=True)
            else:
                # 对手用自己的策略
                idx = ag.select_action(obs, info["action_mask"], valid, deterministic=True)
            obs, r, term, trunc, info = env.step(idx)
            if term or trunc:
                break
        # 计算顺位
        scores = info.get("final_scores", [p.score for p in env.controller.gamestate.players])
        dqn_scores.append(scores[seat])
        order = sorted(range(4), key=lambda i: -scores[i])
        ranks.append(order.index(seat) + 1)

    ranks = np.array(ranks)
    avg_rank = ranks.mean()
    top1 = (ranks == 1).mean() * 100
    top2 = (ranks <= 2).mean() * 100
    last4 = (ranks == 4).mean() * 100
    avg_score = np.mean(dqn_scores)

    print("=" * 50)
    print("评估结果: DQN(ep200) vs %s x3" % opponent)
    print("=" * 50)
    print("  对局数:        %d" % games)
    print("  平均顺位:      %.2f (随机基线=2.5)" % avg_rank)
    print("  头名率(top1):  %.1f%% (随机=25%%)" % top1)
    print("  上位率(top2):  %.1f%% (随机=50%%)" % top2)
    print("  末位率(last):  %.1f%% (随机=25%%)" % last4)
    print("  平均点数:      %.0f" % avg_score)
    print("  顺位分布:      1名=%d 2名=%d 3名=%d 4名=%d" % (
        (ranks == 1).sum(), (ranks == 2).sum(), (ranks == 3).sum(), (ranks == 4).sum()
    ))
    verdict = "学到策略 ✓" if avg_rank < 2.4 else ("无显著提升" if avg_rank < 2.6 else "比随机还差 ✗")
    print("  结论:          %s" % verdict)
    print("=" * 50)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="评估 DQN agent")
    p.add_argument("--ckpt", required=True)
    p.add_argument("--opponent", default="heuristic", choices=["heuristic", "random"])
    p.add_argument("--games", type=int, default=20)
    p.add_argument("--seat", type=int, default=0)
    args = p.parse_args()
    evaluate(args.ckpt, args.opponent, args.games, args.seat)
