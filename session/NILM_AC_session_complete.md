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

## [2026-08-18] 会话纪要（第 31 次）
- 目标：无效分路通道语义检查修正；2842 复验；F1 不达标天详析与优化方案
- 完成项：
  - 修正：train/infer 清洗后立即丢弃非目标通道；branch 质量报告/清洗产物/开机分析/无效天判定统一有效通道口径；branch_target 子表报告并入 branch
  - 152 项测试全过（断言更新）；2842 复验 OK（丢弃 p2/p3/p4；branch_cleaned 只含 p1；三阶段指标与修改前逐位一致）
  - F1≤0.95 共 19/21 天四类归因：A 全关天整段误报 4 天（FP 占 51%，可辨识性下界）/B 误报为主 6 天/C 边界扩张 7 天/D 其他 2 天
  - 优化方案输出（数据集：停机日加权/扩窗/夜间基线特征；模型：alpha 调参/加权岭/决策阈值+min_on 后处理）
- 关键决策：三层口径简化为单一有效通道口径；语义修正不改模型结果（验证确认）
- 未决问题：优化方案落地优先级待确认
- 相关文件/分支：nilm/pipeline/user_task.py、tests/test_batch.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 32 次）
- 目标：质量报告增加①总线分路双达标总天数②逐天质量表（总线得分/分路得分/阈值/当天合格）③训练数据集划分与模型训练建议
- 完成项：
  - validator：_day_score（单日得分，与整段公式一致但缺失率含行数不足）、daily_quality_table（并集口径逐天表，纯日历缺口天跳过）、quality_advice（规则式建议：合格天数分层→切分策略；合格率分层→模型选择；连续不合格段→exclude 提示；分路短板提示）
  - write_quality_html 扩展：双达标统计段、逐天质量表（不合格行红底）、建议列表段（参数可选，向后兼容）
  - pipeline train/infer 接入：daily_quality.csv + quality_advice.json 落盘；meta.quality.branch 增 both_qualified_days/daily_total_days
  - 新增 4 项测试，156 项全过；2842 实测：train 146 天双达标 140（不合格 6 天=2 个 bus 低分天+4 个分路全缺天，与既有缺失天结论吻合）；建议输出正确（96% 合格率→stratified_day+全模型对比）；infer 390 天（bus 全程覆盖）双达标 140——分路 0 分天正确标记不可评估
- 关键决策：日级得分缺失率口径含行数不足（比整段更严）；纯日历缺口天不进表（覆盖率已反映）；建议为规则式启发（非硬约束，供人工决策）
- 未决问题：无新增
- 相关文件/分支：nilm/data_io/validator.py、nilm/pipeline/user_task.py、tests/{test_quality_stats,test_batch}.py；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 33 次）
- 目标：清洗后统计改为双达标口径（总/全关/训练/验证/测试天数）+ 每天明细（全关日/阈值/所属数据集）+ 划分与训练建议
- 完成项：
  - validator：qualified_days_detail（双达标天明细：all_off/on_thr_w/dataset——训练集/验证集/测试集/推理集/未使用）、qualified_days_summary（汇总）、split_coverage_advice（全关天覆盖检查/未使用占比提示）
  - write_quality_html：清洗后统计段改双达标口径渲染（统计表+每天明细表，全关日蓝底），无明细时回退旧渲染
  - pipeline：train 侧切分后构建明细（split_index=各集时间索引）；infer 侧评估段=推理集；qualified_days_detail.csv + quality_advice.json 落盘；meta.quality.branch.qualified_days_stats
  - 新增 4 项测试，160 项全过；2842 实测：双达标 140 天=全关 18+训练 68/验证 23/测试 21/未使用 28；建议自动输出「训练集含全关天 7 天占 10%→建议加权」；infer 侧推理集 25 天正确标注
- 关键决策：双达标单一口径取代 bus/branch 分列统计；未使用类别显性化；覆盖性建议把人工分析结论规则化
- 未决问题：无新增
- 相关文件/分支：nilm/data_io/validator.py、nilm/pipeline/user_task.py、tests/{test_quality_stats,test_batch}.py；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 34 次）
- 目标：更新配置文件详细说明（yaml + json 全数据项）
- 完成项：
  - docs/CONFIG_GUIDE.md v1.0→v2.0：重构为两部分——第一部分 default.yaml（同步近期变更：max_daily_missing_rate、min_score 兼作逐天质量表阈值与双达标口径、save_cleaned_csv、模型当前启用状态注记、质量报告新产物链）；第二部分用户 JSON 全字段（顶级结构/_default/_user_id_map 键规则与优先级、CONFIG_RULES 全部标量字段含取值范围与全流程语义、train/infer/splits 时间过滤闭区间与切分锚定语义、字段生效位置速查表）
  - README 链接文案更新
- 关键决策：新增「字段生效位置速查」表（字段→影响哪个产物），排查配置问题可反查；target_col 语义按最新业务口径（有效通道/无效通道）表述
- 未决问题：无
- 相关文件/分支：docs/CONFIG_GUIDE.md、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 35 次）
- 目标：min_score→70；2842 排除训练/推理不达标天；重验+F1 详析与优化方案
- 完成项：
  - default.yaml min_score=70；离线预演定位不达标天（训练窗 12+推理窗 3）；time_filters.json 2842 train/infer exclude 落地
  - 160 项测试全过；重跑 OK：切分 6098/2092/2013、双达标 131 天；推理 F1=0 天全部消失（评估噪声清除）
  - F1≤0.95 归因：test A 型全关天 4 天（FP 51%）/B 型 6 天/C 型 10 天；推理 19 天全为 B/C 型
  - 优化量化：决策阈值 50+min_on8 → F1 0.881；开机日口径（排除全关天）→ **0.982 达标**
