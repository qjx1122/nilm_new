"""预测后处理模块（与模型解耦）：开态判定与状态整形。"""

from nilm.postprocess.state import power_to_state, enforce_min_on, fill_short_off

__all__ = ["power_to_state", "enforce_min_on", "fill_short_off"]
