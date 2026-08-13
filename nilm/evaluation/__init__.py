"""模块⑤：评估与对比。

边界：只消费 y / ŷ 矩阵产出指标，不画图（reporting 负责）、不碰模型。
"""

from nilm.evaluation.metrics import METRIC_REGISTRY, evaluate_all
from nilm.evaluation.compare import build_comparison_table, summarize

__all__ = ["METRIC_REGISTRY", "evaluate_all", "build_comparison_table", "summarize"]
