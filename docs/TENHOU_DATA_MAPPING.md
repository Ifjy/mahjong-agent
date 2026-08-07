# 天凤牌谱数据 ↔ 项目 Env 映射设计

> **目的**: 把天凤 (Tenhou) 凤桌牌谱 XML 转成可用于 agent 训练的 `(observation, action)` 样本，对接本项目的 `src/env` 与 `src/trainning`。
>
> **当前状态**: 下载链路已打通（7 天 2919 局凤桌牌谱），XML 解析器雏形已跑通单局映射。鸣牌/和了的样本采集为 TODO。

---

## 1. 数据来源与下载链路

### 1.1 为什么用凤桌 (Houou)

| 桌级 | 天凤简称 | 水平 | 是否有公开批量牌谱索引 |
|------|---------|------|----------------------|
| 凤桌 (凤凰) | 四鳳 | 顶级 (段位 三星~十段) | ✅ `scc*.html.gz` |
| 特上 | 四特 | 高级 | ❌ 仅成绩列表，无牌谱 id |
| 上/般 | 四上/四般 | 中低 | ❌ 同上 |

**结论**: 天凤只为凤桌公开牌谱索引 (`scc` 文件)。要批量下牌谱**只能用凤桌**。这恰恰是 IL/监督学习最理想的数据——顶级玩家的高质量决策。用户最初倾向的"特上桌"在天凤当前规则下无法批量获取，故改用凤桌。

### 1.2 下载三步链路

```
① list.cgi  ──→  sccYYYYMMDDHH.html.gz (凤桌牌谱索引, 按小时分片)
                    │  (解压后是 HTML, 每行一场对局含 log=<id>)
                    ▼
② 提取 log id  ──→  data/tenhou/log_ids.json
                    │  规则过滤: "四鳳南" (四人凤桌南喰赤半庄)
                    ▼
③ 0/log/?<id>  ──→  data/tenhou/xml/<id>.xml  (单线程+1s限速)
```

