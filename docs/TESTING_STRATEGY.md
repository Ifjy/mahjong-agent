# 设计文档 08：测试策略 (TESTING_STRATEGY)

> 状态：**草案 v1** ｜ 优先级：**中** ｜ 关联代码：`tests/test_env.py`（当前过时）
> 本文定义单元测试、seed 回归、黄金牌谱回放、规则正确性验收。规则正确性的"金标准"是牌谱回放。

---

## 1. 当前测试现状

`tests/test_env.py` 仅 3 个极简测试，且**接口已过时**：
- `MahjongEnv()` 无参构造 → 会因 `state_encoder_config` 缺失 KeyError。
- `env.step(action)` 当 4 元组解包 → 现在是 5 元组（gymnasium）。
- `env.reset()` 当单返回值 → 现在返回 (obs, info)。

→ **当前测试无法运行，必须重写（C2）。**

---

## 2. 测试金字塔

```
            ┌───────────────────────┐
            │  黄金牌谱回放 (Gold)   │  ← 规则正确性终极验证（少而精）
            └───────────────────────┘
          ┌───────────────────────────┐
          │  Seed 回归测试 (Seed)     │  ← 完整局可复现（中等）
          └───────────────────────────┘
        ┌───────────────────────────────┐
        │  单元测试 (Unit)               │  ← 每模块每函数（大量）
        └───────────────────────────────┘
```

---

## 3. 单元测试（Unit Tests）

按模块组织，建议 `tests/unit/` 下分文件：

### 3.1 test_tile_action.py（数据结构）
- Tile 构造/排序/哈希/frozen。
- ActionType / KanType 枚举完备。
- Action 各 type 的 `__post_init__` 校验（缺参数报错）。
- `to_feature_vector` 维度与非零索引。

### 3.2 test_wall.py（牌墙）
- `_generate_tiles` 总数 = 136（含赤宝牌逻辑）。
- `shuffle_and_setup` 后 dora_indicators/ura 各 1 张。
- `draw_tile` / `draw_replacement_tile`（修 A1 后）/ `reveal_new_dora` 序列。
- `get_current_dora_tiles` 指示牌换算（万9→1、风东→南、白→发等边界）。

### 3.3 test_game_state_apply_action.py
- DISCARD：摸切 vs 手切、并入 drawn_tile、弃牌河更新。
- RIICHI：扣分、立直标记、伴随打牌。
- CHI/PON/KAN：副露创建、手牌移除、is_menzen 更新。
  - ⚠️ 修 A3（meld dict→属性）后，重点测 Meld 字段。
- 暗杠/加杠：含 drawn_tile 的处理、PON→KAN 转换。
- TSUMO/RON：仅设 flag，不改分。

### 3.4 test_hand_analyzer.py（核心，配合 HAND_DECOMPOSITION_DESIGN）
- 标准型分解：123m456p789s11z+2z 等基础形。
- 多分解穷举：清一色多面子组合。
- 七对子 / 国士无双。
- `is_tenpai` / `find_wait_tiles` 边界（多面听、振听来源）。
- 性能断言：`is_tenpai` < 0.1ms。
- 与 value-based 回归对照（1000 随机手牌一致）。

### 3.5 test_action_validator.py
- 每阶段候选动作齐全。
- 互斥逻辑（TSUMO 时不生成 DISCARD）。
- 立直后限制（只摸切、暗杠不改听牌）。
- 食替禁止。
- 响应优先级（RON>碰杠>吃>过）。

### 3.6 test_scoring.py（配合 YAKU_AND_SCORING_DESIGN）
- 逐役种用例（断幺/平和/一杯口/三色/清一色/对对/役满...）。
- 符数：覆盖 20-110 符常见组合，对照天凤计分器。
- 点数支付：自摸/荣和、庄闲、本场/立直棒。
- 振听三种。
- 流局罚符。

### 3.7 test_rules_engine.py（集成）
- determine_next_phase 全动作映射。
- determine_next_hand_state 连庄/换庄/场风推进/西入。
- is_game_over 飞人/终局。

