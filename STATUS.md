# STATUS.md

## 当前目标
- ✅ 已完成：负荷辨识算法开发流程技术方案 + 流水线代码骨架（M1 MVP）
- ✅ 已完成：重构 NILM 任务协议（BOOTSTRAP.md → v2.0）

## 已完成
- [x] 技术方案 v1.0：`docs/TECH_DESIGN_LOAD_ID_PIPELINE.md`（架构/目录/接口/对齐策略/模型矩阵/评估口径/M0–M4 里程碑/风险）
- [x] 代码骨架落地：`nilm/` 8 个功能模块（common/data_io/preprocess/events/models/evaluation/reporting/pipeline）+ CLI + 配置 + 15 项单测全过
- [x] 多模型对比验证机制：MODEL_REGISTRY 注册表 + 配置驱动，内置 history_profile / proportional / ridge 三模型端到端对比（合成数据 7 天演示跑通，comparison.md 正常产出）
- [x] 288↔96 对齐（5min→15min 均值聚合 + PF 重算 + 重叠率门禁）、防泄漏时序划分、物理约束后处理（非负 + 总和一致性）
- [x] README 全面更新（安装/运行/数据/配置/产物）；REPORT_TEST.md 建立并沉淀本专题报告

## 进行中
- 无

## 下一步（TODO）
1. **M0 数据摸底**：接入真实母线/分路数据（放入 `data/raw/bus|branch/`，配置列映射），跑质量报告，验证时间同步与重叠率
2. **M2 多模型**：实现 `nilm/models/tree_models.py`（GBDT/RF）与 `seq_models.py`（Seq2Point/LSTM），先装 `requirements-ml.txt`
3. **M3 对比选型**：实验矩阵 + 超参扫描，结论沉淀 REPORT_TEST.md → REPORT.md
4. 事件检测模块（`nilm/events`）按需在 M3+ 启用

## 决策记录 / 踩坑
- 任务粒度判断：分路侧仅 96 点/天三相总有功 → 辨识上限为**分路级有功分解**，不做设备级；训练主分辨率取 15min（信息无损方向），禁止把分路上采样伪造 5min 标签
- 解耦实现：六条硬约束（单向依赖/面向接口/配置驱动/注册表/schema 契约/产物隔离）；models 互不感知，评估层对模型族无感
- 核心依赖零 sklearn：ridge 用 numpy 闭式解，基线可直接跑；重依赖（lightgbm/torch）隔离在 requirements-ml.txt
- Registry 区分 `create`（实例化，模型用）与 `get`（取可调用对象，指标函数用）——首版混用导致 TypeError，已修复
- DataSource 拆为 BusSource/BranchSource 两个接口再组合，避免 CsvBusSource 被迫实现 load_branch
- 指标宏平均语义：macro = 各分路指标的均值（非全元素展平），测试期望值曾按展平写错，已修正
- 环境：PEP 668 禁止系统 pip，改用 `.venv`（已 gitignore）；Python 3.11.2，pandas 3.0.5（频率字符串用 "5min"/"15min"）

## 关键文件路径
- `docs/TECH_DESIGN_LOAD_ID_PIPELINE.md`：技术方案（本任务主交付物）
- `nilm/`：流水线源码包；`scripts/run_pipeline.py`：CLI；`configs/default.yaml`：实验配置
- `tests/`：15 项单测（含端到端 smoke）；`requirements.txt` / `requirements-ml.txt`：依赖分层
- `outputs/`：实验产物（不入库）；`data/`：原始数据（不入库）
- `REPORT_TEST.md`：专题报告（含本专题）；`session/NILM_AC_session_complete.md`：会话纪要
