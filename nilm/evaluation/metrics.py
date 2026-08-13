"""指标体系（技术方案 §7.1）：逐分路 + 宏平均，装饰器注册新指标。

输入约定：y_true / y_pred 均为 (n_samples, n_branches)。
"""

from __future__ import annotations

import numpy as np

from nilm.common.registry import Registry

METRIC_REGISTRY: Registry = Registry("metric")

EPS = 1e-9


def _per_branch(fn) -> dict:
    """包装：返回 {'per_branch': [...], 'macro': float}。"""

    def wrapped(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
        per = [float(fn(y_true[:, k], y_pred[:, k])) for k in range(y_true.shape[1])]
        return {"per_branch": per, "macro": float(np.mean(per))}

    return wrapped


@METRIC_REGISTRY.register("mae")
@_per_branch
def mae(t: np.ndarray, p: np.ndarray) -> float:
    """平均绝对误差。"""
    return np.mean(np.abs(t - p))


@METRIC_REGISTRY.register("rmse")
@_per_branch
def rmse(t: np.ndarray, p: np.ndarray) -> float:
    """均方根误差。"""
    return float(np.sqrt(np.mean((t - p) ** 2)))


@METRIC_REGISTRY.register("r2")
@_per_branch
def r2(t: np.ndarray, p: np.ndarray) -> float:
    """决定系数；标签方差近零时记 0（无信息可拟合）。"""
    ss_res = float(np.sum((t - p) ** 2))
    ss_tot = float(np.sum((t - t.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > EPS else 0.0


@METRIC_REGISTRY.register("sae")
def sae(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """信号聚合误差（NILM 惯例）：|Σpred − Σtrue| / |Σtrue|，按分路整段计算。"""
    per = []
    for k in range(y_true.shape[1]):
        denom = abs(float(y_true[:, k].sum()))
        per.append(abs(float(y_pred[:, k].sum() - y_true[:, k].sum())) / (denom + EPS))
    return {"per_branch": per, "macro": float(np.mean(per))}


@METRIC_REGISTRY.register("mape")
@_per_branch
def mape(t: np.ndarray, p: np.ndarray, eps: float = 1e-3) -> float:
    """平均绝对百分比误差（分母加 ε 保护，近零负荷仅作参考）。"""
    return float(np.mean(np.abs(t - p) / (np.abs(t) + eps)))


def evaluate_all(y_true: np.ndarray, y_pred: np.ndarray,
                 metric_names: list[str]) -> dict[str, dict]:
    """按名批量计算指标。"""
    y_true = np.atleast_2d(y_true)
    y_pred = np.atleast_2d(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError(f"形状不一致: {y_true.shape} vs {y_pred.shape}")
    return {name: METRIC_REGISTRY.get(name)(y_true, y_pred) for name in metric_names}
