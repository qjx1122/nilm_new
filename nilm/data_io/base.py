"""数据接入抽象接口。

实现方只需保证：输出的 DataFrame 符合 ``nilm.common.schema`` 定义的标准列模式。
编排层（pipeline）只面向接口编程，不感知 CSV/Parquet/数据库等具体实现。

接口按侧拆分（母线侧 / 分路侧），具体实现各取其一；``DataSource`` 为组合接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class BusSource(ABC):
    """母线侧数据源。"""

    @abstractmethod
    def load_bus(self, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        """加载母线时序，返回标准 schema DataFrame（DatetimeIndex，5min）。"""


class BranchSource(ABC):
    """分路侧数据源。"""

    @abstractmethod
    def load_branch(self, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        """加载分路时序，返回标准 schema DataFrame（DatetimeIndex，15min，列 branch_<id>）。"""


class DataSource(BusSource, BranchSource):
    """组合接口：同时提供母线与分路加载（编排层面向此接口）。"""
