"""
配置加载器 —— 把 YAML 嵌套 schema 展开为环境代码读取的扁平 key。

设计见 docs/CONFIG_AND_RULESET.md 与 docs/RL_AGENT_EXPERIMENT_DESIGN.md §9。

环境代码读取的扁平 key (来自 src/env 各模块):
    num_players, initial_score, use_red_fives, allow_kuitan,
    game_rules (dict, 含 game_length/tobi_rule),
    state_encoder_config (dict, 含 max_actions/action_feature_dim),
    render (bool)

本加载器把实验 YAML 的嵌套段映射到上述扁平结构, 使 Env 无需改动。
"""

from __future__ import annotations
import copy
from typing import Dict, Any
import yaml


def load_config(path: str) -> Dict[str, Any]:
    """加载 YAML 配置文件并规范化。

    Args:
        path: YAML 文件路径。
    Returns:
        规范化后的 config dict (含扁平 env key + algo/train 等实验段)。
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return normalize_config(raw)


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把嵌套 YAML schema 规范化为代码可读的扁平结构, 并填充默认值。

    支持两种输入:
    1. 实验配置 (嵌套, 见 RL_AGENT_EXPERIMENT_DESIGN §9):
       game./ruleset./state_encoder_config./render./algo./algo_config./reward./...
    2. 旧扁平配置 (直接含 num_players 等, 向后兼容)。
    """
    cfg = copy.deepcopy(raw)

    game = cfg.get("game", {})
    ruleset = cfg.get("ruleset", {})
    encoder = cfg.get("state_encoder_config", cfg.get("state_encoder", {}))

    flat_env: Dict[str, Any] = {}

    # game 段 -> 扁平
    if game:
        flat_env["num_players"] = game.get("num_players", 4)
        flat_env["initial_score"] = game.get("initial_score", 25000)

    # ruleset 段 -> 扁平 + game_rules 嵌套 (RulesEngine 读 game_rules)
    if ruleset or game:
        flat_env["use_red_fives"] = ruleset.get("use_red_fives", True)
        flat_env["allow_kuitan"] = ruleset.get(
            "allow_kuitan", ruleset.get("kuitan_allowed", False)
        )
        game_rules: Dict[str, Any] = {}
        game_rules["game_length"] = ruleset.get("game_length", "hanchan")
        game_rules["tobi_rule"] = ruleset.get("tobi_rule", "any")
        if "open_tanyao_allowed" in ruleset:
            game_rules["open_tanyao_allowed"] = ruleset["open_tanyao_allowed"]
        flat_env["game_rules"] = game_rules

    # state_encoder 段
    if encoder:
        flat_env["state_encoder_config"] = {
            "max_actions": encoder.get("max_actions", 100),
            "action_feature_dim": encoder.get("action_feature_dim", 128),
        }

    if "render" in cfg:
        flat_env["render"] = cfg["render"]

    # 扁平 env key 注入 (覆盖旧扁平值), 同时保留实验语义段供 Trainer 读
    result = copy.deepcopy(cfg)
    result.update(flat_env)
    return result


def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """校验 config 必需 key 与一致性。失败抛 ValueError。"""
    num_players = cfg.get("num_players", 4)
    if num_players != 4:
        raise ValueError(f"目前仅支持 4 人麻将, got num_players={num_players}")

    max_actions = cfg.get("state_encoder_config", {}).get("max_actions", 100)
    if not (10 <= max_actions <= 1000):
        raise ValueError(f"max_actions 超范围: {max_actions}")

    action_feature_dim = cfg.get("state_encoder_config", {}).get(
        "action_feature_dim", 128
    )
    if action_feature_dim < 114:
        raise ValueError(
            f"action_feature_dim 过小: {action_feature_dim}, 最少需要 114"
        )

    agents = cfg.get("agents")
    if agents is not None:
        if not isinstance(agents, list) or len(agents) != num_players:
            raise ValueError(
                f"agents 列表长度必须 == num_players ({num_players}), got {agents}"
            )
    return cfg


def get_default_config() -> Dict[str, Any]:
    """返回最小可用的默认 env 配置 (无实验段)。"""
    return {
        "num_players": 4,
        "initial_score": 25000,
        "use_red_fives": True,
        "allow_kuitan": False,
        "game_rules": {"game_length": "hanchan", "tobi_rule": "any"},
        "state_encoder_config": {"max_actions": 100, "action_feature_dim": 128},
        "render": False,
    }
