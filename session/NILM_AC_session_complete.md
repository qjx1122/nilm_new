# NILM_AC 会话纪要（只追加，不新建文件）

## [2026-08-13] 会话纪要
- 目标：重构 nilm 任务协议（用户开局指令：读 BOOTSTRAP.md 并按开局/收尾仪式工作）
- 完成项：
  - 完整执行开局仪式（仓库现状拉取 / 环境检查 / STATUS.md 骨架创建 / 向用户汇报）
  - 重构 `BOOTSTRAP.md` 至 v2.0：新增「指定文件台账」「任务协议（任务生命周期 5 条）」「离场自检清单」「修订记录」；依赖恢复命令泛化（不限 pnpm）；开局第 4 步明确「先汇报后动手、有阻塞先确认」
  - 修复 `README.md` 编码（UTF-16LE → UTF-8）并补充仓库说明与协议入口
  - 两个里程碑小步提交：`b38e166`（协议重构）、`fb0b5f6`（README 修复）
- 关键决策：
  - 用户跳过澄清问题，按最合理解释执行：仓库唯一协议文件即 BOOTSTRAP.md，就地重构、不新建协议文件（遵守文件治理约束）
  - 本次属工程重构而非用户/实验/验证专题 → 不触发 REPORT_TEST.md；无算法/KPI 变化 → 不触发 REPORT.md
- 未决问题：
  - v2.0 协议的措辞与结构是否符合用户预期，待用户确认后可再迭代（在「修订记录」追加条目即可）
- 相关文件/分支：`BOOTSTRAP.md`、`STATUS.md`、`README.md`、`session/NILM_AC_session_complete.md`；分支 `arena/019ffa35-nilm-new`

## [2026-08-13] 会话纪要（第 2 次）
- 目标：基于母线（288 点/天，三相 U/I/P/PF）与分路（96 点/天，三相总有功）时序数据，生成负荷辨识算法开发流程技术方案；要求模块化目录、解耦隔离、支持多模型对比验证
- 完成项：
  - 技术方案 v1.0（docs/TECH_DESIGN_LOAD_ID_PIPELINE.md）：分层架构、六条解耦硬约束、目录结构、288↔96 对齐策略、候选模型矩阵、指标体系与验收口径、M0–M4 里程碑、风险应对
  - 代码骨架落地：nilm/ 8 个功能模块 + CLI + YAML 配置 + 15 项单测全部通过
  - 多模型对比机制验证：注册表 + 配置驱动，3 模型（history_profile/proportional/ridge）端到端跑通并产出 comparison.md
  - README 全面更新；REPORT_TEST.md 建立
- 关键决策：
  - 辨识粒度上限 = 分路级有功分解（分路标签只有 96 点有功）；主分辨率 15min，禁止上采样伪造标签
  - 核心依赖零 sklearn（ridge 用 numpy 闭式解）；重依赖隔离在 requirements-ml.txt
  - Registry 区分 create（实例化）与 get（取可调用），修复首版指标调用 TypeError
- 未决问题：
  - 真实数据尚未接入（列映射/时间同步/缺失率待 M0 验证）；GBDT/深度模型为 M2 计划
- 相关文件/分支：docs/TECH_DESIGN_LOAD_ID_PIPELINE.md、nilm/、scripts/、tests/、configs/default.yaml；分支 arena/019ffa35-nilm-new
