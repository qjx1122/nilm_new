"""分析模块：训练前置的强制性分析（与建模解耦，独立产出报告）。"""

from nilm.analysis.identifiability import identifiability_report

__all__ = ["identifiability_report"]
