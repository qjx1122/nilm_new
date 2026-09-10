"""标准列模式与频率约定（跨模块数据契约，字段名对齐指南 §4）。

内部统一字段名（§4）：timestamp、ua、ub、uc、ia、ib、ic、pa、pb、pc、
pfa、pfb、pfc（可选 ptotal）；分路列为 p1、p2、…（单位 W，§3.3）。
"""

from __future__ import annotations

import re

import pandas as pd

# ---- 频率与点数约定（指南 §1/§2）----
BUS_FREQ_FAST = "5min"          # 总线原始采样：288 点/天
BUS_POINTS_PER_DAY_FAST = 288
FREQ_MAIN = "15min"             # 统一建模频率（指南 §1：统一建模频率为 15 分钟）
POINTS_PER_DAY_MAIN = 96
BRANCH_POINTS_PER_DAY = 96

# ---- 标准列（指南 §4/§2.2）----
BUS_REQUIRED = [
    "ua", "ub", "uc",           # 三相电压 (V)
    "ia", "ib", "ic",           # 三相电流 (A)
    "pa", "pb", "pc",           # 三相有功功率 (W)
    "pfa", "pfb", "pfc",        # 三相功率因素（无量纲）
]
BUS_OPTIONAL = ["ptotal"]       # 若存在 Ptotal，保留并检查与 PA+PB+PC 一致性（§8.1）

RE_BRANCH_COL = re.compile(r"^p\d+$")   # 分路功率列 p1、p2、…（§3.3，单位 W）
RE_POWER_COL = re.compile(r"^(p(a|b|c|total|\d+))$")  # 有功功率列（不含 pf*）


def is_power_column(name: str) -> bool:
    """是否为有功功率列（pa/pb/pc/ptotal/pN）；功率因素 pf* 不是。"""
    return bool(RE_POWER_COL.match(str(name).lower()))


class SchemaError(ValueError):
    """数据不符合标准列模式。"""


def branch_power_columns(df: pd.DataFrame) -> list[str]:
    """按序号返回分路功率列（p1, p2, …）。"""
    cols = [c for c in df.columns if RE_BRANCH_COL.match(str(c).lower())]
    return sorted(cols, key=lambda c: int(str(c)[1:]))


def bus_total(bus: pd.DataFrame) -> pd.Series:
    """总线总有功：优先 ptotal，否则 PA+PB+PC。"""
    if "ptotal" in bus.columns:
        return bus["ptotal"]
    return bus[["pa", "pb", "pc"]].sum(axis=1)


def validate_bus_frame(df: pd.DataFrame, expected_freq: str | None = None) -> None:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SchemaError("总线数据索引必须是 DatetimeIndex（event_time 需解析为索引）")
    if not df.index.is_monotonic_increasing:
        raise SchemaError("总线数据索引必须单调递增")
    missing = [c for c in BUS_REQUIRED if c not in df.columns]
    if missing:
        raise SchemaError(f"总线数据缺少必备列: {missing}")
    if expected_freq is not None and len(df) > 2:
        freq = pd.infer_freq(df.index)
        if freq is not None and freq != expected_freq:
            raise SchemaError(f"总线数据频率 {freq} 与期望 {expected_freq} 不符")


def validate_branch_frame(df: pd.DataFrame, expected_freq: str | None = FREQ_MAIN) -> None:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SchemaError("分路数据索引必须是 DatetimeIndex（time 列需解析为索引）")
    if not df.index.is_monotonic_increasing:
        raise SchemaError("分路数据索引必须单调递增")
    if not branch_power_columns(df):
        raise SchemaError("分路数据至少需要一列 pN（三相总有功功率，单位 W）")
    if expected_freq is not None and len(df) > 2:
        freq = pd.infer_freq(df.index)
        if freq is not None and freq != expected_freq:
            raise SchemaError(f"分路数据频率 {freq} 与期望 {expected_freq} 不符")
