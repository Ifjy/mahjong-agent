import pytest
from src.env.mahjong_env import MahjongEnv

def test_env_initialization():
    env = MahjongEnv()
    assert env.observation_space is not None
    assert env.action_space is not None

def test_env_reset():
    env = MahjongEnv()
    obs = env.reset()
    assert obs is not None

def test_env_step():
    env = MahjongEnv()
    env.reset()
    action = env.action_space.sample()
    obs, reward, done, info = env.step(action)
    assert obs is not None
    assert isinstance(reward, (int, float))
    assert isinstance(done, bool)
