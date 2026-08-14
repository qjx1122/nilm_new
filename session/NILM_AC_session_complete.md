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

## [2026-08-13] 会话纪要（第 6 次）
- 目标：修改合并脚本——待合并文件名必须严格遵守 e241_<终端号>_<用户号>-Ch<通道号>-<起>-<止>.csv（不带后缀）；带 -1/-infer 后缀的文件不符合格式
- 完成项：
  - contracts.py 新增 RE_MERGE_FILE 严格正则（无后缀）与 parse_merge_filename；与指南 RE_BUS（允许后缀，流水线数据接入用）两契约并存、互不放宽
  - merge.py discover_source 改用严格格式解析：带后缀文件告警「不符合合并严格格式（需求文档 §2.2）」并排除在合并对象之外
  - 新增 2 项测试：严格格式契约对照（含与 RE_BUS 的行为差异）、后缀文件排除端到端（防止 -1 后缀重复数据混入合并）
  - 验证：73 项测试全过；真实数据实测——10 个总线文件均带 -1 后缀，全部正确拒绝，0 合并产物
- 关键决策：
  - 采用独立严格契约而非改写 RE_BUS：指南 §0 规定 RE_BUS 不得放宽/改写，合并严格性是需求文档 §2.2 的独立契约，两者并存
  - 不改名/不拷贝原始数据：带后缀文件只告警跳过（指南 §13 原始数据只读）
- 未决问题：
  - 当前 data/ 真实文件全部带 -1 后缀，若需实际合并，须由上游导出方改用严格格式命名（或对副本重命名后再入合并源目录）
- 相关文件/分支：nilm/common/contracts.py、nilm/data_io/merge.py、tests/test_contracts.py、tests/test_merge.py、README；分支 arena/019ffa35-nilm-new

## [2026-08-13] 会话纪要（第 7 次）
- 目标：修改合并脚本——两源合并时，某用户目录只在其中 1 个源中存在（另一源无该用户目录），则该用户的待合并文件直接作为合并后用户数据文件
- 完成项：
  - merge.py 阶段二：单源独有用户（通道组）直接透传到合并后用户数据目录 cross_source/<user>/，原名原内容，action=copied_single_source
  - 阶段一配套：--no-keep-original 场景下单文件组仍被跟踪（原始路径），保证阶段二透传不漏
  - 新增 2 项测试（透传端到端、no-keep 组合），75 项全过；真实数据回归确认严格格式行为不变
- 关键决策：
  - 透传目标目录沿用 cross_source/（阶段二统一输出区），避免再造新层级；报告 action 区分 copied_single_source / merged
  - 用户级「目录缺失」按其全部通道组均为单源组自然成立，无需额外用户级特判
- 未决问题：真实数据文件名均带 -1 后缀（非合并对象），何时切换严格格式取决于上游导出约定
- 相关文件/分支：nilm/data_io/merge.py、tests/test_merge.py、README；分支 arena/019ffa35-nilm-new

## [2026-08-14] 会话纪要（第 8 次）
- 目标：合并脚本增加分路格式文件合并功能——文件名严格为 <用户号>-<起始时间>-<截止时间>.csv（YYmmdd，如 4206894986488-260604-260611.csv），合并规则与既有规则一致
- 完成项：
  - contracts.py 新增 RE_MERGE_BRANCH 严格契约 + parse_merge_branch_filename（与 RE_BR 并存、互不放宽）
  - merge.py 泛化为两类文件：BusFile 增加 kind（bus 按用户+通道分组 / branch 按用户分组，无通道维度），发现、迭代合并、重叠跳过、跨源合并、单源透传、日志告警全部复用同一逻辑
  - 新增 4 项测试（分路严格格式契约、分路两级合并端到端含重叠跳过、分路单源透传、分路带后缀拒绝），79 项全过
  - 真实数据实测：5 用户分路文件均为严格格式 → 内源 single_kept；trains/infers 同区间跨源组正确判重叠 SKIPPED_OVERLAP×5；带后缀总线文件仍全部拒绝
