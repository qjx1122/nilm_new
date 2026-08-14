"""evaluation.metrics 状态分类指标（F1/Accuracy/Precision/Recall）手算验证。

按 on_thr_w 把功率二值化为开/关态，计算混淆矩阵。验证空真约定与阈值透传。
"""

import numpy as np

from nilm.evaluation.metrics import (METRIC_REGISTRY, _confusion, evaluate_all)


def _binary_case():
    """单分路：阈值 10。
    true 开态: idx0(20), idx2(15)；其余关。
    pred 开态: idx0(18), idx1(12)；其余关。
    TP=1(idx0) FP=1(idx1) FN=1(idx2) TN=2(idx3,4)
    """
    y_true = np.array([[20.], [0.], [15.], [0.], [5.]])
    y_pred = np.array([[18.], [12.], [0.], [0.], [6.]])
    return y_true, y_pred


def test_confusion_matrix():
    y_true, y_pred = _binary_case()
    tp, fp, fn, tn = _confusion(y_true[:, 0], y_pred[:, 0], thr=10.0)
    assert (tp, fp, fn, tn) == (1, 1, 1, 2)


def test_classification_metrics_hand_computed():
    y_true, y_pred = _binary_case()
    out = evaluate_all(y_true, y_pred, ["accuracy", "precision", "recall", "f1"],
                       on_thr_w=10.0)
    # accuracy=(TP+TN)/N=3/5; precision=1/2; recall=1/2; f1=0.5
    assert abs(out["accuracy"]["macro"] - 0.6) < 1e-9
    assert abs(out["precision"]["macro"] - 0.5) < 1e-9
    assert abs(out["recall"]["macro"] - 0.5) < 1e-9
    assert abs(out["f1"]["macro"] - 0.5) < 1e-9


def test_threshold_changes_state():
    """阈值升高 → 同样功率被判为关态，指标随之变化（阈值透传生效）。"""
    y_true = np.array([[20.], [0.]])
    y_pred = np.array([[18.], [0.]])
    low = evaluate_all(y_true, y_pred, ["accuracy"], on_thr_w=10.0)["accuracy"]["macro"]
    high = evaluate_all(y_true, y_pred, ["accuracy"], on_thr_w=25.0)["accuracy"]["macro"]
    assert low == 1.0          # 阈值10：true开/pred开 + true关/pred关 → 全对
    assert high == 1.0         # 阈值25：都判关，但 true idx0(20<25)也关 → 仍全对
    # 改用能区分的场景：true 开、pred 开，阈值提到 pred 之上 → 误判
    y_true2 = np.array([[20.], [0.]])
    y_pred2 = np.array([[18.], [0.]])
    acc = evaluate_all(y_true2, y_pred2, ["accuracy"], on_thr_w=19.0)["accuracy"]["macro"]
    # 阈值19：true idx0=开(20≥19)，pred idx0=关(18<19) → 1 错 → acc=0.5
    assert abs(acc - 0.5) < 1e-9


def test_vacuous_truth_convention():
    """标签与预测全关：recall/precision/f1/accuracy 记空真 1.0。"""
    y_true = np.array([[0.], [3.], [5.]])
    y_pred = np.array([[0.], [1.], [2.]])
    out = evaluate_all(y_true, y_pred, ["recall", "precision", "f1", "accuracy"],
                       on_thr_w=10.0)
    assert out["recall"]["macro"] == 1.0        # 无开态标签 → 空真
    assert out["precision"]["macro"] == 1.0     # 无开态预测且无漏报 → 空真
    assert out["f1"]["macro"] == 1.0
    assert out["accuracy"]["macro"] == 1.0


def test_false_positive_only_precision_zero():
    """标签全关但预测有开：precision=0（有 FP），recall=1（空真）。"""
    y_true = np.array([[0.], [0.]])
    y_pred = np.array([[20.], [0.]])
    out = evaluate_all(y_true, y_pred, ["precision", "recall"], on_thr_w=10.0)
    assert out["precision"]["macro"] == 0.0
    assert out["recall"]["macro"] == 1.0


