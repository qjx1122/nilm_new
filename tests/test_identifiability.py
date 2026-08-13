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
