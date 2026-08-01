# 设计文档 10：配置与规则集设计 (CONFIG_AND_RULESET)

> 状态：**草案 v1** ｜ 优先级：**低** ｜ 关联：`configs/default_config.yaml`、各模块 `config.get(...)`
> 本文定义统一 config schema，解决当前 config key 与代码严重不一致的问题（ROADMAP A6/C2）。

---

## 1. 当前问题

代码各处用 `config.get(key, default)` 读 config，但 key 名散乱、`default_config.yaml` 内容与代码读取的 key 几乎完全不匹配：

| 代码读取的 key | 位置 | default_config.yaml 有? |
|----------------|------|-------------------------|
| `num_players` | game_state.py:276 | ❌ |
| `initial_score` | game_state.py:277 | ❌ |
| `state_encoder_config.action_feature_dim` | state_encoder.py:16 | ❌（play.py 写 128） |
| `render` | mahjong_env.py:29 | ❌ |
| `use_red_fives` | game_state.py:120 | ❌ |
| `game_rules` | rules_engine.py:63 | ❌（yaml 用 `environment.name`） |
| `allow_kuitan` | scoring.py:84 | ❌ |
| `training.episodes` 等 | (yaml 有，但代码未读) | ✅（但无用） |

→ **配置系统需要统一重设计（A6）。**

---

## 2. 统一 Config Schema（顶层结构）

```yaml
# configs/default_config.yaml
game:
  num_players: 4              # 3/4 人麻将（当前仅支持4）
  initial_score: 25000
  game_length: "hanchan"      # "tonpuusen"|"hanchan"|"issousen"

ruleset:
  use_red_fives: true         # 赤宝牌（每种5各1张赤）
  red_fives_per_suit: 1       # 每花色赤宝牌数
  kuitan_allowed: true        # 食断
  open_tanyao_allowed: true   # 食断幺
  ippan_shibari: true         # 一番缚
  multiple_ron: true          # 多家荣和（false=头跳）
  atamahane: false            # 头跳（与 multiple_ron 互斥）
  kazan_dora: true            # 杠宝牌
  akadora_dora: true          # 赤宝牌计役
 Extensions:
    nashi_yon: false          # 西入（false=南4即终）
  tobi_rule: "any"            # "any"|"dealer_only"|"none"
  chombo_penalty: 8000        # 罚符点数

state_encoder:
  max_actions: 100            # 候选动作上限（须与 Env.max_candidates 一致！）
  action_feature_dim: 128     # 动作特征维度

render:
  enabled: false
  mode: "text"                # "text"|"human"

reward:                       # 详见 REWARD_DESIGN.md
  mode: "placement"
  placement_rewards: [1.0, 0.3, -0.3, -1.0]
  score_alpha: 0.5
  hand_reward_scale: 0.001
  step_penalty: 0.0
  shaping_enabled: false
  gamma: 0.99

training:                     # 阶段3
  episodes: 1000
  learning_rate: 0.001
  gamma: 0.99
  batch_size: 64
  buffer_size: 10000
  target_update_freq: 10
```

---

## 3. Config 加载与校验

### 3.1 加载器（utils/config_loader.py，当前空）
```python
# src/utils/config_loader.py
import yaml
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("configs/default_config.yaml")

def load_config(path=None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return validate_config(cfg)

def validate_config(cfg: dict) -> dict:
    # 必填与范围校验
    assert cfg["game"]["num_players"] == 4, "仅支持4人"
    assert cfg["state_encoder"]["max_actions"] >= 50
    assert cfg["state_encoder"]["action_feature_dim"] >= 114  # 见 OBSERVATION_ENCODING §6.2
    # 互斥
    r = cfg["ruleset"]
    assert not (r["multiple_ron"] and r["atamahane"]), "多家荣和与头跳互斥"
    return cfg
```

### 3.2 config 透传路径
```
play.py / train.py
  └─> load_config()
        └─> MahjongEnv(config)
              ├─> GameController(config)
              │     ├─> GameState(config)        读 game.num_players/initial_score
              │     ├─> Wall(config)             读 ruleset.use_red_fives
              │     └─> RulesEngine(config)
              │           └─> Scoring(config)    读 ruleset.kuitan_allowed 等
              ├─> StateEncoder(config["state_encoder"])
              └─> Renderer(config["render"])
```

**关键**：每个模块只读自己关心的子 dict，不读其它层的 key。

---

## 4. 各规则项对实现的影响

| 规则项 | 影响模块 | 实现要点 |
|--------|----------|----------|
| use_red_fives | Wall | _generate_tiles 赤宝牌生成（已实现） |
| kuitan_allowed | Scoring | 断幺判定时副露是否算（_check_yaku_tanyao，已实现） |
| open_tanyao_allowed | Scoring | 同 kuitan（合并） |
| ippan_shibari | Scoring | best_han==0 时 is_valid_win=False（已实现） |
| multiple_ron / atamahane | ActionValidator/RulesEngine | resolve_response_priorities 返回 List 或单值 |
| kazan_dora | Wall/Controller | 杠后 reveal_new_dora（已有方法，需接入 Controller） |
| Extensions.nashi_yon | RulesEngine.is_game_over | round_wind 是否允许到 2（西） |
| tobi_rule | RulesEngine.is_game_over | score<0 判定范围 |
| chombo_penalty | Scoring | INVALID_WIN 时罚符 |

---

## 5. 与现有代码对齐 & 修复清单

| 现状 | 设计要求 | 优先级 |
|------|----------|--------|
| default_config.yaml 与代码 key 全不匹配 | 按 §2 重写 yaml | **A6 高** |
| utils/config_loader.py 空 | 按 §3 实现 | 高 |
| max_actions play.py=30 / Env=100 / Encoder=100 | 统一 100，schema 校验 | 高 |
| 各模块 config.get key 散乱 | 按 §3.2 规范化 key 路径 | 中 |
| 无 config 校验 | validate_config | 中 |

---

## 6. 验收标准

1. ✅ §2 schema 完整，每个 key 都有代码读取。
2. ✅ `load_config` + `validate_config` 可用，错误 config 报清晰错误。
3. ✅ play.py / train.py 用统一 config 入口。
4. ✅ max_actions / action_feature_dim 全局一致且校验。
5. ✅ 切换 ruleset（如关赤宝牌、关食断）能正确改变行为。

---

## 7. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，统一 schema + 加载校验 + 规则项映射 |