### 3.8 test_game_controller.py（流程）
- 完整一局 phase 序列（无死锁）。
- auto-flow 摸牌/岭上/开新局。
- 响应收集 3 人。

---

## 4. Seed 回归测试（Seed-based Regression）

目的：**完整一局可复现**，验证模块协作与无崩溃。

```python
def test_full_hand_deterministic():
    config = {...}
    env = MahjongEnv(config)
    obs, info = env.reset(seed=42)

    # 用固定策略（如随机但 seed 化，或"总选第k个候选"）
    rng = np.random.default_rng(123)
    actions_log = []
    terminated = False
    while not terminated:
        k = rng.integers(0, info["action_mask"].sum())
        valid_idx = np.where(info["action_mask"] == 1)[0]
        a = valid_idx[k]
        actions_log.append(a)
        obs, reward, terminated, truncated, info = env.step(int(a))

    # 断言：局正常结束，分数守恒（总和=初始）
    total = sum(p.score for p in env.controller.gamestate.players)
    assert total == 4 * initial_score  # 零和（除立直棒流转）
```

- 用多种 seed 跑 N 局，确保无异常、无死循环、分数守恒。
- **seed 支持前提**：reset 处理 seed（C3），Wall 用 random.Random(seed)。

> 注意：麻将涉及多人决策，"完全确定"需固定所有玩家策略。此测试主要验证"不崩"，不验证策略最优。

---

## 5. 黄金牌谱回放（Gold Replay）—— 规则正确性金标准

这是**最重要的正确性验证**，强烈推荐。

### 5.1 数据来源
- 公开牌谱：天凤/雀魂的 log（注意版权，用其规则作对照，不拷贝数据）。
- 或自建：用规则完善的引擎（如 tenhou.net 的公开 Web 版）打几局，记录每步。

### 5.2 回放流程
```python
def test_gold_replay(kifu):
    env = MahjongEnv(config)
    env.reset(seed=kifu.wall_seed)   # 复现牌山

    for step_record in kifu.steps:
        # step_record: (player_idx, expected_action, expected_phase)
        candidates = env.controller.rules_engine.generate_candidate_actions(
            env.controller.gamestate, step_record.player_idx
        )
        # 断言1：牌谱中的 declared action 必须在候选中
        assert step_record.expected_action in candidates, \
            f"违规动作或漏判：{step_record.expected_action} 不在 {candidates}"

        env.controller.step(step_record.player_idx, step_record.expected_action)

        # 断言2：phase 一致
        assert env.controller.gamestate.game_phase == step_record.expected_phase

    # 断言3：和牌点数与官方一致
    assert final_scores == kifu.expected_scores
```

### 5.3 覆盖目标
- 至少 10 局完整牌谱，覆盖：自摸/荣和/流局/杠/立直/振听/多家荣和。
- 役种：每役至少 1 个用例。

---

## 6. 性能测试

```python
def test_candidate_generation_perf():
    # 中盘 4 人门清场景
    setup_midgame(env)
    import time
    t0 = time.time()
    for _ in range(1000):
        env.controller.rules_engine.generate_candidate_actions(gs, 0)
    dt = (time.time() - t0) / 1000
    assert dt < 0.005  # < 5ms
```

---

## 7. 测试基础设施

- 框架：pytest（已在 requirements 之外，需加）。
- fixtures：`conftest.py` 提供 `env`、`gamestate_midgame`、`kifu_loader`。
- CI：建议 GitHub Actions 跑全量测试（seed 回归 + 单元）。
- 覆盖率：目标规则层 > 90%。

---

## 8. 验收标准

1. ✅ `tests/test_env.py` 重写，接口对齐 gymnasium 5 元组。
2. ✅ §3 单元测试全覆盖（每个模块独立可跑）。
3. ✅ §4 seed 回归：100 局无崩溃、分数守恒。
4. ✅ §5 至少 10 局黄金牌谱回放通过。
5. ✅ §6 性能达标。

---

## 9. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，金字塔策略 + seed 回归 + 黄金牌谱回放 |
