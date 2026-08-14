"""多模型对比汇总（技术方案 §7.2）：模型 × 指标 矩阵、排序与最优模型挑选。"""

from __future__ import annotations

import pandas as pd

# 指标方向：True = 越小越好（分类指标均为越大越好）
LOWER_IS_BETTER = {"mae": True, "rmse": True, "sae": True, "mape": True, "r2": False,
                   "f1": False, "accuracy": False, "precision": False, "recall": False}

# 混淆矩阵计数（TP/FP/FN/TN）：诊断性输出，随样本量变化，不参与最优模型排序
COUNT_METRICS = {"tp", "fp", "fn", "tn"}


def build_comparison_table(results: dict[str, dict[str, dict]]) -> pd.DataFrame:
    """把 {model: {metric: {'macro': ...}}} 汇总为 模型×指标 的宏平均矩阵。"""
    rows = {}
    for model, metrics in results.items():
        rows[model] = {m: v["macro"] for m, v in metrics.items()}
    table = pd.DataFrame(rows).T  # index=model, columns=metric
    table.index.name = "model"
    return table


def summarize(table: pd.DataFrame) -> dict:
    """按指标方向挑出每个指标的最优模型，并给出综合胜出次数。"""
    best_per_metric: dict[str, str] = {}
    wins: dict[str, int] = {}
    for metric in table.columns:
        if metric in COUNT_METRICS:  # 计数类指标不参与优劣排序
            continue
        s = table[metric].dropna()
        if s.empty:
            continue
        best = s.idxmin() if LOWER_IS_BETTER.get(metric, True) else s.idxmax()
        best_per_metric[metric] = best
        wins[best] = wins.get(best, 0) + 1
    overall = max(wins, key=wins.get) if wins else None
    return {"best_per_metric": best_per_metric, "wins": wins, "overall_best": overall}
