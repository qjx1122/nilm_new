"""preprocess.splits：四种策略、互斥完备、不随机打散（指南 §11/§12.3）。"""

import pandas as pd

from nilm.preprocess.splits import build_split_masks, initial_split

IDX = pd.date_range("2026-01-01", periods=96 * 60, freq="15min")  # 60 天
RATIOS = [0.6, 0.2, 0.2]


def _check(masks):
    total = masks["train"] | masks["val"] | masks["test"]
    assert total.all()                                    # 完备
    assert not ((masks["train"] & masks["val"]).any()
                or (masks["train"] & masks["test"]).any()
                or (masks["val"] & masks["test"]).any())  # 互斥
    # 切分以整天为单位（日内不打散，§11）
    for m in masks.values():
        per_day = m.groupby(m.index.date).nunique()
        assert (per_day == 1).all()


def test_all_strategies_valid():
    for strategy in ("time", "stratified_day", "stratified", "global_stratified"):
        masks = initial_split(IDX, RATIOS, strategy)
        _check(masks)


def test_time_strategy_chronological():
    masks = initial_split(IDX, RATIOS, "time")
    tr, va, te = IDX[masks["train"]], IDX[masks["val"]], IDX[masks["test"]]
    assert tr.max() < va.min()
    assert va.max() < te.min()


def test_ratio_approximation():
    masks = initial_split(IDX, RATIOS, "time")
    n = len(IDX)
    assert abs(masks["train"].sum() / n - 0.6) < 0.02
    assert abs(masks["val"].sum() / n - 0.2) < 0.02


def test_build_with_anchors_and_repair():
    spec = {"test": {"include": [["2026-02-25", "2026-02-25"]]}}
    masks = build_split_masks(IDX, [0.7, 0.15, 0.15], "time", spec)
    _check(masks)
    assert masks["test"][IDX.date == pd.Timestamp("2026-02-25").date()].all()


def test_invalid_ratios():
    import pytest
    with pytest.raises(ValueError):
        initial_split(IDX, [0.5, 0.5, 0.5], "time")
    with pytest.raises(ValueError):
        initial_split(IDX, RATIOS, "unknown_strategy")
