# 设计文档 02：手牌分解算法设计 (HAND_DECOMPOSITION_DESIGN)

> 状态：**草案 v1** ｜ 优先级：**最高** ｜ 关联代码：`src/env/core/rules/hand_analyzer.py`
> 本文是**规则层最大技术债**的设计。当前 `HandAnalyzer` 的核心递归分解是 TODO 占位（回退到 value-based），导致无法判定一杯口/三色/符数。本设计目标是彻底解决。

---

## 1. 为什么这是最难的部分

手牌分解是规则层的"地基"，向上支撑：
- **和牌形判定**（能否和）
- **听牌判定 / 听牌枚举**（振听、立直宣言依赖）
- **役种判定**（一杯口需要知道哪两个顺子同值；三色需要跨花色比对；国士/七对子是特殊形）
- **符数计算**（雀头符需知雀头是否役牌；刻子/杠子符需知明暗、是否幺九）

难点在于：
1. **同一副牌可能有多种分解**，必须穷举所有分解取"役/番最优"。
2. **性能敏感**：`find_riichi_discards` 会对每张可打牌做一次 tenpai 判定，tenpai 内部又对 34 种牌做 win 检查。朴素递归会导致 `O(34 × 递归分解)` 每次立直判定。
3. **Tile 实例 vs value**：赤宝牌要单独计数，但役种判定主要按 value；必须同时维护两种视图。

---

## 2. 三种和牌形定义

任何和牌必居其一（互斥）：

| 形 | 别名 | 结构要求 | 门清要求 |
|----|------|----------|----------|
| **standard（标准型）** | 4面子1雀头 | 4个面子(顺子/刻子/杠子) + 1个雀头(对子) = 14张 | 否（副露后只需补齐手牌内面子） |
| **chiitoitsu（七对子）** | 七对 | 7个不同的对子 = 14张 | **必须门清** |
| **kokushi（国士无双）** | 十三面 | 13种幺九字牌各1 + 任1种成对 = 14张 | **必须门清** |

> 杠子(kantsu)在分解中视为特殊的刻子（4张），但符数按杠子算。

---

## 3. 数据结构（复用并扩展现有定义）

现有 `HandComponent` / `WinForm`（hand_analyzer.py:27-69）已基本可用，明确字段语义：

```python
@dataclass(frozen=True)
class HandComponent:
    type: str                      # "shuntsu"|"koutsu"|"kantsu"|"pair"|"kokushi_single"
    tiles: Tuple[Tile, ...]        # 必须是 Tile 实例元组，按 value 排序
    is_open: bool = False          # True=来自副露（player.melds），False=手牌内

    @property
    def value(self) -> int:        # 代表值（最小value），用于排序
        return self.tiles[0].value

@dataclass(frozen=True)
class WinForm:
    hand_type: str                 # "standard"|"chiitoitsu"|"kokushi"
    components: List[HandComponent]
    winning_tile: Tile             # 和了哪张（役种判定需要，如岭上/海底区分）

    @property
    def pair(self) -> Optional[HandComponent]: ...   # standard 的雀头
    @property
    def all_tiles(self) -> List[Tile]: ...
```

**关键约束**：
- `tiles` 必须是 **Tile 实例**而非 value，否则丢失赤宝牌/具体来源信息。
- 副露面子 `is_open=True`，其 `tiles` 来自 `Meld.tiles`。
- 一个 `WinForm` 描述"一种"分解；一副和牌可能有多个 `WinForm`，Scoring 取最优。

---

## 4. 算法选型：预计算 shanten 表 + 实例级回溯分解（双阶段）

经评估，单一算法无法兼顾性能与正确性，采用 **双阶段** 设计：

### 阶段 A：向听数 / 听牌 —— 用**预计算表**（高性能）

- 为每种数牌花色（万/筒/索，各 9 个 value，每种最多 4 张）预计算"面子+搭子分解表"。
- 字牌只需刻子/对子，直接按计数处理。
- 用经典的 **shanten DP / 枚举表算法**（开源 mahjong 库通用思路：把每花色 1-9 的计数向量，枚举"取几个顺子/刻子/雀头"后的最小向听）。
- 预计算在 `HandAnalyzer.__init__` 时一次性生成（约 1ms），之后 O(1) 查表。

