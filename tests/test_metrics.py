"""evaluation.metrics：指标数值正确性（手算值对照）。"""

import numpy as np

from nilm.evaluation.metrics import METRIC_REGISTRY, evaluate_all


def test_perfect_prediction():
    y = np.array([[1.0, 2.0], [3.0, 4.0]])
    out = evaluate_all(y, y.copy(), ["mae", "rmse", "r2", "sae"])
    assert out["mae"]["macro"] == 0.0
    assert out["rmse"]["macro"] == 0.0
    assert out["r2"]["macro"] == 1.0
    assert out["sae"]["macro"] == 0.0


def test_mae_rmse_hand_computed():
    y_true = np.array([[1.0, 2.0], [3.0, 4.0]])
    y_pred = np.array([[2.0, 2.0], [2.0, 2.0]])
    mae = METRIC_REGISTRY.get("mae")(y_true, y_pred)
    # |1|,|0|,|1|,|2| -> 平均 1.0
    assert abs(mae["macro"] - 1.0) < 1e-9
    rmse = METRIC_REGISTRY.get("rmse")(y_true, y_pred)
    # 宏平均 = 各分路 RMSE 的均值：支路0 sqrt((1+1)/2)=1.0，支路1 sqrt((0+4)/2)=sqrt(2)
    assert abs(rmse["macro"] - (1.0 + np.sqrt(2.0)) / 2) < 1e-9


def test_sae_hand_computed():
    y_true = np.array([[1.0, 2.0], [3.0, 4.0]])   # 列和: 4, 6
    y_pred = np.array([[2.0, 1.0], [2.0, 3.0]])   # 列和: 4, 4
    sae = METRIC_REGISTRY.get("sae")(y_true, y_pred)
    assert abs(sae["per_branch"][0] - 0.0) < 1e-9
    assert abs(sae["per_branch"][1] - 2.0 / 6.0) < 1e-9


def test_shape_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        evaluate_all(np.zeros((2, 2)), np.zeros((2, 3)), ["mae"])