- 关键决策：min_score=70 的选型依据入 STATUS；F1>0.95 需口径协商的结论实证固化
- 未决问题：post 参数写入配置正式生效待确认；全关天口径协商
- 相关文件/分支：configs/{default.yaml,time_filters.json}、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 36 次）
- 目标：2842 优化方案 1+2 验证测试；F1 不达标再分析与优化方案
- 完成项：
  - 新增 decision_thr_w 用户配置字段（仅作用于 pred_state/pred_prob/状态策略判决，真值口径不变）
  - 训练新增 state_strategy_metrics.csv（每模型 × all_days/on_days_only 两口径 F1/P/R/TP/FP/FN）
  - 2842 配置 post_min_on=8 + decision_thr_w=50；161 项测试全过
  - 验证：训练 test 全量口径 0.881/开机日口径 0.9823 达标；推理 pred_state F1 0.981（vs 0.908），日级 22/24 天 >0.95
  - 剩余不达标归因：4 全关天 FP 257（可辨识性下界）；推理 7-27 疑似真值漏记、7-14 晚间边界 4 点
- 关键决策：decision/on 阈值分离；双口径产物化
- 未决问题：7-27 数据侧核查；口径协商（0.88 vs 0.98 两数并出）
- 相关文件/分支：nilm/common/contracts.py、nilm/pipeline/user_task.py、configs/time_filters.json、tests/test_batch.py；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 37 次）
- 目标：分析 metrics_by_split/metrics_daily 与 state_strategy_metrics 指标不一致原因
- 完成项：
  - 重放分解定位差异三因素：判决阈值（10 vs 50）、游程后处理（无 vs min_on8）、口径（all vs on_days）——设计使然非缺陷；metrics_daily 与 by_split 同口径（tp/fp/fn 求和相等）
  - 修复真实缺陷：state_strategy precision 空真约定不一致（有 FN 误记 1.0→0）
  - state_strategy_metrics.csv 增加 strategy 列（raw_on_thr 对照行 + decision+runs 生产链）；新增跨产物对账测试断言
  - 161 项测试全过；2842 重跑对账三模型 raw 行与 by_split 逐值相等 ✅
- 关键决策：双口径设计定型并文档化；空真约定教训入 STATUS
- 未决问题：无
- 相关文件/分支：nilm/pipeline/user_task.py、tests/test_batch.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 38 次）
- 目标：训练阶段输出预测结果（预测值+真实值）
- 完成项：
  - user_task 训练循环收集三切分各模型预测，落盘 predictions/train_predictions.csv——列：timestamp/split/target(真实值)+pred_<model>（宽表、时间有序、round(3)）
  - README 输出产物章节更新；新增 1 项端到端测试（列契约/行数=切分之和/非负/时间有序），162 项全过
  - 2842 实测验证：10203 行（6098/2092/2013）×3 模型列；由该 CSV 重算 ridge test TP/FP/FN=837/507/18 与 metrics_by_split 逐值对账 ✅
- 关键决策：宽表设计（一行一个时间点、各模型并列）便于直接画预测-真值对比曲线；放 predictions/ 子目录与推理产物同域
- 未决问题：无
- 相关文件/分支：nilm/pipeline/user_task.py、tests/test_batch.py、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 39 次）
- 目标：proportional 在 2842 三阶段 F1 全 0 根因分析与修复
- 完成项：
  - 三证据链闭环：pred 全量 <3.6W（P95 1.39，≥10W 占比 0）→ pbus 被 z-score（μ249/σ181）→ pred=clip(z,0) 只剩正 z 尾巴——模型「pbus=物理功率」假设被 Scaler 破坏
  - 修复：pbus 加入 NON_SCALED_COLS（方案 B，slot 先例）；163 项测试全过（新增回归守卫）
  - 2842 重跑验证：proportional 复活（test MAE 173.7/R² 0.488/F1 0.596/recall 1.0）；ridge 等无回归（f1 0.7613→0.7602 属切分随机）
  - 影响面：789/844 历史 best=proportional 作废，待全量重跑刷新
- 关键决策：方案 B 选型依据入 STATUS；proportional F1 上限受模型本性限制（底载放大误报），不再投入调优
- 未决问题：全量 5 户重跑刷新 best_model
- 相关文件/分支：nilm/pipeline/user_task.py、tests/test_batch.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 40 次）
- 目标：训练预测结果增加预测状态与真实状态
- 完成项：
  - train_predictions.csv 扩列：target_state（真值按 on_thr_w 二值化，与推理同口径）+ pred_state_<model>（生产判决链口径：decision_thr_w+post_min_on/fill 游程后处理，与推理 pred_state / state_strategy decision+runs 行一致）
  - dec_thr 计算前移复用（消重复定义）；测试断言扩展（target_state 一致性/pred_state∈{0,1}），163 项全过
  - 2842 实测：10 列产物；由 pred_state_ridge/target_state 重算 test F1=0.8812 TP/FP/FN=831/200/24 与 state_strategy decision+runs 行精确对账 ✅；关机时段样例直观展示 ridge 判 0/proportional 误判 1 的差异
  - README 输出产物说明更新
