import os
import sys

# 允许从 scripts/ 目录直接运行
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.env.mahjong_env import MahjongEnv


def build_default_config():
    return {
        "num_players": 4,
        "initial_score": 25000,
        "render": False,
        "state_encoder_config": {
            "max_actions": 100,
            "action_feature_dim": 128,
        },
    }


def main():
    # 初始化环境
    env = MahjongEnv(build_default_config())

    # 示例：打印环境的状态空间和动作空间
    print("Observation Space:", env.observation_space)
    print("Action Space:", env.action_space)

    # 示例：运行一个回合
    obs, info = env.reset()
    terminated = False
    truncated = False
    while not (terminated or truncated):
        valid_actions = info.get("valid_actions", [])
        if not valid_actions:
            break

        action_idx = 0  # 使用候选动作索引
        obs, reward, terminated, truncated, info = env.step(action_idx)
        print(
            f"Action Index: {action_idx}, Reward: {reward}, "
            f"Terminated: {terminated}, Truncated: {truncated}"
        )


if __name__ == "__main__":
    main()
