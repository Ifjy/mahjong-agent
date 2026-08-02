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

    def train_parallel(self, total_episodes: int, num_envs: int = 8,
                       collect_steps_per_update: int = 1000,
                       update_per_collect: int = 10,
                       resume_from: Optional[str] = None):
        """批量并行训练 (DQN 专用, 用 ParallelCollector 多 env 采集 + 批量推理)。

        流程:
        1. ParallelCollector 维持 num_envs 个 env 并行采集。
        2. 每采集 collect_steps_per_update 步, 训练 update_per_collect 次。
        3. 重复直到 total_episodes 局完成。
        4. 共享同一个 DQN 权重 (self-play)。
        """
        from src.trainning.collector import ParallelCollector
        from src.agent.dqn_agent import DQNAgent

        # DQN 模式: 4 个 agent 共享同一网络 (用 agent0 作主网络)
        dqn_agent = self.agents[0]
        if not isinstance(dqn_agent, DQNAgent):
            raise ValueError("train_parallel 仅支持 DQN agent")

        if resume_from is not None:
            self._resume(resume_from)

        env_config = {
            k: v for k, v in self.config.items()
            if k in (
                "num_players", "initial_score", "use_red_fives", "allow_kuitan",
                "game_rules", "state_encoder_config", "render", "reward",
            )
        }
        base_seed = self.config.get("experiment", {}).get("seed", 42)
        collector = ParallelCollector(env_config, dqn_agent, num_envs=num_envs,
                                      base_seed=base_seed)
        checkpoint_freq = self.config.get("experiment", {}).get("checkpoint_freq", 0)

        log.info("批量训练: num_envs=%d, target_episodes=%d", num_envs, total_episodes)
        t0 = time.time()
        last_loss = 0.0
        while collector.episodes_completed < total_episodes:
            # 采集
            collector.collect(collect_steps_per_update)
            # 训练
            total_loss = 0.0
            n_updates = 0
            for _ in range(update_per_collect):
                metrics = dqn_agent.update()
                if metrics:
                    total_loss += metrics.get("loss", 0)
                    n_updates += 1
            if n_updates:
                last_loss = total_loss / n_updates

            self.global_step = collector.total_steps
            eps = collector.episodes_completed
            if eps % 5 < num_envs:  # 每若干局日志一次
                log.info(
                    "episodes=%d steps=%d loss=%.4f eps=%.3f buffer=%d sps=%d",
                    eps, collector.total_steps, last_loss,
                    dqn_agent._current_epsilon(), len(dqn_agent.buffer),
                    int(collector.total_steps / max(1, time.time() - t0)),
                )

            if checkpoint_freq and eps and eps % checkpoint_freq == 0:
                self._save_checkpoint_parallel(eps, dqn_agent)

        log.info("批量训练完成: %d 局, %d 步, %.1fs",
                 collector.episodes_completed, collector.total_steps, time.time() - t0)

    def _rollout_one(self, ep: int, base_seed: Optional[int]):
        """单局 rollout: 4 agent 轮转决策 + 经验采集 (s,a,r,s')。

        每个独立维护"上次决策" (s, a):
        - 下次该 agent 决策前, 把 (s, a, reward_since_last, s') 入 buffer。
        - 局终时该 agent 的剩余 reward + episode reward 以 done=True 入 buffer。
        reward_since_last = 该 agent 两次决策间累加的步内 reward (零和稀疏)。
        """
        seed = (base_seed + ep) if base_seed is not None else None
        obs, info = self.env.reset(seed=seed)

        steps = 0
        last_action_player = 0
        # 各 agent 上次决策的 (obs, action_idx) 与自上次以来的累计 reward
        pending = {i: None for i in range(4)}  # {i: (obs, action_idx)}
        accrue = [0.0] * 4  # 各 agent 自上次决策以来累加的 dense reward
        dense_total = [0.0] * 4

        while True:
            acting_player = info.get("current_player", 0)
            agent = self.agents[acting_player]

            # 若该 agent 有 pending, 入 buffer: (s,a,accrue,s')
            if pending[acting_player] is not None:
                p_obs, p_act = pending[acting_player]
                agent.store_transition(p_obs, p_act, accrue[acting_player], obs, False, info)
                accrue[acting_player] = 0.0
                pending[acting_player] = None

            action_idx = agent.select_action(
                obs, info["action_mask"], info["valid_actions"], deterministic=False
            )
            pending[acting_player] = (obs, action_idx)

            obs, reward, terminated, truncated, info = self.env.step(action_idx)
            # 这一步的 dense reward 归给"所有还没结算的 agent"? 简化: 归给当前 acting player
            accrue[acting_player] += reward
            dense_total[acting_player] += reward
            last_action_player = acting_player
            steps += 1

            if terminated or truncated:
                break

        # 终局: per-player episode reward
        final_scores = info.get("final_scores", [0, 0, 0, 0])
        per_player_rewards = info.get("rewards", {i: 0.0 for i in range(4)})

        # 回填各 agent: pending + 剩余 accrue + episode reward, done=True
        for i, agent in enumerate(self.agents):
            ep_r = dense_total[i] + per_player_rewards.get(i, 0.0)
            if pending[i] is not None:
                p_obs, p_act = pending[i]
                final_r = accrue[i] + per_player_rewards.get(i, 0.0)
                agent.store_transition(p_obs, p_act, final_r, obs, True, info)
                pending[i] = None
            agent.assign_episode_reward(ep_r)
            agent.update()

        ep_reward = dense_total[last_action_player] + per_player_rewards.get(
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

    def _save_checkpoint_parallel(self, ep: int, dqn_agent):
        """保存 DQN checkpoint (torch 格式, 含权重)。"""
        import torch
        ckpt_dir = os.path.join(self.log_dir, "ckpt")
        os.makedirs(ckpt_dir, exist_ok=True)
        ckpt_path = os.path.join(ckpt_dir, f"ep_{ep}.pt")
        torch.save({
            "episode": ep,
            "global_step": self.global_step,
            "agent": dqn_agent.get_state(),
        }, ckpt_path)
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
