# Mahjong RL Project

这是一个基于强化学习的日麻 AI 项目，包含以下主要模块：

- **环境 (Environment)**: 实现符合 Gym API 的日麻环境。
- **智能体 (Agent)**: 实现多种强化学习算法（如 DQN、PPO）。
- **训练 (Training)**: 提供训练循环和相关工具。
- **工具 (Utils)**: 包含日志记录、配置加载等辅助功能。
## 项目结构

```
mahjong-rl/
├── configs/                  # 配置文件
├── data/                     # 数据集、模型、日志
├── notebooks/                # Jupyter notebooks
├── scripts/                  # 可执行脚本
├── src/                      # 核心代码
├── tests/                    # 测试代码
├── .gitignore                # Git 忽略文件
├── requirements.txt          # 依赖库列表
└── README.md                 # 项目说明文档
```

## 快速开始

1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```

2. 运行训练脚本：
   ```bash
   python scripts/train.py
   ```

3. 查看训练日志：
   - 使用 TensorBoard 或直接查看日志文件。

## 贡献

欢迎提交 PR 或 Issue！
