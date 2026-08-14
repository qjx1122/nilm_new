"""data_io.csv_source：官方点位表加载行为——缺列置 0（用户规则）与哨兵值处理。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from nilm.data_io.csv_source import CsvBusLoader

IDX = pd.date_range("2026-01-01 00:04:59", periods=6, freq="5min")
SENT = -2147483648


def _write_bus(tmp_path: Path, cols: dict) -> Path:
    df = pd.DataFrame({"event_time": IDX.strftime("%Y-%m-%d %H:%M:%S"), **cols})
    p = tmp_path / "e241_8001_9001-Ch1-260101-260101.csv"
    df.to_csv(p, index=False)
    return p


OFFICIAL_MAP = {
    "ua": {"ch": 1, "column": "load_iden_data9", "multiplier": 1.0, "unit": "V"},
    "ia": {"ch": 1, "column": "load_iden_data1", "multiplier": 1.0, "unit": "A"},
    "pa": {"ch": 1, "column": "load_iden_data7", "multiplier": 1.0, "unit": "W"},
    "pfa": {"ch": 1, "column": "load_iden_data8", "multiplier": 1.0},
    "ub": {"ch": 1, "column": "load_iden_data45", "multiplier": 1.0, "unit": "V"},
    "ib": {"ch": 1, "column": "load_iden_data37", "multiplier": 1.0, "unit": "A"},
    "pb": {"ch": 1, "column": "load_iden_data43", "multiplier": 1.0, "unit": "W"},
    "pfb": {"ch": 1, "column": "load_iden_data44", "multiplier": 1.0},
    "uc": {"ch": 1, "column": "load_iden_data81", "multiplier": 1.0, "unit": "V"},
    "ic": {"ch": 1, "column": "load_iden_data73", "multiplier": 1.0, "unit": "A"},
    "pc": {"ch": 1, "column": "load_iden_data79", "multiplier": 1.0, "unit": "W"},
    "pfc": {"ch": 1, "column": "load_iden_data80", "multiplier": 1.0},
}


def test_multiplier_applied(tmp_path):
    """倍率规则：实际物理量 = 原始数据 × multiplier（官方 /1000 → 0.001）。"""
    fmap = {
        "ua": {"ch": 1, "column": "load_iden_data9", "multiplier": 0.001, "unit": "V"},
        "pa": {"ch": 1, "column": "load_iden_data7", "multiplier": 0.001, "unit": "W"},
        "pfa": {"ch": 1, "column": "load_iden_data8", "multiplier": 0.001, "unit": ""},
        "ia": {"ch": 1, "column": "load_iden_data1", "multiplier": 0.001, "unit": "A"},
    }
    f = _write_bus(tmp_path, {"load_iden_data9": 220000.0, "load_iden_data7": 56573.0,
                              "load_iden_data8": 916.0, "load_iden_data1": 756.0})
    df, report = CsvBusLoader().load([f], fmap)
    assert np.allclose(df["ua"], 220.0)        # 220000/1000 = 220 V
    assert np.allclose(df["pa"], 56.573)       # 56573/1000
    assert np.allclose(df["pfa"], 0.916)       # PF 归一到无量纲
    assert np.allclose(df["ia"], 0.756)
    assert report["fields"]["ua"]["multiplier"] == 0.001


def test_missing_columns_zero_filled(tmp_path):
    """文件中找不到映射列 → 该列置 0，日志/报告标记 MISSING_COLUMN_ZERO_FILLED。"""
    # 文件只有 data1(ia)/data7(pa)/data8(pfa)，缺 ua(data9) 及 b/c 相全部列
    f = _write_bus(tmp_path, {"load_iden_data1": 10.0, "load_iden_data7": 1000.0,
                              "load_iden_data8": 0.9})
    df, report = CsvBusLoader().load([f], OFFICIAL_MAP)
    # 存在的列正常加载
    assert np.allclose(df["ia"], 10.0) and np.allclose(df["pa"], 1000.0)
    # 缺失列全部置 0
    for c in ["ua", "ub", "uc", "ib", "ic", "pb", "pc", "pfb", "pfc"]:
        assert np.allclose(df[c], 0.0), c
    # 报告标记（非致命，不含 SCHEMA_UNCONFIRMED）
    zf = [i for i in report["issues"] if "MISSING_COLUMN_ZERO_FILLED" in i]
    assert len(zf) == 9
    assert not any("SCHEMA_UNCONFIRMED" in i for i in report["issues"])
    assert report["fields"]["ua"]["zero_filled"] is True
    assert report["fields"]["ia"].get("zero_filled") is not True


def test_all_columns_present_no_zero_fill(tmp_path):
    cols = {OFFICIAL_MAP[k]["column"]: (220.0 if k.startswith("u") else
                                        10.0 if k.startswith("i") else
                                        1000.0 if k.startswith("p") and len(k) <= 2 else 0.9)
            for k in OFFICIAL_MAP}
    f = _write_bus(tmp_path, cols)
    df, report = CsvBusLoader().load([f], OFFICIAL_MAP)
    assert not any("MISSING_COLUMN_ZERO_FILLED" in i for i in report["issues"])
    assert np.allclose(df["ua"], 220.0) and np.allclose(df["pc"], 1000.0)


def test_sentinels_and_zero_fill_combined(tmp_path):
    """哨兵值置 NaN 与缺列置 0 互不干扰。"""
    f = _write_bus(tmp_path, {"load_iden_data1": [SENT] * 6, "load_iden_data7": 500.0})
    df, report = CsvBusLoader().load([f], OFFICIAL_MAP, sentinels=[SENT])
    assert df["ia"].isna().all()          # 哨兵 → NaN（不是 0）
    assert np.allclose(df["pa"], 500.0)
    assert np.allclose(df["ua"], 0.0)     # 缺列 → 0


def test_pf_recompute_fallback_on_zero_voltage(tmp_path):
    """电压置 0 无法重算 PF → 回退文件 PF 均值（不产生 NaN 吞掉样本）。"""
    from nilm.preprocess.align import resample_bus
    from nilm.common.schema import BUS_REQUIRED

    idx = pd.date_range("2026-01-01", periods=9, freq="5min")
    data = {c: np.full(9, 0.0) for c in BUS_REQUIRED}
    data.update({"ia": np.full(9, 10.0), "pa": np.full(9, 1000.0),
                 "pfa": np.full(9, 0.85)})       # ua=0（置 0 场景），文件 PF 存在
    bus = pd.DataFrame(data, index=idx)
    bus15, record = resample_bus(bus, strategy={"pf": "recompute"})
    assert np.allclose(bus15["pfa"], 0.85)        # 回退到文件 PF 均值
    assert not bus15["pfa"].isna().any()
    assert record["strategy"]["pf"] == "recompute"
