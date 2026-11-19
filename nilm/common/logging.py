"""统一日志：所有模块经由此处获取 logger，避免各自配置。"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_ROOT_NAME = "nilm"
_ANSI_RED = "\033[91m"
_ANSI_RESET = "\033[0m"
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def red_text(msg: str) -> str:
    """包装红色高亮文本（控制台 ANSI 亮红，供关键自动熔断/回退提示使用）。"""
    return f"{_ANSI_RED}{msg}{_ANSI_RESET}"


def _enable_windows_ansi() -> None:
    """Windows 控制台启用虚拟终端 ANSI 序列支持（PowerShell 5.1/cmd 兼容，失败静默忽略）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:  # noqa: BLE001
        os.environ.setdefault("TERM", "xterm")


class _PlainFormatter(logging.Formatter):
    """文件日志 Formatter：剥离 ANSI 颜色控制符，保持落盘文本干净。"""

    def format(self, record: logging.LogRecord) -> str:
        return _ANSI_ESCAPE_RE.sub("", super().format(record))


def get_logger(name: str = _ROOT_NAME) -> logging.Logger:
    if not name.startswith(_ROOT_NAME):
        name = f"{_ROOT_NAME}.{name}"
    return logging.getLogger(name)


def setup_logging(log_path: str | Path | None = None, level: int = logging.INFO) -> None:
    """配置根 logger：控制台必选，文件日志可选（写入实验产物目录）。"""
    _enable_windows_ansi()
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
        fh.setFormatter(_PlainFormatter(_FMT))
        root.addHandler(fh)
