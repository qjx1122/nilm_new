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
    """按 15min 槽位（0..95）记录各分路画像。需要特征列 ``slot``。

    agg：槽位聚合方式——"mean"（默认，原行为）或 "median"。
    停机日占比高的用户建议 median：均值画像被开机日拉高，停机时段
    整段误报；中位画像天然压 FP（2842 实测 FP 363→184、F1 0.825→0.859）。

    pbus_bins：>1 时启用**条件画像**——每槽位内按 pbus 分位再分桶，
    画像=profile[slot, pbus_bin]。修补画像模型的「条件缺失」本质缺陷
    （无条件画像回答"这个时刻通常开吗"而非"今天开吗"，全关天必然整段
    误报）：低 pbus 桶由停机样本主导 → 当天总线低时输出关机值。
    需要特征列 ``pbus``；对总线可见性好的用户有效（778 实测 F1
    0.9851→0.9881），可见性差的用户（如 2842，信号/背景日间漂移≈1.9）
    受信息论边界限制收益有限。默认 1 = 关闭（原行为）。
    """

    name = "history_profile"

    def __init__(self, agg: str = "mean", pbus_bins: int = 1, **params) -> None:
        super().__init__(agg=agg, pbus_bins=pbus_bins, **params)
        if agg not in ("mean", "median"):
            raise ValueError(f"history_profile.agg 仅支持 mean/median: {agg!r}")
        if int(pbus_bins) < 1:
            raise ValueError(f"history_profile.pbus_bins 必须 ≥1: {pbus_bins!r}")
        self.agg = agg
        self.pbus_bins = int(pbus_bins)

    def _agg_fn(self, arr: np.ndarray) -> np.ndarray:
        return np.median(arr, axis=0) if self.agg == "median" else arr.mean(axis=0)

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        self._slot_idx = _col_index(feature_names, "slot")
        slots = X[:, self._slot_idx].astype(int)
        n_slots = max(96, int(slots.max()) + 1)
        self._profile = np.zeros((n_slots, y.shape[1]))
        self._pbus_idx = None
        self._cuts = None
        self._cond_profile = None
        for s in np.unique(slots):
            self._profile[s] = self._agg_fn(y[slots == s])
        if self.pbus_bins > 1:  # 条件画像：slot × pbus 分位桶
            self._pbus_idx = _col_index(feature_names, "pbus")
            pbus = X[:, self._pbus_idx].astype(float)
            self._cuts = np.zeros((n_slots, self.pbus_bins - 1))
            self._cond_profile = np.zeros((n_slots, self.pbus_bins, y.shape[1]))
            qs = np.linspace(0, 1, self.pbus_bins + 1)[1:-1]
            for s in np.unique(slots):
                m = slots == s
                self._cuts[s] = np.quantile(pbus[m], qs)
                bins = np.digitize(pbus[m], self._cuts[s])
                for b in range(self.pbus_bins):
                    bm = bins == b
                    self._cond_profile[s, b] = (self._agg_fn(y[m][bm]) if bm.any()
                                                else self._profile[s])
        self._fallback = y.mean(axis=0)
        # 显式记录已见槽位：不得用 profile==0 代理（合法的 0 值画像会被误判 unseen）
        self._seen = np.zeros(n_slots, dtype=bool)
        self._seen[np.unique(slots)] = True

    def predict(self, X) -> np.ndarray:
        slots = np.clip(X[:, self._slot_idx].astype(int), 0, len(self._profile) - 1)
        if self._cond_profile is not None:  # 条件画像路径
            pbus = X[:, self._pbus_idx].astype(float)
            out = np.empty((len(X), self._profile.shape[1]))
            for i, (s, pb) in enumerate(zip(slots, pbus)):
                b = int(np.digitize(pb, self._cuts[s]))
                out[i] = self._cond_profile[s, b]
        else:
            out = self._profile[slots].copy()
        # 训练时未见过的槽位回退到全局均值
        seen = getattr(self, "_seen", None)
        if seen is None:  # 兼容旧 pickle：退回旧代理判定
            seen = self._profile.sum(axis=1) != 0
        unseen_mask = ~seen[slots]
        if unseen_mask.any():
            out[unseen_mask] = self._fallback
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
    """多输出加权岭回归闭式解：W = (X'ΩX + αI)^{-1} X'ΩY（含截距列）。

    off_weight/off_thr_w：关态样本加权——目标功率 < off_thr_w 的样本权重
    乘 off_weight（>1 时模型更重视"预测准 0"，显著压误报 FP；
    2842 实测 off_weight=5 使 FP 481→342、F1 0.769→0.818）。
    默认 1.0 = 不加权（行为与原版一致）。
    """

    name = "ridge"

    def __init__(self, alpha: float = 1.0, off_weight: float = 1.0,
                 off_thr_w: float = 10.0, **params) -> None:
        super().__init__(alpha=alpha, off_weight=off_weight,
                         off_thr_w=off_thr_w, **params)
        self.alpha = float(alpha)
        self.off_weight = float(off_weight)
        self.off_thr_w = float(off_thr_w)

    def fit(self, X, y, feature_names=None, X_val=None, y_val=None) -> None:
        Xb = np.hstack([X, np.ones((len(X), 1))])
        reg = self.alpha * np.eye(Xb.shape[1])
        reg[-1, -1] = 0.0  # 不惩罚截距
        if self.off_weight != 1.0:
            # 行权重：关态（各输出全部 < off_thr_w）样本权重放大
            off = (np.asarray(y) < self.off_thr_w).all(axis=1)
            w = np.where(off, self.off_weight, 1.0)
            self._W = np.linalg.solve(Xb.T @ (Xb * w[:, None]) + reg,
                                      Xb.T @ (y * w[:, None]))
        else:
            self._W = np.linalg.solve(Xb.T @ Xb + reg, Xb.T @ y)

    def predict(self, X) -> np.ndarray:
        Xb = np.hstack([X, np.ones((len(X), 1))])
        return Xb @ self._W
