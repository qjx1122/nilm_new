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

## [2026-08-14] 会话纪要（第 14 次）
- 目标：分类指标输出增加 TP/FP/FN/TN；全 5 用户重跑验证测试
- 完成项：
  - metrics.py 注册 tp/fp/fn/tn 计数指标（同 on_thr_w 二值化口径；per_branch=各分路计数，macro=跨分路总数）；default.yaml metrics 扩为 12 项
  - compare.py 新增 COUNT_METRICS，summarize 排序豁免计数指标（防退化模型因 FP=0 胜出）
  - 新增 4 项测试，96 项全过；全 5 用户 train+infer 重跑 10/10 OK
  - 守恒校验：TP+FP+FN+TN = test 样本数逐户成立（1622/2/670/96/96）；comparison.csv/md、metrics.json、offline_metrics.json 均输出四类计数
- 关键决策：
  - 计数走注册表指标而非改 evaluate_all 返回结构：零侵入全链路贯通，配置可开关；macro 对计数取总和（docstring 说明）
  - 计数不参与最优模型排序（COUNT_METRICS 豁免）
- 未决问题：844 test 切分仅 2 点统计意义弱；789/800 推理段 FN 全量待 M2 分析
- 相关文件/分支：nilm/evaluation/{metrics,compare}.py、configs/default.yaml、tests/test_classification_metrics.py、STATUS.md、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-14] 会话纪要（第 15 次）
- 目标：批量/单用户执行增加强制重新训练推理功能（--force）
- 完成项：
  - pipeline/batch.py：run_batch 新增 force 参数，force=True 时忽略 _DONE 断点续跑判定强制重新训练/推理（优先级高于 resume），并打日志提示
  - scripts/run_batch_users.py：新增 --force CLI 参数，可与 --user-key/--stage 任意组合
  - user_task.py `_new_outdir`：同秒重复运行目录冲突时追加 _1/_2 序号（mkdir 不再 exist_ok），保证 force 重跑产物目录唯一、历史产物不被覆盖
  - 新增 2 项测试（force 忽略 _DONE 且产物目录 +1、force 优先于 resume），98 项全过
  - 真实数据三轮验证：无 force 二跑 SKIPPED_RESUME×10；单用户 --force OK×2（train+infer）；全量 --force OK×10
  - README.md 运行命令章节新增 --force 用法（条件触发更新）
- 关键决策：
  - --force 与 --no-resume 行为等价但意图区分：--force 是「强制重跑」的显式入口且优先级最高，README 推荐使用
  - force 不删除历史产物，写入新时间戳目录（审计可追溯）
- 未决问题：无新增
- 相关文件/分支：nilm/pipeline/{batch,user_task}.py、scripts/run_batch_users.py、tests/test_batch.py、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-14] 会话纪要（第 16 次）
- 目标：增加清洗后数据保存 CSV 功能
- 完成项：
  - user_task.py 新增 `_save_cleaned_csv`：清洗（去重/负功率裁剪/短缺口插值）后的数据落盘到运行目录 cleaned/{bus,branch}_cleaned.csv（时间索引列 timestamp，UTF-8）
  - 覆盖三处清洗点：train（bus+branch）、infer（bus）、infer 离线评估（branch，仅当存在分路文件）
  - configs/default.yaml 新增 preprocess.save_cleaned_csv 开关（默认 true）
  - 新增 2 项测试（产物存在性+清洗语义抽查【功率非负/时间戳唯一】、配置关闭不产出），100 项全过
  - 真实数据 --force 全量重跑 10/10 OK：5 用户 train+infer 共 20 个 cleaned CSV 全部产出并抽查验证（负值 0、时间戳唯一）
  - README.md 输出产物章节更新（条件触发）
- 关键决策：
  - 落盘位置在运行时间戳目录内（与配置快照/质量报告同域，可追溯）；时机在清洗后重采样前（bus 保留 5min 原始粒度）
  - 默认开启，数据量大时可配置关闭
