# NILM Project

NILM（Non-Intrusive Load Monitoring，非侵入式负荷监测）项目工作区。
任务：基于总线时序（288 点/天，三相 U/I/P/PF）与分路时序（96 点/天，三相总有功）完成工商业负荷辨识。

- **开发规范（最高优先级）**：[`docs/工商业负荷辨识算法开发指南.pdf`](docs/工商业负荷辨识算法开发指南.pdf)（V2.1，多算法模型物理隔离与运行模式版）
- 技术方案：[`docs/TECH_DESIGN_LOAD_ID_PIPELINE.md`](docs/TECH_DESIGN_LOAD_ID_PIPELINE.md)
- Agent 协作协议：[`BOOTSTRAP.md`](BOOTSTRAP.md)

## 安装与环境

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # 核心：numpy/pandas/pyyaml/tabulate
.venv/bin/pip install -r requirements-ml.txt     # 可选：M2 起的树模型/深度模型/绘图
```

## 运行命令（指南 §12 规定入口）

```bash
# 多用户批量执行（自动扫描 data/trains 与 data/infers）
.venv/bin/python scripts/run_batch_users.py --time-filter-config configs/time_filter.example.json

# 单用户执行（同一代码路径：单用户 = users=[一个 key] 的批量）
.venv/bin/python scripts/run_batch_users.py --time-filter-config <cfg.json> --user-key <device>_<user>

# 其他选项：--stage train|infer|all   --no-resume   --data-root   --output-root   --base-config

# 多数据源用户数据批量合并（先内源合并、后跨源合并，重叠告警跳过，原始数据只读）
.venv/bin/python scripts/merge_user_data.py --sources <源1> <源2> [<源3> ...] \
    [--output-root outputs/merged] [--log-dir <日志目录>] [--no-keep-original]

.venv/bin/python -m pytest tests/ -q             # 71 项测试（含解耦守卫与合并逻辑）
```

合并脚本输出：`<output-root>/<数据源名>/<终端号_用户号>/` 复刻原层级（阶段一结果）+
`cross_source/<终端号_用户号>/`（阶段二跨源结果）+ `logs/`（运行日志、告警日志、merge_report.json）。

## 数据目录结构（指南 §3.1，原始数据只读、不入库）

```
data/
├── trains/<device>_<user>/     # 训练单元：总线 CSV + 分路 CSV
└── infers/<device>_<user>/     # 推理单元：总线 CSV（分路若存在仅离线评估）
```

- 总线文件名严格匹配 `RE_BUS`（`e241_<device>_<user>-Ch<N>-<start>-<end>[suffix].csv`），时间列 `event_time`
- 分路文件名严格匹配 `RE_BR`（`<user>-<start>-<end>[suffix].csv`），时间列 `time`，功率列 `p1,p2,…`（W）
- ChN 只是通道标识，物理含义由 `configs/default.yaml` 的 `bus_field_map` 确认（CT/PT 倍率配置化）

## 配置文件结构

- `configs/default.yaml`：基础配置（质量门禁 / 聚合策略 / 特征 / L=96 窗口 / 模型清单 / 字段映射）
- `--time-filter-config` 用户 JSON（指南 §12）：键 = `user_key=<device>_<user>`，优先级
  `具体 user_key > _default > 硬编码默认`；字段校验规则见指南 §12.3（target_col、on_thr_w、
  split_ratios、split_strategy、post_min_on、post_fill_short_off、weather_* 等）

## 输出产物（不入库）

```
outputs/
├── batch/<timestamp>/batch_status.csv        # 批量状态表（user_id/user_key/mode/status/…）
└── <user_key>/
    ├── train/<timestamp>/                    # 配置快照/schema 报告/质量报告/聚合策略记录/
    │   …                                     # 可辨识性报告/窗口索引/模型/指标/对比报告/_DONE
    └── infer/<timestamp>/
        └── predictions/inference_result.csv  # 输出契约：timestamp,user_id,target,pred,pred_state
```

## 代码结构（按功能模块解耦，依赖方向单向）

```
nilm/common      契约(schema/contracts/timefilter) + 注册表 + 日志   ← 所有模块仅依赖此层
nilm/data_io     用户目录扫描(discovery) / CSV 加载 / 质量门禁
nilm/preprocess  对齐(5min→15min 可配置聚合) / 清洗 / 特征 / 目标列 / 切分 / 缩放 / L=96 窗口
nilm/analysis    可辨识性分析（训练前强制执行，指南 §9）
nilm/models      模型适配层（注册表，多模型对比；物理约束后处理）
nilm/evaluation  指标与对比            nilm/reporting  报告
nilm/postprocess 开态后处理（on_thr_w/post_min_on/post_fill_short_off）
nilm/pipeline    编排层：user_config(§12) / user_task(单用户) / batch(§13 批量)
scripts/run_batch_users.py   CLI 入口
```

解耦由 `tests/test_decoupling.py` 静态守卫：业务模块间禁止横向 import。

## 当前状态

- 已对齐指南 V2.1：数据契约（RE_BUS/RE_BR/user_key）、用户 JSON 配置、时间过滤与切分锚定、
  可辨识性分析、批量执行（失败隔离/断点续跑/状态码）、单用户与多用户同路径执行
- 内置 3 个基线/线性模型；GBDT/Seq2Point/LSTM 为 M2 计划（`nilm/models/tree_models.py`、`seq_models.py`）
- 待办：真实数据 M0 摸底；指南附件中「日级指标 23 字段 / 启动段字段」契约待确认（PDF 未含附件原文）
