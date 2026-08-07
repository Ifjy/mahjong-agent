"""
BC → RL 桥接: 把行为克隆的权重转成 DQNAgent 可 resume 的 checkpoint
====================================================================

train_bc.py 保存的是 {model_state_dict, state_dim, ...} 格式 (仅 q_net).
DQNAgent.get_state() 期望 {q_net, target_net, state_dim, action_dim, ...}.
本脚本把 BC 权重包装成完整 RL checkpoint, 使 train.py --resume 可直接加载,
从而以 BC 预训练权重作为 RL 微调的起点。

用法:
    python scripts/bc_to_rl.py --bc data/models/bc/bc_best.pt --out data/models/bc/bc_rl_init.pt
    python scripts/train.py --resume data/models/bc/bc_rl_init.pt  # 接着 RL 微调
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def convert(bc_path: Path, out_path: Path):
    bc = torch.load(bc_path, map_location="cpu", weights_only=False)
    print(f"[bc_to_rl] 加载 BC: {bc_path.name}")
    print(f"  state_dim={bc.get('state_dim')}, action_dim={bc.get('action_dim')}, "
          f"hidden_dim={bc.get('hidden_dim')}, top1_acc={bc.get('top1_acc'):.4f}")

    rl_state = {
        "agent_id": 0,
        "initialized": True,
        "state_dim": bc["state_dim"],
        "action_dim": bc["action_dim"],
        "q_net": bc["model_state_dict"],
        "target_net": bc["model_state_dict"],   # target 同步 q_net
        "train_steps": 0,
    }

    # RL checkpoint 通常还包 episode/step 等训练状态, train.py 的 _resume 会读
    # 看是否需要额外字段
    rl_ckpt = {
        "episode": 0,
        "global_step": 0,
        "agents": [rl_state],     # train_parallel 用 agents[0]
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(rl_ckpt, out_path)
    print(f"[bc_to_rl] 写入 RL checkpoint: {out_path}")
    print(f"[bc_to_rl] 现在可运行: python scripts/train.py --resume {out_path}")


def main():
    ap = argparse.ArgumentParser(description="BC 权重转 RL checkpoint")
    ap.add_argument("--bc", required=True, help="train_bc.py 输出的 bc_best.pt")
    ap.add_argument("--out", default=str(ROOT / "data" / "models" / "bc" / "bc_rl_init.pt"),
                    help="输出 RL checkpoint 路径")
    args = ap.parse_args()
    convert(Path(args.bc), Path(args.out))


if __name__ == "__main__":
    main()
