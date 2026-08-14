"""分路开机情况分析：开机段切分 / 整天关机行 / 统计量 / 电量 / 采样间隔推断。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.analysis import analyze_branch_sessions
from nilm.analysis.branch_sessions import SESSION_COLUMNS, _interval_minutes


def _day_frame(values: list[float], start: str = "2026-01-01",
               freq: str = "15min", col: str = "p1") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.DataFrame({col: values}, index=idx)


def test_single_on_session_stats():
    """一天一个开机段：起止/时长/最小/平均/峰值/电量/状态全部正确。"""
    # 96 点 15min；点 40..47（10:00–11:45）开机 100..170W，其余 0
    vals = [0.0] * 96
    for k in range(8):
        vals[40 + k] = 100.0 + 10 * k
    df = analyze_branch_sessions(_day_frame(vals), on_thr_w=50.0)
    assert list(df.columns) == SESSION_COLUMNS
    on = df[df["state"] == 1]
    assert len(on) == 1
    r = on.iloc[0]
    assert r["start_time"] == "2026-01-01 10:00:00"
    assert r["end_time"] == "2026-01-01 11:45:00"
    assert r["duration_min"] == 8 * 15
    assert r["p_min_w"] == 100.0 and r["p_max_w"] == 170.0
    assert abs(r["p_mean_w"] - 135.0) < 1e-9
    # 电量 = Σ(P×0.25h)/1000 = (100+..+170)*0.25/1000 kWh
    assert abs(r["energy_kwh"] - sum(range(100, 180, 10)) * 0.25 / 1000) < 1e-9


def test_multiple_sessions_per_day():
    """一天多个开机段：session_id 递增，段间关机不产生行。"""
    vals = [0.0] * 96
    vals[10:14] = [80.0] * 4    # 段1
    vals[50:52] = [120.0] * 2   # 段2
    df = analyze_branch_sessions(_day_frame(vals), on_thr_w=50.0)
    on = df[df["state"] == 1]
    assert list(on["session_id"]) == [1, 2]
    assert list(on["duration_min"]) == [60.0, 30.0]


def test_all_off_day_gets_whole_day_row():
    """整天无开机：一行整天记录，state=0，统计整天数据。"""
    vals = [1.0, 2.0, 3.0, 4.0] * 24  # 全部 < 阈值
    df = analyze_branch_sessions(_day_frame(vals), on_thr_w=50.0)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["state"] == 0 and r["session_id"] == 0
    assert r["start_time"] == "2026-01-01 00:00:00"
    assert r["end_time"] == "2026-01-01 23:45:00"
    assert r["duration_min"] == 96 * 15
    assert r["p_min_w"] == 1.0 and r["p_max_w"] == 4.0
    assert abs(r["p_mean_w"] - 2.5) < 1e-9
    assert abs(r["energy_kwh"] - sum(vals) * 0.25 / 1000) < 1e-9


def test_multi_day_multi_branch():
    """跨天+多分路：逐分路逐天独立统计；开机跨午夜按天切开。"""
    idx = pd.date_range("2026-01-01 23:00", periods=8, freq="30min")  # 跨午夜
    br = pd.DataFrame({"p1": [100.0] * 8, "p2": [0.0] * 8}, index=idx)
    df = analyze_branch_sessions(br, on_thr_w=50.0)
    p1 = df[df["branch"] == "p1"]
    assert set(p1["date"]) == {"2026-01-01", "2026-01-02"}   # 按天切开
    assert (p1["state"] == 1).all()
    p2 = df[df["branch"] == "p2"]
    assert (p2["state"] == 0).all() and len(p2) == 2         # 两天各一条整天关机


def test_interval_inference_5min():
    """5min 数据的时长/电量按 5min 推断。"""
    vals = [100.0] * 12  # 1 小时 5min 数据
    df = analyze_branch_sessions(_day_frame(vals, freq="5min"), on_thr_w=50.0)
    r = df.iloc[0]
    assert r["duration_min"] == 60.0
    assert abs(r["energy_kwh"] - 100 * 1 / 1000) < 1e-9      # 100W×1h


def test_nan_rows_excluded():
    """NaN 行剔除后再分组（不产生虚假关机段）。"""
    vals = [np.nan] * 4 + [100.0] * 4 + [np.nan] * 88
    df = analyze_branch_sessions(_day_frame(vals), on_thr_w=50.0)
    assert len(df) == 1 and df.iloc[0]["state"] == 1
    assert df.iloc[0]["n_points"] == 4


def test_interval_minutes_single_point():
    idx = pd.DatetimeIndex(["2026-01-01 10:00"])
    assert _interval_minutes(idx) == 15.0