- 未决问题：无新增
- 相关文件/分支：nilm/pipeline/user_task.py、configs/default.yaml、tests/test_batch.py、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-14] 会话纪要（第 17 次）
- 目标：训练阶段增加三阶段（train/val/test）+ 日级指标输出；推理阶段增加状态真值/开态概率 + 日级指标输出
- 完成项：
  - evaluation/metrics.py 新增 evaluate_daily（按自然日分组评估，date/n_points/各指标宏平均，返回 DataFrame）
  - postprocess/state.py 新增 state_probability（以 on_thr_w 为中心的 sigmoid 伪概率，阈值处 0.5，决策边界与 pred_state 一致）
  - user_task 训练：每模型在 train/val/test 三切分上评估 → metrics_by_split.csv（model×split）；每模型×每阶段×每天 → metrics_daily.csv；选型口径不变（test）
  - user_task 推理：inference_result.csv 契约扩列 target_state（真值二值化，Int64 可空）+ pred_prob；离线评估增加日级 metrics_daily.csv（model×date）
  - contracts.INFER_RESULT_COLUMNS 更新为 7 列；README 输出产物章节更新
  - 新增 5 项测试，105 项全过；真实数据 --force 全量重跑 10/10 OK（842 抽查：9 行三阶段汇总、306 行训练日级、决策边界/真值状态一致性通过）
- 关键决策：
  - pred_prob 是 sigmoid 伪概率非校准概率（文档注明）；M2 分类头模型可替换为真概率
  - target_state 可空整型：无真值为空而非 0；三阶段指标用于过拟合诊断，选型仍按 test；日级指标定位诊断非选型
- 未决问题：无新增
- 相关文件/分支：nilm/evaluation/metrics.py、nilm/postprocess/state.py、nilm/pipeline/user_task.py、nilm/common/contracts.py、tests/{test_batch,test_classification_metrics}.py、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-14] 会话纪要（第 18 次）
- 目标：新增随机森林/XGBoost/LSTM/1D-CNN/Transformer 五个回归模型
- 完成项：
  - tree_models.py：random_forest（sklearn multioutput）、xgboost（每分路一回归器+val 早停）
  - seq_models.py：_SeqTorchModel 适配器基类（L=96 滑窗 Seq2Point 逐点输出/早停/批推理/state_dict 持久化/种子可复现）+ lstm/cnn1d/transformer
  - 注册表接入 + default.yaml models 扩为 8 项 + requirements-ml.txt 增补 xgboost + README 更新
  - 新增 19 项测试（注册/形状/学习能力优于均值基线/save-load 往返/DL 同种子复现/滑窗对齐），124 项全过
  - 真实数据 --force 全量重跑 10/10 OK：778 xgboost r2 0.951（+0.124）、800 random_forest 0.768（+0.102）；DL 未超越树模型（预期内）
- 关键决策：
  - DL 窗口语义 Seq2Point 逐点版（头部复制填充，输出行数=输入行数），评估/推理零改动
  - torch 局部类不可 pickle → __getstate__/__setstate__ 序列化 state_dict，load 时重建结构
  - xgboost 早停仅有 val 时启用；DL 打乱用独立 RNG 保证同种子复现
- 未决问题：DL 调参/GPU；大用户 transformer CPU 约 25 min
- 相关文件/分支：nilm/models/{tree_models,seq_models,__init__}.py、configs/default.yaml、requirements-ml.txt、tests/test_ml_models.py、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-14] 会话纪要（第 19 次）
- 目标：①GPU 自动检测（有 GPU 用 GPU 否则 CPU）；②训练/推理前分路开机情况分析并落盘 CSV
- 完成项：
  - seq_models.resolve_device：device 默认 auto（CUDA→MPS→CPU 优先级），显式 cuda 不可用回退 cpu 并告警；predict 每次独立解析设备并 net.to(device)（跨设备加载可用）
  - nilm/analysis/branch_sessions.py：analyze_branch_sessions——逐分路逐天按 on_thr_w 切开机段，输出起止/时长(min)/最小/平均/峰值功率(W)/电量(kWh)/state；整天无开机输出整天一行 state=0 统计整天数据；采样间隔中位差推断
  - pipeline 接入：train 清洗后、infer 特征前（有分路文件时）各落盘 branch_sessions.csv；infer 分路数据加载提前复用（离线评估不再二次加载）
  - README/default.yaml 文档更新；新增 10 项测试，134 项全过
  - 真实数据验证：800 用户 train 3 分路×71 天（118 开机段/129 全关天）、infer 3 分路×53 天，统计量一致性通过；GPU 检测日志正常（无 GPU→CPU）
- 关键决策：
  - 设备解析放模型层，fit/predict 独立解析（模型跨设备迁移）
  - 采样间隔用 timedelta64 除法（新版 pandas us 底层，view(int64) 会错 900 倍——踩坑记录）
  - 开机段跨午夜按天切开；整天关机行 session_id=0；时间段取该日实际数据范围
