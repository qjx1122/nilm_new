"""CSV 数据源实现：读取原始 CSV → 列映射 → 标准 schema → 质量校验。

原始文件列名千差万别，通过 ``column_map``（原始列名 -> 标准列名）映射，
使上游数据格式变化时只需改配置，不改代码（解耦点）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from nilm.common import schema
from nilm.common.logging import get_logger
from nilm.data_io.base import BranchSource, BusSource
from nilm.data_io.validator import assert_quality, quality_report

log = get_logger("data_io.csv")


def _read_concat(path: str | Path) -> pd.DataFrame:
    """path 为文件则直接读；为目录则按文件名排序读取全部 ``*.csv`` 并拼接。"""
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.csv"))
        if not files:
            raise FileNotFoundError(f"目录中没有 CSV 文件: {path}")
        frames = [pd.read_csv(f) for f in files]
        df = pd.concat(frames, ignore_index=True)
    elif path.is_file():
        df = pd.read_csv(path)
    else:
        raise FileNotFoundError(f"数据路径不存在: {path}")
    return df


class _CsvSourceBase:
    """公共逻辑：读取 + 列映射 + 时间索引化。"""

    def __init__(self, path: str | Path, timestamp_col: str = "timestamp",
                 column_map: dict[str, str] | None = None) -> None:
        self.path = Path(path)
        self.timestamp_col = timestamp_col
        self.column_map = column_map or {}

    def _load_raw(self, start: datetime | None, end: datetime | None) -> pd.DataFrame:
        df = _read_concat(self.path)
        if self.column_map:
            df = df.rename(columns=self.column_map)
        if self.timestamp_col not in df.columns:
            raise ValueError(f"缺少时间列 {self.timestamp_col!r}，现有列: {list(df.columns)}")
        df[self.timestamp_col] = pd.to_datetime(df[self.timestamp_col])
        df = df.set_index(self.timestamp_col).sort_index()
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        return df


class CsvBusSource(_CsvSourceBase, BusSource):
    """母线 CSV 数据源（288 点/天，5min）。"""

    def load_bus(self, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        df = self._load_raw(start, end)
        schema.validate_bus_frame(df, expected_freq=schema.BUS_FREQ_FAST)
        report = quality_report(df, kind="bus", points_per_day=schema.BUS_POINTS_PER_DAY_FAST)
        log.info("母线质量: %s", report)
        assert_quality(report, max_missing_ratio=0.1)
        return df


class CsvBranchSource(_CsvSourceBase, BranchSource):
    """分路 CSV 数据源（96 点/天，15min，每分路一列三相总有功）。"""

    def load_branch(self, start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        df = self._load_raw(start, end)
        schema.validate_branch_frame(df, expected_freq=schema.BRANCH_FREQ)
        report = quality_report(df, kind="branch", points_per_day=schema.BRANCH_POINTS_PER_DAY)
        log.info("分路质量: %s", report)
        assert_quality(report, max_missing_ratio=0.1)
        return df
