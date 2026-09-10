"""preprocess.align：288→96 可配置聚合 + 策略记录 + 时滞证据（指南 §5）。"""

import numpy as np
import pandas as pd
import pytest

from nilm.common.schema import BUS_REQUIRED
from nilm.preprocess.align import align_frames, estimate_time_offset, resample_bus


def _bus_5min(days=2, value=100.0):
    idx = pd.date_range("2026-01-01", periods=288 * days, freq="5min")
    data = {}
    for c in BUS_REQUIRED:
        if c.startswith("u"):
            data[c] = np.full(len(idx), 220.0)
        elif c.startswith("i"):
            data[c] = np.full(len(idx), 10.0)
        elif c.startswith("pf"):
            data[c] = np.full(len(idx), 0.9)
        else:
            data[c] = np.full(len(idx), value)
    return pd.DataFrame(data, index=idx)


def test_resample_288_to_96_and_record():
    bus15, record = resample_bus(_bus_5min(value=123.0))
    assert len(bus15) == 96 * 2
    assert np.allclose(bus15[["pa", "pb", "pc"]], 123.0)
    assert record["strategy"]["pf"] == "recompute"     # 策略必须记录（§5.2）
    assert record["n_in"] == 288 * 2 and record["n_out"] == 96 * 2
    # PF 重算 = P/(U*I) = 123/(220*10) ≈ 0.0559
    assert np.allclose(bus15["pfa"], 123.0 / (220.0 * 10.0), atol=1e-9)


def test_pf_mean_strategy_requires_explicit():
    bus15, record = resample_bus(_bus_5min(), strategy={"pf": "mean"})
    assert record["strategy"]["pf"] == "mean"
    with pytest.raises(ValueError):
        resample_bus(_bus_5min(), strategy={"pf": "blackbox"})


def test_align_identical_grid_and_shift_fails():
    bus15, _ = resample_bus(_bus_5min())
    idx = pd.date_range("2026-01-01", periods=96 * 2, freq="15min")
    target = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    bus_al, t_al = align_frames(bus15, target)
    assert bus_al.index.equals(t_al.index)

    shifted = target.copy()
    shifted.index = shifted.index + pd.Timedelta(minutes=7)
    with pytest.raises(ValueError, match="重叠率"):
        align_frames(bus15, shifted)


def test_estimate_time_offset_report_only():
    idx = pd.date_range("2026-01-01", periods=96 * 5, freq="15min")
    pbus = pd.Series(100 + 30 * np.sin(np.arange(len(idx)) / 12), index=idx)
    target = pbus.shift(2).fillna(100)               # target 滞后 pbus 2 个点
    out = estimate_time_offset(pbus, target, max_lag=4)
    assert out["best_tau"] in (2, -2)                # 找到证据即可
    assert "report_only" in out["action"]            # 绝不改时间戳（§5.3）
