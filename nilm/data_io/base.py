"""数据接入抽象接口（按指南 §3/§4 重构：面向「用户目录」而非单个文件）。

实现方保证：输出符合 ``nilm.common.schema`` 标准列模式，并返回 schema 报告。
编排层只面向接口编程，不感知 CSV/Parquet/数据库等具体实现（解耦点）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

import pandas as pd


class BusLoader(ABC):
    """总线侧加载器：多个 Ch 文件按时间轴关联（§3.2），字段映射由配置给定。"""

    @abstractmethod
    def load(self, files: Sequence[Path], field_map: dict) -> tuple[pd.DataFrame, dict]:
        """返回 (标准 schema DataFrame[5min], schema 报告 dict)。"""


class BranchLoader(ABC):
    """分路侧加载器：输出 time 索引 + p1..pN 列（§3.3）。"""

    @abstractmethod
    def load(self, files: Sequence[Path], sentinels: list | None = None) -> tuple[pd.DataFrame, dict]:
        """返回 (标准 schema DataFrame[15min], schema 报告 dict)。"""
