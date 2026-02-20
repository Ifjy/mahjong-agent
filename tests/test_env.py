import os
import sys

# 确保项目根目录在路径中（兼容直接 pytest）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.env.mahjong_env import MahjongEnv


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


def test_env_initialization():
    env = MahjongEnv(_config())
    assert env.observation_space is not None
    assert env.action_space is not None


def test_env_reset():
    env = MahjongEnv(_config())
    obs, info = env.reset()
    assert obs is not None
    assert "valid_actions" in info


def test_env_step():
    env = MahjongEnv(_config())
    env.reset()
    obs, reward, terminated, truncated, info = env.step(0)
    assert obs is not None
    assert isinstance(reward, (int, float))
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert isinstance(info, dict)


def test_env_multi_step_smoke():
    env = MahjongEnv(_config())
    obs, info = env.reset()

    for _ in range(30):
        valid_actions = info["valid_actions"]
        if len(valid_actions) == 0:
            break
        obs, reward, terminated, truncated, info = env.step(0)
        assert not truncated
        if terminated:
            break
