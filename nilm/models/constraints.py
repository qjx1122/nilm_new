"""物理约束后处理（模型无关，任何模型输出都可挂接，技术方案 §6.3）。

- 非负投影：负荷有功不为负；
- 总和一致性：各分路估计之和 ≈ 母线有功（按比例缩放投影）。
"""

from __future__ import annotations

import numpy as np

EPS = 1e-9


def clip_nonnegative(y_hat: np.ndarray) -> np.ndarray:
    return np.clip(y_hat, 0.0, None)


def project_sum_consistency(y_hat: np.ndarray, p_bus: np.ndarray,
                            eps: float = EPS) -> np.ndarray:
    """按行把 Σ_k ŷ_k 投影到 p_bus（保持各分路比例不变）。

    某行 Σŷ ≈ 0 时不做缩放（无信息可投影），直接返回原值。
    """
    y_hat = np.asarray(y_hat, dtype=np.float64)
    p_bus = np.asarray(p_bus, dtype=np.float64)
    row_sum = y_hat.sum(axis=1, keepdims=True)
    scale = np.where(row_sum > eps, p_bus[:, None] / row_sum, 1.0)
    return y_hat * scale


def apply_constraints(y_hat: np.ndarray, p_bus: np.ndarray | None = None,
                      nonnegative: bool = True, sum_consistency: bool = True) -> np.ndarray:
    """统一入口：按开关顺序施加约束（先非负、后总和一致性）。"""
    if nonnegative:
        y_hat = clip_nonnegative(y_hat)
    if sum_consistency and p_bus is not None:
        y_hat = project_sum_consistency(y_hat, p_bus)
    return y_hat
