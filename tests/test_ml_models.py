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


def test_resolve_device_auto_and_fallback(monkeypatch):
    """GPU 自动检测：auto 依可用性返回 cuda/cpu；显式 cuda 不可用时回退 cpu。"""
    import torch

    from nilm.models.seq_models import resolve_device

    # 真实环境：auto 结果必属合法集合且与 CUDA 可用性一致
    dev = resolve_device("auto")
    if torch.cuda.is_available():
        assert dev == "cuda"
    else:
        assert dev in ("cpu", "mps")

    # 模拟无 CUDA：auto→cpu（或 mps），显式 cuda→回退 cpu
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert resolve_device("cuda") == "cpu"
    # 显式 cpu 永远尊重
    assert resolve_device("cpu") == "cpu"

    # 模拟有 CUDA：auto→cuda
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda i: "FakeGPU")
    assert resolve_device("auto") == "cuda"
    assert resolve_device("cuda") == "cuda"


def test_seq_model_default_device_auto():
    """序列模型默认 device=auto（自动检测），实例化不触发 torch 导入错误。"""
    for name in DL_MODELS:
        m = MODEL_REGISTRY.create(name, **{**EXTRA[name]})
        assert m.params["device"] == "auto"


def test_ridge_off_weight_reduces_false_positives():
    """加权岭：关态样本加权后，关态时段的预测残余显著降低（压 FP 机制）。"""
    rng = np.random.default_rng(0)
    n = 800
    # 构造：一半时间开机（y=100+噪声，x1 高），一半关机（y=0，x1 仍有底载噪声）
    on = np.arange(n) % 2 == 0
    x1 = np.where(on, 5.0, 1.0) + rng.normal(0, 0.8, n)   # 底载重叠，制造误报倾向
    X = np.column_stack([x1, rng.normal(0, 1, n)])
    y = np.where(on, 100.0, 0.0)[:, None]
    base = MODEL_REGISTRY.create("ridge", alpha=1.0)
    base.fit(X, y)
    weighted = MODEL_REGISTRY.create("ridge", alpha=1.0, off_weight=5.0,
                                     off_thr_w=10.0)
    weighted.fit(X, y)
    off_res_base = float(np.clip(base.predict(X)[~on, 0], 0, None).mean())
    off_res_w = float(np.clip(weighted.predict(X)[~on, 0], 0, None).mean())
    assert off_res_w < off_res_base, "关态加权应降低关态时段预测残余"
    # off_weight=1 与原版行为一致
    plain = MODEL_REGISTRY.create("ridge", alpha=1.0, off_weight=1.0)
    plain.fit(X, y)
    assert np.allclose(plain.predict(X), base.predict(X), atol=1e-8)


def test_history_profile_median_agg():
    """中位画像：槽位内多数为 0、少数极大时，median 输出 0（mean 会被拉高）。"""
    n_days, slots = 5, 96
    slot = np.tile(np.arange(slots), n_days)
    X = slot[:, None].astype(float)
    y = np.zeros((len(slot), 1))
    y[slot == 40] = 0.0
    # 槽位 40：5 天中 1 天开机 500W，4 天 0 → median=0, mean=100
    day_idx = np.repeat(np.arange(n_days), slots)
    y[(slot == 40) & (day_idx == 0)] = 500.0
    m_mean = MODEL_REGISTRY.create("history_profile")
    m_mean.fit(X, y, feature_names=["slot"])
    m_med = MODEL_REGISTRY.create("history_profile", agg="median")
    m_med.fit(X, y, feature_names=["slot"])
    x40 = np.array([[40.0]])
    assert m_mean.predict(x40)[0, 0] == 100.0
    assert m_med.predict(x40)[0, 0] == 0.0
    import pytest
    with pytest.raises(ValueError):
        MODEL_REGISTRY.create("history_profile", agg="p25")


def test_history_profile_conditional_pbus_bins():
    """条件画像 pbus_bins：同槽位低总线桶输出关机值、高总线桶输出开机值——
    修补无条件画像的『条件缺失』缺陷（全关天整段误报根因）。"""
    rng = np.random.default_rng(0)
    n_days, slots = 10, 96
    slot = np.tile(np.arange(slots), n_days)
    day = np.repeat(np.arange(n_days), slots)
    # 3 个全关天（target=0，pbus=背景 300）；7 个开机日白天 target=700，pbus=1000
    off_day = day < 3
    is_daytime = (slot >= 36) & (slot < 80)
    target = np.where(~off_day & is_daytime, 700.0, 0.0)
    pbus = np.where(~off_day & is_daytime, 1000.0, 300.0) + rng.normal(0, 10, len(slot))
    X = np.column_stack([slot.astype(float), pbus])
    y = target[:, None]
    names = ["slot", "pbus"]

    base = MODEL_REGISTRY.create("history_profile", agg="median")
    base.fit(X, y, feature_names=names)
    cond = MODEL_REGISTRY.create("history_profile", agg="median", pbus_bins=2)
    cond.fit(X, y, feature_names=names)

    # 白天槽位：无条件画像输出开机值（多数日开机）；条件画像按当天 pbus 区分
    x_low = np.array([[50.0, 300.0]])    # 全关天形态（总线低）
    x_high = np.array([[50.0, 1000.0]])  # 开机日形态
    assert base.predict(x_low)[0, 0] > 500      # 无条件：误报开机值
    assert cond.predict(x_low)[0, 0] < 10       # 条件画像：正确输出关机
    assert cond.predict(x_high)[0, 0] > 500     # 高桶仍输出开机值
    # pbus_bins=1 与原行为一致
    plain = MODEL_REGISTRY.create("history_profile", agg="median", pbus_bins=1)
    plain.fit(X, y, feature_names=names)
    assert np.allclose(plain.predict(x_low), base.predict(x_low))
    import pytest
    with pytest.raises(ValueError):
        MODEL_REGISTRY.create("history_profile", pbus_bins=0)
