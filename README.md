# NILM Project

NILM（Non-Intrusive Load Monitoring，非侵入式负荷监测）项目工作区。
任务：基于母线时序（288 点/天，三相 U/I/P/PF）与分路时序（96 点/天，三相总有功）完成负荷辨识算法开发。

- 技术方案：[`docs/TECH_DESIGN_LOAD_ID_PIPELINE.md`](docs/TECH_DESIGN_LOAD_ID_PIPELINE.md)
- Agent 协作协议：[`BOOTSTRAP.md`](BOOTSTRAP.md)

## 安装与环境

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # 核心依赖：numpy/pandas/pyyaml/tabulate
.venv/bin/pip install -r requirements-ml.txt     # 可选：M2 起的树模型/深度模型/绘图扩展
```

## 运行命令

```bash
.venv/bin/python scripts/run_pipeline.py --config configs/default.yaml --stage all
# stage: all | train | evaluate | compare
.venv/bin/python -m pytest tests/ -q             # 单元测试
```

## 数据目录结构

```
data/                       # 不入库（.gitignore）
├── raw/bus/*.csv           # 母线原始时序：288 点/天（5min），含 timestamp 列
└── raw/branch/*.csv        # 分路原始时序：96 点/天（15min），branch_<id> 列
```

原始列名通过配置中的 `bus_column_map` / `branch_column_map` 映射为标准 schema
（`u_a..u_c, i_a..i_c, p_total, pf_a..pf_c`；分路列 `branch_<id>`），
标准定义见 `nilm/common/schema.py`。

## 配置文件结构

`configs/default.yaml`：数据路径与列映射、预处理参数（划分比例/插值缺口）、
特征参数、模型清单（`models:` 列表，加一行即接入新模型）、物理约束开关、指标清单。

## 输出产物

每次实验落盘 `outputs/<experiment_name>/<timestamp>/`（不入库）：

| 文件 | 内容 |
| --- | --- |
| `config.snapshot.yaml` | 配置快照（可复现） |
| `meta.json` | 特征列/分路列/划分规模 |
| `models/*.pkl` | 各候选模型 |
| `metrics.json` | 测试集逐分路 + 宏平均指标 |
| `comparison.csv` / `comparison.md` | 模型 × 指标对比矩阵与报告 |

## 代码结构

按功能模块划分、单向依赖（详见技术方案 §2–§4）：
`common`（契约/注册表/日志）→ `data_io` / `preprocess` / `events` / `models` / `evaluation` / `reporting` → `pipeline`（编排）→ `scripts`（CLI）。

## 当前状态

- M1 流水线 MVP 已完成：数据接入→清洗对齐→特征→训练→评估→多模型对比端到端跑通（含 3 个基线/线性模型与 15 项单测）
- 待真实数据接入（M0 数据摸底）与 M2 多模型扩展（GBDT/Seq2Point/LSTM 等）