**关键政策约束** (来源: [houou-logs](https://github.com/Apricot-S/houou-logs) README + 天凤作者 [tsuno 推文](https://x.com/tsuno_s/status/1804487739657580636)):
- 🔴 **禁止再分发**：下载的 XML 不得公开/镜像/分享，仅限本地训练使用
- 🔴 **单线程/单会话**：多线程下载会被封 IP
- 建议请求间隔 ≥ 1 秒（本项目 `tenhou_download.py` 默认 `--sleep 1.0`）

### 1.3 实测数据量 (2026-07-29 ~ 08-05, 7 天)

| 项目 | 数量 |
|------|------|
| scc 索引文件 | 175 个 (按小时) |
| 提取的 log id (四人凤桌南半庄) | **2919 局** |
| 每 XML 平均大小 | ~15 KB |
| 7 天 XML 总量 (估算) | ~45 MB |
| 全量下载耗时 (单线程 1s 间隔) | ~50 分钟 |

### 1.4 相关文件

| 文件 | 作用 |
|------|------|
| `scripts/tenhou_download.py` | 三步下载器 (download-index / extract-ids / download-xml / stats) |
| `scripts/tenhou_parser.py` | XML → (obs, Action) 解析器雏形 |
| `data/tenhou/scc_cache/` | 原始 scc 索引 (gzip) |
| `data/tenhou/log_ids.json` | 提取的 log id 列表 |
| `data/tenhou/xml/` | 解压后的 XML 牌谱 |
| `data/tenhou/download_state.json` | XML 下载断点续传状态 |

---

## 2. 天凤 XML 格式 ↔ 项目数据结构映射

### 2.1 牌编码 (Hai 布局，需转换)

⚠️ **天凤 XML 里所有数字（`hai`、`<T66/>`、`<D127/>`、`mentsu`）都是【Hai 布局】**，不是 `pict_type + 34*instance`：
- **牌型 = `id >> 2`**（除以4，得到 0-33）
- 同牌型的 4 张实例是连续 id（如 1m = {0,1,2,3}，5m = {16,17,18,19}，E = {108,109,110,111}）
- **赤牌** = `(id & 3) == 0 且牌型是 5m/5p/5s`（即 5m赤=16, 5p赤=52, 5s赤=88）

转换函数（见 `scripts/tenhou_parser.py: tenhou_id_to_tile`）：
```python
value = tid >> 2                                  # 牌型 0-33
is_red = (tid & 3) == 0 and value < 27 and value % 9 == 4   # 5m/5p/5s 的第0张实例是赤
return Tile(value=value, is_red=is_red)
```

✅ 与本项目 `actions.py` 的 `Tile.value`（0-33）**编码一致**，只需 `>>2` 一步转换。此转换经 `mjlog2mjai` 权威 `translate()` 函数交叉验证（30 个 XML、所有鸣牌事件 645/645 完全匹配）。

### 2.2 动作标签映射

天凤 XML 用单字母前缀区分玩家座位 (who=0/1/2/3)：

| 标签前缀 | 含义 | who | 对应项目动作 |
|---------|------|-----|-------------|
| `T`/`U`/`V`/`W` + 数字 | 摸牌 | 0/1/2/3 | (流程，非玩家决策) |
| `D`/`E`/`F`/`G` + 数字 | 打牌 | 0/1/2/3 | `Action(DISCARD, tile)` |
| `<N who m>` | 鸣牌 | who | `Action(CHI/PON/KAN, ...)` (见 §2.3) |
| `<REACH step=1 who>` | 立直宣言 | who | `Action(RIICHI, riichi_discard=下张打牌)` |
| `<AGARI who ...>` | 和了 | who | `Action(TSUMO 或 RON)` |
| `<RYUUKYOKU ...>` | 流局 | — | `Action(SPECIAL_DRAW)` |

**模切/手切判定**: 天凤不直接标注。需在 replayer 里比较"打出的牌是否等于刚摸的牌"(`st.drawn[who].value == tile.value` → tsumogiri)。这个区分对 IL 有用（专家手切决策信息量更大）。

### 2.3 鸣牌 `m` 参数解码 (`<N who=".." m=".."/>`)

`m` 是十六进制串，低位编码副露类型：

| `m & 0x03` | 类型 | 项目动作 |
|-----------|------|---------|
| `0x1` | 吃 (CHI) | `Action(CHI, chi_tiles=(...))` |
| `0x3` | 碰 (PON) | `Action(PON, tile)` |
| `0x5` | 加杠 (Kakan) | `Action(KAN, kan_type=ADDED)` |
| `0x7` | 大明杠 (Daiminkan) | `Action(KAN, kan_type=OPEN)` |
| `m & 0x10` 类标记 | 暗杠 (Ankan) | `Action(KAN, kan_type=CLOSED)` |

高位编码具体的牌组合。完整解码较复杂（涉及红牌、来源玩家位），当前 parser 雏形留 TODO，参考 [Suphx](https://arxiv.org/abs/2003.13590) 论文附录 B 或 [tenhou-to-mjai](https://github.com/NikkeTryHard/tenhou-to-mjai)。

### 2.4 单局结构 `<INIT>` 字段

```xml
<INIT seed="骰1,骰2,宝指示id,本场,?,?" ten="点0,点1,点2,点3" oya="亲"
      hai0=".." hai1=".." hai2=".." hai3=".."/>
```

→ 对应项目 `GameState.reset_new_hand()` 后的状态：
- `oya` → `dealer_index`
- `ten` → `players[i].score`
- `hai{0..3}` → 初始 13 张手牌
- `seed[2]` → `wall.dora_indicators[0]`
- 场风/局数需从 `<GO>` 标签或 log_id 推断（半庄: 东1~南4）

---

## 3. 训练对接方案

### 3.1 三种可用范式

| 范式 | 用法 | 适配本项目 |
|------|------|-----------|
| **行为克隆 (BC / IL)** | 监督学习直接拟合 `(obs → expert_action)` | ✅ 最直接，可作 RL 预训练 |
| **Offline RL** (CQL/BCQ) | 在固定数据集上学 Q，避免 OOD | ⚠️ 需改造当前 DQN |
| **RL 预训练 + Self-play 微调** | BC 初始化 → 再用 `ParallelCollector` 自博弈 | ✅ 推荐，最佳收益 |

### 3.2 推荐路径：BC 预训练 → RL 微调

```
天凤 XML
   │  tenhou_parser.py (重放 + 采样)
   ▼
(obs, expert_action) 样本集  ──┐
   │                           │
   │  BC 监督训练               │  对接点: 复用 StateEncoder 编码 obs
   ▼                           │          复用 Action.to_feature_vector
DQN 网络权重初始化 (预训练)     │
   │                           │
   │  切到 RL 模式              │
   ▼                           │
ParallelCollector self-play  ←─┘  现有训练管线无需改动
   │
   ▼
最终 agent
```

### 3.3 关键对接点

**(A) Observation 编码一致性**

本项目 `StateEncoder.encode(game_state, player_index)` 输出的字段，parser 需逐一对齐：

| `StateEncoder` 字段 | 来源 | parser 当前是否覆盖 |
|--------------------|------|-------------------|
| `state.hand` (34) | 目标玩家手牌计数 | ✅ 真实 GameState 重放 |
| `state.melds` (34) | 自己副露计数 | ✅ 含 chi/pon/kan 副露维护 |
| `state.discards` (4×34) | 4 家弃牌河 | ✅ 逐事件维护 |
| `state.dora` (34) | 宝牌指示牌 | ✅ INIT seed[5] + `<DORA>` |
| `state.wind` (2) | 场风/自风 | ✅ 从 seed[0] 推场风, seat_wind=(i-oya)%4 |
| `state.game_progress` (4) | 局/本场/立直棒/余牌 | ✅ round_number/honba/riichi_sticks/turn_number |
| `state.last_action` (35) | 上一动作 | ✅ 每步更新 `last_action_info` |
| `state.scores` (4) | 玩家点数 | ✅ INIT ten × 100 |
| `action_candidates` + `mask` | 候选动作集 | ✅ `ActionValidator.get_legal_actions_on_draw/response` |

**(B) Action 对齐**

parser 已能输出项目原生 `Action` 对象：DISCARD / RIICHI / CHI / PON / KAN(closed/added/open)。鸣牌 Action 的 `tile`/`chi_tiles`/`kan_type` 均从 m 解码 + 当前弃牌还原。和了 (TSUMO/RON) 样本采集为 TODO (重放器在 AGARI 处停止)。

**(C) 候选动作集生成 (IL label)**

每个决策点：
1. replayer 用真实 `GameState` 维护状态 (天凤发牌)
2. 调 `ActionValidator.get_legal_actions_on_draw/response(state, player)` 生成候选集
3. 用 `_match_expert_in_candidates` 在候选集中定位专家动作索引 → IL label
4. 调 `StateEncoder.encode(state, player, candidates)` 编码 obs

15 个半庄 × 4 家实测：5231 样本 (DISCARD 5019 / PON 109 / CHI 90 / RIICHI 12 / KAN 1)，匹配率 100%。

---

## 3.4 完整训练工作流 (BC 预训练 → RL 微调)

```bash
# 1. 下载数据 (见 §1)
python scripts/tenhou_download.py download-index --days 7
python scripts/tenhou_download.py extract-ids --filter "四鳳南"
python scripts/tenhou_download.py download-xml --sleep 1.0

# 2. 构建 IL 数据集 (XML -> 分片 npz)
python scripts/build_dataset.py --target-players 0,1,2,3 --shard-size 2000

# 3. 行为克隆训练 (监督学习)
python scripts/train_bc.py --epochs 20 --batch-size 256 --lr 1e-3
# 输出: data/models/bc/bc_best.pt

# 4. (可选 A) 直接评估 BC-only agent
python scripts/evaluate.py --ckpt data/models/bc/bc_best.pt --opponent heuristic --games 20

# 4. (可选 B) BC -> RL 桥接, 再用 self-play 微调
python scripts/bc_to_rl.py --bc data/models/bc/bc_best.pt --out data/models/bc/bc_rl_init.pt
python scripts/train.py --resume data/models/bc/bc_rl_init.pt   # RL 微调起点

# 5. 评估 RL 微调后的 agent
python scripts/evaluate.py --ckpt <rl_ckpt.pt> --opponent heuristic --games 50
```

| 文件 | 作用 |
|------|------|
| `scripts/tenhou_download.py` | 三步下载器 (scc → log id → XML) |
| `src/env/core/tenhou_meld.py` | 鸣牌 m 解码 (mjlog2mjai 校准) |
| `scripts/tenhou_parser.py` | XML → IL 样本 (真实 GameState 重放) |
| `scripts/build_dataset.py` | 样本 → 分片 npz 数据集 |
| `scripts/train_bc.py` | 行为克隆训练 → bc_best.pt |
| `scripts/bc_to_rl.py` | BC 权重 → RL checkpoint |
| `scripts/evaluate.py` | 评估 (兼容 BC/RL checkpoint) |

---

## 4. 当前完成度与 TODO

### ✅ 已完成
- [x] 下载链路打通 (list.cgi → scc → log id → XML)
- [x] 7 天 175 个 scc 索引、2919 个 log id (凤桌南半庄)
- [x] XML 解析器：单局/多局重放，生成 (obs, Action) 样本
- [x] 牌编码 (Hai 布局 `id>>2`)、模切/手切、立直识别正确
- [x] 与项目 `Tile` / `Action` 接口对接验证
- [x] **parser 接入真实 `GameState` 重放**：驱动 `GameState` 按天凤发牌，逐事件维护状态，调 `ActionValidator` 生成合法候选动作集
- [x] **`<N m>` 鸣牌完整解码** (chi/pon/kakan/ankan/daiminkan)：经 `mjlog2mjai` 权威输出交叉验证，645/645 完全匹配
- [x] **observation 全字段填充** (wind/game_progress/last_action/scores)：StateEncoder 8 个字段全部正确
- [x] **多局重放 + 4 家全采集**：`max_kyoku=8` 跑通整半庄，4 家决策均采集
- [x] **批量化数据集导出** (`scripts/build_dataset.py`)：XML → 分片 `.npz`，格式与 DQNAgent 输入一致 (state_flat/action_cands/action_mask/action_idx)
- [x] **BC 训练脚本** (`scripts/train_bc.py`)：监督训练 DuelingDQNNet，输出 `bc_best.pt`
- [x] **BC → RL 切换** (`scripts/bc_to_rl.py`)：BC 权重转 `train.py --resume` 可加载的 checkpoint，无缝衔接 RL 微调
- [x] **评估** (`scripts/evaluate.py`)：已兼容 BC 原始格式 / bc_to_rl 格式 / RL checkpoint，支持 BC-only / RL-only / BC+RL 对比

### 🔲 TODO (后续优化)

**P1 - 提升数据质量**
- [ ] 数据增强：手牌内部排列不变性、座位对称性 (4 家视角已采，但可做旋转增广)
- [ ] RON/TSUMO/SPECIAL_DRAW 样本采集 (当前重放器在 AGARI 处停止，未采和了决策)

**P2 - 训练集成 (进阶)**
- [ ] 大规模 BC 训练 (当前 demo 用 5 XML = 1880 样本；全量 2919 局约 ~2M 样本)
- [ ] BC → RL 实际微调实验 + 段位/胜率对比报告
- [ ] Offline RL 方案探索 (BCQ/CQL) 作为 BC+RL 的替代

---

## 5. 关键参考

- **Suphx 论文**: [https://arxiv.org/abs/2003.13590](https://arxiv.org/abs/2003.13590) (微软, 凤桌超人 AI)
- **houou-logs** (当前唯一维护的下载工具): [https://github.com/Apricot-S/houou-logs](https://github.com/Apricot-S/houou-logs)
- **tenhou-to-mjai** (XML→MJAI 转换, 含完整 m 解码): [https://github.com/NikkeTryHard/tenhou-to-mjai](https://github.com/NikkeTryHard/tenhou-to-mjai)
- **天凤牌谱采集教程 (中文, 2020)**: [github.com/NotoOotori](https://github.com/NotoOotori/notoootori.github.io/blob/master/_posts/2020-07-28-天凤牌谱采集及分析.md)
- **天凤使用政策 (作者推文)**: [https://x.com/tsuno_s/status/1804487739657580636](https://x.com/tsuno_s/status/1804487739657580636)
