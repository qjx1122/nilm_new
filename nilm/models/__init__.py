"""模块④：模型层（多模型对比的核心）。

所有模型（基线 / 传统 ML / 深度学习）适配同一 BaseModel 接口并注册到
MODEL_REGISTRY；编排层按配置实例化，评估层对模型族无感知。
"""

from nilm.models.base import BaseModel
from nilm.models.registry import MODEL_REGISTRY
from nilm.models import baselines  # noqa: F401  触发注册

__all__ = ["BaseModel", "MODEL_REGISTRY"]
