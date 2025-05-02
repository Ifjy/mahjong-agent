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
        "human_player_id": 0,  # 例如，让玩家0由人类控制
    }

    # 创建环境
    env = MahjongEnv(config)
    human_player_id = config["human_player_id"]
    # 重置环境
    observation, info = env.reset()
    env.render()

    terminated = False
    truncated = False
    total_reward = 0

    print("开始测试对局...")

    while not (terminated or truncated):
        current_player = info["current_player"]
        print(f"\n----- 当前回合由 玩家 {current_player} 行动 -----")

        # 获取合法动作信息
        valid_actions: List[Action] = info["valid_actions"]
        # action_mask = info["action_mask"] # action_mask 在这个逻辑中没有直接用到，但可能在 env.step 内部使用或用于其他AI

        if not valid_actions:
            # 如果没有合法动作，这通常表示游戏进入了某种异常状态或结束
            print("错误：规则引擎未生成任何合法动作。终止对局。")
            terminated = True  # 设置终止标志，结束游戏循环
            # 不执行 env.step，直接进入下一循环迭代检查 terminated
            continue  # 跳过当前循环剩余部分

        chosen_index = -1  # 初始化为无效索引

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
                    input_index = int(input_str)  # 尝试将输入转为整数

                    # 检查输入的整数是否在有效动作列表的索引范围内
                    if 0 <= input_index < len(valid_actions):
                        chosen_index = input_index  # 输入有效，接受并跳出输入循环
                    else:
                        print(
                            f"输入序号 {input_index} 超出有效范围。请重新输入 0 到 {len(valid_actions)-1} 之间的整数。"
                        )

                except ValueError:
                    # 如果输入不是一个有效的整数
                    print("输入无效，请输入一个整数序号。")
                except EOFError:  # 处理终端中按下 Ctrl+D 的情况
                    print("\n输入中断，终止对局。")
                    terminated = True  # 设置终止标志
                    break  # 退出输入循环

            # 如果由于 EOFError 导致 terminated，需要检查并跳出主循环
            if terminated:
                break  # 退出 while not (terminated or truncated) 循环

        else:
            # --- AI 玩家选择逻辑 (使用你原有的简单AI，或者可以替换为更复杂的AI) ---
            print(f"玩家 {current_player} (AI) 正在思考并选择动作...")
            # 你原有的简单AI逻辑：选择第一个非PASS动作，如果没有非PASS动作，则选择PASS动作
            ai_chosen_index = -1  # 用于存储AI选中的非PASS动作索引
            pass_index = -1  # 用于存储PASS动作的索引

            for i, action in enumerate(valid_actions):
                # 假设 Action 对象有一个 type 属性，并且 ActionType 是一个枚举或常量类
                # 检查是否是PASS动作，或者是否是其他类型的动作
                if action.type != ActionType.PASS:  # 假设 ActionType 有 PASS 成员
                    ai_chosen_index = i  # 找到第一个非PASS动作，记住它的索引
                    break  # 找到了就退出循环

                elif action.type == ActionType.PASS:
                    pass_index = i  # 记住PASS动作的索引，以防万一没有非PASS动作

            # 如果找到了非PASS动作，就选择它；否则，如果存在PASS动作，就选择PASS
            if ai_chosen_index != -1:
                chosen_index = ai_chosen_index
            elif pass_index != -1:
                chosen_index = pass_index
            else:
                # 理论上 valid_actions 不会为空且没有 PASS，但这作为最后的安全备用
                # 如果合法动作列表中既没有非PASS也没有PASS（这非常异常），则默认选择第一个动作
                print(
                    f"警告：AI玩家 {current_player} 未能从合法动作列表中选定动作。默认选择第一个动作。"
                )
                chosen_index = 0  # 选择第一个动作作为最后的备用

            # AI 选择动作后，可以增加一个短暂的延迟，模拟思考过程
            time.sleep(0.5)  # AI思考延迟，可以根据需要调整

        # --- 执行选定的动作 ---
        # chosen_index 现在已经确定，是人类玩家输入的，或者是AI选择的
        # 只有当 terminated 没有被 EOFError 设置时才执行 step
        if not terminated:
            chosen_action = valid_actions[chosen_index]
            # 将选择的动作转为字符串并打印出来，让玩家知道AI或自己执行了什么
            chosen_action_str = env.renderer.render_action_to_string(chosen_action)
            print(f"玩家 {current_player} 执行动作: {chosen_action_str}")

            # 调用环境的 step 方法，传入选择的动作的索引
            observation, reward, terminated, truncated, info = env.step(chosen_index)

            # 可以选择性地累加人类玩家的奖励
            if current_player == human_player_id:
                total_reward += reward
            # print(f"(您本回合获得奖励: {reward}, 累积总奖励: {total_reward})") # 可选：打印当前奖励

            # 渲染游戏状态，以便玩家或观察者看到变化
            env.render()

            # 在回合之间增加一个小延迟，方便观察
            # 可以根据是否是人类回合调整延迟时间
            # if current_player == human_player_id:
            #     time.sleep(0.1) # 人类回合延迟短一些
            # else:
            #     time.sleep(0.5) # AI回合延迟长一些
            time.sleep(0.2)  # 或者使用统一延迟

    # --- 对局结束后的处理 ---
    # 循环结束后，游戏因 terminated 或 truncated 而终止
    print("\n----- 对局结束 -----")
    if terminated:
        print("对局正常结束 (Terminated).")
        # 可以打印最终状态或得分等信息，如果 info 中包含这些
        # 例如：print("最终状态信息:", info.get("final_info"))

    elif truncated:
        print("对局提前结束 (Truncated).")
        # 打印导致截断的原因等

    # 打印人类玩家的总奖励
    print(f"人类玩家 {human_player_id} 的总奖励: {total_reward}")

    # 如果需要，可以在这里进行其他清理或总结工作
    env.close()  # 如果你的环境需要关闭操作


if __name__ == "__main__":
    main()
