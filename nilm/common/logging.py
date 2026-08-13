"""统一日志：所有模块经由此处获取 logger，避免各自配置。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_ROOT_NAME = "nilm"


def get_logger(name: str = _ROOT_NAME) -> logging.Logger:
    if not name.startswith(_ROOT_NAME):
        name = f"{_ROOT_NAME}.{name}"
    return logging.getLogger(name)


def setup_logging(log_path: str | Path | None = None, level: int = logging.INFO) -> None:
    """配置根 logger：控制台必选，文件日志可选（写入实验产物目录）。"""
    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FMT))
    root.addHandler(console)

    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FMT))
        root.addHandler(fh)