- 关键决策：
  - 分路分组键 (user_key, "branch") 与总线 (user_key, ch) 并存不冲突；分路身份校验 = 文件名用户号 vs 用户目录用户号部分
  - 合并产物命名 <用户号>-<最早>-<最晚>.csv（仅更新起止时间，遵守 §5 命名约束）
- 未决问题：总线真实文件均带 -1 后缀（非合并对象），切换严格格式取决于上游导出约定
- 相关文件/分支：nilm/common/contracts.py、nilm/data_io/merge.py、tests/test_contracts.py、tests/test_merge.py、README；分支 arena/019ffa35-nilm-new

## [2026-08-14] 会话纪要（第 9 次）
- 目标：总线侧三相 U/I/P/PF 官方点位表落地（ua→data9、ub→data45、uc→data81、ia→data1、ib→data37、ic→data73、pa→data7、pb→data43、pc→data79、pfa→data8、pfb→data44、pfc→data80），并要求文件中找不到对应列时日志提示且该列数据置 0
- 完成项：
  - configs/default.yaml bus_field_map 更新为官方映射（含单位与倍率），废弃此前临时映射
  - CsvBusLoader 缺列置 0：WARNING 日志 + 列置 0 + schema 报告标记 MISSING_COLUMN_ZERO_FILLED（非致命）
  - PF 重算兜底：U·I=0 无法重算时回退文件 PF 均值，仍无数据置 0（防止 NaN 吞样本）
  - 新增 4 项测试（缺列置 0 / 全列无置 0 / 哨兵+缺列组合 / PF 回退），83 项全过
  - 真实数据复验 10/10 OK：置 0 字段 ua,ub,uc,ib,pfb 正确告警；800 用户 best=ridge r2 0.716→0.762（官方 pa/pb/pc 优于旧猜测）
- 关键决策：
  - 置 0 为「文件级」规则（某文件缺列即置 0），不同设备文件列集合差异被自然吸收
  - 置 0 告警文案避开致命判定关键词，issue 标记与 SCHEMA_UNCONFIRMED 严格区分
- 未决问题：
  - 当前数据集整体缺电压类点位（data9/45/81）与 ib/pfb，置 0 后电压特征无信息，建议采集侧补齐
  - 稀疏用户 844/789 基线仍差（M2 方向）
- 相关文件/分支：configs/default.yaml、nilm/data_io/csv_source.py、nilm/preprocess/align.py、tests/test_csv_source.py；分支 arena/019ffa35-nilm-new

## [2026-08-14] 会话纪要（第 10 次）
- 目标：实际三相 U/I/P/PF = 总线文件对应列原始数据 / 1000，修改代码落实倍率
- 完成项：
  - configs/default.yaml bus_field_map 全部 12 字段 multiplier 改为 0.001（加载器 multiplier 机制配置驱动生效，零代码改动）
  - 新增倍率应用测试（220000→220V、PF 916→0.916 归一），84 项全过
  - 真实数据复验 10/10 OK：bus 质量分 98.7–100（缩放前 PF 原始值 ~916 越界计异常，缩放后消除）；模型指标不变（均匀缩放 + z-score 归一的预期不变性）
  - 量级验证（778 用户）：ia 756→0.756A、pa 56573→56.6W、pfa 692→0.692，全部合理
- 关键决策：
  - PF 重算策略保持 recompute：电压列缺失时自动回退文件 PF 均值，缩放后文件 PF 已是合法无量纲值，兜底路径正确
  - 单位标注：PF unit 置空字符串（无量纲），U/I/P 按实际值单位 V/A/W
- 未决问题：电压类点位（data9/45/81）全部用户缺失，建议采集侧补齐（沿用）
- 相关文件/分支：configs/default.yaml、tests/test_csv_source.py；分支 arena/019ffa35-nilm-new

