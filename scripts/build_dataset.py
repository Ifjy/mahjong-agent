"""
天凤牌谱 -> IL 数据集构建
=========================

把 data/tenhou/xml/ 下所有 XML 牌谱转成可训练的 IL 数据集 (分片 .npz)。

每个样本包含 (与 DQNAgent.select_action / store_transition 一致):
    state_flat    : 一维 float32, = _flatten_state(obs["state"])
    action_cands  : (max_actions, action_feature_dim) float32
    action_mask   : (max_actions,) int8
    action_idx    : int  (专家动作在候选集中的索引, 即 IL label)

输出: data/tenhou/dataset/shard_XXXX.npz  (每片含 N 个样本的 stacked 数组)

用法:
    python scripts/build_dataset.py --max-xml 200 --target-players 0,1,2,3
    python scripts/build_dataset.py --shard-size 2000
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent.dqn_agent import _flatten_state
from scripts.tenhou_parser import parse_xml_to_samples


DATA_DIR = ROOT / "data" / "tenhou"
XML_DIR = DATA_DIR / "xml"
DATASET_DIR = DATA_DIR / "dataset"


def build_dataset(
    max_xml: Optional[int],
    target_players: List[int],
    shard_size: int,
    max_kyoku: int,
    skip_unmatched: bool = True,
) -> dict:
    """
    遍历 XML, 生成 IL 数据集分片。
    返回统计信息。
    """
    cfg_path = ROOT / "configs" / "default_config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    xmls = sorted(XML_DIR.glob("*.xml"))
    if max_xml:
        xmls = xmls[:max_xml]
    print(f"[dataset] 待处理 {len(xmls)} 个 XML, target_players={target_players}, "
          f"shard_size={shard_size}, max_kyoku={max_kyoku}")

    # 累积缓冲
    buf_state, buf_cands, buf_mask, buf_label = [], [], [], []
    shard_idx = 0
    total_samples = 0
    total_unmatched = 0
    from collections import Counter
    type_dist = Counter()

    def flush_shard():
        nonlocal shard_idx, total_samples
        if not buf_state:
            return
        states = np.stack(buf_state).astype(np.float32)
        # action_cands 维度需统一: 取 max_actions 列 (截断/补零)
        cands = np.stack(buf_cands).astype(np.float32)
        masks = np.stack(buf_mask).astype(np.int8)
        labels = np.array(buf_label, dtype=np.int64)
        out = DATASET_DIR / f"shard_{shard_idx:04d}.npz"
        np.savez_compressed(
            out, state_flat=states, action_cands=cands,
            action_mask=masks, action_idx=labels,
        )
        n = len(buf_state)
        total_samples += n
        print(f"  [shard {shard_idx}] 写入 {n} 样本 -> {out.name} "
              f"(累计 {total_samples})")
        buf_state.clear(); buf_cands.clear(); buf_mask.clear(); buf_label.clear()
        shard_idx += 1

    t0 = time.time()
    for i, xf in enumerate(xmls):
        try:
            samples = parse_xml_to_samples(
                xf, config, target_players=target_players, max_kyoku=max_kyoku)
        except Exception as e:
            print(f"  [warn] {xf.name}: 解析失败 {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue

        for s in samples:
            # 跳过专家动作未匹配候选集的样本 (规则差异导致)
            if s.expert_idx < 0:
                total_unmatched += 1
                continue
            try:
                state_flat = _flatten_state(s.observation["state"])
                cands = np.asarray(s.observation["action_candidates"],
                                    dtype=np.float32)
                mask = np.asarray(s.observation["action_mask"], dtype=np.int8)
            except Exception:
                total_unmatched += 1
                continue
            buf_state.append(state_flat)
            buf_cands.append(cands)
            buf_mask.append(mask)
            buf_label.append(s.expert_idx)
            type_dist[s.expert_action.type.name] += 1

            if len(buf_state) >= shard_size:
                flush_shard()

        if (i + 1) % 20 == 0 or (i + 1) == len(xmls):
            elapsed = time.time() - t0
            done = i + 1
            speed = done / elapsed if elapsed > 0 else 0
            eta = (len(xmls) - done) / speed if speed > 0 else 0
            cur = total_samples + len(buf_state)
            print(f"  进度 {done}/{len(xmls)} XML ({done*100//len(xmls)}%), "
                  f"累计 {cur} 样本, {speed:.1f} XML/s, ETA {eta/60:.1f}min",
                  flush=True)

    flush_shard()   # 收尾

    elapsed = time.time() - t0
    stats = {
        "xml_processed": len(xmls),
        "total_samples": total_samples,
        "unmatched_skipped": total_unmatched,
        "shards": shard_idx,
        "elapsed_sec": round(elapsed, 1),
        "type_distribution": dict(type_dist),
    }
    # 保存统计
    import json
    (DATASET_DIR / "dataset_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def main():
    ap = argparse.ArgumentParser(description="构建天凤 IL 数据集")
    ap.add_argument("--max-xml", type=int, default=None,
                    help="最多处理 N 个 XML (默认全部)")
    ap.add_argument("--target-players", default="0,1,2,3",
                    help="采集哪些玩家的决策 (逗号分隔, 默认 4 家全采)")
    ap.add_argument("--shard-size", type=int, default=2000,
                    help="每个分片包含的样本数 (默认 2000)")
    ap.add_argument("--max-kyoku", type=int, default=8,
                    help="每个半庄最多重放几局 (默认 8 = 整半庄)")
    args = ap.parse_args()

    target = [int(x) for x in args.target_players.split(",") if x.strip()]
    stats = build_dataset(
        max_xml=args.max_xml, target_players=target,
        shard_size=args.shard_size, max_kyoku=args.max_kyoku,
    )
    print("\n=== 数据集构建完成 ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
