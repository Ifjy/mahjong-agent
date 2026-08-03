"""
日志工具 —— 提供可开关、可分级的日志, 替代散落各处的 print。

设计见 docs/RL_AGENT_EXPERIMENT_DESIGN.md §0/§10。
训练时大量 print 会拖慢 I/O, 本 logger 支持全局静默 (训练) / 详细 (调试)。
"""

from __future__ import annotations
import logging
import sys
from typing import Optional

_CONFIGURED = False
_LOGGER_NAME = "mjagent"


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取 logger。首次调用时按默认配置初始化。"""
    global _CONFIGURED
    logger = logging.getLogger(name or _LOGGER_NAME)
    if not _CONFIGURED:
        _configure_default()
        _CONFIGURED = True
    return logger


def _configure_default():
    """默认配置: INFO 级别。
    - 训练进度 (Trainer log.info) 可见。
    - env 内部 print 已重绑到 debug, 默认不可见。
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def set_level(level: str):
    """动态设置日志级别: 'DEBUG'/'INFO'/'WARNING'/'ERROR'/'CRITICAL'。"""
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger(_LOGGER_NAME).setLevel(lvl)


def quiet():
    """静默: ERROR 以上 (彻底屏蔽所有非错误)。"""
    set_level("ERROR")


def verbose():
    """详细: DEBUG 级别 (含 env 内部 print, 调试规则时用)。"""
    set_level("DEBUG")
