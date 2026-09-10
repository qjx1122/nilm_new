"""preprocess.target：target_col 契约（指南 §3.3/§12.3）。"""

import numpy as np
import pandas as pd
import pytest

from nilm.preprocess.target import (TargetSpecError, build_target,
                                    parse_target_col, resolve_target_cols)


def test_parse_case_space_and_duplicate():
    assert parse_target_col("P1 + p2") == ["p1", "p2"]
    assert parse_target_col("p1+p2+p3") == ["p1", "p2", "p3"]
    with pytest.raises(TargetSpecError):
        parse_target_col("p1+p1")          # 禁止重复分量
    with pytest.raises(TargetSpecError):
        parse_target_col("")


def test_composite_sum_skipna_false():
    """复合列按行累加；任一组成列 NaN 时复合目标为 NaN（skipna=False）。"""
    idx = pd.date_range("2026-01-01", periods=3, freq="15min")
    branch = pd.DataFrame({"p1": [10.0, 20.0, np.nan], "p2": [1.0, np.nan, 3.0]}, index=idx)
    y = build_target(branch, ["p1", "p2"])
    assert y.iloc[0] == 11.0
    assert np.isnan(y.iloc[1]) and np.isnan(y.iloc[2])


def test_single_col_target():
    idx = pd.date_range("2026-01-01", periods=2, freq="15min")
    branch = pd.DataFrame({"p1": [5.0, 6.0]}, index=idx)
    y = build_target(branch, ["p1"])
    assert list(y) == [5.0, 6.0] and y.name == "target"


def test_resolve_fallback_and_missing():
    idx = pd.date_range("2026-01-01", periods=2, freq="15min")
    branch = pd.DataFrame({"p1": [1.0, 2.0], "p2": [3.0, 4.0]}, index=idx)
    assert resolve_target_cols(None, branch) == ["p1"]           # 回退链
    assert resolve_target_cols("P2", branch) == ["p2"]           # 忽略大小写
    with pytest.raises(TargetSpecError):
        resolve_target_cols("p9", branch)                        # 不存在
