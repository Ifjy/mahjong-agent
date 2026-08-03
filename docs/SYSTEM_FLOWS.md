# 系统流程链路文档 (SYSTEM_FLOWS)

> 日期：2026-08-03 ｜ 由总到分，覆盖全部 9 条主流程
> 每条流程含：调用链、数据传递、关键不变量

## 9 条流程总览

| # | 流程 | 入口 | 核心链路 |
|---|------|------|----------|
| 1 | 训练主流程 | train.py --parallel | Trainer→Collector→Env→Controller→Rules |
| 2 | 断点恢复 | --resume ckpt.pt | _resume→load_state→续训 |
| 3 | 单局完整 | env.reset→step循环 | reset→step→terminated→reward |
| 4 | 响应阶段 | WAITING_FOR_RESPONSE | 3人响应→优先级→鸣牌/荣和/全PASS |
| 5 | 杠流程 | KAN | ACTION_PROCESSING→岭上摸牌→翻宝 |
| 6 | 和牌结算 | TSUMO/RON | process_hand_outcome→计分→payout |
| 7 | 局间切换 | 局结束 | determine_next_hand_state→新局/终局 |
| 8 | 评估 | evaluate.py | 加载ckpt→对局→顺位统计 |
| 9 | checkpoint保存 | checkpoint_freq触发 | get_state→torch.save |

## 跨流程关键不变量

| 不变量 | 适用流程 |
|--------|----------|
| 张数守恒(13hand+1drawn=14) | 3,5,6 |
| reward零和(sum=0) | 3 |
| terminated ⟺ game_over | 1,3,7 |
| 优先级 RON>PON/KAN>CHI>PASS | 4 |
| 点数守恒(payout零和) | 6 |
| Env不写GameState.current_player_index | 1,3,4 |
| truncated恒False | 3 |
| epsilon连续衰减(依赖train_steps) | 2,9 |

详细调用链见各流程审计（agent 已完成，此处作为测试编写依据）。
