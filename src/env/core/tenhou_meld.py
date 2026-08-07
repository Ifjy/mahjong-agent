"""
天凤 mjlog XML 牌谱的鸣牌 (m 参数) 解码器
==========================================

完整支持 5 种鸣牌: CHI(吃) / PON(碰) / KAKAN(加杠) / ANKAN(暗杠) / DAIMINKAN(大明杠)
算法移植自天凤官方 http://tenhou.net/img/tehai.js (经 fstqwq/mjlog2mjai 验证),
并用 mjlog2mjai 在真实牌谱上的转换输出做权威校准。

⚠️ 关键: 天凤 XML 里所有数字 (hai / <T..> / <D..> / mentsu) 都是【Hai 布局】:
    牌型 = id >> 2 (0-33), 4 个连续 id 表示同牌型的 4 张实例,
    赤牌 = (id & 3) == 0 且牌型是 5m/5p/5s.
    (不是 pict_type + 34*instance 布局!)

位定义 (m 参数, 十进制整数, 二进制位运算):
    bit 2 (0x04): CHI
    bit 3 (0x08): PON
    bit 4 (0x10): KAKAN (加杠)
    bit 5 (0x20): Nuki (北抜, 三麻专用, 不支持)
    bit 0-1 (0x03): callee 相对方向 (callee_abs = (caller + rel) % 4)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class TenhouMeld:
    """解码后的鸣牌。所有 tile 字段存【Hai 布局 id】(0-135), 由调用方 >>2 转牌型。"""
    kind: str                        # 'chi'|'pon'|'kakan'|'ankan'|'daiminkan'
    mentsu: List[int]                # Hai id 列表: chi/pon/kakan=3张; ankan/daiminkan 解码见下
    called_idx: int                  # mentsu[called_idx] 是被叫的那张 (chi:在顺子中; pon/kakan:第0位)
    callee_who: int                  # 被叫玩家绝对座位 (0-3); ankan 时无意义


def _parse_shuntsu(meld: int) -> Tuple[List[int], int]:
    """吃: 返回 (3 个 Hai id, called_index). 移植自 tehai.js / mjlog2mjai."""
    t = (meld & 0xfc00) >> 10
    r = t % 3                       # called_position
    t = t // 3
    t = 9 * (t // 7) + (t % 7)      # pict_type
    t *= 4
    h = [
        t + ((meld & 0x18) >> 3),
        t + 4 + ((meld & 0x60) >> 5),
        t + 8 + ((meld & 0x180) >> 7),
    ]
    # 重排使 called 移到 index r 对应位置 (与 mjlog2mjai 一致, called 在 mentsu[0])
    if r == 1:
        h = [h[1], h[0], h[2]]
    elif r == 2:
        h = [h[2], h[0], h[1]]
    return h, 0   # called 在 h[0]


def _parse_koutsu(meld: int) -> Tuple[List[int], int]:
    """碰: 返回 (3 个 Hai id, called_index=0). called 在 mentsu[0]."""
    unused = (meld & 0x60) >> 5
    t = (meld & 0xfe00) >> 9
    r = t % 3                       # called_index (原始)
    t = (t // 3) * 4
    if unused == 0:
        h = [t + 1, t + 2, t + 3]
    elif unused == 1:
        h = [t, t + 2, t + 3]
    elif unused == 2:
        h = [t, t + 1, t + 3]
    else:  # unused == 3
        h = [t, t + 1, t + 2]
    # 重排: 把原始 h[r] (called) 移到 index 0
    if r == 1:
        h = [h[1], h[0], h[2]]
    elif r == 2:
        h = [h[2], h[0], h[1]]
    return h, 0


def _parse_kan(meld: int) -> Tuple[int, bool]:
    """杠 (ankan/daiminkan): 返回 (基准 Hai id, is_ankan). 完整4张 = base, base+1, base+2, base+3."""
    hai0 = (meld & 0xff00) >> 8
    kui = meld & 0x3
    is_ankan = (kui == 0)
    if is_ankan:
        hai0 = (hai0 & ~3) + 3       # 暗杠: 标准化到该牌型第4张位置
    return hai0, is_ankan


def decode_meld(meld: int, caller: int) -> TenhouMeld:
    """
    解码 <N who=caller m=meld>. 返回的 mentsu 是 Hai id (caller 手牌消耗的牌 + called).
    对 chi/pon/kakan: mentsu[0] 是 called (被叫那张), mentsu[1:] 是手牌消耗的牌.
    """
    callee_rel = meld & 0x3
    callee_abs = (caller + callee_rel) % 4

    if meld & 0x04:                  # CHI
        h, _ = _parse_shuntsu(meld)
        return TenhouMeld(kind='chi', mentsu=h, called_idx=0, callee_who=callee_abs)
    if meld & 0x08:                  # PON
        h, _ = _parse_koutsu(meld)
        return TenhouMeld(kind='pon', mentsu=h, called_idx=0, callee_who=callee_abs)
    if meld & 0x10:                  # KAKAN
        h, _ = _parse_koutsu(meld)   # kakan 与 pon 同构 (返回原3张)
        return TenhouMeld(kind='kakan', mentsu=h, called_idx=0, callee_who=callee_abs)
    if meld & 0x20:                  # NUKI
        raise NotImplementedError("北抜 (三麻) 不支持")
    # ANKAN / DAIMINKAN
    base, is_ankan = _parse_kan(meld)
    return TenhouMeld(
        kind='ankan' if is_ankan else 'daiminkan',
        mentsu=[base],
        called_idx=0,
        callee_who=caller if is_ankan else callee_abs,
    )
