"""common.schema：指南 §4 内部字段名契约（ua/…/pfc、分路 pN）。"""

import numpy as np
import pandas as pd
import pytest

from nilm.common import schema


def _bus_df(n=96, freq="15min"):
    idx = pd.date_range("2026-01-01", periods=n, freq=freq)
    data = {}
    for c in schema.BUS_REQUIRED:
        data[c] = np.full(n, 220.0 if c.startswith("u") else (10.0 if c.startswith("i") else (100.0 if c.startswith("p") else 0.9)))
    return pd.DataFrame(data, index=idx)


def test_validate_bus_ok():
    schema.validate_bus_frame(_bus_df(), expected_freq="15min")


def test_missing_column_raises():
    df = _bus_df().drop(columns=["ua"])
    with pytest.raises(schema.SchemaError, match="ua"):
        schema.validate_bus_frame(df)


def test_branch_columns_sorted():
    idx = pd.date_range("2026-01-01", periods=4, freq="15min")
    df = pd.DataFrame({"p2": [1] * 4, "p10": [1] * 4, "p1": [1] * 4, "x": [1] * 4}, index=idx)
    assert schema.branch_power_columns(df) == ["p1", "p2", "p10"]
    schema.validate_branch_frame(df)


def test_branch_without_pn_raises():
    idx = pd.date_range("2026-01-01", periods=4, freq="15min")
    with pytest.raises(schema.SchemaError):
        schema.validate_branch_frame(pd.DataFrame({"power": [1] * 4}, index=idx))


def test_power_column_detection():
    assert schema.is_power_column("pa") and schema.is_power_column("ptotal")
    assert schema.is_power_column("p1") and schema.is_power_column("p12")
    assert not schema.is_power_column("pfa") and not schema.is_power_column("pf")


def test_bus_total_prefers_ptotal():
    df = _bus_df()
    assert np.allclose(schema.bus_total(df), 300.0)  # pa+pb+pc
    df["ptotal"] = 250.0
    assert np.allclose(schema.bus_total(df), 250.0)
