import os
import sys

import numpy as np

# 确保项目根目录在路径中（兼容直接 pytest）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.env.mahjong_env import MahjongEnv
from src.env.core.actions import ActionType


def _config():
    return {
        "num_players": 4,
        "initial_score": 25000,
        "render": False,
        "state_encoder_config": {
            "max_actions": 100,
            "action_feature_dim": 128,
        },
    }


def _deep_equal_obs(left, right):
    if isinstance(left, dict):
        return (
            isinstance(right, dict)
            and left.keys() == right.keys()
            and all(_deep_equal_obs(left[k], right[k]) for k in left)
        )
    return np.array_equal(left, right)


def test_env_initialization():
    env = MahjongEnv(_config())
    assert env.observation_space is not None
    assert env.action_space is not None


def test_env_reset_returns_candidates_and_phase():
    env = MahjongEnv(_config())
    obs, info = env.reset(seed=7)
    assert obs is not None
    assert "valid_actions" in info
    assert info["current_phase"] == "PLAYER_DISCARD"
    assert len(info["valid_actions"]) > 0


def test_env_seed_reproducible_reset_observation():
    env_a = MahjongEnv(_config())
    env_b = MahjongEnv(_config())

    obs_a, info_a = env_a.reset(seed=123)
    obs_b, info_b = env_b.reset(seed=123)

    assert _deep_equal_obs(obs_a, obs_b)
    assert info_a["current_phase"] == info_b["current_phase"]
    assert len(info_a["valid_actions"]) == len(info_b["valid_actions"])


def test_env_discard_action_transitions_to_response_phase():
    env = MahjongEnv(_config())
    _, info = env.reset(seed=11)

    discard_idx = None
    for i, action in enumerate(info["valid_actions"]):
        if action.type == ActionType.DISCARD:
            discard_idx = i
            break

    assert discard_idx is not None, "Expected at least one DISCARD action on dealer turn"

    _, _, terminated, truncated, next_info = env.step(discard_idx)

    assert not terminated
    assert not truncated
    assert next_info["current_phase"] == "WAITING_FOR_RESPONSE"
