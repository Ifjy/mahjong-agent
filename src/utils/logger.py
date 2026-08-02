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
    """默认配置: WARNING 级别 (屏蔽 env 内的 DEBUG/INFO print 等价物)。"""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.WARNING)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)


def set_level(level: str):
    """动态设置日志级别: 'DEBUG'/'INFO'/'WARNING'/'ERROR'/'CRITICAL'。
    训练时设 WARNING/ERROR 可屏蔽环境内部噪音。"""
    lvl = getattr(logging, level.upper(), logging.WARNING)
    logging.getLogger(_LOGGER_NAME).setLevel(lvl)


def quiet():
    """静默: 只保留 ERROR 以上 (训练时用)。"""
    set_level("ERROR")


def verbose():
    """详细: DEBUG 级别 (调试环境规则时用)。"""
    set_level("DEBUG")