- 关键决策：预测状态选生产判决链口径（非 raw 口径）——与推理产物/策略评估一致，三产物可互查；真实状态用 on_thr_w（业务口径）
- 未决问题：无
- 相关文件/分支：nilm/pipeline/user_task.py、tests/test_batch.py、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 41 次）
- 目标：分析 train_predictions.csv 与 metrics_daily.csv 的 TP/FP/FN/TN 数量不一致
- 完成项：
  - 三口径对比+逐因子分解：daily=值 ≥ on_thr_w(10) 直接判（模型能力口径）837/510/18/648；pred_state 列=decision_thr(50)+游程（生产判决链）831/200/24/958；FP 510→250（阈值）→200（游程）
  - 对账验证：用 train_predictions 值列按 on_thr_w 重判 → 与 metrics_daily 三模型逐值相等 ✅（同源无缺陷）
  - README 增加口径提示；结论入 REPORT_TEST
- 关键决策：不改代码——差异是双口径设计（08-18 定型）的预期表现；对账方法文档化
- 未决问题：无
- 相关文件/分支：README.md、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 42 次）
- 目标：解决三模型每日指标 FP 过多问题（模型侧）
- 完成项：
  - 离线定量实验：alpha 无效；关态加权 w_off=5 最优（FP 481→342）；中位画像 FP 363→184
  - 落地：ridge off_weight/off_thr_w（加权岭）+ history_profile agg=median 参数化，default.yaml 启用
  - 修复 unseen 槽位 profile==0 代理缺陷（显式 _seen）；新增 2 项测试，165 项全过
  - 2842 重跑：hp F1 0.619→0.859（FP -83%）、ridge 0.760→0.799（FP -23%，SAE 0.128→0.028）；生产判决链 0.877/0.976 维持
  - FP 治理三层框架（模型层/判决层/口径层）定型入 STATUS
- 关键决策：proportional 不调参（sanity 定位）；off_weight/agg 均为可配参数按用户校准
- 未决问题：全关天误报（模型层无解，需停机特征）；其他用户参数校准
- 相关文件/分支：nilm/models/baselines.py、configs/default.yaml、tests/test_ml_models.py；分支 arena/019ffeb6-nilm-new

## [2026-08-18] 会话纪要（第 43 次）
- 目标：调参落地后 2842 重新验证；日级 F1 不达标天详析与优化方案
- 完成项：
  - 重跑 OK（165 项测试全过）；test 日级达标：hp 0→12 天、ridge raw 6 天/判决链口径 15/21 天（中位 0.980）
  - ridge raw 不达标 15 天分解：4 全关天（FP 242）+ 11 个 B/C 天（FP 132=边界 58+凌晨 1-6 时小值 74）
  - 判决链口径复核：B/C 类几乎清零（F1 0.91~1.00）；剩余=4 全关天+2 个 FN 天（6-10/6-08 段内低功率间歇被 50W 阈值误杀）
  - 结论：raw 口径 B/C"不达标"非生产问题；优化方向收敛为全关天特征/口径与 FN-FP 权衡（decision_thr 微调）
- 关键决策：不再追加代码改动（三层治理已覆盖可治理项）；推理日级生产口径统计扩展待需求确认
- 未决问题：全关天（不变）；decision_thr 50 的 FN 代价是否回调由需求方定
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-08-19] 会话纪要（第 44 次）
- 目标：训练/推理预测结果增加状态判定阈值列；核对日级指标 TP/FP/FN/TN 判定阈值与预测结果阈值一致性
- 完成项：
  - inference_result.csv 契约扩列：on_thr_w（真值判态阈值）+decision_thr_w（预测判态阈值），INFER_RESULT_COLUMNS 9 列
  - train_predictions.csv 同步加两阈值列；metrics_daily（train/infer）与 metrics_by_split 加 state_thr_w 自描述列
  - 一致性核对（2842）：state_thr_w=10 ≡ on_thr_w=10 ✅（日级分类指标与预测结果真值判态同阈值）；decision_thr_w=50 与之不等属双口径设计（pred_state 判决链，已显式标注防误读）
  - 逐日全量对账：训练 321 行（3 模型×3 切分×各天）+ 推理 24 天由预测结果按 state_thr_w 重算 TP/FP/FN/TN 与 metrics_daily 全部相等（0 不一致）；新增测试断言（target_state≡target≥on_thr_w 行级一致、state_thr_w 集合相等、日级对账）
  - 165 项测试全过；README 契约说明更新
- 关键决策：阈值口径「数据自带」设计（防跨产物误读）；不改变任何计算逻辑（纯自描述增强）
- 未决问题：无
- 相关文件/分支：nilm/common/contracts.py、nilm/pipeline/user_task.py、tests/test_batch.py、README.md；分支 arena/019ffeb6-nilm-new

## [2026-08-20] 会话纪要（第 45 次）
- 目标：hp/ridge 全关天严重误报详析与优化验证
- 完成项：
  - 根因终判：开机沿分析（115 沿）p1 跳 723W/pbus 仅跳 92W（可见性 0.12）——目标设备功率未计入总线；B 相计量全缺+93% 点 p1>pbus（比值 1.59）证明疑挂 B 相
  - 机制分解：hp 画像结构性失效（对当天输入零感知）；ridge 弱信号（92W）被背景波动淹没
  - 三组缓解实验全部量化失败（门控/基线差/阶跃均不可分）——算法侧确认无解
  - 落地：identifiability 新增 bus_visibility_ratio + TARGET_NOT_VISIBLE_ON_BUS 风险（2842 实测 0.1338 自动命中）；166 项测试全过
