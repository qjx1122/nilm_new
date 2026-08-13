"""common.timefilter：include/exclude 闭区间语义与 splits 锚定（指南 §12.4）。"""

import pandas as pd
import pytest

from nilm.common.timefilter import (anchor_splits, apply_time_filter,
                                    filter_dataframe, parse_endpoint,
                                    parse_intervals)

IDX = pd.date_range("2026-01-01", periods=96 * 3, freq="15min")


def test_date_only_expands_to_full_day():
    assert parse_endpoint("2026-01-02", is_end=False) == pd.Timestamp("2026-01-02 00:00:00")
    assert parse_endpoint("2026-01-02", is_end=True) == pd.Timestamp("2026-01-02 23:59:59")


def test_include_union_then_exclude():
    mask = apply_time_filter(IDX, include=[["2026-01-01", "2026-01-01"],
                                           ["2026-01-03", "2026-01-03"]],
                             exclude=[["2026-01-01 12:00", "2026-01-01 12:00"]])
    s = pd.Series(IDX, index=IDX)
    assert mask[s.dt.day == 2].sum() == 0            # 1 月 2 日不在 include
    assert mask.sum() == 96 * 2 - 1                  # 两天减去 1 个排除点
    assert not mask[pd.Timestamp("2026-01-01 12:00")]


def test_empty_include_means_all():
    mask = apply_time_filter(IDX, include=None, exclude=[["2026-01-01", "2026-01-01"]])
    assert mask.sum() == 96 * 2


def test_invalid_interval_raises():
    with pytest.raises(ValueError):
        parse_intervals([["2026-01-03", "2026-01-01"]])
    with pytest.raises(ValueError):
        parse_intervals([["2026-01-01"]])


def test_filter_dataframe():
    df = pd.DataFrame({"x": range(len(IDX))}, index=IDX)
    out = filter_dataframe(df, include=[["2026-01-02", "2026-01-02"]], exclude=None)
    assert len(out) == 96


def test_anchor_splits_include_conflict_priority():
    """include 冲突按 train → val → test 优先（§12.4）。"""
    base = {
        "train": pd.Series([True] * 96 + [False] * 192, index=IDX),
        "val": pd.Series([False] * 96 + [True] * 96 + [False] * 96, index=IDX),
        "test": pd.Series([False] * 192 + [True] * 96, index=IDX),
    }
    spec = {
        "val": {"include": [["2026-01-01", "2026-01-01"]]},   # 与 train 冲突 → train 优先？
        "train": {"include": [["2026-01-01", "2026-01-01"]]},
        "test": {"exclude": [["2026-01-03 00:00", "2026-01-03 00:15"]]},
    }
    masks = anchor_splits(IDX, base, spec)
    day1 = IDX.day == 1
    assert masks["train"][day1].all()                  # train 优先级最高，抢回 day1
    assert not masks["val"][day1].any()
    assert masks["test"][day1].sum() == 0
    assert masks["test"].sum() == 96 - 2               # 排除 2 个点
    # 互斥性
    assert not ((masks["train"] & masks["val"]) | (masks["train"] & masks["test"])
                | (masks["val"] & masks["test"])).any()
