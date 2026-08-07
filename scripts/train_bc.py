"""
行为克隆 (Behavior Cloning) 训练
=================================

用 build_dataset.py 生成的 IL 数据集, 监督训练 DQN 网络 (DuelingDQNNet),
让网络预测的 Q 值在候选动作集上把专家动作排在最高。

训练目标: 最小化 (Q 值上的) 交叉熵, 使 argmax(Q[mask]) == expert_idx。
训练完成后, 权重可:
  - 直接保存为 BC-only 模型
  - 加载到 DQNAgent 作为 RL 微调的预训练初始化 (见 bc_to_rl.py)

数据加载: 流式逐分片 (避免全量加载 OOM, action_cands 维度大)。
  全量 96万样本 action_cands 需 ~49GB, 必须分片处理。

用法:
    python scripts/train_bc.py --epochs 20 --batch-size 256
    python scripts/train_bc.py --dataset data/tenhou/dataset --lr 0.001
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent.models import DuelingDQNNet


DATASET_DIR = ROOT / "data" / "tenhou" / "dataset"
CKPT_DIR = ROOT / "data" / "models" / "bc"


def list_shards(dataset_dir: Path) -> list:
    """列出所有分片路径 (排序)。"""
    return sorted(glob.glob(str(dataset_dir / "shard_*.npz")))


def iterate_minibatches(shard_paths, batch_size, shuffle=True, rng=None):
    """
    流式生成器: 逐分片加载, 在分片内切 batch, 产出 (state, cands, mask, label)。
    每个分片处理完即释放内存, 避免全量 OOM。
    """
    if rng is None:
        rng = np.random.default_rng()
    shard_order = list(range(len(shard_paths)))
    if shuffle:
        rng.shuffle(shard_order)
    for si in shard_order:
        d = np.load(shard_paths[si])
        states = d["state_flat"]
        cands = d["action_cands"]
        masks = d["action_mask"]
        labels = d["action_idx"]
        n = len(states)
        idx = np.arange(n)
        if shuffle:
            rng.shuffle(idx)
        for start in range(0, n, batch_size):
            sel = idx[start:start + batch_size]
            yield (states[sel], cands[sel], masks[sel], labels[sel])
        # 分片用完, 释放
        del states, cands, masks, labels, d


def train(args):
    device = torch.device("cuda" if (torch.cuda.is_available()
                                      and args.device != "cpu") else "cpu")
    print(f"[bc] 设备: {device}")

    shard_paths = list_shards(Path(args.dataset))
    if not shard_paths:
        print(f"[bc] 错误: {args.dataset} 下无 shard_*.npz, 请先运行 build_dataset.py",
              file=sys.stderr)
        sys.exit(1)

    # 从第一个分片推断维度
    d0 = np.load(shard_paths[0])
    state_dim = d0["state_flat"].shape[1]
    action_dim = d0["action_cands"].shape[2]
    # 估算总样本数 (各分片可能不均)
    total_samples = 0
    for sp in shard_paths:
        dz = np.load(sp, mmap_mode="r")
        total_samples += len(dz["state_flat"])
    print(f"[bc] 数据集: {len(shard_paths)} 分片, {total_samples} 样本, "
          f"state_dim={state_dim}, action_dim={action_dim}")

    # 网络初始化 (与 DQNAgent 一致)
    net = DuelingDQNNet(state_dim, action_dim, hidden_dim=args.hidden_dim).to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)

    # 类加权: 解决 DISCARD(96%) 主导、RIICHI(0.2%)/KAN(0.008%) 学不到的问题。
    # 权重来自数据集类型分布的逆频率 (归一化), 通过 action_cands[label] 的类型 one-hot 推断动作类型。
    # ActionType 枚举顺序 (actions.py): DISCARD=1,RIICHI=2,CHI=3,PON=4,KAN=5,TSUMO=6,RON=7,PASS=8,SPECIAL_DRAW=9
    # action_cands 的前9维是类型 one-hot (to_feature_vector type_offset=0)
    use_class_weight = args.class_weight > 0
    if use_class_weight:
        # 精确统计各类样本数 (向量化扫描所有分片, RIICHI 仅0.2% 抽样会漏)
        type_counts = np.zeros(10, dtype=np.float64)   # 索引 1-9 对应 ActionType 1-9
        for sp in shard_paths:
            dz = np.load(sp, mmap_mode="r")
            lbs = dz["action_idx"]
            cds = dz["action_cands"]   # (N, 100, 128)
            # 每个样本专家候选的 type one-hot: cds[i, label[i], :9]
            picked = cds[np.arange(len(lbs)), lbs, :9]   # (N, 9)
            picked_types = picked.argmax(axis=1) + 1     # ActionType 值 1-9
            for t in range(1, 10):
                type_counts[t] += np.sum(picked_types == t)
        total_t = type_counts.sum()
        # 逆频率权重, 截断到 [1, args.class_weight], 避免极端值
        weights = np.ones(10, dtype=np.float32)
        for t in range(1, 10):
            if type_counts[t] > 0:
                freq = type_counts[t] / total_t
                weights[t] = min(max(1.0 / (freq * 10), 1.0), args.class_weight)
        type_names = ["DISCARD","RIICHI","CHI","PON","KAN","TSUMO","RON","PASS","SPECIAL"]
        print(f"[bc] 类分布: " + " ".join(f"{type_names[t-1]}={int(type_counts[t])}" for t in range(1,10)))
        print(f"[bc] 类权重 (DISCARD..SPECIAL_DRAW): "
              f"{[round(float(weights[t]),2) for t in range(1,10)]}")

    criterion = nn.CrossEntropyLoss()

    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    best_acc = 0.0
    batch_size = args.batch_size

    for epoch in range(args.epochs):
        net.train()
        total_loss = 0.0
        total_correct = 0
        total_seen = 0
        batches = 0
        t0 = time.time()

        for states, cands, masks, labels in iterate_minibatches(
                shard_paths, batch_size, shuffle=True, rng=rng):
            s = torch.from_numpy(states).to(device)
            c = torch.from_numpy(cands).to(device)
            m = torch.from_numpy(masks).to(device)
            y = torch.from_numpy(labels).to(device).long()

            q = net(s, c, m)
            if use_class_weight:
                # 推断每个样本专家动作的类型, 取对应权重
                picked = c[torch.arange(len(y)), y, :9]    # (B, 9)
                types = picked.argmax(dim=-1) + 1           # ActionType 值 1-9
                w = torch.from_numpy(weights).to(device)[types]   # (B,)
                loss = nn.functional.cross_entropy(q, y, weight=None, reduction='none')
                loss = (loss * w).mean()
            else:
                loss = criterion(q, y)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item() * len(labels)
            pred = q.argmax(dim=-1)
            total_correct += (pred == y).sum().item()
            total_seen += len(labels)
            batches += 1

            if batches % args.log_every == 0:
                cur_acc = total_correct / total_seen
                elapsed = time.time() - t0
                print(f"  [epoch {epoch+1}] batch {batches} | "
                      f"running loss={total_loss/total_seen:.4f} "
                      f"acc={cur_acc:.4f} | {total_seen}/{total_samples} "
                      f"({total_seen*100//total_samples}%) | {elapsed:.0f}s",
                      flush=True)

        avg_loss = total_loss / total_seen
        acc = total_correct / total_seen
        elapsed = time.time() - t0
        print(f"  [epoch {epoch+1}/{args.epochs}] 完成: loss={avg_loss:.4f} "
              f"top1_acc={acc:.4f} ({total_correct}/{total_seen}) "
              f"{elapsed:.0f}s", flush=True)

        # 真实 eval-mode acc (在整个数据集上用 eval 模式重测, 不受"边学边测"影响)
        # 用最后一个分片做快速评估 (避免全量太慢)
        net.eval()
        eval_correct = eval_total = 0
        with torch.no_grad():
            d_eval = np.load(shard_paths[-1])
            for j in range(0, len(d_eval["state_flat"]), batch_size):
                s_e = torch.from_numpy(d_eval["state_flat"][j:j+batch_size]).to(device)
                c_e = torch.from_numpy(d_eval["action_cands"][j:j+batch_size]).to(device)
                m_e = torch.from_numpy(d_eval["action_mask"][j:j+batch_size]).to(device)
                y_e = torch.from_numpy(d_eval["action_idx"][j:j+batch_size]).to(device).long()
                q_e = net(s_e, c_e, m_e)
                eval_correct += (q_e.argmax(dim=-1) == y_e).sum().item()
                eval_total += len(y_e)
        eval_acc = eval_correct / eval_total
        print(f"           eval_acc(独立)={eval_acc:.4f} "
              f"(真实推理精度, 不含训练态乐观估计)", flush=True)

        # 保存最佳 (用 eval_acc 判定, 更真实)
        if eval_acc > best_acc:
            best_acc = eval_acc
            torch.save({
                "epoch": epoch + 1, "model_state_dict": net.state_dict(),
                "state_dim": state_dim, "action_dim": action_dim,
                "hidden_dim": args.hidden_dim, "top1_acc": eval_acc,
            }, CKPT_DIR / "bc_best.pt")
        if (epoch + 1) % args.save_every == 0 or (epoch + 1) == args.epochs:
            torch.save({
                "epoch": epoch + 1, "model_state_dict": net.state_dict(),
                "state_dim": state_dim, "action_dim": action_dim,
                "hidden_dim": args.hidden_dim, "top1_acc": acc,
            }, CKPT_DIR / f"bc_epoch{epoch+1:03d}.pt")

    print(f"\n[bc] 训练完成. 最佳 top1_acc={best_acc:.4f}")
    print(f"[bc] 最佳模型: {CKPT_DIR / 'bc_best.pt'}")
    print(f"[bc] BC->RL: python scripts/bc_to_rl.py --bc {CKPT_DIR / 'bc_best.pt'}")


def main():
    ap = argparse.ArgumentParser(description="行为克隆训练 (流式分片加载)")
    ap.add_argument("--dataset", default=str(DATASET_DIR),
                    help="数据集目录 (含 shard_*.npz)")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden-dim", type=int, default=256,
                    help="与 DQNAgent 的 hidden_dim 保持一致")
    ap.add_argument("--save-every", type=int, default=5,
                    help="每 N 个 epoch 存一次 checkpoint")
    ap.add_argument("--log-every", type=int, default=200,
                    help="每 N 个 batch 打印一次进度")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--class-weight", type=float, default=0.0,
                    help="类加权交叉熵上限 (>0 启用, 给 RIICHI/KAN 等低频动作更高权重, 默认0=关闭)")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = ap.parse_args()
    train(args)


if __name__ == "__main__":
    main()
