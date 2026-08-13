"""母线特征工程（技术方案 §5.4）：基础量 / 三相不平衡 / 滑窗统计 / 滞后 / 日历。

只依赖母线自身历史（过去信息），不引入未来泄漏。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common import schema

EPS = 1e-6


def _imbalance(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """三相不平衡度 = (max - min) / (mean + eps)。"""
    sub = df[cols]
    return (sub.max(axis=1) - sub.min(axis=1)) / (sub.mean(axis=1) + EPS)


def build_features(bus: pd.DataFrame,
                   lags: tuple[int, ...] = (1, 2, 3, 4),
                   rolling_windows: tuple[str, ...] = ("1h", "6h", "24h")) -> pd.DataFrame:
    """由 15min 母线数据构造特征矩阵（DatetimeIndex 不变）。

    输出包含 ``slot``（一天内 15min 槽位 0..95）与 ``p_total`` 列，
    供 history_profile / proportional 等基线模型按列名取用。
    """
    schema.validate_bus_frame(bus, expected_freq=None)
    feat = bus.copy()

    # 三相不平衡度
    feat["imb_i"] = _imbalance(bus, ["i_a", "i_b", "i_c"])
    feat["imb_u"] = _imbalance(bus, ["u_a", "u_b", "u_c"])

    # 滑窗统计（仅过去信息）
    for w in rolling_windows:
        feat[f"p_roll_mean_{w}"] = bus["p_total"].rolling(w, min_periods=1).mean()
        feat[f"p_roll_std_{w}"] = bus["p_total"].rolling(w, min_periods=1).std().fillna(0.0)

    # 滞后特征
    for lag in lags:
        feat[f"p_lag_{lag}"] = bus["p_total"].shift(lag)

    # 日历特征（15min 网格）
    idx = bus.index
    feat["slot"] = idx.hour * 4 + idx.minute // 15
    feat["dow"] = idx.dayofweek
    feat["hour_sin"] = np.sin(2 * np.pi * idx.hour / 24)
    feat["hour_cos"] = np.cos(2 * np.pi * idx.hour / 24)
    return feat