> 此阶段产出：`shanten(hand, melds) -> int` 和 `find_wait_tiles(hand, melds) -> Set[int]`。
> 用于：ActionValidator 的立直判定、振听判定。**这是性能关键路径，必须用表。**

### 阶段 B：完整和牌分解 —— 用**实例级回溯**（仅在确认和牌后）

- 仅当阶段 A 判定 shanten == -1（已和牌）时才执行。
- 回溯枚举所有 standard 分解（保留 Tile 实例），用于役种/符数。
- 七对子/国士直接按计数判定，无需回溯。

> 此阶段产出：`find_all_winning_forms(hand, melds, winning_tile) -> List[WinForm]`。
> 用于：Scoring 役种/符数计算。**频率低（仅和牌时），可容忍较高单次成本。**

**为何不全部用回溯？** 立直判定时每张可打牌都要 tenpai 检查，全回溯会慢 10~100 倍。

---

## 5. 阶段 A 详细设计：shanten 表

### 5.1 数牌分解表构造

对单一花色（9 种 value，每种 0~4 张），定义状态为 `(c1,c2,...,c9)` 共 5^9 ≈ 200万 状态——过大。
**优化**：实际只需枚举"取了多少个顺子/刻子/雀头"，经典做法是分段 DP：

```
对每个花色独立计算，把"去掉 k 个面子、j 个部分搭子(toitsu/kanchan/penchan)"后剩余最少无效牌数记入表。
再用全局组合得到 4 面子 + 0/1 雀头 的最小向听。
```

> 实现可参考公开的"mahjong shanten"算法（如 Tomohxx 的 C++/Python 实现）。**注意：参考算法思路，不拷贝版权代码，自行实现并加测试。**

### 5.2 find_wait_tiles 算法

```
输入: 13 张手牌(含副露折算)
对每个候选 value v in 0..33 (跳过已有4张的):
    if 加入 v 后 shanten(hand+[v]) == -1:
        waits.add(v)
return waits
```

- 复用 shanten 表，34 次查表，极快。
- 注意赤宝牌：wait 只关心 value，不关心 is_red。

### 5.3 性能预算
- 表构造：< 5ms（init 时一次）。
- `is_tenpai`：< 0.1ms。
- `find_wait_tiles`：< 1ms。

---

## 6. 阶段 B 详细设计：实例级标准型回溯分解

### 6.1 输入预处理

```python
def find_all_winning_forms(hand_tiles, melds, winning_tile):
    # 1. 副露转 HandComponent（is_open=True）
    open_components = [self._meld_to_component(m) for m in melds]
    # 2. 手牌必须是 14 - sum(副露张数) 张（含 winning_tile）
    # 3. 若门清，先尝试 kokushi / chiitoitsu
    forms = []
    if not melds:
        forms += self._find_kokushi_forms(hand_tiles, winning_tile)
        forms += self._find_chiitoitsu_forms(hand_tiles, winning_tile)
    # 4. standard 分解
    forms += self._find_standard_forms(hand_tiles, open_components, winning_tile)
    return forms
```

### 6.2 standard 回溯算法

```
_find_standard_forms(hand_tiles, open_components, winning_tile):
    forms = []
    tile_counts = Counter(hand_tiles, by value)   # value 视图
    # 副露已贡献的面子数
    melds_needed = 4 - len(open_components)

    # 枚举雀头候选：所有 count>=2 的 value
    for pair_value in {v for v,c in tile_counts.items() if c>=2}:
        # 从手牌移除2张 pair_value 的 Tile 实例
        pair_tiles = 取2张(pair_value)
        remaining = hand_tiles - pair_tiles   # Tile 实例列表

        # 回溯找 melds_needed 个面子
        for meld_set in _backtrack_melds(remaining, melds_needed):
            forms.append(WinForm("standard",
                                 open_components + meld_set + [pair_tile_component],
                                 winning_tile))
    return forms
```

### 6.3 `_backtrack_melds` 回溯（核心，必须返回 Tile 实例）

