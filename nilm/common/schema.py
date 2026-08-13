"""标准列模式与频率约定（跨模块数据契约）。

跨模块传递的 DataFrame 必须满足这里定义的 schema：
- 索引：DatetimeIndex（单调、网格对齐）
- 母线（BUS）：必备列 BUS_REQUIRED，可选列 BUS_OPTIONAL
- 分路（BRANCH）：timestamp + 每分路一列 ``branch_<id>``（三相总有功功率）

schema 校验在数据边界（data_io 出口 / preprocess 各步骤）执行，非法数据快速失败。
"""

from __future__ import annotations

import pandas as pd

# ---- 频率与点数约定 ----
BUS_FREQ_FAST = "5min"          # 母线原始采样：288 点/天
BUS_POINTS_PER_DAY_FAST = 288
BUS_FREQ_MAIN = "15min"         # 训练主分辨率（与分路对齐后）
BUS_POINTS_PER_DAY_MAIN = 96
BRANCH_FREQ = "15min"           # 分路原始采样：96 点/天
BRANCH_POINTS_PER_DAY = 96

# ---- 标准列 ----
BUS_REQUIRED = [
    "u_a", "u_b", "u_c",        # 三相电压 (V)
    "i_a", "i_b", "i_c",        # 三相电流 (A)
    "p_total",                  # 有功功率（三相总和）
    "pf_a", "pf_b", "pf_c",     # 三相功率因素
]
BUS_OPTIONAL = ["p_a", "p_b", "p_c"]  # 分相有功（若采集系统提供）
BRANCH_PREFIX = "branch_"       # 分路列前缀：branch_<id> = 该分路三相总有功


class SchemaError(ValueError):
    """数据不符合标准列模式。"""


def branch_columns(df: pd.DataFrame) -> list[str]:
    """返回分路 DataFrame 中的分路列名列表。"""
    return [c for c in df.columns if c.startswith(BRANCH_PREFIX)]


def validate_bus_frame(df: pd.DataFrame, expected_freq: str | None = None) -> None:
    """校验母线 DataFrame 符合标准 schema，否则抛 SchemaError。"""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SchemaError("母线数据索引必须是 DatetimeIndex（timestamp 列需解析为索引）")
    if not df.index.is_monotonic_increasing:
        raise SchemaError("母线数据索引必须单调递增")
    missing = [c for c in BUS_REQUIRED if c not in df.columns]
    if missing:
        raise SchemaError(f"母线数据缺少必备列: {missing}")
    if expected_freq is not None and len(df) > 1:
        freq = pd.infer_freq(df.index)
        if freq is not None and freq != expected_freq:
            raise SchemaError(f"母线数据频率 {freq} 与期望 {expected_freq} 不符")


def validate_branch_frame(df: pd.DataFrame, expected_freq: str | None = "15min") -> None:
    """校验分路 DataFrame 符合标准 schema，否则抛 SchemaError。"""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SchemaError("分路数据索引必须是 DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise SchemaError("分路数据索引必须单调递增")
    if not branch_columns(df):
        raise SchemaError(f"分路数据至少需要一列 '{BRANCH_PREFIX}<id>'（三相总有功功率）")
    if expected_freq is not None and len(df) > 1:
        freq = pd.infer_freq(df.index)
        if freq is not None and freq != expected_freq:
            raise SchemaError(f"分路数据频率 {freq} 与期望 {expected_freq} 不符")