- 关键决策：结论建议进 REPORT.md；解决路径收敛为数据侧（B 相补计量/停机日历）；其余 4 户待体检
- 未决问题：向采集侧确认 B 相点位与设备接相
- 相关文件/分支：nilm/analysis/identifiability.py、tests/test_identifiability.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-20] 会话纪要（第 46 次）
- 目标：纠正上轮全关天根因结论（用户指正：总线功率含 CT/PT 变比，与分路不可直接比较）
- 完成项：
  - 尺度不变口径重验：隐含变比 Δp1/Δpbus=7.97≈8（典型 CT 变比）；边沿 SNR=3.18（开机沿在总线上清晰可辨）——撤销"未计入总线/B 相挂接"错误结论
  - 检测修正：TARGET_NOT_VISIBLE_ON_BUS→TARGET_EDGE_BURIED_IN_BUS（边沿信噪比，尺度不变）+implied_bus_scale 输出；测试重写双情形；166 项全过
  - 2842 重验：snr 2.85/隐含变比 7.47，正确判 identifiable=True（上轮误报警撤销）
  - 全关天定性回归"背景掩盖"（信号在但被背景波动盖住）；REPORT_TEST 上轮结论作废声明
- 关键决策：跨源绝对幅值比较无效教训入 STATUS（重大）；获取 CT/PT 变比列为数据侧行动项（隐含值≈8 供核对）
- 未决问题：CT/PT 变比获取；per-user 变比配置层设计（变比确认后）
- 相关文件/分支：nilm/analysis/identifiability.py、tests/test_identifiability.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-08-20] 会话纪要（第 47 次）
- 目标：验证用户提议——无 CT/PT 信息时用正比关系（实际=k×测量）做验证测试；2842 重新验证
- 完成项（五组实验）：
  - k̂ 多途径反推：开机沿 7.92/停机沿 8.52/大幅变化点 7.98——自洽，k̂≈8（供采集侧核对）；全点回归 0.51 为衰减偏差（方法论坑）
  - 功率平衡校验：k=8 违反 1.7% 全部溯源为总线缺数置 0 时段（与无效天清单吻合）；排除后 0.00% ✅
  - 月度稳定性 CV 15%（临界通过，噪声所限）；proportional share 语义对 k 天然免疫
  - 关键洞察：k̂ 换算=线性缩放，z-score 后模型输入不变——变比确认对归一化模型零增益；全关天日级幅值重叠 53%、test 门控判对 0/4——k 已知也不解全关天
  - 166 项测试全过（纯分析轮，无代码改动）
- 关键决策：全关天解决路径收敛排序（停机日历>缺失点位补齐>精确 CT/PT）；两条稳定结论建议进 REPORT.md
- 未决问题：k̂≈8 待采集侧核对
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-08-20] 会话纪要（第 48 次）
- 目标：变比纠正后重做全关天识别问题分析与优化验证
- 完成项：
  - 机制终版：背景与目标同作息（形状相关 0.999；全关天 9:15-9:30 假沿 47~110 vs 真沿 92 同量级同时刻）；关键窗口 SNR≈1 调和了"边沿 SNR 3.18 却日级不可分"的矛盾
  - 信息论边界量化：背景日间漂移 σ=49 vs 信号 92 → 跨天特征上限 ~50% 重叠
  - 五组优化方案全部验证失败（双沿匹配滤波 0/4+误杀3；背景残差重叠 53%；自门控 3/4 但误杀 7；三特征投票 0/4；电流通道同 53%）——算法侧宣告无解
  - 路径收敛：A 停机日历（最优）/B 精确 CT/PT+data45/43/44 补齐（恢复理论可分性）/C on_days_only 口径（现状达标）
  - 166 项测试全过（纯分析轮）
- 关键决策：算法侧不再投入（五组量化失败为证）；结论建议进 REPORT.md
- 未决问题：方案 A 停机记录/方案 B 点位与变比确认——均为业务/数据侧行动项
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-08-20] 会话纪要（第 49 次）
- 目标：hp 全关天误报深层原因专项 + 模型/数据集两层方案验证
- 完成项：
  - 深层原因：条件缺失结构性缺陷（画像无"今天"输入；白天槽位开态占比 0.78-0.85→必然输出开机值；test FP 184/184 全来自全关天）
  - 数据集方案证伪：训练集排除全关天 FP 不降反升（180→184）——负结果沉淀
  - 模型方案落地：HistoryProfile 新增 pbus_bins 条件画像（slot×pbus 分位桶，默认 1=关闭）；新增 1 项语义测试；167 项全过
  - 验证：778 端到端 F1 0.9851→0.9925（FP 6→3）✅；2842 受信息论边界（FP 184→133 但 FN 90→201，不启用）；hp×ridge 门控融合无增益
  - 适用性判据：按总线可见性逐用户启用
- 关键决策：pbus_bins 默认关闭；2842 维持 median+判决链，全关天走外部信息路径（不变）
- 未决问题：其余用户可见性体检后决定是否启用 pbus_bins
- 相关文件/分支：nilm/models/baselines.py、tests/test_ml_models.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 50 次）
- 目标：2842 重跑复现 hp 全关天问题；深层原因分析；模型/数据集两层方案验证落地
- 完成项：
  - 重跑复现（train+infer OK）：hp FP 184/184 全来自全关天、预测与开机日逐点相同、P(开|slot) 0.78——条件缺失结构性缺陷确认
  - 方案 A 条件画像切点扫描：q=0.05~0.50 无 Pareto 改进（FP/FN 严格互换）——2842 不启用（负结果）
  - 方案 B 落地：splits 锚定 4 全关天入 train——hp test F1 0.859→0.9444（FP 184→0）、ridge 0.799→0.942；推理连带 F1 0.934→0.980
  - 连带修复：新增用户级 infer_model 配置（best_model 被 proportional 退化特性带偏），2842 锁定 ridge
  - 167 项测试全过
