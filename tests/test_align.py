"""preprocess.align：288→96 降采样与时间网格对齐。"""

import numpy as np
import pandas as pd
import pytest

from nilm.common import schema
from nilm.preprocess.align import align_frames, resample_bus_to_branch_freq


def _bus_5min(days=2, value=100.0):
    idx = pd.date_range("2026-01-01", periods=288 * days, freq="5min")
    data = {c: np.full(len(idx), value) for c in schema.BUS_REQUIRED}
    return pd.DataFrame(data, index=idx)


def _branch_15min(days=2):
    idx = pd.date_range("2026-01-01", periods=96 * days, freq="15min")
    return pd.DataFrame({"branch_1": np.arange(len(idx), dtype=float)}, index=idx)


def test_resample_288_to_96_keeps_mean():
    bus15 = resample_bus_to_branch_freq(_bus_5min(value=123.0))
    assert len(bus15) == 96 * 2
    assert np.allclose(bus15["p_total"], 123.0)  # 常数序列均值不变


def test_align_frames_identical_grid():
    bus15 = resample_bus_to_branch_freq(_bus_5min())
    bus_al, br_al = align_frames(bus15, _branch_15min())
    assert bus_al.index.equals(br_al.index)
    assert len(bus_al) == 96 * 2


def test_align_frames_time_shift_fails():
    """时间不同步（7min 偏移）→ 重叠率为 0，必须快速失败。"""
    bus15 = resample_bus_to_branch_freq(_bus_5min())
    shifted = _branch_15min()
    shifted.index = shifted.index + pd.Timedelta(minutes=7)
    with pytest.raises(ValueError, match="重叠率"):
        align_frames(bus15, shifted)