- 未决问题：无新增
- 相关文件/分支：nilm/models/seq_models.py、nilm/analysis/branch_sessions.py、nilm/pipeline/user_task.py、tests/{test_branch_sessions,test_ml_models,test_batch}.py、README.md、configs/default.yaml；分支 arena/019ffeb6-nilm-new

## [2026-08-14] 会话纪要（第 20 次）
- 目标：将 default.yaml 配置说明沉淀为文档
- 完成项：
  - 新增 docs/CONFIG_GUIDE.md v1.0：全局/data/quality/preprocess/features/dataset/bus_field_map/metrics/infer_model 逐项详解（以代码实际语义为准）；8 模型重点说明（原理/参数/训练效率因素/实测定位）；5 户实测结论速览与调优建议
  - README「配置文件结构」章节添加文档链接
- 关键决策：配置说明独立成 docs/ 文档而非塞进 README（README 保持速查，详解可长文维护；配置结构变化时两处同步——收尾仪式条件触发项）
- 未决问题：无新增
- 相关文件/分支：docs/CONFIG_GUIDE.md、README.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-08-14] 会话纪要（第 21 次）
- 目标：注释 transformer 重新批量验证；日级指标达标分析（SAE<0.2 且 F1>0.9）并归因不达标用户数据
- 完成项：
  - default.yaml models 注释 transformer（保留参数便于恢复）；--force 全量重跑 10/10 OK（~13min，提速 4 倍）
  - 新增 scripts/analyze_daily_metrics.py（达标判定+原因归类+汇总 CSV，参数可调口径）
  - 归因分析：346 行中 277 行不达标（80.1%）——842 全关日误报+边界 FP；844 推理期数据脱节全漏报；778 test 全达标/infer 仅 SAE 轻超（低估 17%）；789 阈值与负荷形态不匹配（TN=0）；800 训练期仅 6 天代表性不足（低估 43%）
  - 共性结论：F1（开关判定）树模型已达标，SAE 缺口来自训练/推理期负荷漂移；SAE 全关日分母 0 口径缺陷
  - 134 项测试全过
- 关键决策：SAE 口径修订建议（全关日只考核 F1）；改善方向定为扩充训练时间范围而非换模型
- 未决问题：正式达标口径待需求方确认；transformer 恢复时机
- 相关文件/分支：configs/default.yaml、scripts/analyze_daily_metrics.py、outputs/analysis/（不入库）、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 22 次）
- 目标：逐用户×逐模型日级指标详析；F1 不达标日重点归因
- 完成项：
  - 沙箱重置后重建环境+全量重跑（10/10 OK，与 08-14 结果逐行一致，可复现性验证通过）
  - 日级全景：用户×模型 达标率/F1 达标率/SAE 达标率/F1 中位与最小值矩阵（train-test + infer）
  - F1 不达标日混淆矩阵形态学六分类 + 数据交叉验证（branch_sessions/cleaned/inference_result/meta）
  - 【重要发现】proportional 基线工程缺陷：pbus 被 Scaler 标准化后 clip(0) 预测恒≈0，5 户 test 全期 TP=0；844/789 的 proportional "best" 是退化假象
  - 789 D 形态根因：真值双峰分布（P50 32W/P75 1312W）与 on_thr_w=60 不匹配 + cnn1d 无低功率分辨力（pred 恒 371~610W）
  - 842 B 形态根因：停机日仅 11% 且停机日总线水平（237W）与开机日（296W）重叠；800 尾段误报同类（停机但总线有底载，训练仅 6 天无此模式）
  - 778 F1 全期 100% 达标（标杆）
- 关键决策：详析结论落 REPORT_TEST；proportional 修复列 TODO 高优先（倾向 pbus 移出 scale_cols），待修复后重跑再更新 best_model 结论
- 未决问题：proportional 修复方案确认；789 阈值复核或 best 换 xgboost
- 相关文件/分支：outputs/analysis/（不入库）、REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 23 次）
- 目标：数据质量报告增加清洗后数据统计（总天数/全关天数量/全关天日期清单）
- 完成项：
  - validator.cleaned_daily_stats：行级最大功率按天聚合，日峰值 < on_thr_w 判全关天；输出 total_days/all_off_days/all_off_dates
  - quality_report 增加可选 on_thr_w 参数（附加 cleaned_stats，不传向后兼容）；write_quality_html 新增「清洗后数据统计」表与全关天日期清单段
  - pipeline train 侧 bus/branch 质量报告传入用户 on_thr_w（meta.json quality 同步携带）
  - 新增 6 项测试，140 项全过；真实数据 800 户验证（bus 71 天全关 4 天、branch 全关 18 天，与 branch_sessions 交叉吻合）
