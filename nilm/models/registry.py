"""模型注册表：对比实验 = 配置里加一行，不改编排代码。"""

from nilm.common.registry import Registry
from nilm.models.base import BaseModel

MODEL_REGISTRY: Registry[BaseModel] = Registry("model")
