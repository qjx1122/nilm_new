"""深度序列模型占位（M2 实现，技术方案 §6.1）：Seq2Point CNN / Seq2Seq LSTM / TCN。

实现要点：
1. 适配器内部用 ``preprocess.dataset.build_windows`` 构造窗口张量，
   对外仍暴露 BaseModel.fit/predict 矩阵接口（评估层对模型族无感知）；
2. 训练细节（早停、设备、随机种子）经 params 传入，种子来自全局配置；
3. save/load 覆盖为保存模型权重 + 结构参数，而非整对象 pickle；
4. 依赖（torch 等）写入 requirements-ml.txt，惰性导入。
"""