- 关键决策：全关天口径=日峰值<on_thr_w（与状态判据/branch_sessions 同口径三处互证）；on_thr_w 可选参数保持向后兼容
- 未决问题：无新增
- 相关文件/分支：nilm/data_io/validator.py、nilm/pipeline/user_task.py、tests/{test_quality_stats,test_batch}.py；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 24 次）
- 目标：①训练质量报告增加 train/val/test 切分级总天数/全关天统计；②推理阶段（有分路数据）增加同构质量报告
- 完成项：
  - validator：新增 series_daily_stats + _daily_stats_from_pmax 共用内核；write_quality_html 渲染 split_stats（「数据集·切分」行 + 逐切分全关天清单）
  - train：切分完成后 branch 质量报告附 split_stats（目标功率口径）并重写 HTML；infer：bus15+branch 质量报告（不设门禁）+ 评估段 split_stats.infer + meta.json quality 键
  - 新增 2 项单元测试 + 批量端到端断言扩展，142 项全过
  - 真实数据 800 户验证：infer 评估段全关 3 天（7-29/30/31）与此前 F1 误报日归因完全吻合
- 关键决策：切分级统计用目标功率口径（标签视角）；推理质量报告只报告不阻断；infer bus 用 15min 口径与训练可比
- 未决问题：无新增
- 相关文件/分支：nilm/data_io/validator.py、nilm/pipeline/user_task.py、tests/{test_quality_stats,test_batch}.py；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 25 次）
- 目标：审计各功能是否只针对配置指定的分路通道，并修正越界处
- 完成项：
  - 审计结论：核心建模链路（resolve_target_cols → build_target → 训练/指标/推理评估/切分统计）已严格限定 target_col（含 p1+p2 复合）；两处整表口径——branch_sessions 开机分析、质量门禁 assert_quality（整表缺失率）
  - 修正（用户确认方案）：开机分析限定目标分路（train/infer 均传 columns=target_cols）；新增 branch_target 目标子表质量报告，门禁改按子表判定（整表报告保留全景参考）；split_stats 迁移至 branch_target；infer 同构
  - 142 项测试全过；真实数据 789 户（4 分路配 p1+p2）验证：sessions 只含 p1/p2，整表全关 0 天 vs 目标子表全关 11 天（口径区分价值直观呈现）
- 关键决策：三层口径定型（整表全景/目标子表门禁/目标分路 sessions）
- 未决问题：无新增
- 相关文件/分支：nilm/pipeline/user_task.py、tests/test_batch.py；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 26 次）
- 目标：①无效天（全天缺失/缺失率超阈值）不参与训练与评估；②质量报告增实际天数；③全关天剔除全天缺失天；④审计全天缺失天的统计使用
- 完成项：
  - validator：invalid_data_days（功率列口径+max_daily_missing_rate 新配置，默认 0.9）；cleaned_stats 扩展 actual_days/missing_days/missing_dates；全关天只在实际天中判定；HTML 增实际天数/全天缺失天列与清单
  - pipeline：train 侧 bus∪branch(target) 无效天整天剔除（时间过滤前）；infer 侧离线评估同口径剔除；两侧落盘 excluded_days.json
  - 审计结论：branch_sessions/evaluate_daily/identifiability 均已天然安全；唯一漏洞为全关天 fillna(0) 把全缺失天计入，已修
  - 146 项测试全过（新增 4 项）；真实数据验证：842 train 剔 7 天（含 6-20/6-22 此前发现的 2 点天）、日级指标不再含剔除天；844 暴露真实数据问题（49 个 0 点天）转 INSUFFICIENT_TIME_RANGE
  - CONFIG_GUIDE.md 增 max_daily_missing_rate 条目
