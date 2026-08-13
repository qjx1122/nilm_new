"""特征缩放（指南 §11）：Scaler/Normalizer 只能由 Train 拟合。

日历/槽位类离散列不参与缩放（保持基线模型按列名取用的语义）。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

EPS = 1e-12


class TrainFitScaler:
    """z-score 标准化（仅指定列）；fit 只接受训练集，transform 用于任意划分。"""

    def __init__(self) -> None:
        self.cols_: np.ndarray | None = None
        self.mean_: np.ndarray | None = None
        self.std_: np.ndarray | None = None

    def fit(self, X_train: np.ndarray, cols: Sequence[int] | None = None) -> "TrainFitScaler":
        self.cols_ = np.arange(X_train.shape[1]) if cols is None else np.asarray(cols)
        sub = X_train[:, self.cols_]
        self.mean_ = sub.mean(axis=0)
        self.std_ = sub.std(axis=0)
        self.std_[self.std_ < EPS] = 1.0
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.mean_ is None:
            raise RuntimeError("Scaler 尚未由训练集 fit（§11：只能由 Train 拟合）")
        out = X.astype(np.float64).copy()
        out[:, self.cols_] = (out[:, self.cols_] - self.mean_) / self.std_
        return out

    def state(self) -> dict:
        return {
            "cols": self.cols_.tolist() if self.cols_ is not None else None,
            "mean": self.mean_.tolist() if self.mean_ is not None else None,
            "std": self.std_.tolist() if self.std_ is not None else None,
        }

    @classmethod
    def from_state(cls, state: dict) -> "TrainFitScaler":
        s = cls()
        s.cols_ = np.asarray(state["cols"]) if state.get("cols") is not None else None
        s.mean_ = np.asarray(state["mean"]) if state.get("mean") is not None else None
        s.std_ = np.asarray(state["std"]) if state.get("std") is not None else None
        return s
