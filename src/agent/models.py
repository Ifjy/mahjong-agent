"""
DQN 神经网络 —— 处理麻将的变长动作空间。

设计:
- 状态编码器: 展平的 state(283维) -> 共享 backbone -> state_emb (hidden_dim)
- 动作编码器: 每个 action_candidate(128维) -> MLP -> action_emb (hidden_dim)
- Q 值: state_emb 与 action_emb 做内积 -> Q(mask_size), 再用 mask 屏蔽

这样候选动作通过 embedding 共享权重, 自然处理变长候选, 且支持批量推理。

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §2/§5。
"""

from __future__ import annotations
import torch
import torch.nn as nn


class StateEncoder(nn.Module):
    """状态特征提取器: state_flat -> state_emb。"""

    def __init__(self, state_dim: int, hidden_dim: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.out_dim = hidden_dim

    def forward(self, state_flat: torch.Tensor) -> torch.Tensor:
        """state_flat: (B, state_dim) -> (B, hidden_dim)"""
        return self.net(state_flat)


class ActionEncoder(nn.Module):
    """动作特征提取器: action_candidates -> action_emb (与 state_emb 同维)。"""

    def __init__(self, action_dim: int, hidden_dim: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(action_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.out_dim = hidden_dim

    def forward(self, actions: torch.Tensor) -> torch.Tensor:
        """actions: (B, N, action_dim) 或 (N_total, action_dim) -> (*, hidden_dim)"""
        return self.net(actions)


class DuelingDQNNet(nn.Module):
    """
    Q 网络 (内积式):
        Q(s, a) = state_emb(s) · action_emb(a)

    state 和 action 各自编码到同维 hidden, 再做内积得到标量 Q。
    用 action_mask 屏蔽非法动作。
    支持批量推理。
    """

    def __init__(self, state_dim: int, action_dim: int, hidden_dim: int = 256):
        super().__init__()
        self.state_encoder = StateEncoder(state_dim, hidden_dim)
        self.action_encoder = ActionEncoder(action_dim, hidden_dim)
        self.out_dim = hidden_dim

    def forward(
        self,
        state_flat: torch.Tensor,
        action_candidates: torch.Tensor,
        action_mask: torch.Tensor,
    ) -> torch.Tensor:
        """
        Args:
            state_flat: (B, state_dim)
            action_candidates: (B, N, action_dim)
            action_mask: (B, N) bool/int (1=有效)
        Returns:
            q_values: (B, N) 已用 mask 屏蔽 (非法动作 = -1e9)
        """
        state_emb = self.state_encoder(state_flat)            # (B, H)
        action_emb = self.action_encoder(action_candidates)   # (B, N, H)
        # 内积: Q(s,a) = sum_h state_emb_h * action_emb_h
        q = (state_emb.unsqueeze(1) * action_emb).sum(dim=-1)  # (B, N)
        # mask 屏蔽 (非法动作 Q = -1e9, 便于 argmax)
        q = q.masked_fill(action_mask == 0, -1e9)
        return q

    @torch.no_grad()
    def q_for_single(
        self,
        state_flat: torch.Tensor,
        action_candidates: torch.Tensor,
        action_mask: torch.Tensor,
    ) -> torch.Tensor:
        """单样本推理快捷方法。返回 (N,) Q 值。"""
        self.eval()
        if state_flat.dim() == 1:
            state_flat = state_flat.unsqueeze(0)
        if action_candidates.dim() == 2:
            action_candidates = action_candidates.unsqueeze(0)
        if action_mask.dim() == 1:
            action_mask = action_mask.unsqueeze(0)
        q = self.forward(state_flat, action_candidates, action_mask)
        return q.squeeze(0)
