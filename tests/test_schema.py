"""common.schema：母线/分路标准列模式校验。"""

import numpy as np
import pandas as pd
import pytest

from nilm.common import schema


def _bus_df(n=96, freq="15min"):
    idx = pd.date_range("2026-01-01", periods=n, freq=freq)
    data = {c: np.full(n, 220.0) for c in schema.BUS_REQUIRED}
    return pd.DataFrame(data, index=idx)


def test_validate_bus_ok():
    schema.validate_bus_frame(_bus_df(), expected_freq="15min")


def test_missing_column_raises():
    df = _bus_df().drop(columns=["u_a"])
    with pytest.raises(schema.SchemaError, match="u_a"):
        schema.validate_bus_frame(df)


def test_non_datetime_index_raises():
    df = _bus_df().reset_index(drop=True)
    with pytest.raises(schema.SchemaError):
        schema.validate_bus_frame(df)


def test_branch_columns_and_validation():
    idx = pd.date_range("2026-01-01", periods=96, freq="15min")
    good = pd.DataFrame({"branch_1": np.ones(96), "branch_2": np.ones(96)}, index=idx)
    schema.validate_branch_frame(good)
    assert schema.branch_columns(good) == ["branch_1", "branch_2"]

    bad = pd.DataFrame({"power": np.ones(96)}, index=idx)
    with pytest.raises(schema.SchemaError):
        schema.validate_branch_frame(bad)
