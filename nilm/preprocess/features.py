"""特征工程（指南 §8）：原始 / 统计变化 / 三相结构 / 时间特征。

禁用项（§8.5）：5/15 分钟级数据不得把 FFT/THD/高频谐波作为核心输入。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common.logging import get_logger
from nilm.common.schema import bus_total, validate_bus_frame

log = get_logger("preprocess.features")

EPS = 1e-6


def _imbalance(sub: pd.DataFrame) -> pd.Series:
    return (sub.max(axis=1) - sub.min(axis=1)) / (sub.mean(axis=1).abs() + EPS)


def build_features(bus: pd.DataFrame,
                   lags: tuple[int, ...] = (1, 2, 3, 4),
                   rolling_windows: tuple[str, ...] = ("1h", "6h", "24h")) -> pd.DataFrame:
    """由 15min 母线数据构造特征矩阵（DatetimeIndex 不变，仅用过去信息）。

    输出含 ``pbus``（总线总有功）与 ``slot``（96 槽位）列，供基线模型按列名取用。
    """
    validate_bus_frame(bus, expected_freq=None)
    feat = bus.copy()

    # §8.1 Ptotal 一致性检查
    psum = bus[["pa", "pb", "pc"]].sum(axis=1)
    if "ptotal" in bus.columns:
        rel = ((bus["ptotal"] - psum).abs() / (psum.abs() + EPS)).median()
        if rel > 0.05:
            log.warning("ptotal 与 PA+PB+PC 中位相对偏差 %.2f%%，请核对计量口径（§8.1）", rel * 100)
    feat["pbus"] = bus_total(bus)

    # §8.2 统计/变化：差分、滚动均值/标准差、滞后
    for col, base in [("p", "pbus"), ("i", "ia"), ("u", "ua"), ("pf", "pfa")]:
        feat[f"d_{col}"] = feat[base].diff()
        for w in rolling_windows:
            feat[f"roll_mean_{col}_{w}"] = feat[base].rolling(w, min_periods=1).mean()
            feat[f"roll_std_{col}_{w}"] = feat[base].rolling(w, min_periods=1).std().fillna(0.0)
    for lag in lags:
        feat[f"p_lag_{lag}"] = feat["pbus"].shift(lag)

    # §8.3 三相结构：不平衡度与占比
    feat["imb_i"] = _imbalance(bus[["ia", "ib", "ic"]])
    feat["imb_u"] = _imbalance(bus[["ua", "ub", "uc"]])
    feat["imb_p"] = _imbalance(bus[["pa", "pb", "pc"]])
    i_sum = bus[["ia", "ib", "ic"]].sum(axis=1).abs() + EPS
    for ph in ("a", "b", "c"):
        feat[f"i_share_{ph}"] = bus[f"i{ph}"].abs() / i_sum

    # §8.4 时间特征（sin/cos 周期编码）
    idx = bus.index
    feat["slot"] = idx.hour * 4 + idx.minute // 15
    feat["hour"] = idx.hour
    feat["minute"] = idx.minute
    feat["day_of_week"] = idx.dayofweek
    feat["is_weekend"] = (idx.dayofweek >= 5).astype(int)
    feat["month"] = idx.month
    feat["day_of_year"] = idx.dayofyear
    feat["hour_sin"] = np.sin(2 * np.pi * idx.hour / 24)
    feat["hour_cos"] = np.cos(2 * np.pi * idx.hour / 24)
    feat["doy_sin"] = np.sin(2 * np.pi * idx.dayofyear / 365)
    feat["doy_cos"] = np.cos(2 * np.pi * idx.dayofyear / 365)
    return feat
