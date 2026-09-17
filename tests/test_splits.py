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
    # stratified_by_state 需 target，按日开/关分层后各组内按比例均摊
    import numpy as np
    # 构造合成 target：前 30 天关（max<on_thr），后 30 天开
    target = pd.Series(np.nan, index=IDX)
    cutoff = IDX[0] + pd.Timedelta(days=30)
    target.loc[IDX < cutoff] = 5.0
    target.loc[IDX >= cutoff] = 500.0
    masks = initial_split(IDX, RATIOS, "stratified_by_state",
                          target=target, on_thr_w=50)
    _check(masks)
    # 验证均衡：池均 50% 关，各 split 关占比≈50%（极差<15%）
    def _off_ratio(m):
        days = sorted(pd.Series(m.index[m].normalize()).unique())
        # 统计落在关期的天数
        off = sum(1 for d in days if d < cutoff.normalize())
        return off / len(days) if days else 0
    ratios = {k: _off_ratio(v) for k, v in masks.items()}
    assert max(ratios.values()) - min(ratios.values()) < 0.15
    # 缺 target 时退化为 time 而非抛错
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        masks_fb = initial_split(IDX, RATIOS, "stratified_by_state")
        _check(masks_fb)
        assert any("退化为 time" in str(x.message) for x in w)


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


def test_stratified_by_state_build_with_target():
    import numpy as np
    target = pd.Series(np.nan, index=IDX)
    cutoff = IDX[0] + pd.Timedelta(days=30)
    target.loc[IDX < cutoff] = 5.0
    target.loc[IDX >= cutoff] = 500.0
    masks = build_split_masks(IDX, RATIOS, "stratified_by_state",
                              target=target, on_thr_w=50)
    _check(masks)
    def _off_ratio(m):
        days = sorted(pd.Series(m.index[m].normalize()).unique())
        off = sum(1 for d in days if d < cutoff.normalize())
        return off / len(days) if days else 0
    ratios = {k: _off_ratio(v) for k, v in masks.items()}
    assert max(ratios.values()) - min(ratios.values()) < 0.15
