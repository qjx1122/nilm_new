"""模型适配抽象接口（sklearn 风格，DL 模型同样适配此接口）。

约定：
- X : (n_samples, n_features) numpy 矩阵；序列模型在适配器内部消化窗口张量；
- y : (n_samples, n_branches) numpy 矩阵（各分路三相总有功）；
- feature_names : 与 X 列对应的列名，基线模型按列名取用（如 slot / p_total）。
"""

from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

import numpy as np


class BaseModel(ABC):
    """所有候选模型的统一接口。"""

    name: str = "base"

    def __init__(self, **params) -> None:
        self.params = params

    @abstractmethod
    def fit(self, X: np.ndarray, y: np.ndarray,
            feature_names: Sequence[str] | None = None,
            X_val: np.ndarray | None = None,
            y_val: np.ndarray | None = None,
            index=None, val_index=None) -> None:
        """训练。X_val/y_val 供早停类模型使用，其余模型可忽略。

        index/val_index : 样本时间索引（DatetimeIndex，与 X/X_val 行对应）——
        序列模型用于按时间连续段构造滑窗（指南 §10「窗口必须连续」，W-1）；
        非序列模型可忽略。
        """

    @abstractmethod
    def predict(self, X: np.ndarray, index=None) -> np.ndarray:
        """输出 (n_samples, n_branches) 的各分路有功功率估计。

        index : X 的时间索引（可缺省）；序列模型用于按连续段构窗（W-1）。
        """

    # ---- 持久化：默认 pickle，子类可覆盖（如 DL 模型保存权重） ----
    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: str | Path) -> "BaseModel":
        with open(path, "rb") as f:
            return pickle.load(f)
