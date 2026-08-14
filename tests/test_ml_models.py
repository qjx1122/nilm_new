"""M2 模型族：随机森林 / XGBoost / LSTM / 1D-CNN / Transformer 接口与学习能力验证。

统一契约：fit/predict 矩阵接口 (n, f) → (n, k)、save/load 往返一致、
在可学习的合成数据上优于「预测均值」基线（sanity 下界）。
DL 模型用小窗口/少轮数保证测试速度。
"""

from __future__ import annotations

import numpy as np
import pytest

from nilm.models import MODEL_REGISTRY

ML_MODELS = ["random_forest", "xgboost"]
DL_MODELS = ["lstm", "cnn1d", "transformer"]
DL_PARAMS = {"window": 8, "epochs": 40, "batch_size": 64, "patience": 10,
             "lr": 5e-3, "random_state": 0}
EXTRA = {
    "random_forest": {"n_estimators": 30, "random_state": 0},
    "xgboost": {"n_estimators": 60, "max_depth": 4, "random_state": 0},
    "lstm": {**DL_PARAMS, "hidden_size": 16},
    "cnn1d": {**DL_PARAMS, "channels": 8, "num_blocks": 2},
    "transformer": {**DL_PARAMS, "d_model": 16, "nhead": 2, "num_layers": 1},
}


def _make_data(n: int = 600, seed: int = 0):
    """可学习的合成任务：y 是特征的平滑非线性函数 + 小噪声。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    x1 = np.sin(2 * np.pi * t / 96)
    x2 = np.cos(2 * np.pi * t / 96)
    x3 = rng.normal(0, 0.1, n)
    X = np.column_stack([x1, x2, x3]).astype(np.float64)
    y = (2.0 + 1.5 * x1 + 0.8 * np.maximum(x2, 0) + rng.normal(0, 0.05, n))[:, None]
    n_tr = int(n * 0.7)
    return (X[:n_tr], y[:n_tr]), (X[n_tr:], y[n_tr:])


@pytest.mark.parametrize("name", ML_MODELS + DL_MODELS)
def test_registered(name):
    assert name in MODEL_REGISTRY.names()


@pytest.mark.parametrize("name", ML_MODELS + DL_MODELS)
def test_fit_predict_shape_and_learning(name):
    (X_tr, y_tr), (X_te, y_te) = _make_data()
    model = MODEL_REGISTRY.create(name, **EXTRA[name])
    model.fit(X_tr, y_tr, feature_names=["x1", "x2", "x3"],
              X_val=X_te, y_val=y_te)
    pred = model.predict(X_te)
    assert pred.shape == y_te.shape                      # (n, 1) 矩阵接口
    mse = float(np.mean((pred - y_te) ** 2))
    mse_mean = float(np.mean((y_tr.mean() - y_te) ** 2))  # 均值基线
    assert mse < mse_mean, f"{name} 未优于均值基线: {mse:.4f} vs {mse_mean:.4f}"


@pytest.mark.parametrize("name", ML_MODELS + DL_MODELS)
def test_save_load_roundtrip(name, tmp_path):
    from nilm.models.base import BaseModel

    (X_tr, y_tr), (X_te, _) = _make_data(n=300)
    model = MODEL_REGISTRY.create(name, **EXTRA[name])
    model.fit(X_tr, y_tr, feature_names=["x1", "x2", "x3"])
    p1 = model.predict(X_te)
    path = tmp_path / f"{name}.pkl"
    model.save(path)
    p2 = BaseModel.load(path).predict(X_te)
    assert np.allclose(p1, p2, atol=1e-5), f"{name} save/load 预测不一致"


@pytest.mark.parametrize("name", DL_MODELS)
def test_dl_deterministic_with_seed(name):
    """同种子两次训练预测一致（种子经 params 传入，可复现）。"""
    (X_tr, y_tr), (X_te, _) = _make_data(n=300)
    preds = []
    for _ in range(2):
        m = MODEL_REGISTRY.create(name, **EXTRA[name])
        m.fit(X_tr, y_tr)
        preds.append(m.predict(X_te))
    assert np.allclose(preds[0], preds[1], atol=1e-6)


def test_padded_windows_alignment():
    """滑窗对齐：输出行数 = 输入行数；窗口末行 = 当前时刻特征。"""
    from nilm.models.seq_models import _padded_windows

    X = np.arange(20, dtype=np.float64).reshape(10, 2)
    W = _padded_windows(X, window=4)
    assert W.shape == (10, 4, 2)
    assert np.array_equal(W[:, -1, :], X)          # 每窗末行 = 该时刻特征
    assert np.array_equal(W[0, 0], X[0])           # 头部复制首行填充