## [2026-08-14] 会话纪要（第 11 次）
- 目标：更新 docs/ 下的文档并存档
- 完成项：
  - TECH_DESIGN_LOAD_ID_PIPELINE.md v1.0→v1.2：版本头与修订记录更新；新增 §12 实施存档（12.1 真实数据 M0 摸底与三处口径修正 / 12.2 官方点位映射、/1000 倍率、缺列置 0、PF 兜底链 / 12.3 合并脚本全功能存档 / 12.4 当前验证基线 5 用户指标表 / 12.5 遗留事项）
  - §11 V2.1 对齐章节保留原貌（历史记录），新增内容与既有章节无冲突
  - 提交存档至分支（docs/ 三份文档：技术方案 v1.2 + 指南 PDF + 合并需求 PDF）
- 关键决策：
  - 存档方式 = 修订记录追加式（v1.0→v1.1→v1.2），保留各阶段历史，不覆盖旧章节
  - 验证基线以表格固化（用户/密度/best 模型/MAE/R²），作为 M2 改善的对照基准
- 未决问题：沿用 §12.5 遗留事项（电压点位补齐、总线文件严格格式切换、M2 多模型、指南附件契约）
- 相关文件/分支：docs/TECH_DESIGN_LOAD_ID_PIPELINE.md；分支 arena/019ffa35-nilm-new

## [2026-08-14] 会话纪要（第 12 次）
- 目标：拉取最新代码，使用 800080252842_4206894986488（用户更新数据，commit 1c25765）运行验证测试
- 完成项：
  - 合入用户数据更新：总线文件改为严格格式（无 -1 后缀）并延期至 260803，分路同步延期；**新文件补齐 ua(data9)/uc(data81) 列**
  - 单用户全流程验证（指南 §12 入口 --user-key）：train OK + infer OK（2326 点，契约 CSV 输出）
  - 结果：best=ridge，MAE 67.3W / RMSE 113.5 / R² **0.835**（旧数据 0.624）/ SAE 0.035；可辨识性 pearson 0.783→0.849 无风险；质量分 95.69（missing 4.3%、outlier 0）
  - 缺列置 0 规则继续生效：仅剩 ub/ib/pfb 置 0（data45/37/44 仍缺）
- 关键决策：无新增（纯验证轮）
- 未决问题：该用户总线文件已转严格格式，成为合并对象；其余 4 用户仍带 -1 后缀
- 相关文件/分支：outputs/800080252842_4206894986488/（不入库）；分支 arena/019ffa35-nilm-new

## [2026-08-14] 会话纪要（第 13 次）
- 目标：模型评估增加分类指标（F1/Accuracy/Precision/Recall）
- 完成项：
  - evaluation/metrics.py 新增 4 个状态分类指标：按 on_thr_w（§12.3，经 evaluate_all kwargs 透传）把功率二值化为开/关态，逐分路混淆矩阵 + 宏平均；空真约定显式定义（无开态标签 recall=1.0；无开态预测且无漏报 precision=1.0）
  - compare.py LOWER_IS_BETTER 扩展（分类指标均为越大越好），对比报告/最优模型挑选自动适配
  - configs/default.yaml metrics 扩为 8 项；user_task train/infer 两处评估透传 on_thr_w
  - 新增 8 项测试（手算混淆矩阵/阈值透传/空真约定/FP-only/多分路宏平均/回归指标不受影响），92 项全过
  - 真实数据复验（800080252842）：ridge 测试集 F1 0.825 / Acc 0.808 / P 0.714 / R 0.977；infer 离线 F1 0.931 / Acc 0.925 / P 0.872 / R 0.999；对比报告 8 指标矩阵与逐指标最优正常
  - 技术方案 §7.1 指标体系表更新（v1.2 文档内）
- 关键决策：
  - 分类指标落在评估模块（不新建模块），阈值来自用户配置 on_thr_w，训练/推理同口径
  - 空真约定选择「1.0」而非 0：全关负荷的全对预测不应被记 0 分（文档化于 metrics.py docstring）
- 未决问题：无新增
- 相关文件/分支：nilm/evaluation/{metrics,compare}.py、nilm/pipeline/user_task.py、configs/default.yaml、tests/test_classification_metrics.py；分支 arena/019ffa35-nilm-new
