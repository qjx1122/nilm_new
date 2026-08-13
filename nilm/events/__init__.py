"""模块③：事件检测（预留模块，技术方案 M3+ 启用）。

规划：基于母线 5min 有功功率的大功率投切事件检测（如 CUSUM/差分阈值），
输出作为特征增强与事件级评估的扩展点。当前仅提供抽象接口，保持目录结构完整。
"""

from nilm.events.base import EventDetector, Event

__all__ = ["EventDetector", "Event"]
