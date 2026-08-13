"""基线与线性模型（M1 即可参与对比，是验收的 sanity 下界，技术方案 §6.1）。

- history_profile : 按一天内 15min 槽位的历史均值画像（sanity baseline）
- proportional    : 按历史功率占比把母线 P 分摊到各分路
- ridge           : 多输出岭回归（numpy 闭式解，核心依赖零 sklearn）
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from nilm.models.base import BaseModel
from nilm.models.registry import MODEL_REGISTRY


def _col_index(feature_names: Sequence[str] | None, col: str) -> int:
    if feature_names is None:
        raise ValueError(f"模型需要 feature_names 以定位列 {col!r}")
    names = list(feature_names)
    if col not in names:
        raise KeyError(f"特征矩阵缺少列 {col!r}，现有: {names}")
    return names.index(col)


@MODEL_REGISTRY.register("history_profile")
class HistoryProfile(BaseModel):
    """按 15min 槽位（0..95）记录各分路均值画像。需要特征列 ``slot``。"""

    name = "history_profile"

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        self._slot_idx = _col_index(feature_names, "slot")
        slots = X[:, self._slot_idx].astype(int)
        n_slots = max(96, int(slots.max()) + 1)
        acc = np.zeros((n_slots, y.shape[1]))
        cnt = np.zeros(n_slots)
        np.add.at(acc, slots, y)
        np.add.at(cnt, slots, 1)
        self._profile = acc / np.maximum(cnt, 1)[:, None]
        self._fallback = y.mean(axis=0)

    def predict(self, X) -> np.ndarray:
        slots = np.clip(X[:, self._slot_idx].astype(int), 0, len(self._profile) - 1)
        out = self._profile[slots].copy()
        # 训练时未见过的槽位回退到全局均值
        unseen = self._profile.sum(axis=1) == 0
        if unseen.any():
            out[np.isin(slots, np.where(unseen)[0])] = self._fallback
        return np.clip(out, 0.0, None)


@MODEL_REGISTRY.register("proportional")
class ProportionalAllocation(BaseModel):
    """按训练集功率占比把母线总有功 pbus 分摊到各目标。需要特征列 ``pbus``。"""

    name = "proportional"

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        self._p_idx = _col_index(feature_names, "pbus")
        total = float(y.sum())
        self._shares = y.sum(axis=0) / total if total > 0 else np.full(y.shape[1], 1.0 / y.shape[1])

    def predict(self, X) -> np.ndarray:
        p_bus = X[:, self._p_idx]
        return np.clip(p_bus[:, None] * self._shares[None, :], 0.0, None)


@MODEL_REGISTRY.register("ridge")
class RidgeDisaggregator(BaseModel):
    """多输出岭回归闭式解：W = (X'X + αI)^{-1} X'Y（含截距列）。"""

    name = "ridge"

    def __init__(self, alpha: float = 1.0, **params) -> None:
        super().__init__(alpha=alpha, **params)
        self.alpha = float(alpha)

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        Xb = np.hstack([X, np.ones((len(X), 1))])
        reg = self.alpha * np.eye(Xb.shape[1])
        reg[-1, -1] = 0.0  # 不惩罚截距
        self._W = np.linalg.solve(Xb.T @ Xb + reg, Xb.T @ y)

    def predict(self, X) -> np.ndarray:
        Xb = np.hstack([X, np.ones((len(X), 1))])
        return Xb @ self._W
