"""负荷可辨识性分析（指南 §9）：模型训练前强制执行。

产出：Pbus↔目标分路相关性、最优时滞 τ、贡献率、总线解释率、
工作日/周末与昼夜分层分析、风险标记（IDENTIFIABILITY_LOW）。
边界：纯分析，不改数据、不碰模型；报告由 pipeline 落盘为 JSON。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _pearson(a: pd.Series, b: pd.Series) -> float:
    d = pd.concat([a, b], axis=1).dropna()
    if len(d) < 3 or d.iloc[:, 0].std() < 1e-12 or d.iloc[:, 1].std() < 1e-12:
        return float("nan")
    return float(np.corrcoef(d.iloc[:, 0], d.iloc[:, 1])[0, 1])


def _spearman(a: pd.Series, b: pd.Series) -> float:
    d = pd.concat([a, b], axis=1).dropna()
    if len(d) < 3:
        return float("nan")
    return _pearson(d.iloc[:, 0].rank(), d.iloc[:, 1].rank())


def _bus_total(bus: pd.DataFrame) -> pd.Series:
    if "ptotal" in bus.columns:
        return bus["ptotal"]
    return bus[["pa", "pb", "pc"]].sum(axis=1)


def identifiability_report(bus: pd.DataFrame, target: pd.Series,
                           on_thr_w: float = 10.0, max_lag: int = 8) -> dict:
    """生成可辨识性报告（dict，可直接 JSON 化）。

    bus    : 15min 母线对齐数据（含 pa/pb/pc 或 ptotal）
    target : 15min 目标分路功率序列（与 bus 同索引）
    """
    pbus = _bus_total(bus)
    df = pd.concat({"pbus": pbus, "target": target}, axis=1).dropna()

    rep: dict = {"n_valid_points": int(len(df))}
    if len(df) < 96:
        rep["risk"] = ["INSUFFICIENT_TIME_RANGE"]
        rep["identifiable"] = False
        return rep

    # 相关性与最优时滞 τ（pbus 超前 target τ 个 15min）
    rep["pearson"] = _pearson(df["pbus"], df["target"])
    rep["spearman"] = _spearman(df["pbus"], df["target"])
    lags = {}
    for tau in range(-max_lag, max_lag + 1):
        lags[tau] = _pearson(df["pbus"].shift(tau), df["target"])
    valid = {k: v for k, v in lags.items() if v == v}
    best_tau = max(valid, key=valid.get) if valid else 0
    rep["lag_scan"] = {str(k): (None if v != v else round(v, 4)) for k, v in lags.items()}
    rep["best_tau"] = int(best_tau)
    rep["best_tau_corr"] = valid.get(best_tau)

    # 贡献率与总线解释率（target 对 pbus 的 OLS R²）
    mean_pbus = float(df["pbus"].mean())
    rep["contribution_ratio"] = (float(df["target"].mean()) / mean_pbus) if mean_pbus > 1e-9 else None
    ss_tot = float(((df["pbus"] - df["pbus"].mean()) ** 2).sum())
    if ss_tot > 1e-9:
        k = float((df["target"] @ df["pbus"]) / (df["pbus"] @ df["pbus"]))
        resid = df["pbus"] - k * df["target"]
        rep["explained_rate_r2"] = float(1 - (resid ** 2).sum() / ss_tot)
    else:
        rep["explained_rate_r2"] = None

    # 分层分析：工作日/周末 × 昼(06-21)/夜
    hour = df.index.hour
    dow = df.index.dayofweek
    strata = {
        "weekday_day": df[(dow < 5) & (hour >= 6) & (hour < 21)],
        "weekday_night": df[(dow < 5) & ((hour < 6) | (hour >= 21))],
        "weekend_day": df[(dow >= 5) & (hour >= 6) & (hour < 21)],
        "weekend_night": df[(dow >= 5) & ((hour < 6) | (hour >= 21))],
    }
    rep["strata_pearson"] = {k: _pearson(v["pbus"], v["target"]) for k, v in strata.items()}

    # 风险标记（§9）
    risks: list[str] = []
    on_rate = float((df["target"] >= on_thr_w).mean())
    rep["target_on_rate"] = on_rate
    rep["target_std"] = float(df["target"].std())
    if on_rate < 0.01:
        risks.append("BRANCH_LONG_OFF")          # 长期关闭
    # 低方差 = 目标近似恒定（变异系数 CV < 5%）。
    # 不用总线均值的绝对阈值：总线倍率未确认（DATA_UNIT_UNKNOWN）时绝对阈值失真，
    # 且间歇性负荷（大量 0 值）天然 CV 高，不会误报（实测数据校准，见 STATUS.md）。
    t_mean = float(df["target"].mean())
    rep["target_cv"] = float(df["target"].std() / (abs(t_mean) + 1e-9))
    if rep["target_cv"] < 0.05:
        risks.append("BRANCH_LOW_VARIANCE")      # 低方差（近似恒定负荷）
    corr = rep["pearson"]
    if corr == corr and abs(corr) < 0.3:
        risks.append("WEAK_BUS_CORRELATION")     # 与总线弱相关（可能大量未监测负荷）
    # 总线可见性（尺度不变口径）：CT/PT 变比未知时，目标与总线的绝对幅值
    # 不可直接比较（2842 教训：Δp1/Δpbus≈8 实为 CT×PT 综合倍率，并非
    # "未计入总线"）。改用边沿信噪比：目标开机沿处总线跳变幅度 vs 总线
    # 自身背景跳变（同一单位内比较，不受变比影响）——
    #   snr = |Δpbus@开机沿| 中位 / |Δpbus| 背景 P90；
    #   snr < 1 = 开机沿被总线背景波动淹没（停机/开机在总线上不可辨）。
    # 同时输出隐含变比 implied_bus_scale = Δtarget/Δpbus 中位（供 CT/PT 核对）。
    t = df["target"]
    edge = (t.shift(1) < on_thr_w) & (t > max(on_thr_w * 2, 50.0))
    n_edges = int(edge.sum())
    rep["n_on_edges"] = n_edges
    if n_edges >= 10:
        d_bus_all = df["pbus"].diff().abs()
        d_bus = d_bus_all[edge]
        d_t = (t - t.shift(1))[edge]
        bg = d_bus_all[~edge].quantile(0.9)
        snr = float(d_bus.median() / bg) if bg and bg > 0 else float("inf")
        rep["bus_edge_snr"] = round(snr, 4)
        with np.errstate(divide="ignore", invalid="ignore"):
            signed_d_bus = (df["pbus"] - df["pbus"].shift(1))[edge]
            scale = float((d_t / signed_d_bus.replace(0, np.nan)).median())
        rep["implied_bus_scale"] = round(scale, 4) if scale == scale else None
        if snr == snr and snr < 1.0:
            risks.append("TARGET_EDGE_BURIED_IN_BUS")  # 开机沿被总线背景淹没
    else:
        rep["bus_edge_snr"] = None
        rep["implied_bus_scale"] = None
    if risks:
        risks.append("IDENTIFIABILITY_LOW")
    rep["risk"] = risks
    rep["identifiable"] = "IDENTIFIABILITY_LOW" not in risks
    return rep
