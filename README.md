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
.venv/bin/pip install -r requirements-ml.txt     # M2 模型依赖：sklearn/xgboost/lightgbm/torch/绘图
                                                 # （random_forest/xgboost/lstm/cnn1d/transformer 需要；
                                                 #   仅跑 3 个基线模型可跳过，依赖均为惰性导入）
```

## 运行命令（指南 §12 规定入口）

```bash
# 多用户批量执行（自动扫描 data/trains 与 data/infers）
.venv/bin/python scripts/run_batch_users.py --time-filter-config configs/time_filter.example.json

# 单用户执行（同一代码路径：单用户 = users=[一个 key] 的批量）
.venv/bin/python scripts/run_batch_users.py --time-filter-config <cfg.json> --user-key <device>_<user>

# 强制重新训练/推理（忽略已完成产物 _DONE，产物写入新时间戳目录；可与 --user-key 组合）
.venv/bin/python scripts/run_batch_users.py --time-filter-config <cfg.json> --force

# 其他选项：--stage train|infer|all   --no-resume   --force   --data-root   --output-root   --base-config

# 多数据源用户数据批量合并（先内源合并、后跨源合并，重叠告警跳过，原始数据只读）
# ⚠️ 待合并文件名必须严格符合以下两种格式之一（均不带任何后缀，需求文档 §2.2）：
#    总线：e241_<终端号>_<用户号>-Ch<通道号>-<起>-<止>.csv（如 e241_800080252844_4206894986488-Ch1-260604-260611.csv）
#    分路：<用户号>-<起>-<止>.csv（如 4206894986488-260604-260611.csv）
#    带 -1/-infer 后缀的文件不参与合并（会告警提示）；两类文件合并规则一致
.venv/bin/python scripts/merge_user_data.py --sources <源1> <源2> [<源3> ...] \
    [--output-root outputs/merged] [--log-dir <日志目录>] [--no-keep-original]

.venv/bin/python -m pytest tests/ -q             # 98 项测试（含解耦守卫与合并逻辑）
```

合并脚本输出：`<output-root>/<数据源名>/<终端号_用户号>/` 复刻原层级（阶段一结果）+
`cross_source/<终端号_用户号>/`（阶段二合并后用户数据目录：跨源合并结果；**仅存在于单一数据源的用户，
其文件直接作为合并后用户数据文件放入**）+ `logs/`（运行日志、告警日志、merge_report.json）。

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
  ——**逐项详解见 [`docs/CONFIG_GUIDE.md`](docs/CONFIG_GUIDE.md)**（v2.0：yaml + 用户 JSON 全字段、8 模型说明与训练效率因素、字段生效位置速查）
- `--time-filter-config` 用户 JSON（指南 §12）：键 = `user_key=<device>_<user>`，优先级
  `具体 user_key > _default > 硬编码默认`；字段校验规则见指南 §12.3（target_col、on_thr_w、
  split_ratios、split_strategy、post_min_on、post_fill_short_off、weather_* 等）

## 输出产物（不入库）

```
outputs/
├── batch/<timestamp>/batch_status.csv        # 批量状态表（user_id/user_key/mode/status/…）
└── <user_key>/
    ├── train/<timestamp>/                    # 配置快照/schema 报告/质量报告/聚合策略记录/
    │   ├── cleaned/{bus,branch}_cleaned.csv  # 清洗后数据（去重/负功率裁剪/短缺口插值；
    │   │                                     #   preprocess.save_cleaned_csv: false 可关闭）
    │   ├── metrics_by_split.csv              # 每模型 train/val/test 三阶段指标汇总
    │   ├── metrics_daily.csv                 # 每模型×每阶段×每天 日级指标
    │   ├── predictions/train_predictions.csv # 训练预测结果：timestamp/split/target(真实值)/
    │   │                                     #   target_state(真实状态,on_thr_w 口径) +
    │   │                                     #   pred_<model>/pred_state_<model>（预测值与
    │   │                                     #   预测状态,生产判决链口径，时间有序）
    │   │                                     # ⚠ 口径提示：metrics_daily/by_split 的 TP/FP/FN/TN
    │   │                                     #   由「pred 值 ≥ on_thr_w」直接判（模型能力口径），
    │   │                                     #   与 pred_state 列（decision_thr_w+游程后处理）
    │   │                                     #   不同属预期；用值列同阈值判态即可精确对账
    │   ├── branch_sessions.csv               # 分路开机分析：逐分路逐天开机段（起止/时长/
    │   │                                     #   最小/平均/峰值功率/电量kWh/状态；整天关机
    │   │                                     #   输出整天一行 state=0）
    │   …                                     # 可辨识性报告/窗口索引/模型/指标/对比报告/_DONE
    └── infer/<timestamp>/
        ├── cleaned/{bus,branch}_cleaned.csv  # 同上（branch 仅当存在分路文件时产出）
        ├── branch_sessions.csv               # 分路开机分析（同 train，有分路文件时产出）
        ├── metrics_daily.csv                 # 推理离线评估日级指标（有分路真值时产出）
        └── predictions/inference_result.csv  # 输出契约：timestamp,user_id,target,target_state,
                                              #   on_thr_w(真值判态阈值),pred,pred_state,
                                              #   decision_thr_w(预测判态阈值),pred_prob
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
- 内置 8 个模型：3 基线/线性（history_profile/proportional/ridge，零 ML 依赖）+
  2 树模型（random_forest/xgboost，`tree_models.py`）+
  3 深度时序（lstm/cnn1d/transformer，`seq_models.py`，L=96 滑窗 Seq2Point 逐点输出；
  device 默认 `auto` 自动检测——有 CUDA GPU 用 GPU、其次 Apple MPS、否则 CPU，
  显式配置 `params: {device: cpu|cuda}` 可覆盖）；
  全部经 MODEL_REGISTRY 注册、configs/default.yaml `models:` 配置驱动
- 待办：真实数据 M0 摸底；指南附件中「日级指标 23 字段 / 启动段字段」契约待确认（PDF 未含附件原文）
