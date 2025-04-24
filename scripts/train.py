import gym
from src.env.mahjong_env import MahjongEnv

def main():
    # 初始化环境
    env = MahjongEnv()

    # 示例：打印环境的状态空间和动作空间
    print("Observation Space:", env.observation_space)
    print("Action Space:", env.action_space)

    # 示例：运行一个回合
    obs = env.reset()
    done = False
    while not done:
        action = env.action_space.sample()  # 随机动作
        obs, reward, done, info = env.step(action)
        print(f"Action: {action}, Reward: {reward}, Done: {done}")

if __name__ == "__main__":
    main()