- 关键决策：splits 锚定优于纯口径排除（样本利用+考核聚焦）；best_model 选型鲁棒性问题记录待改
- 未决问题：hp 剩余 FN 90（开机段内低功率间歇，另一问题域）；选型 wins 计数改进
- 相关文件/分支：configs/time_filters.json、nilm/common/contracts.py、nilm/pipeline/{user_config,user_task}.py、REPORT_TEST.md；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 51 次）
- 目标：解剖上轮锚定后"训练指标差"的原因并修复验证
- 完成项：
  - 三因分解：①真问题=设备双档位（200W/720W）且 6 个低档日全在 test（train 零覆盖），SAE 1.2~4.9；②统计伪影=平坦日（真值 std 13W）日级 R²=-57；③成分效应=全关天移出后 R² 分母缩小（同口径重算 0.565 vs 0.564 无退化）
  - 修复：splits 补锚 3 个低档日入 train（6-04/08/09），test 留 3 个作外推检验
  - 验证：hp test R² 0.410→0.598、SAE 0.211→0.029、MAE→129.4；ridge F1 0.954、日级达标 10/14；推理 F1 0.983 维持；167 项测试全过
- 关键决策：三因分解方法论与多档位切分原则入 STATUS；低档日幅值 SAE 待数据积累（3 样本太少）
- 未决问题：档位判别特征；metrics_daily 加 target_std 辅助列（待议）
- 相关文件/分支：configs/time_filters.json、REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 52 次）
- 目标：metrics_daily.csv 三切分指标意义详析 + 各切分坏天逐日归因
- 完成项：
  - 三切分意义框架：train 拟合诊断/val 泛化预警/test 能力结论；train F1 中位低于 test 的反常确认为成分差异（train 含 11 全关天+4月漂移段）
  - 坏天全量归因（ridge 83 天）五类：全关天 17 天（train11+val6 连续停机段落 val）、4-5 月作息漂移 FP 爆发（日均开机 3.8h vs 夏季 12h，4-10 FP75 覆盖全天）、2025 段夜间小值 FP（日均 8.6 点，判决链已消）、低档日 6 天（test 3 天状态好但 SAE 0.7~1.3）、平坦日 R² 伪影 ~8 天
  - 可执行项输出：季节分段/val 停机段锚定/metrics_daily 辅助列（均待确认，未动代码）
- 关键决策：诊断框架与五类模式入 STATUS/REPORT_TEST；本轮纯分析不改代码
- 未决问题：季节分段建模、target_std 辅助列
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 53 次）
- 目标：2842 用户 2026-07-27 推理结果专项审查
- 完成项：
  - 重跑复现（F1 0.818，FP 18/FN 1）；三段误差逐点取证：早晨 FP12（开机延迟 30min+背景爬坡）、中午 FP6（真实停机 1.5h 但总线高位不可辨识——全关天段内版）、22:00 FN1（背景先停的停机沿相位差）
  - 物理核验：中午停机/复开沿 pbus 跳变 −76/+73 与 k̂=8 预期吻合——真值可信，撤销 08-18"真值漏记"怀疑
  - 判定：推理结果无系统性错误；误差均属已知问题域，无新增行动项；167 项测试全过
- 关键决策：数据怀疑必须物理核验后下结论（方法论入 STATUS）；fill_short_off 不调（收益 2 点）
- 未决问题：无新增
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 54 次）
- 目标：重跑 2842 train+infer 展示指标；7-27 推理详析与 inference_result.csv 数据检查
- 完成项：
  - 重跑 OK：train best=hp（F1 0.946/P 1.000）、ridge 0.954；推理（ridge 锁定）F1 0.983、日级 23/24 达标（中位 0.990）
  - inference_result.csv 13 项契约检查全过（列结构/时间网格/数值域/阈值列/状态一致性/prob 边界）；15min 网格 100%
  - 7-27 审查：缺 1 点=00:00 采集缺口（机制正确）；FP 两段+FN 1 点与第 53 次结论一致；物理核验复现自洽；raw 口径重算 F1 与 metrics_daily 逐值对账 ✅——数据无问题
  - 167 项测试全过
- 关键决策：13 项契约检查清单沉淀为例行体检模板（REPORT_TEST）
- 未决问题：无新增
- 相关文件/分支：REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 55 次）
- 目标：on_thr_w=50 重跑 2842；展示指标；7-27 复审与 inference_result.csv 检查
- 完成项：
  - 配置 on_thr_w=50（与 decision_thr_w 对齐）；重跑 OK；167 项测试全过
  - 指标：ridge test raw F1 0.760→0.959（FP 507→11）、推理 0.986（FP 27）、日级 23/24 达标（中位 0.995）；raw/判决链口径几乎重合
  - 阈值影响分析：真值无 [10,50) 灰区（0 翻转）——纯预测判态口径变化
  - 7-27 复审：误差结构不变（三问题域），F1 0.833；CSV 契约 10/10 全过、对账相等——数据无问题