def test_multi_branch_macro():
    """多分路：各分路独立算混淆矩阵后取宏平均。"""
    # 分路0 全对(acc=1)，分路1 全错(acc=0) → 宏平均 0.5
    y_true = np.array([[20., 20.], [0., 0.]])
    y_pred = np.array([[20., 0.], [0., 20.]])
    out = evaluate_all(y_true, y_pred, ["accuracy"], on_thr_w=10.0)
    assert abs(out["accuracy"]["macro"] - 0.5) < 1e-9
    assert out["accuracy"]["per_branch"] == [1.0, 0.0]


def test_regression_metrics_ignore_on_thr_w():
    """回归指标忽略透传的 on_thr_w（不受影响）。"""
    y = np.array([[1.0, 2.0], [3.0, 4.0]])
    out = evaluate_all(y, y.copy(), ["mae", "r2"], on_thr_w=99.0)
    assert out["mae"]["macro"] == 0.0
    assert out["r2"]["macro"] == 1.0


def test_registry_names():
    for name in ["f1", "accuracy", "precision", "recall"]:
        assert name in METRIC_REGISTRY.names()


def test_confusion_count_metrics_hand_computed():
    """TP/FP/FN/TN 计数指标与手算混淆矩阵一致（单分路）。"""
    y_true, y_pred = _binary_case()
    out = evaluate_all(y_true, y_pred, ["tp", "fp", "fn", "tn"], on_thr_w=10.0)
    assert out["tp"]["macro"] == 1.0
    assert out["fp"]["macro"] == 1.0
    assert out["fn"]["macro"] == 1.0
    assert out["tn"]["macro"] == 2.0
    assert out["tp"]["per_branch"] == [1.0]


def test_confusion_count_metrics_multi_branch_sum():
    """多分路：per_branch 为各分路计数，macro 为跨分路总数（求和而非平均）。"""
    # 分路0：true开/pred开 (TP)，true关/pred关 (TN)
    # 分路1：true开/pred关 (FN)，true关/pred开 (FP)
    y_true = np.array([[20., 20.], [0., 0.]])
    y_pred = np.array([[20., 0.], [0., 20.]])
    out = evaluate_all(y_true, y_pred, ["tp", "fp", "fn", "tn"], on_thr_w=10.0)
    assert out["tp"]["per_branch"] == [1.0, 0.0]
    assert out["fn"]["per_branch"] == [0.0, 1.0]
    assert out["fp"]["per_branch"] == [0.0, 1.0]
    assert out["tn"]["per_branch"] == [1.0, 0.0]
    assert out["tp"]["macro"] == 1.0
    assert out["fp"]["macro"] == 1.0
    assert out["fn"]["macro"] == 1.0
    assert out["tn"]["macro"] == 1.0
    # 四类计数之和 = 样本数 × 分路数
    total = sum(out[m]["macro"] for m in ("tp", "fp", "fn", "tn"))
    assert total == y_true.size


def test_confusion_counts_respect_threshold():
    """计数指标同样受 on_thr_w 透传控制。"""
    y_true = np.array([[20.], [0.]])
    y_pred = np.array([[18.], [0.]])
    lo = evaluate_all(y_true, y_pred, ["tp"], on_thr_w=10.0)["tp"]["macro"]
    hi = evaluate_all(y_true, y_pred, ["tp"], on_thr_w=19.0)["tp"]["macro"]
    assert lo == 1.0   # 阈值10：20/18 均为开 → TP
    assert hi == 0.0   # 阈值19：pred 18 判关 → FN，无 TP


def test_count_metrics_registered_and_excluded_from_ranking():
    """tp/fp/fn/tn 已注册；且不参与 summarize 的最优模型排序。"""
    import pandas as pd

    from nilm.evaluation.compare import COUNT_METRICS, summarize

    for name in ["tp", "fp", "fn", "tn"]:
        assert name in METRIC_REGISTRY.names()
        assert name in COUNT_METRICS
    table = pd.DataFrame({"f1": [0.9, 0.5], "tp": [10, 999]},
                         index=["A", "B"])
    table.index.name = "model"
    s = summarize(table)
    assert "tp" not in s["best_per_metric"]      # 计数不参与排序
    assert s["best_per_metric"]["f1"] == "A"
    assert s["overall_best"] == "A"
