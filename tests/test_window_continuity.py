"""时间连续性守卫（指南 §10「窗口必须连续」，W-1 修复回归 2026-09-10）。

缺陷背景：修复前 Seq 模型滑窗与 build_windows 均按纯数组位置构造，
切分锚定/排除日/缺失剔除造成的时间洞会被拼进同一窗口
（实证：800 户生产配置下 50.5% 训练窗口跨间断，最大跨 4.99 天）。
本文件锁定四层防线：segment_bounds 原语 / build_windows / seq 模型 / 端到端产物。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nilm.common.schema import MODEL_STEP, segment_bounds
from nilm.models import MODEL_REGISTRY
from nilm.models.seq_models import _padded_windows
from nilm.preprocess.dataset import build_windows
from nilm.pipeline.batch import run_batch

from conftest import USER_KEY, write_user_dir

STEP = MODEL_STEP  # 15min


def _cont_index(n: int, start: str = "2026-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq=STEP)


def _gapped_index(n1: int, n2: int, gap_days: int = 2, n3: int = 0) -> pd.DatetimeIndex:
    """两段（可选第三段）连续索引，中间挖 gap_days 天的洞。"""
    seg1 = _cont_index(n1)
    seg2 = _cont_index(n2, start=str(seg1[-1] + pd.Timedelta(days=gap_days) + STEP))
    parts = [seg1, seg2]
    if n3:
        parts.append(_cont_index(n3, start=str(seg2[-1] + pd.Timedelta(days=1) + STEP)))
    return pd.DatetimeIndex(np.concatenate([x.values for x in parts]))


# ---------------------------------------------------------------- 原语层
def test_segment_bounds_contiguous_single_run():
    assert segment_bounds(_cont_index(10)) == [(0, 10)]
    assert segment_bounds(_cont_index(1)) == [(0, 1)]
    assert segment_bounds(pd.DatetimeIndex([])) == []


def test_segment_bounds_split_at_gaps():
    idx = _gapped_index(96, 48, gap_days=2, n3=10)
    bounds = segment_bounds(idx)
    assert bounds == [(0, 96), (96, 144), (144, 154)]
    # 段内相邻间隔恰为 15min
    for s, e in bounds:
        assert (idx[s:e][1:] - idx[s:e][:-1]).min() == STEP


# ---------------------------------------------------------------- dataset 层
def test_build_windows_contiguous_regression():
    """连续数据上行为与修复前一致（回归锚）。"""
    n, w = 200, 8
    X = np.arange(n * 3, dtype=float).reshape(n, 3)
    y = np.arange(n, dtype=float)[:, None]
    Xw, yw, meta = build_windows(X, y, _cont_index(n), window=w)
    assert Xw.shape == (n - w + 1, w, 3) and yw.shape == (n - w + 1, w, 1)
    assert len(meta) == n - w + 1
    span = (pd.to_datetime(meta["win_end"]) - pd.to_datetime(meta["win_start"]))
    assert (span == (w - 1) * STEP).all()          # 所有窗口跨度=标称
    # 窗口内容与位置一致（无重排/无跳取）
    assert np.array_equal(Xw[0, 0], X[0]) and np.array_equal(Xw[-1, -1], X[n - 1])


def test_build_windows_never_cross_gaps():
    """间断两侧不拼窗；短段尾部不产窗（W-1 核心语义）。"""
    n1, n2, n3, w = 100, 80, 5, 10
    idx = _gapped_index(n1, n2, gap_days=2, n3=n3)
    X = np.arange((n1 + n2 + n3) * 2, dtype=float).reshape(n1 + n2 + n3, 2)
    y = np.zeros(n1 + n2 + n3)[:, None]
    Xw, yw, meta = build_windows(X, y, idx, window=w)
    m = len(Xw)
    assert m == (n1 - w + 1) + (n2 - w + 1)        # 短段 n3=5 < w=10 不产窗
    span = (pd.to_datetime(meta["win_end"]) - pd.to_datetime(meta["win_start"]))
    assert (span == (w - 1) * STEP).all()          # 没有任何窗口跨间断
    # 窗口内容校验：第一段最后一个窗口的末行必须是 seg1 末行（而非 seg2 首行）
    assert np.array_equal(Xw[n1 - w, -1], X[n1 - 1])
    # 第二段第一个窗口的首行必须是 seg2 首行（段头填充不跨段取 seg1 数据）
    assert np.array_equal(Xw[n1 - w + 1, 0], X[n1])


def test_build_windows_all_segments_too_short_raises():
    # 总样本 40 ≥ window 30，但最长连续段仅 20 < 30 → 连续段守卫拦截
    with pytest.raises(ValueError, match="连续段"):
        build_windows(np.zeros((40, 1)), np.zeros((40, 1)), _gapped_index(20, 20), window=30)


def test_build_windows_index_len_mismatch_raises():
    with pytest.raises(ValueError, match="不一致"):
        build_windows(np.zeros((10, 1)), np.zeros((10, 1)), _cont_index(9), window=4)


# ---------------------------------------------------------------- seq 模型层
def test_seq_padded_windows_segmented_boundary():
    """边界窗口内容：段首窗口=段内头填充（不跨段取数）。"""
    n1, w = 6, 4
    X = np.arange(12 * 1, dtype=float).reshape(12, 1)   # 0..11
    idx = _gapped_index(n1, 12 - n1, gap_days=2)
    bounds = segment_bounds(idx)
    W = _padded_windows(X, w, bounds)
    assert W.shape == (12, w, 1)
    # 段2 首行（全局行 6）的窗口 = [X6, X6, X6, X6]（段内首行填充），绝不是 X3..X6
    assert np.array_equal(W[n1, :, 0], np.array([6.0, 6.0, 6.0, 6.0]))
    # 段1 末行（全局行 5）的窗口 = X2..X5（段内连续）
    assert np.array_equal(W[n1 - 1, :, 0], np.array([2.0, 3.0, 4.0, 5.0]))
    # bounds=None 保留旧行为：跨段窗口存在（仅测试兼容路径）
    W_old = _padded_windows(X, w)
    assert np.array_equal(W_old[n1, :, 0], np.array([3.0, 4.0, 5.0, 6.0]))


@pytest.mark.parametrize("name", ["lstm", "cnn1d", "transformer"])
def test_seq_models_fit_predict_with_gapped_index(name, caplog):
    """三个序列模型：带间断索引的 fit/predict 可用，且走连续段路径。"""
    import logging

    n1, n2, f = 40, 25, 2
    X = np.random.default_rng(0).normal(size=(n1 + n2, f))
    y = np.abs(X[:, :1]) * 10 + 1
    idx = _gapped_index(n1, n2, gap_days=3)
    model = MODEL_REGISTRY.create(name, window=4, epochs=2, batch_size=16,
                                  patience=5, lr=5e-3, random_state=0,
                                  **({"hidden_size": 8} if name == "lstm" else
                                     {"channels": 4, "num_blocks": 1} if name == "cnn1d" else
                                     {"d_model": 8, "nhead": 2, "num_layers": 1}))
    with caplog.at_level(logging.INFO):
        model.fit(X[:n1], y[:n1], index=idx[:n1],
                  X_val=X[n1:], y_val=y[n1:], val_index=idx[n1:])
        pred = model.predict(X, index=idx)
    assert pred.shape == (n1 + n2, 1)
    assert any("连续段" in r.message for r in caplog.records)


def test_seq_model_without_index_warns_backward_compat(caplog):
    """未提供索引：退回纯位置滑窗并告警（兼容旧调用/测试）。"""
    import logging

    X = np.random.default_rng(0).normal(size=(30, 2))
    y = np.abs(X[:, :1])
    model = MODEL_REGISTRY.create("lstm", window=4, epochs=1, batch_size=16,
                                  hidden_size=8, random_state=0)
    with caplog.at_level(logging.WARNING):
        model.fit(X, y)
        pred = model.predict(X)
    assert pred.shape == (30, 1)
    assert any("纯位置" in r.message for r in caplog.records)


def test_all_models_accept_index_kwarg():
    """接口契约：全部注册模型 fit/predict 接受 index/val_index（非序列模型忽略）。"""
    X = np.random.default_rng(0).normal(size=(40, 2))
    y = np.abs(X[:, :1])
    idx = _cont_index(40)
    specs = [("history_profile", {}, ["slot"]),
             ("proportional", {}, ["pbus"]),
             ("ridge", {"alpha": 1.0}, ["a", "b"])]
    for name, params, cols in specs:
        m = MODEL_REGISTRY.create(name, **params)
        Xc = X.copy()
        Xc[:, 0] = np.abs(Xc[:, 0]) * 50          # slot/pbus 需非负物理量
        m.fit(Xc, y, feature_names=cols, index=idx, val_index=idx)
        assert m.predict(Xc, index=idx).shape == (40, 1)
    for name, extra in [("random_forest", {"n_estimators": 5}),
                        ("xgboost", {"n_estimators": 5})]:
        m = MODEL_REGISTRY.create(name, **extra)
        m.fit(X, y, index=idx, val_index=idx)
        assert m.predict(X, index=idx).shape == (40, 1)


# ---------------------------------------------------------------- 端到端层
def test_pipeline_window_index_continuous(tmp_path, base_cfg_file, time_filter_file):
    """端到端：产物 train_window_index.csv 的所有窗口跨度=标称（合成连续数据）。"""
    data_root = tmp_path / "data"
    write_user_dir(data_root, USER_KEY, days=21)
    out_root = tmp_path / "outputs"
    info = run_batch(time_filter_file, base_config_path=base_cfg_file,
                     data_root=data_root, output_root=out_root, stages=("train",))
    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    w = pd.read_csv(train_dir / "train_window_index.csv")
    assert len(w) > 0
    span = (pd.to_datetime(w["win_end"]) - pd.to_datetime(w["win_start"]))
    assert (span == 95 * STEP).all()               # L=96 → 跨度 23.75h，无一跨间断
