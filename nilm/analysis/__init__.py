"""分析模块：训练前置的强制性分析（与建模解耦，独立产出报告）。"""

from nilm.analysis.identifiability import identifiability_report
from nilm.analysis.branch_sessions import analyze_branch_sessions

__all__ = ["identifiability_report", "analyze_branch_sessions"]
