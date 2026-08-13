"""数据集构造：时序划分（防泄漏）+ 监督矩阵 / 序列窗口构建。

规则（技术方案 §5.5）：
- 按时间顺序划分 train/val/test，不打乱；
- 任一划分内部行含 NaN 即剔除（长缺口不参与训练）；
- 序列窗口只在单一划分内部滑动，不跨 split。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common import schema


def time_split(index: pd.DatetimeIndex, train_frac: float = 0.7,
               val_frac: float = 0.15) -> dict[str, pd.DatetimeIndex]:
    """按时间顺序切分索引，返回 {'train','val','test'}。"""
    if not (0 < train_frac < 1) or not (0 < val_frac < 1) or train_frac + val_frac >= 1:
        raise ValueError("需要 0 < train_frac, 0 < val_frac, train_frac+val_frac < 1")
    n = len(index)
    i_train = int(n * train_frac)
    i_val = int(n * (train_frac + val_frac))
    return {
        "train": index[:i_train],
        "val": index[i_train:i_val],
        "test": index[i_val:],
    }


def build_supervised_matrix(features: pd.DataFrame, branch: pd.DataFrame,
                            split_index: pd.DatetimeIndex
                            ) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """在指定划分上构造 (X, y, feature_names)，剔除含 NaN 的行。"""
    schema.validate_branch_frame(branch, expected_freq=None)
    idx = features.index.intersection(split_index).intersection(branch.index)
    f = features.loc[idx]
    y_df = branch.loc[idx]

    mask = (~f.isna().any(axis=1)) & (~y_df.isna().any(axis=1))
    f, y_df = f.loc[mask], y_df.loc[mask]
    if len(f) == 0:
        raise ValueError("该划分上无有效样本（全为 NaN 或索引不重叠）")

    feature_names = [str(c) for c in f.columns]
    return f.to_numpy(dtype=np.float64), y_df.to_numpy(dtype=np.float64), feature_names


def build_windows(X: np.ndarray, y: np.ndarray, window: int = 96,
                  stride: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """滑窗样本（序列模型用）：X -> (m, window, f)，y 取窗口末点。

    调用方必须传入单一划分内部的数据，保证窗口不跨 split。
    """
    if window < 2:
        raise ValueError("window 至少为 2")
    n = len(X)
    if n < window:
        raise ValueError(f"样本数 {n} 小于窗口长度 {window}")
    starts = np.arange(0, n - window + 1, stride)
    Xw = np.stack([X[s:s + window] for s in starts], axis=0)
    yw = y[starts + window - 1]
    return Xw, yw
