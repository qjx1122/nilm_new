"""analysis.identifiability：§9 训练前强制分析（相关性/贡献率/风险标记）。"""

import numpy as np
import pandas as pd

from nilm.analysis.identifiability import identifiability_report

IDX = pd.date_range("2026-01-01", periods=96 * 10, freq="15min")


def _bus(pbus):
    return pd.DataFrame({"pa": pbus / 3, "pb": pbus / 3, "pc": pbus / 3}, index=IDX)


def test_identifiable_branch():
    rng = np.random.default_rng(0)
    pbus = pd.Series(100 + 30 * np.sin(np.arange(len(IDX)) / 10) + rng.normal(0, 1, len(IDX)), index=IDX)
    target = 0.4 * pbus + rng.normal(0, 0.5, len(IDX))
    rep = identifiability_report(_bus(pbus), target)
    assert rep["identifiable"] is True
    assert rep["pearson"] > 0.95
    assert abs(rep["contribution_ratio"] - 0.4) < 0.05
    assert "best_tau" in rep and "lag_scan" in rep
    assert rep["risk"] == []


def test_long_off_branch_flagged():
    pbus = pd.Series(np.linspace(100, 200, len(IDX)), index=IDX)
    target = pd.Series(np.zeros(len(IDX)), index=IDX)  # 长期关闭
    rep = identifiability_report(_bus(pbus), target)
    assert "BRANCH_LONG_OFF" in rep["risk"]
    assert "IDENTIFIABILITY_LOW" in rep["risk"]
    assert rep["identifiable"] is False


def test_insufficient_data():
    pbus = pd.Series([100.0] * 10, index=IDX[:10])
    rep = identifiability_report(_bus(pbus), pbus * 0.5)
    assert rep["identifiable"] is False
    assert "INSUFFICIENT_TIME_RANGE" in rep["risk"]


def test_bus_visibility_ratio_flags_invisible_target():
    """总线可见性：目标开机沿在总线无同步跳变 → TARGET_NOT_VISIBLE_ON_BUS。"""
    import numpy as np
    import pandas as pd

    from nilm.analysis import identifiability_report

    idx = pd.date_range("2026-01-01", periods=96 * 14, freq="15min")
    rng = np.random.default_rng(0)
    # 目标：每天 9:00-18:00 开机 700W；总线：与目标无关的背景 300W±噪声
    hour = idx.hour
    target = pd.Series(np.where((hour >= 9) & (hour < 18), 700.0, 0.0), index=idx)
    bus_bg = 300 + rng.normal(0, 10, len(idx))
    bus = pd.DataFrame({"pa": bus_bg / 2, "pb": 0.0, "pc": bus_bg / 2}, index=idx)
    rep = identifiability_report(bus, target, on_thr_w=10.0)
    assert rep["n_on_edges"] >= 10
    assert rep["bus_visibility_ratio"] is not None
    assert rep["bus_visibility_ratio"] < 0.5
    assert "TARGET_NOT_VISIBLE_ON_BUS" in rep["risk"]
    assert rep["identifiable"] is False

    # 对照：目标计入总线 → 比值≈1，不触发
    bus2 = pd.DataFrame({"pa": (bus_bg + target.to_numpy()) / 2, "pb": 0.0,
                         "pc": (bus_bg + target.to_numpy()) / 2}, index=idx)
    rep2 = identifiability_report(bus2, target, on_thr_w=10.0)
    assert rep2["bus_visibility_ratio"] > 0.9
    assert "TARGET_NOT_VISIBLE_ON_BUS" not in rep2["risk"]
