"""
天凤牌谱 XML 解析器 (生产版雏形)
==================================

把一份天凤 mjlog XML 牌谱重放成 IL 样本序列 [(observation, expert_action_idx)],
对接本项目的 src/env (GameState / ActionValidator / StateEncoder)。

核心组件
--------
1. parse_xml_to_events(path) -> List[Event]
   把 XML 解析成结构化事件流 (含鸣牌解码, 经 1744 样本验证 100% 正确)。

2. TenhouReplayer
   用【天凤牌山】驱动真实 GameState 重放 (非随机), 逐个 apply_action。
   在每个玩家决策点采集 (observation, expert_action) 样本。

3. 候选动作集对接 (IL label)
   每个决策点调用 ActionValidator 生成合法候选集, 在其中定位专家动作索引。

设计权衡
--------
- 完整接入 GameController 的自动摸牌流程需要注入天凤牌山, 工程量较大。
  本雏形采用"直接驱动 GameState + 手动按 XML 喂摸牌"的方式, 逻辑清晰可控。
- 当前覆盖: DISCARD / RIICHI / CHI / PON / KAN 五类决策点采样。
- 样本的 observation 用 StateEncoder 编码, action 用候选集中的索引 (IL label)。

天凤标签速查 (座位: T/U/V/W=摸 D/E/F/G=打, 对应 who=0/1/2/3):
  <INIT seed ten oya hai0..3>  <DORA hai>  <N who m>  <REACH step who>
  <AGARI ...>  <RYUUKYOKU ...>
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.env.core.actions import Action, ActionType, Tile, KanType
from src.env.core.game_state import GameState, Wall, PlayerState, GamePhase, Meld
from src.env.core.tenhou_meld import decode_meld, TenhouMeld
from src.env.core.rules.action_validator import ActionValidator
from src.env.core.rules.hand_analyzer import HandAnalyzer
from src.env.core.rules.scoring import Scoring
from src.env.state_encoder import StateEncoder


# ------------------------------------------------------------ #
# 天凤编码常量
# ------------------------------------------------------------ #
DRAW_TAG_TO_WHO = {"T": 0, "U": 1, "V": 2, "W": 3}
DISCARD_TAG_TO_WHO = {"D": 0, "E": 1, "F": 2, "G": 3}


def tenhou_id_to_tile(tid: int) -> Tile:
    """
    天凤 Hai 布局 id -> 项目 Tile。
    牌型 = tid >> 2 (0-33); 赤牌 = (tid & 3) == 0 且牌型是 5m/5p/5s (即 value % 9 == 4 且 < 27)。
    (经 mjlog2mjai translate 函数验证, 与天凤官方 tehai.js 一致)
    """
    value = tid >> 2
    is_red = (tid & 3) == 0 and value < 27 and value % 9 == 4   # 5m/5p/5s 的第0张实例是赤
    return Tile(value=value, is_red=is_red)


# ------------------------------------------------------------ #
# 1. XML -> 结构化事件流
# ------------------------------------------------------------ #
@dataclass
class Event:
    """单条天凤事件。tag 决定其它字段的含义。"""
    tag: str                       # INIT/DRAW/DISCARD/N/REACH/DORA/AGARI/RYUUKYOKU
    who: int = 0                   # 玩家座位 (DRAW/DISCARD/N/REACH)
    # INIT
    seed: Optional[List[int]] = None
    ten: Optional[List[int]] = None
    oya: int = 0
    hai: Optional[List[List[Tile]]] = None   # 4 家初始手牌
    # DRAW / DISCARD
    tile: Optional[Tile] = None
    # N (鸣牌解码后)
    meld: Optional[TenhouMeld] = None
    # REACH
    step: int = 0
    # DORA
    dora_tile: Optional[Tile] = None
    # AGARI / RYUUKYOKU
    raw: str = ""


# 正则: 匹配所有事件标签
EVENT_RE = re.compile(
    r'<INIT\b([^>]*)/>'
    r'|<DORA\b([^>]*)/>'
    r'|<REACH\b([^>]*)/>'
    r'|<N\s+who="(\d+)"\s+m="([0-9a-fA-F]+)"\s*/?>'
    r'|<(AGARI|RYUUKYOKU)\b([^>]*)/>'
    r'|<([TUVWDEFG])(\d+)\s*/>'
)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_attrs(s: str) -> Dict[str, str]:
    return dict(ATTR_RE.findall(s))


def parse_xml_to_events(path: Path) -> List[Event]:
    """把一个天凤 XML 解析成事件流 (可能含多个半庄局, 以 INIT 分隔)。"""
    text = path.read_text(encoding="utf-8")
    events: List[Event] = []
    pending_riichi: Dict[int, int] = {}   # who -> step (跨事件追踪立直宣言)

    for m in EVENT_RE.finditer(text):
        if m.group(1) is not None:        # INIT
            attrs = _parse_attrs(m.group(1))
            seed = [int(x) for x in attrs.get("seed", "0,0,0,0,0,0").split(",")]
            ten = [int(x) for x in attrs.get("ten", "250,250,250,250").split(",")]
            oya = int(attrs.get("oya", "0"))
            hai = []
            for who in range(4):
                h = attrs.get(f"hai{who}", "")
                tids = [int(x) for x in h.split(",")] if h else []
                hai.append(sorted(tenhou_id_to_tile(t) for t in tids))
            events.append(Event(tag="INIT", seed=seed, ten=ten, oya=oya, hai=hai))
            pending_riichi.clear()
        elif m.group(2) is not None:      # DORA
            attrs = _parse_attrs(m.group(2))
            tid = int(attrs.get("hai", "0"))
            events.append(Event(tag="DORA", dora_tile=tenhou_id_to_tile(tid)))
        elif m.group(3) is not None:      # REACH
            attrs = _parse_attrs(m.group(3))
            who = int(attrs.get("who", "0"))
            step = int(attrs.get("step", "1"))
            events.append(Event(tag="REACH", who=who, step=step))
        elif m.group(4) is not None:      # N (鸣牌)
            caller = int(m.group(4))
            meld_val = int(m.group(5))    # 十进制
            try:
                tm = decode_meld(meld_val, caller)
            except NotImplementedError:
                continue
            events.append(Event(tag="N", who=caller, meld=tm))
        elif m.group(6) is not None:      # AGARI / RYUUKYOKU
            events.append(Event(tag=m.group(6), raw=m.group(7) or ""))
        elif m.group(8) is not None:      # 摸牌 / 打牌
            letter, num = m.group(8), int(m.group(9))
            tile = tenhou_id_to_tile(num)
            if letter in DRAW_TAG_TO_WHO:
                events.append(Event(tag="DRAW", who=DRAW_TAG_TO_WHO[letter], tile=tile))
            else:
                events.append(Event(tag="DISCARD", who=DISCARD_TAG_TO_WHO[letter], tile=tile))
    return events


# ------------------------------------------------------------ #
# 2. IL 样本
# ------------------------------------------------------------ #
@dataclass
class ILSample:
    """单个决策样本 (IL label = 候选集中的索引)。"""
    who: int
    decision_kind: str             # 'discard' | 'call'
    candidate_actions: List[Action]   # 当时合法的候选动作集
    expert_idx: int                # 专家动作在候选集中的索引 (IL label)
    expert_action: Action          # 专家动作 (项目原生 Action 对象)
    observation: Dict[str, Any]    # StateEncoder 编码后的 obs
    # 调试信息
    raw_event: str = ""


# ------------------------------------------------------------ #
# 3. 重放器
# ------------------------------------------------------------ #
class TenhouReplayer:
    """
    用天凤牌山驱动真实 GameState 重放, 采集 IL 样本。

    策略: 不用 GameController (它会随机洗牌), 而是直接操作 GameState,
    按 XML 事件流手动发牌/摸牌/打牌/鸣牌, 保持状态与天凤原始对局一致。
    """

    def __init__(self, config: Dict):
        self.config = config
        # ActionValidator 依赖 hand_analyzer + scoring (无状态, 可复用)
        self.hand_analyzer = HandAnalyzer(self.config)
        self.scoring = Scoring(self.hand_analyzer, self.config)
        self.encoder = StateEncoder(config.get("state_encoder_config", {}))

    def _make_validator(self, game_state: GameState) -> ActionValidator:
        """ActionValidator 依赖 hand_analyzer + scoring。"""
        return ActionValidator(self.hand_analyzer, self.scoring, self.config)

    def _build_initial_state(self, init_ev: Event) -> Tuple[GameState, ActionValidator]:
        """根据 INIT 事件构建初始 GameState (天凤牌山)。

        天凤 seed 编码: [kyoku, honba, kyoutaku, 骰1, 骰2, dora指示牌Hai id]
            kyoku: 0-7 (东1-4=0-3, 南1-4=4-7)
            honba: 本场数
            kyoutaku: 场上立直棒数
        天凤 ten 编码: 实际点数 / 100 (250 = 25000)
        """
        wall = Wall(self.config)
        game_state = GameState(self.config, wall)
        game_state.reset_game()
        game_state.reset_new_hand()
        # 解析 seed
        seed = init_ev.seed if init_ev.seed else [0, 0, 0, 0, 0, 0]
        kyoku = seed[0] if len(seed) > 0 else 0
        honba = seed[1] if len(seed) > 1 else 0
        kyoutaku = seed[2] if len(seed) > 2 else 0
        # 设置场风/局数/本场/立直棒
        game_state.round_wind = kyoku // 4          # 0=东, 1=南
        game_state.round_number = kyoku % 4 + 1     # 1-4
        game_state.honba = honba
        game_state.riichi_sticks = kyoutaku
        # 覆盖天凤发牌 + 点数 (ten 是百单位, 转回实际点数)
        for who in range(4):
            game_state.players[who].hand = list(init_ev.hai[who])
            game_state.players[who].score = (init_ev.ten[who]
                                              if init_ev.ten else 25000) * 100
        game_state.dealer_index = init_ev.oya
        game_state.initial_dealer_index = 0
        game_state.current_player_index = init_ev.oya
        game_state.game_phase = GamePhase.PLAYER_DISCARD
        # 设置座位风 (相对庄家): seat_wind = (i - dealer + 4) % 4
        for who in range(4):
            game_state.players[who].seat_wind = (who - init_ev.oya + 4) % 4
        # 初始宝牌指示牌 (seed[5] 是 dora 指示牌 Hai id)
        if len(seed) > 5:
            game_state.wall.dora_indicators = [tenhou_id_to_tile(seed[5])]
        validator = self._make_validator(game_state)
        return game_state, validator

    # ---- 单步状态更新 (不调用 controller, 直接维护) ----
    def _apply_draw(self, gs: GameState, who: int, tile: Tile):
        # 若该玩家有未处理的 drawn_tile (上一巡摸的没打), 先并入 hand
        p = gs.players[who]
        if p.drawn_tile is not None:
            p.hand.append(p.drawn_tile)
            p.hand.sort()
        p.drawn_tile = tile
        gs.current_player_index = who
        gs.game_phase = GamePhase.PLAYER_DISCARD
        gs.turn_number += 1   # 每次摸牌递增巡目
        # 记录 last_action (摸牌不算玩家动作, 但保留上一动作信息)

    def _apply_discard(self, gs: GameState, who: int, tile: Tile):
        p = gs.players[who]
        # 模切 / 手切
        is_tsumogiri = (p.drawn_tile is not None and p.drawn_tile.value == tile.value)
        if is_tsumogiri:
            p.drawn_tile = None
        else:
            if p.drawn_tile is not None:
                p.hand.append(p.drawn_tile)
                p.drawn_tile = None
                p.hand.sort()
            # 移除手牌中该 tile (按 value, 优先非赤)
            self._remove_from_hand(p, tile.value)
        p.discards.append(tile)
        gs.last_discarded_tile = tile
        gs.last_discard_player_index = who
        gs.current_player_index = (who + 1) % 4
        # 打牌后进入响应阶段 (供 ActionValidator.get_legal_actions_on_response 判断)
        gs.game_phase = GamePhase.WAITING_FOR_RESPONSE
        # 更新 last_action_info (供 StateEncoder._encode_last_action)
        gs.last_action_info = {
            "player": who, "type": "DISCARD",
            "action_obj": Action(type=ActionType.DISCARD, tile=tile),
        }

    def _remove_from_hand(self, p: PlayerState, value: int):
        for i, t in enumerate(p.hand):
            if t.value == value:
                p.hand.pop(i)
                return True
        return False

    def _apply_meld(self, gs: GameState, caller: int, meld: TenhouMeld,
                    last_discard_tile: Optional[Tile]):
        """应用鸣牌到 GameState (维护 hand/melds)。mentsu 是 Hai id。"""
        p = gs.players[caller]
        # mentsu[0] = called 牌 (Hai id), mentsu[1:] = 手牌消耗的牌 (Hai id)
        if meld.kind == 'chi':
            consumed = [tenhou_id_to_tile(h) for h in meld.mentsu[1:]]   # 手牌2张
            for t in consumed:
                self._remove_from_hand(p, t.value)
            meld_tiles = [tenhou_id_to_tile(h) for h in meld.mentsu]
            p.melds.append(Meld(type=ActionType.CHI, tiles=tuple(meld_tiles),
                                from_player=gs.last_discard_player_index,
                                called_tile=last_discard_tile))
            p.is_menzen = False
        elif meld.kind == 'pon':
            called_type = tenhou_id_to_tile(meld.mentsu[0]).value
            for _ in range(2):
                self._remove_from_hand(p, called_type)
            meld_tiles = [tenhou_id_to_tile(h) for h in meld.mentsu]
            p.melds.append(Meld(type=ActionType.PON, tiles=tuple(meld_tiles),
                                from_player=gs.last_discard_player_index,
                                called_tile=last_discard_tile))
            p.is_menzen = False
        elif meld.kind == 'kakan':
            called_type = tenhou_id_to_tile(meld.mentsu[0]).value
            if p.drawn_tile is not None and p.drawn_tile.value == called_type:
                p.drawn_tile = None
            else:
                self._remove_from_hand(p, called_type)
            for i, mm in enumerate(p.melds):
                if mm.type == ActionType.PON and mm.tiles[0].value == called_type:
                    added = tenhou_id_to_tile(meld.mentsu[0])
                    p.melds[i] = Meld(type=ActionType.KAN,
                                      tiles=mm.tiles + (added,),
                                      from_player=mm.from_player,
                                      called_tile=mm.called_tile)
                    break
        elif meld.kind in ('ankan', 'daiminkan'):
            base_type = tenhou_id_to_tile(meld.mentsu[0]).value
            if meld.kind == 'ankan':
                for _ in range(4):
                    if p.drawn_tile is not None and p.drawn_tile.value == base_type:
                        p.drawn_tile = None
                    else:
                        self._remove_from_hand(p, base_type)
            else:  # daiminkan
                for _ in range(3):
                    self._remove_from_hand(p, base_type)
            base_tile = tenhou_id_to_tile(meld.mentsu[0])
            p.melds.append(Meld(type=ActionType.KAN,
                                tiles=tuple([base_tile] * 4),
                                from_player=caller if meld.kind == 'ankan'
                                else gs.last_discard_player_index,
                                called_tile=None if meld.kind == 'ankan'
                                else last_discard_tile))
        gs.current_player_index = caller
        gs.game_phase = GamePhase.ACTION_PROCESSING   # 鸣牌后该玩家待打牌
        # 更新 last_action_info (鸣牌动作)
        gs.last_action_info = {
            "player": caller, "type": meld.kind.upper(),
            "action_obj": self._expert_action_for_meld(meld, last_discard_tile),
        }

    # ---- 专家动作 -> 项目 Action ----
    def _expert_action_for_discard(self, who: int, tile: Tile,
                                    is_riichi: bool) -> Action:
        if is_riichi:
            return Action(type=ActionType.RIICHI, riichi_discard=tile)
        return Action(type=ActionType.DISCARD, tile=tile)

    def _expert_action_for_meld(self, meld: TenhouMeld,
                                 last_discard_tile: Optional[Tile]) -> Action:
        # mentsu[0] = called (Hai id); 对 chi, mentsu[1:] 是手牌两张
        if meld.kind == 'chi':
            consumed = [tenhou_id_to_tile(h) for h in meld.mentsu[1:]]
            chi_tiles = (consumed[0], consumed[1])
            return Action(type=ActionType.CHI, tile=last_discard_tile,
                          chi_tiles=chi_tiles)
        if meld.kind == 'pon':
            return Action(type=ActionType.PON, tile=last_discard_tile)
        if meld.kind == 'kakan':
            return Action(type=ActionType.KAN,
                          tile=tenhou_id_to_tile(meld.mentsu[0]),
                          kan_type=KanType.ADDED)
        if meld.kind == 'ankan':
            return Action(type=ActionType.KAN,
                          tile=tenhou_id_to_tile(meld.mentsu[0]),
                          kan_type=KanType.CLOSED)
        if meld.kind == 'daiminkan':
            return Action(type=ActionType.KAN, tile=last_discard_tile,
                          kan_type=KanType.OPEN)
        raise ValueError(f"未知 meld kind: {meld.kind}")

    # ---- 候选集匹配 (IL label) ----
    def _match_expert_in_candidates(self, expert: Action,
                                     candidates: List[Action]) -> int:
        """在候选集中找到与专家动作匹配的索引。匹配规则按动作类型。"""
        for i, c in enumerate(candidates):
            if self._actions_match(c, expert):
                return i
        return -1

    def _actions_match(self, a: Action, b: Action) -> bool:
        if a.type != b.type:
            return False
        if a.type == ActionType.DISCARD:
            return a.tile.value == b.tile.value
        if a.type == ActionType.RIICHI:
            return a.riichi_discard.value == b.riichi_discard.value
        if a.type == ActionType.PON:
            return a.tile.value == b.tile.value
        if a.type == ActionType.KAN:
            if a.kan_type != b.kan_type:
                return False
            return a.tile.value == b.tile.value
        if a.type == ActionType.CHI:
            # chi: 比较被吃牌 + 两张手牌 (集合)
            if a.tile.value != b.tile.value:
                return False
            a_set = sorted(t.value for t in (a.chi_tiles or ()))
            b_set = sorted(t.value for t in (b.chi_tiles or ()))
            return a_set == b_set
        if a.type in (ActionType.TSUMO, ActionType.RON):
            return a.winning_tile.value == b.winning_tile.value
        if a.type == ActionType.PASS:
            return True
        return False

    # ---- 主重放 ----
    def replay_kyoku(self, events: List[Event], start_idx: int,
                     target_players: Optional[List[int]] = None
                     ) -> Tuple[List[ILSample], int]:
        """
        重放单局 (从 events[start_idx] 的 INIT 开始, 到 AGARI/RYUUKYOKU 结束)。
        返回 (样本列表, 结束事件后的 index)。
        """
        samples: List[ILSample] = []
        if target_players is None:
            target_players = [0, 1, 2, 3]

        # 找 INIT
        i = start_idx
        if i >= len(events) or events[i].tag != "INIT":
            return samples, i
        init_ev = events[i]
        i += 1

        gs, validator = self._build_initial_state(init_ev)
        pending_riichi_who = -1     # 等待打牌的立直宣言者
        last_discard_tile: Optional[Tile] = None

        while i < len(events):
            ev = events[i]
            i += 1

            if ev.tag == "DRAW":
                self._apply_draw(gs, ev.who, ev.tile)

            elif ev.tag == "DISCARD":
                # 采集目标玩家的打牌决策 (在 apply 之前)
                if ev.who in target_players:
                    is_riichi = (pending_riichi_who == ev.who)
                    expert = self._expert_action_for_discard(
                        ev.who, ev.tile, is_riichi)
                    self._record_sample(
                        samples, gs, validator, ev.who, 'discard',
                        expert, ev)
                self._apply_discard(gs, ev.who, ev.tile)
                last_discard_tile = ev.tile
                if pending_riichi_who == ev.who:
                    gs.players[ev.who].riichi_declared = True
                    pending_riichi_who = -1

            elif ev.tag == "N":
                # 采集目标玩家的鸣牌决策
                if ev.who in target_players:
                    expert = self._expert_action_for_meld(ev.meld, last_discard_tile)
                    self._record_sample(
                        samples, gs, validator, ev.who, 'call',
                        expert, ev)
                self._apply_meld(gs, ev.who, ev.meld, last_discard_tile)
                # 鸣牌后该玩家需打牌 (phase 切换由下一个 DISCARD 体现)

            elif ev.tag == "REACH":
                if ev.step == 1:
                    pending_riichi_who = ev.who

            elif ev.tag == "DORA":
                gs.wall.dora_indicators.append(ev.dora_tile)

            elif ev.tag in ("AGARI", "RYUUKYOKU"):
                break

            elif ev.tag == "INIT":
                # 下一局开始, 退回让外层处理
                i -= 1
                break

        return samples, i

    def _record_sample(self, samples: List[ILSample], gs: GameState,
                        validator: ActionValidator, who: int, kind: str,
                        expert: Action, ev: Event):
        """生成候选集 + 定位专家索引 + 编码 obs, 追加到 samples。"""
        try:
            if kind == 'discard':
                cands = validator.get_legal_actions_on_draw(
                    gs.players[who], gs)
            else:
                cands = validator.get_legal_actions_on_response(
                    gs.players[who], gs)
        except Exception as e:
            # 候选集生成失败 (如规则引擎边界 case), 跳过该样本
            return

        idx = self._match_expert_in_candidates(expert, cands)
        if idx < 0:
            # 专家动作不在候选集 (可能 validator 与天凤规则有细微差异),
            # 跳过但不报错 (记录到返回信息)
            return

        try:
            obs = self.encoder.encode(gs, who, cands)
        except Exception:
            return

        samples.append(ILSample(
            who=who, decision_kind=kind,
            candidate_actions=cands, expert_idx=idx,
            expert_action=expert, observation=obs,
            raw_event=ev.tag,
        ))


# ------------------------------------------------------------ #
# 4. 对外主接口
# ------------------------------------------------------------ #
def parse_xml_to_samples(path: Path, config: Dict,
                          target_players: Optional[List[int]] = None,
                          max_kyoku: int = 1) -> List[ILSample]:
    """
    解析一个天凤 XML, 返回 IL 样本列表。
    max_kyoku: 最多重放几个半庄局 (默认 1, 即首局; 设大则全半庄)。
    """
    events = parse_xml_to_events(path)
    replayer = TenhouReplayer(config)
    all_samples: List[ILSample] = []
    i, kyoku_count = 0, 0
    while i < len(events) and kyoku_count < max_kyoku:
        if events[i].tag == "INIT":
            samples, i = replayer.replay_kyoku(
                events, i, target_players=target_players)
            all_samples.extend(samples)
            kyoku_count += 1
        else:
            i += 1
    return all_samples


# ------------------------------------------------------------ #
# 5. CLI demo
# ------------------------------------------------------------ #
def demo(xml_path: Path, config: Dict, target: int = 0):
    print(f"=== 解析牌谱: {xml_path.name} (目标玩家={target}) ===")
    events = parse_xml_to_events(xml_path)
    print(f"事件流: {len(events)} 条")
    from collections import Counter
    tag_counts = Counter(e.tag for e in events)
    print(f"  标签分布: {dict(tag_counts)}")

    samples = parse_xml_to_samples(xml_path, config,
                                    target_players=[target], max_kyoku=1)
    print(f"\n目标玩家在首局的 IL 样本数: {len(samples)}")

    if not samples:
        print("(无样本 — 可能 ActionValidator 与天凤规则有差异, 见 docs TODO)")
        return

    print("\n=== 前 5 个样本 (obs 形状 + 专家动作) ===")
    for k, s in enumerate(samples[:5]):
        atype = s.expert_action.type.name
        tile_v = (s.expert_action.tile.value if s.expert_action.tile
                  else (s.expert_action.riichi_discard.value
                        if s.expert_action.type == ActionType.RIICHI else None))
        cand_kinds = [c.type.name[:4] for c in s.candidate_actions]
        print(f"  样本{k+1} [{s.decision_kind:7s}] 专家={atype}(tile={tile_v}) "
              f"候选数={len(s.candidate_actions)} 专家idx={s.expert_idx}")
        # obs 形状
        if "state" in s.observation:
            print(f"         obs.state keys: {list(s.observation['state'].keys())}")

    # IL label 分布
    from collections import Counter as C
    label_dist = C(s.expert_action.type.name for s in samples)
    print(f"\n=== 样本动作类型分布 ===\n  {dict(label_dist)}")
    print(f"\n[OK] 生成 {len(samples)} 个 (observation, expert_idx) 样本, "
          f"可直接用于行为克隆 (BC) 训练")


if __name__ == "__main__":
    import argparse
    import yaml

    ap = argparse.ArgumentParser(description="天凤 XML 牌谱解析 (生产版)")
    ap.add_argument("xml", nargs="?")
    ap.add_argument("--target", type=int, default=0)
    args = ap.parse_args()

    # 加载默认 config
    cfg_path = ROOT / "configs" / "default_config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if args.xml:
        p = Path(args.xml)
    else:
        xmls = sorted((ROOT / "data" / "tenhou" / "xml").glob("*.xml"))
        if not xmls:
            print("无 XML 文件, 请先运行 tenhou_download.py download-xml")
            sys.exit(1)
        p = xmls[0]
    demo(p, config, target=args.target)
