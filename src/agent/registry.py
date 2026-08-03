"""
Agent Registry —— 算法注册与工厂。

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §2。
用 @register_agent("name") 装饰器注册, build_agent(name) 工厂构造。
支持 DQN/PPO/自定义算法热插拔。
"""

from __future__ import annotations
from typing import Dict, Type
from src.agent.base_agent import BaseAgent

AGENT_REGISTRY: Dict[str, Type[BaseAgent]] = {}


def register_agent(name: str):
    """装饰器: 注册 agent 类。用法: @register_agent("dqn")"""

    def decorator(cls: Type[BaseAgent]) -> Type[BaseAgent]:
        AGENT_REGISTRY[name] = cls
        return cls

    return decorator


def build_agent(algo_name: str, config: Dict, agent_id: int) -> BaseAgent:
    """工厂: 按名称构造 agent。"""
    # 触发各 agent 模块导入以完成注册
    _ensure_registered()
    if algo_name not in AGENT_REGISTRY:
        raise ValueError(
            f"未知算法 '{algo_name}', 已注册: {sorted(AGENT_REGISTRY.keys())}"
        )
    return AGENT_REGISTRY[algo_name](config, agent_id)


def _ensure_registered():
    """导入各 agent 模块以触发 @register_agent 装饰器。"""
    # 始终导入全部内置 agent, 确保注册完整 (即使部分已注册)
    from src.agent import random_agent  # noqa: F401
    from src.agent import heuristic_agent  # noqa: F401
    from src.agent import dqn_agent  # noqa: F401
    # PPO 在阶段 C 实现后在此导入
