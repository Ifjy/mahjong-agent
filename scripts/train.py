"""
训练入口脚本。

用法:
  python scripts/train.py --config configs/experiment/random_baseline.yaml
  python scripts/train.py --config configs/experiment/dqn_hanchan.yaml --resume runs/exp1/ckpt/ep_10000.json
  python scripts/train.py --episodes 50  # 无 config, 用随机 baseline 默认配置

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §9。
"""

from __future__ import annotations
import os
import sys
import argparse

# 允许从 scripts/ 目录直接运行
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.config_loader import load_config, validate_config, get_default_config
from src.utils.logger import get_logger, quiet, verbose
from src.trainning.trainer import Trainer

log = get_logger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="麻将 RL 训练")
    parser.add_argument(
        "--config", type=str, default=None,
        help="实验配置 YAML 路径 (省略则用随机 baseline 默认配置)",
    )
    parser.add_argument(
        "--episodes", type=int, default=None,
        help="覆盖配置中的 total_episodes",
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="从 checkpoint 恢复 (路径)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="详细日志 (DEBUG 级别, 调试规则时用)",
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="批量并行训练 (DQN, 多env采集+批量推理)",
    )
    parser.add_argument(
        "--num_envs", type=int, default=8,
        help="并行训练的 env 数量 (仅 --parallel)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # 日志级别: 默认 INFO (训练进度可见, env 内部 print 屏蔽); --verbose 开 DEBUG
    if args.verbose:
        verbose()

    # 加载配置
    if args.config:
        config = load_config(args.config)
        log.info("加载配置: %s", args.config)
    else:
        # 无 config: 默认随机 baseline (快速闭环验证)
        config = get_default_config()
        config["experiment"] = {"name": "default_random", "seed": 42, "log_dir": "runs"}
        config["algo"] = "random"
        config["agents"] = ["random"] * 4
        config["reward"] = {"mode": "hybrid", "placement_rewards": [1.0, 0.3, -0.3, -1.0],
                            "score_alpha": 0.5, "score_normalize": 10000}
        log.info("无 config 参数, 使用随机 baseline 默认配置")

    # 命令行覆盖
    if args.episodes is not None:
        config.setdefault("experiment", {})["total_episodes"] = args.episodes

    # 校验
    config = validate_config(config)

    total_episodes = config.get("experiment", {}).get("total_episodes", 100)
    log.info(
        "开始训练: %s | episodes=%d | agents=%s",
        config.get("experiment", {}).get("name"),
        total_episodes,
        config.get("agents"),
    )

    # 训练
    trainer = Trainer(config)
    if args.parallel:
        print(f"启动批量并行训练: num_envs={args.num_envs}, episodes={total_episodes}", flush=True)
        trainer.train_parallel(
            total_episodes=total_episodes, num_envs=args.num_envs,
            resume_from=args.resume,
        )
    else:
        trainer.train(total_episodes=total_episodes, resume_from=args.resume)

    log.info("输出目录: %s", trainer.log_dir)


if __name__ == "__main__":
    main()
