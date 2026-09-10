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


def test_bus_edge_snr_scale_invariant():
    """总线可见性（尺度不变）：CT/PT 变比不影响判定；沿淹没时才标记风险。

    2842 纠正教训：总线经 CT/PT 变比（原始值=实际/倍率），Δtarget/Δpbus≈8
    是变比而非"未计入总线"——检测必须在总线自身单位内比较（边沿信噪比）。
    """
    import numpy as np
    import pandas as pd

    from nilm.analysis import identifiability_report

    idx = pd.date_range("2026-01-01", periods=96 * 14, freq="15min")
    rng = np.random.default_rng(0)
    hour = idx.hour
    target = pd.Series(np.where((hour >= 9) & (hour < 18), 700.0, 0.0), index=idx)

    # 情形1：设备计入总线但总线经 1/8 变比（CT×PT=8）+ 小背景噪声
    bus_raw = (300.0 + target.to_numpy()) / 8.0 + rng.normal(0, 1.0, len(idx))
    bus = pd.DataFrame({"pa": bus_raw / 2, "pb": 0.0, "pc": bus_raw / 2}, index=idx)
    rep = identifiability_report(bus, target, on_thr_w=10.0)
    assert rep["n_on_edges"] >= 10
    assert rep["bus_edge_snr"] > 1.0, "变比不应触发风险（尺度不变）"
    assert "TARGET_EDGE_BURIED_IN_BUS" not in rep["risk"]
    assert abs(rep["implied_bus_scale"] - 8.0) < 1.0  # 隐含变比≈8 供核对

    # 情形2：开机沿被总线背景波动淹没（背景抖动 >> 沿幅度）
    bus_noisy = 300.0 + target.to_numpy() / 100.0 + rng.normal(0, 50.0, len(idx))
    bus2 = pd.DataFrame({"pa": bus_noisy / 2, "pb": 0.0, "pc": bus_noisy / 2},
                        index=idx)
    rep2 = identifiability_report(bus2, target, on_thr_w=10.0)
    assert rep2["bus_edge_snr"] < 1.0
    assert "TARGET_EDGE_BURIED_IN_BUS" in rep2["risk"]
    assert rep2["identifiable"] is False
