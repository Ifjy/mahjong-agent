import sys
import os
import time
from typing import List

# 添加项目根目录到 Python 模块搜索路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.env.mahjong_env import MahjongEnv
from src.env.core.actions import ActionType, Action


def main():
    # 创建配置
    config = {
        "num_players": 4,
        "initial_score": 25000,
        "render": True,
        "render_mode": "human",  # 或 "text"
        "game_rule_config": {
            # 游戏规则配置
        },
        "state_encoder_config": {
            "max_actions": 30,  # 最大候选动作数
            "action_feature_dim": 128,  # 动作特征维度>114
        },
    }

    # 创建环境
    env = MahjongEnv(config)

    # 重置环境
    observation, info = env.reset()
    env.render()

    terminated = False
    truncated = False
    total_reward = 0

    print("开始测试对局...")

    while not (terminated or truncated):
        current_player = info["current_player"]
        print(f"\n玩家 {current_player} 的回合")

        # 获取合法动作信息
        valid_actions: List[Action] = info["valid_actions"]
        action_mask = info["action_mask"]

        # 打印可用动作
        print(f"可选动作({len(valid_actions)}个):")
        for i, action in enumerate(valid_actions):
            action_str = env.render.render_action_to_string(action)
            print(f"{i}: {action_str}")

        # --- 改进的简单AI：选择逻辑 ---
        chosen_index = -1  # 初始化为无效索引

        if not valid_actions:
            # 如果没有合法动作，这通常是规则引擎的问题
            print("错误：规则引擎未生成任何合法动作。终止对局。")
            terminated = True  # 设置终止标志，结束游戏循环
            # 不执行 env.step，直接进入下一循环迭代检查 terminated
            continue  # 跳过当前循环剩余部分

        # 如果有合法动作，尝试找到第一个非PASS动作
        pass_index = -1
        for i, action in enumerate(valid_actions):
            if action.type != ActionType.PASS:
                chosen_index = i  # 找到第一个非PASS动作，选它
                break  # 退出循环
            elif action.type == ActionType.PASS:
                pass_index = i  # 记下PASS动作的索引，以防没有其他动作

        # 如果循环结束仍未找到非PASS动作 (chosen_index 仍是 -1)，并且有PASS动作，则选择PASS
        if chosen_index == -1 and pass_index != -1:
            chosen_index = pass_index

        # 如果仍然没有选定有效动作 (这应该只发生在 valid_actions 有元素但既没有非PASS也没有PASS的情况下，极不可能)
        if chosen_index == -1:
            print("警告：未能从合法动作列表中选定动作。默认选择第一个动作。")
            chosen_index = 0  # 作为最后的备用方案，选择第一个动作
        chosen_action = valid_actions[chosen_index]
        chosen_action_str = env.render.render_action_to_string(
            chosen_action
        )  # 调用渲染方法
        print(f"\n执行动作: {chosen_action_str}")
        # --- 结束改进的简单AI选择逻辑 ---

        # 执行动作
        observation, reward, terminated, truncated, info = env.step(chosen_index)
        total_reward += reward

        # 渲染
        env.render()

        # 小延迟方便观察
        time.sleep(0.2)

    print("\n对局结束!")
    print(f"总奖励: {total_reward}")
    env.close()


if __name__ == "__main__":
    main()
