import sys
import os
import time
from typing import List, Dict, Any

# 添加项目根目录到 Python 模块搜索路径（保持不变）
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import necessary components from the Mahjong environment
from src.env.mahjong_env import MahjongEnv
from src.env.core.actions import ActionType, Action


def main():
    # --- 1. Configuration Setup (配置设置) ---
    config = {
        "num_players": 4,
        "initial_score": 25000,
        "render": True,
        "render_mode": "human",  # 或 "text"
        "game_rule_config": {
            # 游戏规则配置。假设 MahjongEnv.reset() 现在负责处理所有发牌逻辑，
            # 即使需要分步渲染，也应在 reset 内部实现。
        },
        "state_encoder_config": {
            "max_actions": 30,
            "action_feature_dim": 128,
        },
        "human_player_id": 0,  # 例如，让玩家0由人类控制
    }

    # 创建环境
    env = MahjongEnv(config)
    human_player_id = config["human_player_id"]

    # --- 2. 环境重置 (回到标准 RL 模式) ---
    # 假设 env.reset() 现在负责完成所有初始设置，包括分步渲染（如果规则启用）。
    print("调用 env.reset()... 假设环境控制器内部已完成所有牌的初始化和发放。")
    observation, info = env.reset()

    # 第一次渲染：显示发完牌后的初始游戏状态
    env.render()

    print("开始测试对局...")

    # --- 3. Main Game Loop (主游戏循环) ---
    # 游戏直接从第一轮摸牌/打牌开始
    terminated = False
    truncated = False
    total_reward = 0

    while not (terminated or truncated):
        current_player = info["current_player"]
        print(f"\n----- 当前回合由 玩家 {current_player} 行动 -----")

        # 获取合法动作信息
        valid_actions: List[Action] = info["valid_actions"]

        if not valid_actions:
            print("错误：规则引擎未生成任何合法动作。终止对局。")
            terminated = True
            continue

        chosen_index = -1

        # --- 玩家行动逻辑 (人类 vs AI) ---
        if current_player == human_player_id:
            # --- 人类玩家输入逻辑 ---
            print(f"您是玩家 {human_player_id}。请选择您的动作:")

            # 打印可用动作列表，带序号供玩家选择
            for i, action in enumerate(valid_actions):
                # 假设 env.renderer.render_action_to_string 方法存在并能将动作转为易读字符串
                action_str = env.renderer.render_action_to_string(action)
                print(f"{i}: {action_str}")

            # 循环直到获取到有效的玩家输入
            while chosen_index == -1:
                try:
                    input_str = input(
                        f"玩家 {human_player_id}，请输入您选择的动作序号 (0-{len(valid_actions)-1}): "
                    )
                    input_index = int(input_str)

                    if 0 <= input_index < len(valid_actions):
                        chosen_index = input_index
                    else:
                        print(
                            f"输入序号 {input_index} 超出有效范围。请重新输入 0 到 {len(valid_actions)-1} 之间的整数。"
                        )

                except ValueError:
                    print("输入无效，请输入一个整数序号。")
                except EOFError:
                    print("\n输入中断，终止对局。")
                    terminated = True
                    break

            if terminated:
                break

        else:
            # --- AI 玩家选择逻辑 (简单启发式: 选择第一个非 PASS 动作，否则选择 PASS) ---
            print(f"玩家 {current_player} (AI) 正在思考并选择动作...")

            ai_chosen_index = -1
            pass_index = -1

            for i, action in enumerate(valid_actions):
                if action.type != ActionType.PASS:
                    ai_chosen_index = i
                    break
                elif action.type == ActionType.PASS:
                    pass_index = i

            # Selection logic
            if ai_chosen_index != -1:
                chosen_index = ai_chosen_index
            elif pass_index != -1:
                chosen_index = pass_index
            else:
                # 警告：作为安全备用，选择第一个动作
                print(
                    f"警告：AI玩家 {current_player} 未能选定动作。默认选择第一个动作。"
                )
                chosen_index = 0

            time.sleep(0.5)  # AI 思考延迟

        # --- 执行选定的动作 ---
        if not terminated:
            chosen_action = valid_actions[chosen_index]
            chosen_action_str = env.renderer.render_action_to_string(chosen_action)
            print(f"玩家 {current_player} 执行动作: {chosen_action_str}")

            # 调用环境的 step 方法
            observation, reward, terminated, truncated, info = env.step(chosen_index)

            # 累加人类玩家的奖励
            if current_player == human_player_id:
                total_reward += reward

            # 渲染游戏状态
            env.render()

            # 回合之间的延迟
            time.sleep(0.2)

    # --- 4. Post-Game Cleanup (对局结束后的处理) ---
    print("\n----- 对局结束 -----")
    if terminated:
        print("对局正常结束 (Terminated).")
    elif truncated:
        print("对局提前结束 (Truncated).")

    print(f"人类玩家 {human_player_id} 的总奖励: {total_reward}")

    env.close()


if __name__ == "__main__":
    main()