```
_backtrack_melds(tiles, k):
    # tiles: 剩余 Tile 实例列表；k: 还需找几个面子
    if k == 0 and tiles 为空: yield []  # 成功
    if tiles 为空 or k == 0: return

    取 tiles 中最小 value 的 tile t（剪枝：必须被某个面子覆盖）
    counts = Counter(tiles, by value)

    # 分支1: t 作刻子 (counts[t.value]>=3)
    if counts[t.value] >= 3:
        取3张(t.value) -> m; yield [m] + _backtrack_melds(tiles-m, k-1)

    # 分支2: t 作顺子 (t.value<27 且 t.value%9<=6)
    if 数牌 and counts[v]>=1 and counts[v+1]>=1 and counts[v+2]>=1:
        取 v,v+1,v+2 各1张 -> m; yield [m] + _backtrack_melds(tiles-m, k-1)

    # 若 t 既不能刻也不能顺 -> 剪枝失败（保证最小value总被消耗，避免重复枚举）
```

**剪枝关键**：始终处理"最小 value 的牌"，保证每个面子从最小牌开始构造，避免顺序不同导致的重复分解。

### 6.4 复杂度与优化
- 最坏情况（如清一色 14 张）分解数可达数十种，但和牌时才触发，可接受。
- **优化**：若仅需"是否存在合法分解"（如 `check_win_shape`），找到第一个即返回；若需全部（Scoring 取最优），才完整枚举。

---

## 7. 七对子 / 国士判定（无需回溯）

### 7.1 七对子
```
len(hand)==14 且 Counter(hand).value 去重后 == 7 且 每种恰好2张
注意：标准规则要求7对"互不相同"（即不能是4张同value当两对）。
```

### 7.2 国士无双
```
len(hand)==14 且 集合(hand values) ⊇ TERMINAL_HONOR_VALUES(13种)
且 其中恰好1种是2张(雀头)，其余12种各1张。
```

---

## 8. 与 HandAnalyzer 现有代码的对齐

现有代码（hand_analyzer.py）已具备：
- ✅ 数据结构 `HandComponent` / `WinForm`（§3）。
- ✅ `_find_chiitoitsu_forms` / `_find_kokushi_forms`（基本正确，§7）。
- ✅ `_find_melds_recursive_by_value`（value-based，可保留作 shanten 兜底）。
- ❌ `_find_melds_recursive_by_tile` 是 TODO（§6.3 要实现的）。
- ❌ 缺 shanten 表（§5 要新增）。
- ❌ `find_wait_tiles` 当前调 `check_win_shape`（慢），应改用 §5.2 表查。

**实施顺序**：
1. 先实现 §5 shanten 表 + `find_wait_tiles`（解锁立直/振听性能）。
2. 再实现 §6 `_backtrack_melds`（解锁役种/符数正确性）。
3. 保留 value-based 作 fallback 与单元测试对照。

---

## 9. 测试用例（必须覆盖）

| 用例 | 期望 |
|------|------|
| 123m 456p 789s 11z + 2z | standard，听 1种 |
| 112233m 4p 55s 66z 77z | 多面听，需穷举分解 |
| 1122334455667p | 七对子（门清）/也可 standard |
| 19m19p19s 1234567z 各1 + 重复1 | 国士 |
| 清一色 14张 多分解 | find_all_forms 返回多个 WinForm |
| 含赤5的顺子 4r5m6m | tiles 保留 is_red 信息 |
| 副露 PON(中) + 手牌 3 面子雀头 | open_components 正确 |
| 4张同value | 不能当两个对子（七对子），可当刻子+单张（standard 失败） |

---

## 10. 验收标准

1. ✅ §5 shanten 表实现，`is_tenpai` < 0.1ms。
2. ✅ §6 实例级回溯实现，`find_all_winning_forms` 返回 Tile 实例级分解。
3. ✅ §9 全部测试用例通过。
4. ✅ 用 `find_all_winning_forms` 喂给 Scoring，能正确识别一杯口/三色/对对和。
5. ✅ 与现有 value-based 实现在 1000 随机手牌上结果一致（回归对照）。

---

## 11. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-01 | v1 初稿，确立双阶段（shanten表 + 实例回溯）方案 |
