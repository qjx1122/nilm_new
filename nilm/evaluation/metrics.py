"""指标体系（技术方案 §7.1）：回归指标 + 状态分类指标，逐分路 + 宏平均。

- 回归指标：mae / rmse / r2 / sae / mape（输入为功率矩阵）；
- 状态分类指标：f1 / accuracy / precision / recall——按用户配置 ``on_thr_w``
  （指南 §12.3）把功率二值化为开/关态后计算混淆矩阵；
- 混淆矩阵计数：tp / fp / fn / tn——同一二值化口径下的原始计数
  （per_branch 为各分路计数，macro 为跨分路总数），仅作诊断输出不参与排序；
- 额外参数（如 on_thr_w）经 ``evaluate_all(..., **kwargs)`` 透传，回归指标忽略。

输入约定：y_true / y_pred 均为 (n_samples, n_branches)。
"""

from __future__ import annotations

import numpy as np

from nilm.common.registry import Registry

METRIC_REGISTRY: Registry = Registry("metric")

EPS = 1e-9
DEFAULT_ON_THR_W = 10.0   # §12.3 默认开态阈值


def _per_branch(fn) -> dict:
    """包装：返回 {'per_branch': [...], 'macro': float}；透传额外参数。"""

    def wrapped(y_true: np.ndarray, y_pred: np.ndarray, **kwargs) -> dict:
        per = [float(fn(y_true[:, k], y_pred[:, k], **kwargs))
               for k in range(y_true.shape[1])]
        return {"per_branch": per, "macro": float(np.mean(per))}

    return wrapped


# ---------------------------------------------------------------- 回归指标
@METRIC_REGISTRY.register("mae")
@_per_branch
def mae(t: np.ndarray, p: np.ndarray, **_) -> float:
    """平均绝对误差。"""
    return np.mean(np.abs(t - p))


@METRIC_REGISTRY.register("rmse")
@_per_branch
def rmse(t: np.ndarray, p: np.ndarray, **_) -> float:
    """均方根误差。"""
    return float(np.sqrt(np.mean((t - p) ** 2)))


