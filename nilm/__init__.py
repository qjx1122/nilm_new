"""NILM 负荷辨识流水线源码包。

模块划分（依赖方向单向，只允许 pipeline 层组合各模块）：
- common     共享内核：schema 契约 / 注册表 / 日志
- data_io    数据接入层
- preprocess 预处理与特征工程
- events     事件检测（预留）
- models     模型适配层（多模型对比核心）
- evaluation 指标与对比
- reporting  报告生成
- pipeline   编排层（唯一允许组合各模块的层）
"""

__version__ = "0.1.0"
