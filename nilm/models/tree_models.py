"""树模型族占位（M2 实现，技术方案 §6.1）：LightGBM / XGBoost / RandomForest 多输出回归。

实现时遵循三条约束即可无缝接入对比：
1. 继承 BaseModel，fit/predict 维持矩阵接口（多输出 = 每分路一个回归器或原生 multioutput）；
2. 用 ``@MODEL_REGISTRY.register("gbdt")`` 等注册；
3. 依赖写入 requirements-ml.txt，本文件 import 需惰性（避免核心流程强依赖）。
"""