@METRIC_REGISTRY.register("r2")
@_per_branch
def r2(t: np.ndarray, p: np.ndarray, **_) -> float:
    """决定系数；标签方差近零时记 0（无信息可拟合）。"""
    ss_res = float(np.sum((t - p) ** 2))
    ss_tot = float(np.sum((t - t.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > EPS else 0.0


@METRIC_REGISTRY.register("sae")
def sae(y_true: np.ndarray, y_pred: np.ndarray, **_) -> dict:
    """信号聚合误差（NILM 惯例）：|Σpred − Σtrue| / |Σtrue|，按分路整段计算。"""
    per = []
    for k in range(y_true.shape[1]):
        denom = abs(float(y_true[:, k].sum()))
        per.append(abs(float(y_pred[:, k].sum() - y_true[:, k].sum())) / (denom + EPS))
    return {"per_branch": per, "macro": float(np.mean(per))}


@METRIC_REGISTRY.register("mape")
@_per_branch
def mape(t: np.ndarray, p: np.ndarray, eps: float = 1e-3, **_) -> float:
    """平均绝对百分比误差（分母加 ε 保护，近零负荷仅作参考）。"""
    return float(np.mean(np.abs(t - p) / (np.abs(t) + eps)))


# ---------------------------------------------------------------- 状态分类指标
def _confusion(t: np.ndarray, p: np.ndarray, thr: float) -> tuple[int, int, int, int]:
    """按阈值二值化后的混淆矩阵 (TP, FP, FN, TN)。"""
    t_on = np.asarray(t) >= thr
    p_on = np.asarray(p) >= thr
    tp = int((p_on & t_on).sum())
    fp = int((p_on & ~t_on).sum())
    fn = int((~p_on & t_on).sum())
    tn = int((~p_on & ~t_on).sum())
    return tp, fp, fn, tn


def _precision_of(tp: int, fp: int, fn: int) -> float:
    """TP/(TP+FP)；无开态预测时：无漏报记 1.0（空真），有漏报记 0.0。"""
    return tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)


def _recall_of(tp: int, fn: int) -> float:
    """TP/(TP+FN)；标签无开态时记 1.0（空真）。"""
    return tp / (tp + fn) if (tp + fn) > 0 else 1.0


def _f1_of(prec: float, rec: float) -> float:
    return 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0


@METRIC_REGISTRY.register("accuracy")
@_per_branch
def accuracy(t: np.ndarray, p: np.ndarray, on_thr_w: float = DEFAULT_ON_THR_W, **_) -> float:
    """开/关态准确率 (TP+TN)/N；on_thr_w 为开态阈值（§12.3）。"""
    tp, fp, fn, tn = _confusion(t, p, on_thr_w)
    n = tp + fp + fn + tn
    return (tp + tn) / n if n else 0.0


@METRIC_REGISTRY.register("precision")
@_per_branch
def precision(t: np.ndarray, p: np.ndarray, on_thr_w: float = DEFAULT_ON_THR_W, **_) -> float:
    """开态精确率 TP/(TP+FP)。"""
    tp, fp, fn, _ = _confusion(t, p, on_thr_w)
    return _precision_of(tp, fp, fn)


@METRIC_REGISTRY.register("recall")
@_per_branch
def recall(t: np.ndarray, p: np.ndarray, on_thr_w: float = DEFAULT_ON_THR_W, **_) -> float:
    """开态召回率 TP/(TP+FN)。"""
    tp, _, fn, _ = _confusion(t, p, on_thr_w)
    return _recall_of(tp, fn)


@METRIC_REGISTRY.register("f1")
@_per_branch
def f1(t: np.ndarray, p: np.ndarray, on_thr_w: float = DEFAULT_ON_THR_W, **_) -> float:
    """开态 F1 = 2·P·R/(P+R)。"""
    tp, fp, fn, _ = _confusion(t, p, on_thr_w)
    return _f1_of(_precision_of(tp, fp, fn), _recall_of(tp, fn))


# ---------------------------------------------------------- 混淆矩阵计数（TP/FP/FN/TN）
def _register_count_metric(name: str, idx: int) -> None:
    """注册混淆矩阵单元计数指标：per_branch 为各分路计数，macro 为跨分路总数。

    注意：计数是诊断性输出（样本量相关），不参与模型优劣排序（见 compare.COUNT_METRICS）。
    """

    def counter(y_true: np.ndarray, y_pred: np.ndarray,
                on_thr_w: float = DEFAULT_ON_THR_W, **_) -> dict:
        per = [float(_confusion(y_true[:, k], y_pred[:, k], on_thr_w)[idx])
               for k in range(y_true.shape[1])]
        return {"per_branch": per, "macro": float(sum(per))}

    counter.__name__ = name
    counter.__doc__ = f"混淆矩阵 {name.upper()} 计数（按 on_thr_w 二值化；macro=跨分路总数）。"
    METRIC_REGISTRY.register(name)(counter)


for _name, _idx in (("tp", 0), ("fp", 1), ("fn", 2), ("tn", 3)):
    _register_count_metric(_name, _idx)


def evaluate_all(y_true: np.ndarray, y_pred: np.ndarray,
                 metric_names: list[str], **kwargs) -> dict[str, dict]:
    """按名批量计算指标；额外参数（如 on_thr_w）透传给需要的指标。"""
    y_true = np.atleast_2d(y_true)
    y_pred = np.atleast_2d(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError(f"形状不一致: {y_true.shape} vs {y_pred.shape}")
    return {name: METRIC_REGISTRY.get(name)(y_true, y_pred, **kwargs)
            for name in metric_names}


def evaluate_daily(y_true: np.ndarray, y_pred: np.ndarray, index,
                   metric_names: list[str], **kwargs):
    """按自然日分组评估：每个日期一行（date / n_points / 各指标宏平均）。

    - index：与样本逐行对应的 DatetimeIndex（或可转换对象）；
    - 单日样本量小（如 96 点）时 r2/sae 波动大，日级指标用于诊断趋势而非选型；
    - 返回 pandas.DataFrame，可直接落盘 CSV。
    """
    import pandas as pd

    y_true = np.atleast_2d(y_true)
    y_pred = np.atleast_2d(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError(f"形状不一致: {y_true.shape} vs {y_pred.shape}")
    idx = pd.DatetimeIndex(index)
    if len(idx) != y_true.shape[0]:
        raise ValueError(f"index 长度 {len(idx)} 与样本数 {y_true.shape[0]} 不一致")

    rows = []
    dates = idx.normalize()
    for day in dates.unique().sort_values():
        m = (dates == day).to_numpy() if hasattr(dates == day, "to_numpy") else (dates == day)
        res = evaluate_all(y_true[m], y_pred[m], metric_names, **kwargs)
        rows.append({"date": day.strftime("%Y-%m-%d"), "n_points": int(m.sum()),
                     **{name: res[name]["macro"] for name in metric_names}})
    return pd.DataFrame(rows)
