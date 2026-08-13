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

## [2026-08-13] 会话纪要（第 3 次）
- 目标：按《工商业负荷辨识算法开发指南 V2.1》修改代码，要求：(1) 各功能模块解耦隔离；(2) 支持单用户或多用户批量执行
- 完成项：
  - PDF 附件经两次上传通道失败后，由用户 git push（a5915e3）合入 docs/，pypdf 解析全文（8 页 §0–§13）
  - 新增契约层（contracts.py：RE_BUS/RE_BR/RE_USER_DIR/状态码/配置规则）、时间过滤引擎（§12.4）、目标列契约（§3.3）、四种切分策略+锚定（§11/§12.4）、可辨识性分析模块（§9）、开态后处理（§12.3）
  - 数据接入重构为「用户目录」契约：扫描→正则校验→身份一致性→多 Ch 关联→字段映射（倍率配置化）→schema/质量报告
  - 编排层重构：user_config(§12) / user_task(单用户端到端) / batch(§13 失败隔离/断点续跑/状态表)；CLI 入口 run_batch_users.py 与指南原文一致
  - 解耦守卫测试 test_decoupling.py（AST 静态审计依赖方向）；65 项测试全过；CLI 实测：2 用户 train+infer OK、非法目录/缺总线正确状态码、单用户模式、SKIPPED_RESUME 续跑、inference_result.csv 契约字段
- 关键决策：
  - 指南契约 > 原技术方案：字段名/目录/正则/状态码全部按指南原文；超出处（回退链、扩展状态码）显式记录
  - 单用户与多用户走同一批量代码路径（users=[key]）
  - 断点续跑以 _DONE 标记实现；原始数据目录只读
  - 天气特征字段保留校验但不生成特征列（数据源未接入）
- 未决问题：
  - 指南附件（日级指标字段清单/启动段契约/target_col 回退链）未随 PDF 提供，列为接口待确认项
  - 真实数据 M0 摸底未开始；GBDT/深度模型为 M2
- 相关文件/分支：nilm/（重构）、scripts/run_batch_users.py、configs/、tests/、docs/TECH_DESIGN §11、README；分支 arena/019ffa35-nilm-new

## [2026-08-13] 会话纪要（第 4 次）
- 目标：使用 data 下 5 个用户数据和 configs/time_filters.json 配置，批量执行验证测试
- 完成项：
  - 合入用户推送的测试数据与配置（f2ccca9）：trains/infers 各 5 个用户目录 + time_filters.json
  - M0 数据摸底：双哨兵值（INT32_MIN/MAX）、总线稀疏（842: 88/288、844: 57/288 点/天）与密集（778/789/800: 282/288）并存、分路百瓦级、总线量纲未确认
  - 数据驱动字段辨识（相关性分析）：data7=总有功（与分路和相关性 0.80+）等，临时映射入 default.yaml 并显式标记
  - 代码适配：哨兵值配置化、ptotal/3 派生三相（显式标记）、覆盖率真实日历口径、门禁阈值校准、min_overlap 配置化
  - 两处口径修正（均有单测保护）：对齐重叠率改为分路标签覆盖率（844 由 FAILED 转 OK）；低方差判据改为目标 CV<5%（消除 5 个误报）
  - 最终结果：批量 10/10 OK（5 用户 train+infer）；断点续跑 10/10 SKIPPED_RESUME；单用户模式通过；65 项测试全过
- 关键决策：
  - 临时字段映射（待点位表确认）优于 SCHEMA_UNCONFIRMED 阻塞：让验证先跑通，不确定性全部显式落盘（schema 报告 DATA_UNIT_UNKNOWN）
  - 稀疏用户基线表现差（844/789 r2<0）如实记录为 M2 输入，不调指标口径粉饰
- 未决问题：设备点位表与 CT/PT 倍率待确认；指南附件（日级指标/启动段契约）待补充；稀疏用户改善待 M2
- 相关文件/分支：configs/default.yaml、configs/time_filters.json、nilm/preprocess/align.py、nilm/analysis/identifiability.py、nilm/data_io/{csv_source,validator}.py、outputs/（不入库）；分支 arena/019ffa35-nilm-new

## [2026-08-13] 会话纪要（第 5 次）
- 目标：根据 docs/多数据源用户数据批量合并脚本-功能需求文档.pdf 增加数据合并脚本
- 完成项：
  - 拉取最新版本（含用户推送的需求文档 c8cb164）；诊断并恢复被外部重置的本地分支指针（git reset --hard FETCH_HEAD）；重建 .venv 环境
  - 实现 nilm/data_io/merge.py（两级串行：内源迭代两两合并 → 跨源同用户同通道合并）+ scripts/merge_user_data.py CLI（--sources 多源必填，--output-root/--log-dir/--no-keep-original 可选）
  - 需求条款逐条落地：RE_BUS 正则复用（不放宽）、闭区间重叠即终止整组并告警（不强制合并/不覆盖）、新文件仅更新起止时间、输出复刻数据源/用户目录层级、原始数据只读
  - 输出四件套：结构化合并 CSV、运行日志（区分内源/跨源）、告警日志（源路径/用户目录/文件名/冲突区间）、merge_report.json
  - 验证：6 项合并专项测试 + 全量 71 项通过（含解耦守卫）；真实数据实测 trains+infers 两源：内源 10 组 OK，5 个跨源组全部检出完全重合并 SKIPPED_OVERLAP
- 关键决策：
  - 重叠跳过语义取「整组跳过不产出」（需求原文），中间产物一并清理；跨源产物独立 cross_source/ 分区
  - 时间列名保持原样（event_time/time），重复时间戳去重保留先出现者
  - 合并模块落位 data_io（数据接入域），只依赖 common，解耦守卫通过
- 未决问题：点位表/倍率待确认（沿用上轮）；合并脚本暂无 M2 前置依赖，可随时用于多段数据预处理
- 相关文件/分支：nilm/data_io/merge.py、scripts/merge_user_data.py、tests/test_merge.py、README；分支 arena/019ffa35-nilm-new
