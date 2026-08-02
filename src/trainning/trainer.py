"""
Trainer —— 多智能体训练循环 (最小版, 阶段 A)。

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §3。
阶段 A 目标: 4 个 agent 共享 env 轮转, 跑完若干局无异常, 输出 metrics.csv。
DQN/PPO 的 update/checkpoint 在阶段 B/C 补充。

核心流程:
1. 构造 env + 4 个 agent (按 config.agents 算法名)。
2. rollout: 每个决策点由当前 agent 选动作, Env 推进。
3. 局结束: 用 info["rewards"] 回填各 agent episode reward。
4. 日志: 写 metrics.csv (episode, 各 agent reward, 平均顺位等)。
"""

from __future__ import annotations
import os
import csv
import time
from typing import Dict, List, Optional

from src.env.mahjong_env import MahjongEnv
from src.agent.registry import build_agent
from src.utils.logger import get_logger

log = get_logger(__name__)


class Trainer:
    """多智能体训练器。"""

    def __init__(self, config: Dict):
        self.config = config
        env_config = {
            k: v for k, v in config.items()
            if k in (
                "num_players", "initial_score", "use_red_fives", "allow_kuitan",
                "game_rules", "state_encoder_config", "render", "reward",
            )
        }
        self.env = MahjongEnv(env_config)

        # 4 个 agent (按 config.agents 算法名构造)
        algo_config = config.get("algo_config", {})
        agent_algo_list = config.get("agents", ["random"] * 4)
        # 每个 agent 的 config = 全局 config + algo_config + seed
        agent_shared_cfg = {**config, **algo_config}
        self.agents = [
            build_agent(algo_name, agent_shared_cfg, agent_id=i)
            for i, algo_name in enumerate(agent_algo_list)
        ]

        # 实验输出目录
        self.exp_name = config.get("experiment", {}).get("name", "default")
        self.log_dir = os.path.join(
            config.get("experiment", {}).get("log_dir", "runs"), self.exp_name
        )
        os.makedirs(self.log_dir, exist_ok=True)
        self.metrics_path = os.path.join(self.log_dir, "metrics.csv")

        self.global_step = 0

    def train(self, total_episodes: int, resume_from: Optional[str] = None):
        """训练循环。"""
        if resume_from is not None:
            start_ep = self._resume(resume_from)
            log.info("从 checkpoint 恢复, start_episode=%d", start_ep)
        else:
            start_ep = 0

        checkpoint_freq = self.config.get("experiment", {}).get("checkpoint_freq", 0)
        eval_freq = self.config.get("eval_freq", 0)
        base_seed = self.config.get("experiment", {}).get("seed", None)

        t0 = time.time()
        for ep in range(start_ep, total_episodes):
            ep_reward, ep_steps, final_scores, per_player_rewards = self._rollout_one(
                ep, base_seed
            )
            self.global_step += ep_steps
            self._log_metrics(
                ep, ep_reward, ep_steps, final_scores, per_player_rewards, t0
            )

            if checkpoint_freq and (ep + 1) % checkpoint_freq == 0:
                self._save_checkpoint(ep + 1)

        log.info("训练完成: %d 局, 总步数 %d", total_episodes, self.global_step)

    def _rollout_one(self, ep: int, base_seed: Optional[int]):
        """单局 rollout: 4 agent 轮转决策。返回 (ep_reward, steps, scores, rewards)。"""
        seed = (base_seed + ep) if base_seed is not None else None
        obs, info = self.env.reset(seed=seed)

        steps = 0
        last_action_player = 0
        # 记录每个 agent 的累计步内 reward (稠密部分)
        dense_rewards = [0.0] * 4

        while True:
            acting_player = info.get("current_player", 0)
            agent = self.agents[acting_player]
            action_idx = agent.select_action(
                obs,
                info["action_mask"],
                info["valid_actions"],
                deterministic=False,
            )
            obs, reward, terminated, truncated, info = self.env.step(action_idx)
            dense_rewards[acting_player] += reward
            last_action_player = acting_player
            steps += 1

            if terminated or truncated:
                break

        # 终局: per-player episode reward (来自 info["rewards"], 与稠密步reward分离)
        final_scores = info.get("final_scores", [0, 0, 0, 0])
        per_player_rewards = info.get(
            "rewards", {i: 0.0 for i in range(4)}
        )
        # 回填各 agent 的 episode reward (稠密步reward + episode reward)
        for i, agent in enumerate(self.agents):
            ep_r = dense_rewards[i] + per_player_rewards.get(i, 0.0)
            agent.assign_episode_reward(ep_r)
            agent.update()

        # ep_reward 记录最后一个动作玩家的总 reward (稠密+episode)
        ep_reward = dense_rewards[last_action_player] + per_player_rewards.get(
            last_action_player, 0.0
        )
        return ep_reward, steps, final_scores, per_player_rewards

    def _log_metrics(
        self, ep, ep_reward, ep_steps, final_scores, per_player_rewards, t0
    ):
        """写 metrics.csv (首次写表头)。"""
        write_header = not os.path.exists(self.metrics_path) or ep == 0
        with open(self.metrics_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(
                    ["episode", "global_step", "steps", "ep_reward",
                     "score_0", "score_1", "score_2", "score_3",
                     "reward_0", "reward_1", "reward_2", "reward_3",
                     "elapsed_s"]
                )
            writer.writerow(
                [ep, self.global_step, ep_steps, f"{ep_reward:.4f}"]
                + final_scores
                + [f"{per_player_rewards.get(i, 0.0):.4f}" for i in range(4)]
                + [f"{time.time() - t0:.1f}"]
            )

        if ep % 10 == 0:
            log.info(
                "ep %d | steps %d | reward %.3f | scores %s",
                ep, ep_steps, ep_reward, final_scores,
            )

    def _save_checkpoint(self, ep: int):
        """保存 checkpoint (阶段 A 最小版: 仅 agent state + episode)。"""
        import json
        ckpt_dir = os.path.join(self.log_dir, "ckpt")
        os.makedirs(ckpt_dir, exist_ok=True)
        ckpt_path = os.path.join(ckpt_dir, f"ep_{ep}.json")
        state = {
            "episode": ep,
            "global_step": self.global_step,
            "agents": [a.get_state() for a in self.agents],
        }
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(state, f, default=str, indent=2)
        log.info("checkpoint 已保存: %s", ckpt_path)

    def _resume(self, ckpt_path: str) -> int:
        """从 checkpoint 恢复 (阶段 A 最小版)。"""
        import json
        with open(ckpt_path, encoding="utf-8") as f:
            state = json.load(f)
        self.global_step = state.get("global_step", 0)
        for i, agent in enumerate(self.agents):
            if i < len(state.get("agents", [])):
                agent.load_state(state["agents"][i])
        return state.get("episode", 0)
