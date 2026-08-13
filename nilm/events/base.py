"""事件检测抽象接口（预留）。实现落地时注册到编排层配置即可，不影响现有模块。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Event:
    """一次负荷投切事件。"""
    timestamp: pd.Timestamp
    delta_p: float          # 有功功率变化量（+ 投入 / - 切除）
    kind: str = "switch"    # 事件类型


class EventDetector(ABC):
    """事件检测器抽象：输入母线原始时序，输出事件列表。"""

    @abstractmethod
    def detect(self, bus_raw: pd.DataFrame) -> list[Event]: ...