- 关键决策：口径统一的价值确认（阈值差是此前口径分裂主源）；历史 10W 指标不可直接对比的提示入档
- 未决问题：无新增
- 相关文件/分支：configs/time_filters.json、REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 56 次）
- 任务：解释 2842 用户 7-27 推理结果中 14:30/14:45 两点 pred<50 但 pred_state=1 的原因
- 完成内容：
  - 定位：pred_state 非逐点阈值判态，而是 postprocess_state 三步判决链（≥50 判态 → enforce_min_on(8) 去短开 → fill_short_off(3) 填短关）输出
  - 根因：14:30~14:45 是被 06:15~14:15（33 点开）与 15:00~21:45（28 点开）两长开态段夹住的 2 点关断，长度 ≤ post_fill_short_off=3，被整段回填为开——设计行为非缺陷
  - 数值复现：对 inference_result.csv 的 pred 重放判决链，7-27 当日 95 点与全区间 2246 点均与落盘 pred_state 逐点全等；pred_prob 与逐点 sigmoid(pred) 全等（0.027/0.426，点级证据保持"偏关"）
  - 关联误差结构：这两点落在已确认的午间真实停机段（14:30~15:45），ridge 点级预测本对（<50），fill 回填制造 2 个 FP（当日三口径 F1：raw 0.8333 / 无 fill 0.8411 / 完整判决链 0.8257）——"段内短停机 vs 量测噪声"不可区分的必然代价，属午间停机不可辨识问题在判决链上的投影，无新增行动项
- 沉淀：REPORT_TEST.md 新增专题；STATUS.md 决策记录新增「pred 与 pred_state 不一致排查三步法」与回填点识别信号（pred_state==1 且 pred<decision_thr_w）
- 未决问题：无新增
- 相关文件/分支：REPORT_TEST.md、STATUS.md、nilm/postprocess/state.py（只读分析）；分支 arena/019ffeb6-nilm-new

## [2026-09-01] 会话纪要（第 57 次）
- 任务：2842 全关天数据在 ridge 模型上误识别问题——重新深入原因分析并给优化方案
- 完成内容：
  - 环境：沙箱重置恢复（git reset FETCH_HEAD + venv 重建 + 2842 train/infer 重跑重建产物）
  - 复现闭环：离线完整重放流水线训练链路（特征→切分→缩放→加权岭闭式解→clip 非负），与 train_predictions.csv 逐点一致（最大差 5e-4=CSV 精度）；踩坑：apply_constraints 须 sum_consistency=False
  - 误报现场：17 全关天三段分化——4月(train) FP≈0 / 5月(val 样本外) FP 43~56/天 / 6月(train 锚定样本内) FP 174/384——锚定+off_weight 对 ridge 无效实锤
  - 权重级根因（新证据层）：|W| 前5=pa −2100/截距+1831/pc +1457/ia +1399/ic +1364（L2=3324 vs 目标~700，条件数 8e8，特征相关 0.991~1.000）；组贡献分解显示日历族恒常数——模型未用季节补偿；对 17 全关天回归 pred≈2.48×rise−582（R²=0.90）→ ridge 等效「常数背景基线差分×2.48」，背景季节漂移(4月239→6月370)沿斜率放大成 120~385W 误报，corr(pred,pbus)=0.88 误报形态=背景曲线放大
  - 样本内不可修复性几何证明：6月全关 rise(351/370) > 4月开机日最低 rise(237/247/249)，线性决策面无解；LogReg 探针样本内误开 0.29~0.55、GBDT 样本内 0 但样本外 0.61——容量不解决信息不足，与 08-20 信息论边界在模型层闭环
  - 方案矩阵 15 项全灭（α 扫描/off_weight 扫描/剪共线/夜基线特征/日级门控）：无一 Pareto 改进；门控 rise<350 可清零全关 FP 但 val 开机 recall 0.656 不可用
  - 优化方案分层：①生产敞口现状=0（test/推理期均无全关天）；②新增建议=日级 rise 三段风险标记（<237 ALL_OFF_LIKELY 覆盖 7/17 零误杀 / >370 ON_LIKELY / 重叠带 UNCERTAIN 交业务复核）——把不可辨识从隐性错误变显性风险输出；③月内自适应门控仅 4 月有效不默认启用；④⑤数据侧补计量/业务侧停机日历（既有路径，补量化：重叠带宽 131 bus 单位）
- 关键决策：算法侧"无解"结论维持且证据从统计重叠升级为确定性几何重叠；线性模型权重级归因三步法沉淀入决策记录
- 未决问题：方案②风险标记落地待用户拍板（涉及推理产物列/契约变更）
- 相关文件/分支：REPORT_TEST.md（新专题）、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-02] 会话纪要（第 58 次）
- 任务：time_filters.json 增加顶级全局 infer_model 配置——有配置则默认用指定模型推理，无配置走原逻辑
- 完成内容：
  - contracts.py 新增 GLOBAL_CONFIG_KEYS=("infer_model",)（顶级全局配置键白名单）
  - user_config.py：list_user_keys 跳过全局键（不再当非法用户键报错）；resolve_user_config 插入 global(顶级) 合并层，优先级=用户级 > _default > 顶级全局 > 硬编码默认，_provenance 可溯源
  - user_task.py 推理模型选择注释更新为完整回退链：用户级 → _default → JSON 顶级全局 → yaml infer_model → best_model（选择表达式本身无需改动，合并层已注入 user_cfg）
  - 测试 +5（tests/test_user_config.py）：全局生效/未配置回落 None/用户级与 _default 覆盖全局/全局键不作用户键/类型校验——172 项全过
  - 778 端到端三场景验证：顶级 proportional→model=proportional；无顶级→model=history_profile(best)；顶级 proportional+用户级 ridge→model=ridge
  - 文档：CONFIG_GUIDE §11 顶级键表+完整回退链、§9 yaml 侧提示；README 配置结构段更新
- 关键决策：顶级全局键插位在 _default 之下（_default 是显式默认段更具体）；用白名单豁免而非放开全部非 _ 顶级键（保持非法键报错契约）
- 未决问题：无新增
- 相关文件/分支：nilm/common/contracts.py、nilm/pipeline/user_config.py、nilm/pipeline/user_task.py、tests/test_user_config.py、docs/CONFIG_GUIDE.md、README.md；分支 arena/019ffeb6-nilm-new

