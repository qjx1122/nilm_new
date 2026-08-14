"""分路开机情况分析（训练/推理前置）：逐分路、逐天统计开机时间段。

规则（用户需求 2026-08-14）：
- 功率 ≥ on_thr_w 判为开机（与状态后处理同一口径）；
- 每个开机时间段一行：起始/结束时间、开机时长、最小/平均/峰值功率、
  电量（kWh）、开机状态 state=1；
- 某天整天无开机：输出一行覆盖整天——时间段与时长为整天（该日实际数据
  范围），state=0，最小/平均/峰值功率与电量按整天数据统计；
- 纯分析只读数据，结果由 pipeline 落盘 CSV。

采样间隔从该日时间戳中位差推断（容忍 5min/15min 混合数据）；
电量 = Σ(P_w × Δt_h) / 1000（P 单位 W → 电量 kWh）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common.logging import get_logger

log = get_logger("analysis.branch_sessions")

SESSION_COLUMNS = ["branch", "date", "session_id", "state",
                   "start_time", "end_time", "duration_min",
                   "p_min_w", "p_mean_w", "p_max_w", "energy_kwh", "n_points"]


def _interval_minutes(idx: pd.DatetimeIndex) -> float:
    """推断采样间隔（分钟）：时间戳中位差；单点日回退 15min。"""
    if len(idx) < 2:
        return 15.0
    diffs = np.diff(idx.values)  # timedelta64（不依赖底层单位 ns/us）
    return float(np.median(diffs) / np.timedelta64(1, "m"))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """布尔序列中 True 游程的 [start, end] 闭区间索引列表。"""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _stats(p: np.ndarray, minutes: float) -> dict:
    """一段功率序列的统计量（功率 W、电量 kWh）。"""
    return {"p_min_w": round(float(np.nanmin(p)), 3),
            "p_mean_w": round(float(np.nanmean(p)), 3),
            "p_max_w": round(float(np.nanmax(p)), 3),
            "energy_kwh": round(float(np.nansum(p) * minutes / 60.0 / 1000.0), 6),
            "n_points": int(np.isfinite(p).sum())}


def analyze_branch_sessions(branch: pd.DataFrame, on_thr_w: float,
                            columns: list[str] | None = None) -> pd.DataFrame:
    """逐分路逐天开机时间段分析。

    branch  : DatetimeIndex × 分路功率列（W）的 DataFrame（清洗后）；
    on_thr_w: 开机功率阈值（W），与 §12.3 状态判据同一口径；
    columns : 参与分析的列（默认全部数值列）。
    返回 SESSION_COLUMNS 结构的 DataFrame（无数据时为空表）。
    """
    cols = columns or [c for c in branch.columns
                       if pd.api.types.is_numeric_dtype(branch[c])]
    rows: list[dict] = []
    for col in cols:
        s = branch[col].dropna()
        if s.empty:
            continue
        for day, day_s in s.groupby(s.index.normalize()):
            idx = day_s.index
            p = day_s.to_numpy(np.float64)
            minutes = _interval_minutes(idx)
            date_str = day.strftime("%Y-%m-%d")
            on_runs = _runs(p >= float(on_thr_w))
            if not on_runs:  # 整天无开机：整天一行，state=0，统计整天数据
                rows.append({"branch": col, "date": date_str, "session_id": 0,
                             "state": 0,
                             "start_time": idx[0].strftime("%Y-%m-%d %H:%M:%S"),
                             "end_time": idx[-1].strftime("%Y-%m-%d %H:%M:%S"),
                             "duration_min": round(len(p) * minutes, 1),
                             **_stats(p, minutes)})
                continue
            for k, (i, j) in enumerate(on_runs, start=1):
                seg = p[i:j + 1]
                rows.append({"branch": col, "date": date_str, "session_id": k,
                             "state": 1,
                             "start_time": idx[i].strftime("%Y-%m-%d %H:%M:%S"),
                             "end_time": idx[j].strftime("%Y-%m-%d %H:%M:%S"),
                             "duration_min": round(len(seg) * minutes, 1),
                             **_stats(seg, minutes)})
    df = pd.DataFrame(rows, columns=SESSION_COLUMNS)
    if len(df):
        n_on = int((df["state"] == 1).sum())
        log.info("分路开机分析：%d 分路 × %d 天，开机段 %d 个、全关天 %d 天",
                 df["branch"].nunique(), df["date"].nunique(),
                 n_on, int((df["state"] == 0).sum()))
    return df