- 关键决策：有效点按功率列判定（PF 兜底填充会误判，踩坑记录）；844 不放宽阈值迁就；全关≠无数据口径修订
- 未决问题：844 数据侧补齐
- 相关文件/分支：nilm/data_io/validator.py、nilm/pipeline/user_task.py、configs/default.yaml、docs/CONFIG_GUIDE.md、tests/{test_quality_stats,test_batch}.py；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 27 次）
- 目标：详细复核数据质量报告各统计数据的正确性
- 完成项：
  - 独立重算复核（不复用 validator 代码路径）：789 户 36 项 + 842 户 train/infer 双侧全部统计量（四项指标+cleaned_stats 六字段+split_stats）——0 不一致
  - 交叉一致性验证：HTML=JSON 逐值相等；训练窗内缺失天⊆excluded_days；剔除天∩评估天=空；全关∩缺失=空；split_stats.total_days=metrics_daily 天数
  - 两口径疑点核查：日历缺口天不计入 total_days（由跨度与覆盖率反映，386−132=254 自洽）；excluded_days 只含时间窗内无效天（窗外不参与训练无需剔除）——均为设计而非缺陷
  - 146 项测试全过（回归确认）
- 关键决策：复核方法论（独立路径重算+交叉断言）入 STATUS；gap_days 字段暂不加
- 未决问题：无
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 28 次）
- 目标：排查 2842 用户 6-13 清洗后不再是全天缺失天的问题；修复并全用户重验
- 完成项：
  - 根因定位：interpolate(limit=N) 对长缺口部分填充前 N 点（pandas 语义陷阱），6-12 尾部 0 值延伸 2 个伪点进 6-13
  - 修复：clean.py 新增 _interp_short_gaps（缺口游程整段决策：≤max_gap 全补/>max_gap 全不补；limit_area=inside 禁首尾外推）
  - 新增 tests/test_clean.py 6 项回归测试；152 项全过
  - 全 5 户 --force 重跑：8 项 OK（844 既有数据问题除外）；842 目标通道缺失天 1→6、全关 16→11 归位；6-13 进缺失+剔除清单、不在全关/日级评估
- 关键决策：插值语义修正是统计正确性的前置依赖（无效天判定/缺失天统计均受污染）；教训入 STATUS
- 未决问题：无新增
- 相关文件/分支：nilm/preprocess/clean.py、tests/test_clean.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 29 次）
- 目标：拉取最新版本；models 只保留 history_profile/proportional/ridge（其余注释）；2842 用户验证测试
- 完成项：
  - 拉取远程最新 60a738f（用户更新 2842/2844 数据至 2026-08-03，训练窗数据 132→146 天）
  - default.yaml models 注释 random_forest/xgboost/lstm/cnn1d（transformer 原已注释），保留 3 基线；恢复取消注释即可
  - 152 项测试全过；2842 train+infer OK：best=ridge test r2 0.651/f1 0.750（新数据下较旧 0.616/0.769 r2 提升）；infer 离线 r2 0.871/f1 0.930
  - 既有功能在新数据上保持生效：无效天剔除 train 7 天/infer 11 天（新数据 7-31/8-2 等新增缺失天正确识别）；6-13 仍在缺失清单（插值修复有效）；剔除天∩评估天=空；质量报告 bus 146/143/3、target 146/140/6/全关18
- 关键决策：无新增（配置调整+验证轮）
- 未决问题：待用户确认后可扩展到其余用户批量验证
- 相关文件/分支：configs/default.yaml；分支 arena/019ffeb6-nilm-new

## [2026-08-17] 会话纪要（第 30 次）
- 目标：2842 三模型训练 F1（>95% 口径）不达标详析与优化方案
- 完成项：
  - 缺口结构分解：R≈1.0/P 0.46~0.61，FP 唯一瓶颈；真值无灰区（关态 97%=0W）
  - FP 三源定位：全关天白天整段误报 50%（4 天×48 点）、开机边界扩张、夜间小值抖动
  - 优化实验：决策阈值 50W+min_on8 → F1 0.750→0.874；分类器/门控/夜间置零/单段约束边际收益≈0
  - 天花板结论：点级手段上限 ~0.87-0.88；剩余 FP 集中在 4 个全关天（总线无停机信号，可辨识性下界）
  - 优化方案输出（训练数据/超参/后处理/结构四层，见 REPORT_TEST 专题）
- 关键决策：分析方法论入 STATUS；方案落地待用户确认优先级
- 未决问题：post 参数调整（配置级，零开发）是否执行；样本加权/分类头（开发级）是否立项
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new