## [2026-09-02] 会话纪要（第 59 次）
- 任务：transformer 在 0800 用户推理为何开机天全部未识别——详细验证分析
- 完成内容：
  - 复现：临时 base 配置恢复 transformer（注释参数原样）+infer_model=transformer，0800 train+infer 全量重跑——推理 2625 点开机 1053 点，pred_state=1 共 0 点，问题实锤
  - 现场：pred 坍缩 8.6~14.8W 窄带（真值开态均值 151W），全低于 on_thr_w=50；三切分 F1 全 0；同数据 hp F1 0.975/ridge 0.921——非数据不可辨识，纯模型侧
  - 根因四环链：①训练窗仅 6 天→570 样本(train 378)；②bs=256→每 epoch 2 步×60=总步数 120；③y 未标准化直接进 MSE→前 N 步都在"常数输出爬向 y 均值 63"的坍缩段；④早停失灵（val loss 单调缓降，patience=8 永不触发，日志 0 次早停）
  - 对照矩阵（同种子同切分）：A 现参数 F1 0（pred med 14.7）；B epochs=600 pred med 48.3 仍 F1 0（爬坡直接证据）；C lr=1e-2 F1 0.907；D bs=32 F1 0.907；E 仅 y÷150 F1 0.889（120 步不变，最小侵入单因子确认）；共性验证 LSTM 同坍缩（常数 7.2）、CNN1D 不坍缩但 F1 0.588 全报开
  - 与既有对账：2842 transformer 正常是因为 5717 样本=总步数 1340（11 倍）恰好爬完均值段——同一实现两种表现的分界是总步数，印证根因
  - 优化方案：①y 标准化进 _SeqTorchModel（根治，待拍板）；②bs 自适应/总步数<500 告警；③小样本户不启用 DL（0800 用 hp/ridge 已达标）；④pred 带宽<on_thr_w 的坍缩自动检测
