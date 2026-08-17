"""Cleaner 短缺口插值语义：长缺口不得部分填充（2842 用户 6-13 问题回归测试）。

问题：pandas interpolate(limit=N) 对长缺口仍填前 N 个点（部分填充），
把全天缺失天"漏"出伪有效点 → 缺失天统计/无效天判定被污染。
正确语义：缺口游程 ≤ max_gap_interp 整段插补，> max_gap_interp 整段保留 NaN。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.preprocess.clean import Cleaner, _interp_short_gaps


def _series(vals: list) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(vals), freq="15min")
    return pd.Series(vals, index=idx, dtype=float)


def test_short_gap_fully_interpolated():
    """≤ max_gap 的内部缺口：整段线性插补。"""
    s = _series([10.0, np.nan, np.nan, 40.0])
    out = _interp_short_gaps(s, max_gap=2)
    assert np.allclose(out.to_numpy(), [10.0, 20.0, 30.0, 40.0])


def test_long_gap_not_partially_filled():
    """> max_gap 的缺口：整段保留 NaN（不得只填前 N 个点）。"""
    s = _series([10.0] + [np.nan] * 5 + [40.0])
    out = _interp_short_gaps(s, max_gap=2)
    assert out.iloc[1:6].isna().all(), "长缺口被部分填充"
    assert out.iloc[0] == 10.0 and out.iloc[6] == 40.0


def test_trailing_gap_not_extrapolated():
    """尾部缺口（后无有效值）：不外推填充——全天缺失天保持全 NaN。"""
    s = _series([10.0, 20.0] + [np.nan] * 4)
    out = _interp_short_gaps(s, max_gap=2)
    assert out.iloc[2:].isna().all(), "尾部缺口被外推填充"


def test_leading_gap_not_filled():
    """头部缺口（前无有效值）：不得填充。"""
    s = _series([np.nan, np.nan, 30.0, 40.0])
    out = _interp_short_gaps(s, max_gap=2)
    assert out.iloc[:2].isna().all()


def test_all_missing_day_stays_missing_after_clean():
    """端到端回归（2842 场景）：前一天有值、次日整天 NaN → 清洗后该日仍全 NaN。"""
    idx = pd.date_range("2026-06-12", periods=96 * 2, freq="15min")
    p1 = np.concatenate([np.zeros(96), np.full(96, np.nan)])  # 6-12 全 0，6-13 全缺失
    df = pd.DataFrame({"p1": p1}, index=idx)
    out = Cleaner(clip_negative=True, max_gap_interp=2).transform(df)
    day = out.loc["2026-06-13", "p1"]
    assert day.isna().all(), f"全天缺失天被漏入 {int(day.notna().sum())} 个伪有效点"
    # 缺失天统计随之正确
    from nilm.data_io.validator import cleaned_daily_stats
    st = cleaned_daily_stats(out, on_thr_w=10.0)
    assert st["missing_dates"] == ["2026-06-13"]
    assert st["actual_days"] == 1


def test_multiple_gaps_mixed():
    """混合场景：短缺口补、长缺口留，互不影响。"""
    s = _series([1.0, np.nan, 3.0, np.nan, np.nan, np.nan, 7.0, np.nan, 9.0])
    out = _interp_short_gaps(s, max_gap=2)
    assert out.iloc[1] == 2.0            # 短缺口(1)已补
    assert out.iloc[3:6].isna().all()    # 长缺口(3)保留
    assert out.iloc[7] == 8.0            # 短缺口(1)已补