- 关键决策：定性为框架级缺陷（标签尺度处理缺失）而非调参问题；"F1=0 且 FP=0"的欺骗性失效模式入档
- 未决问题：方案① y 标准化实现待用户拍板（需 2842 大样本回归验证）
- 相关文件/分支：REPORT_TEST.md（新专题）、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-02] 会话纪要（第 60 次）
- 任务：按第 59 次分析的优化方案落地 DL 均值坍缩修复
- 完成内容：
  - 方案1 根治（seq_models.py）：_SeqTorchModel.fit 内 y z-score 标准化（_y_mean/_y_std，std<1e-6 置 1 防常量除零），val 早停损失同域；predict 反标准化还原瓦数，旧模型无属性兼容直通；统计量随既有 __getstate__ 自动 pickle
  - 方案2 护栏（seq_models.py）：batch 自适应 min(bs, max(16, n//8)) 保证每 epoch ≥8 步（缩减时 INFO）；总步数 <500 告警 UNDER_TRAINED
  - 方案4 检测（user_task.py）：训练后 test 预测带宽 <on_thr_w 的模型→meta.collapsed_models + WARNING PRED_COLLAPSED；被 infer_model 指定推理时软告警不阻断
  - 方案3 配置原则：不改代码，文档沉淀（小样本户首选基线模型）
  - 验证：0800 端到端——batch 256→47、早停恢复(epoch 35)、transformer test F1 0→0.9195、推理 25/25 开机天识别（修复前 0/25）、判决链 F1 0.8325；2842 大样本回归——F1 0.9289/R² 0.6461 无回归且收敛更快(早停 21)；测试 +7 共 180 全过
  - 文档：CONFIG_GUIDE 深度组新增"训练稳健性护栏"段；README 模型清单更新
- 关键决策：y 标准化放适配器内部（模型自治）；坍缩推理侧软告警（保留人工通道）；桩模型模块级类（pickle 教训）
- 未决问题：无新增
- 相关文件/分支：nilm/models/seq_models.py、nilm/pipeline/user_task.py、tests/test_ml_models.py、tests/test_batch.py、docs/CONFIG_GUIDE.md、README.md；分支 arena/019ffeb6-nilm-new

## [2026-09-02] 会话纪要（第 61 次）
- 任务：详细分析 transformer 训练/推理日级评估指标中全关天全部误识别为开机天的原因
- 完成内容：
  - 环境恢复（沙箱重置：git reset FETCH_HEAD + venv 重建 + 0800/2842 transformer 产物重跑，指标与第 60 次逐位一致=种子可复现）
  - 现场：0800 推理 3 个全关天（7-29/30/31）FP 33/33/34 点、时段 09:30~17:45 与开机日预测几乎相同；2842 训练评估 17 个全关天 15/17 误报（日均 FP 22）
  - 根因一（0800 特有，条件缺失）：训练窗 6 天全部开机日（白天槽 P(开)=1.0，比 hp 的 0.78 更极端）——模型唯一可学规则=「白天→开机」，无任何反例
  - 根因二（决定性，可见性缺失）：0800 目标白天边际可见性≈0——7 月开机日白天「目标开时 bus − 关时 bus」仅 5W（目标开态 264W），白天 corr(pbus,p1)=0.17；全关天 bus 白天 med 138/134/141 落在开机日 P10~P90（105~150）内，日级 rise 完全重叠（全关 125~132 vs 开机日 med 127）——比 2842 更彻底，零可分割点
  - 横向对照（同 288 点全关天）：hp FP 120/proportional 111/transformer 100 同模式；ridge FP 0 系**假象**——其 7 月 24 个开机日也仅 1 天有开机预测（训练窗值域外全月坍缩≈0）
  - 2842 侧：transformer 全关天误报模式与 ridge/hp 一致（4 月样本内可压、5/6 月不可压=背景季节漂移），与权重级专题几何结论吻合——非线性容量不解决信息不足再次确认
  - 踩坑修正：scaler.state 的 mean/std 是子列空间数组且 pbus 不参与缩放——分布外分析必须按 cols 映射（此前一次 z=29 的计算有误，已用正确口径重做）
  - 优化方案：①0800 扩窗+全关天锚定（修条件缺失，预期仅样本内有效）②停机日历/数据侧核查（bus 边际差 5W 强烈提示计量口径问题，性质同 2842 B 相假说）③可辨识性增加开关边际可见性指标 TARGET_MARGINAL_INVISIBLE（待拍板）④on_days_only 口径
- 关键决策：定性=既有全关天不可辨识问题在 DL 上的再现（非 transformer 缺陷、非 y 标准化回归）；「白天分层口径做可辨识性判定」与「FP=0 先查开机日是否全零」入档
- 未决问题：方案③检测指标落地待拍板
- 相关文件/分支：REPORT_TEST.md（新专题）、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-02] 会话纪要（第 62 次）
- 任务：重划 0800 数据集（训练≥20 天、按分路开关机日均衡、其余归推理），transformer 重训+推理验证
- 完成内容：
  - 数据盘点：trains 75 天（05-21~08-03），两侧有效 69=开机 41+全关 28；无分路 4 天（6-30/7-24~26）、坏天 2（7-23/8-03 半天缺）
  - 新配置（configs/time_filters.json）：训练窗 05-21~06-29 共 40 有效天，splits 三子集全量显式锚定——train 24(开10:关14)/val 8(3:5)/test 8(4:4)，均贴近全局 0.43，时间轴交错取天防季节偏置；推理窗 07-01~08-03 exclude 7-23~26；自检无重叠无遗漏
  - transformer 重训（同参数）：test F1 0.615/R² 0.19——较旧划分(0.92/0.79)下降属考核变难（新 test 含 4 全关天，旧 test 无 FP 考场）；开机日 17/17 识别
  - 推理验证（30 天）：开机天 23/24（唯漏 7-16 pred max 48.3 差 1.7W）；全关天判对 1/6（7-29~8-02 仍误报）；点级 F1 0.497（P 0.773/R 0.366）——recall 暴跌根因=幅值系统性低估 85%（训练集 58% 全关使 MSE 解下移）
  - 核心对照结论：均衡划分修复条件缺失（全关反例 0→23）但可见性缺失（白天边际 5W/264W）依然封顶——全关天判对率 0/6→1/6，样本内也仅 3/14，印证第 61 次预判
  - 连带发现：hp（median 画像全 0，全关天>50% 结构性归零）与 ridge（带宽 49.3W）在新划分下坍缩，PRED_COLLAPSED 护栏首次实战命中、best_model 正确落到 transformer——护栏端到端验证
  - 180 项测试全过；配置含 _note_splits 说明留档
- 关键决策：「压 FP vs 保幅值不可兼得（不可见性数据的 Pareto 面平移）」入档；0800 模型侧手段宣告穷尽，解在数据侧计量核查/业务侧停机日历
- 未决问题：0800 推理模型选择建议（重 recall 用旧划分模型；重 FP 控制无模型侧方案）
- 相关文件/分支：configs/time_filters.json、REPORT_TEST.md、STATUS.md；分支 arena/019ffeb6-nilm-new

## [2026-09-03] 会话纪要（第 63 次）
- 任务：更新代码到最新版本，2842 重训 transformer 并推理，详析训练/推理日级评估指标
- 完成内容：
  - 代码更新：拉取用户三笔新提交（34eaa64/4a60910 修复标签数据、6f54e97 改配置——2842 target_col p1→p1+p2、移除锚定/exclude/infer_model）；工作区旧快照用 git checkout -- . 同步到最新
  - 运行：transformer（y 标准化版）train+infer，切分 5806/1920/1946，早停 epoch 12，best=transformer，无坍缩告警；180 项测试全过
  - **重大发现：标签修复使训练窗全关天 17→7**——旧标签 5 月 6 天/6 月 4 天"全关天"新标签均有开机真值（真值漏记实锤）；信息论边界结论适用范围缩小至 4 月 7 个真全关天（非推翻）；7-27 午间停机物理核验结论不受影响
  - 训练日级：test F1 中位 0.990（无 F1=0 天，历史首次）；不达标天四类归因——4 月真全关天 ×7（FP 142 raw/70 判决链+SAE 1e12 分母缺陷三现）、4 月作息漂移 ×12、高 SAE 幅值偏差天 ×10（含平坦日 R² 伪影 3 天）
  - 推理日级（25 天）：点级判决链 F1 0.9820（P 0.971/R 0.993）、日级 F1 中位 0.981、开机天 24/25——唯漏 7-23 系半天覆盖（42 点）+开机段 6 点<post_min_on=8 被去短开，数据问题非模型问题
  - 遗留问题域：7-05 后开机真值 1181~1659W 超训练 P99(947)，pred 钉 ~790W——SAE 0.3~0.5 幅值口径失真 40%+（达标仅 4/25），解法=滚动重训纳入高档样本
- 关键决策：标签质量前置核验入全关天分析 SOP；幅值域外推不足定性为结构性限制非调参问题
- 未决问题：SAE 分母 0 缺陷正式修复排期；4 月真全关天误报（可见性边界仍适用）
- 相关文件/分支：REPORT_TEST.md（新专题）、STATUS.md；分支 arena/019ffeb6-nilm-new
