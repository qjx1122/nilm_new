# STATUS.md

## 当前目标
- ✅ 已完成：time_filters.json 顶级全局 infer_model 配置——配置后所有用户默认用它推理，可被 _default/用户级覆盖，未配置走原逻辑（172 项测试全过，778 端到端三场景验证）
- ✅ 已完成：2842 全关天 ridge 误识别权重级再解剖——根因=病态抵消结构等效「常数背景差分×2.48」（R²=0.90），背景季节漂移沿斜率放大成 120~385W 误报；样本内不可修复（6月全关 rise 351 vs 4月开机 rise 237 几何重叠）；15 项方案矩阵全灭；新增建议=日级 rise 三段风险标记（待拍板）
- ✅ 已完成：7-27 14:30/14:45 pred<50 但 pred_state=1 机制解释——post_fill_short_off=3 短关断填充（2 点关断被两长开态段夹住整段回填）；数值复现全区间 0 不一致，非缺陷
- ✅ 已完成：2842 on_thr_w=50 重跑——ridge raw F1 0.760→0.959（口径统一消化小值 FP）、推理 0.986/日级 23/24；7-27 复审+CSV 检查 10/10 无问题
- ✅ 已完成：2842 重跑指标全量展示 + 7-27 inference_result.csv 审查——13 项契约检查全过、物理核验自洽、口径对账相等，数据无问题
- ✅ 已完成：7-27 推理结果专项审查——无系统性错误；物理核验（k̂=8 跳变吻合）推翻此前'真值漏记'怀疑，中午 1.5h 停机真实（段内停机不可辨识=全关天问题局部版）
- ✅ 已完成：metrics_daily 三切分意义阐明 + 坏天全量归因（五类：全关天/4-5月作息漂移/夜间小值/低档日/平坦日伪影）——train F1 低于 test 系成分差异非缺陷
- ✅ 已完成：锚定后指标差三因分解（低档日零覆盖/平坦日R²伪影/成分效应）+低档日补锚修复——hp R² 0.410→0.598、SAE 0.211→0.029，ridge F1 0.954
- ✅ 已完成：hp 全关天方案 B 落地——4 全关天 splits 锚入 train（hp test F1 0.859→0.944/FP 184→0；推理 F1 0.934→0.980）；新增用户级 infer_model 字段锁定 ridge
- ✅ 已完成：hp 全关天误报深层原因（条件缺失本质，FP 100% 来自全关天）+ 条件画像 pbus_bins 落地（778 F1 0.9851→0.9925）；训练集排除全关天方案证伪
- ✅ 已完成：全关天识别终版分析——背景与目标同作息（形状相关 0.999、9 点假沿同量级）+信息论边界（信号 92 vs 背景日间漂移 σ49）；五组算法方案量化失败，路径收敛 A 停机日历/B 补计量/C 口径
- ✅ 已完成：无 CT/PT 下正比关系验证测试（2842 五组实验）——k̂≈8 三途径自洽/功率平衡 0 违反/月度稳定；关键洞察：归一化模型对变比免疫，k̂ 换算不解全关天误报
- ✅ 已完成：全关天根因结论更正（用户纠错）——Δp1/Δpbus≈8 是 CT/PT 变比非'未计入总线'；检测改尺度不变边沿信噪比口径（2842 snr 2.85 正确判可辨识）
- ✅ 已完成：全关天误报根因终判——目标设备功率未计入总线（可见性比值 0.12，疑挂缺失计量的 B 相）；可辨识性新增 TARGET_NOT_VISIBLE_ON_BUS 自动检测
- ✅ 已完成：预测结果增加状态判定阈值列（on_thr_w/decision_thr_w）+ 日级指标 state_thr_w 自描述；训练 321 行/推理 24 天逐日对账 0 不一致
- ✅ 已完成：压 FP 调参后 2842 复验——ridge 判决链口径日级达标 15/21（中位 0.980）；raw 口径 B/C 类已被判决链治理，剩余=4 全关天+2 FN 天
- ✅ 已完成：FP 过多模型侧治理——ridge 加权岭（off_weight=5，FP -23%）+ history_profile 中位画像（FP -83%）；顺带修复 unseen 槽位误回退缺陷
- ✅ 已完成：train_predictions vs metrics_daily 混淆计数差异审计——同源同预测、判决链不同（值列同阈值重判可逐值对账，无缺陷）
- ✅ 已完成：train_predictions.csv 增加 target_state（真实状态）与 pred_state_<model>（预测状态，生产判决链口径）
- ✅ 已完成：proportional F1 全 0 根因修复（pbus 移出缩放列）——test MAE 173.7/F1 0.596 复活，其他模型无回归
- ✅ 已完成：训练阶段预测结果落盘 predictions/train_predictions.csv（timestamp/split/真实值+各模型预测列）
- ✅ 已完成：三份评估产物口径差异审计——主因设计口径不同（模型能力 vs 生产判决链）；修复 precision 空真不一致；增加 raw 对照行跨产物对账
- ✅ 已完成：2842 优化方案 1+2 落地——decision_thr_w 配置字段+state_strategy_metrics.csv 两口径产出；推理 F1 0.908→0.981，开机日口径 0.982 达标
- ✅ 已完成：min_score 70 + 2842 排除不达标天重验——推理 F1=0 天消失；开机日口径+后处理 F1 可达 0.982（>0.95）
- ✅ 已完成：CONFIG_GUIDE.md 更新至 v2.0（yaml 近期变更同步 + 用户 JSON 全字段说明 + 字段生效位置速查）
- ✅ 已完成：清洗后统计改双达标口径（总/全关/训练/验证/测试天数）+ 双达标天每天明细表（全关日/阈值/所属数据集）+ 切分覆盖性建议
- ✅ 已完成：质量报告双达标天数统计 + 逐天质量表（总线/分路得分/阈值/合格列）+ 训练划分与模型建议（daily_quality.csv/quality_advice.json）
- ✅ 已完成：无效分路通道语义落地（非目标通道清洗后丢弃，branch 报告=有效通道口径）+ 2842 复验 + F1 不达标 19 天四类归因
- ✅ 已完成：2842 三模型 F1 不达标详析（FP 唯一瓶颈；点级手段上限~0.87；4 个全关天为可辨识性下界）+ 优化方案
- ✅ 已完成：拉取最新数据（60a738f 更新 2842/2844 至 2026-08-03），models 只保留 3 基线（其余注释），2842 验证测试全通
- ✅ 已完成：清洗插值泄漏缺陷修复（interpolate(limit=N) 部分填充长缺口→整段游程决策），2842 用户 6-13 全天缺失天归位，全 5 户重跑更新质量报告
- ✅ 已完成：质量报告统计数据正确性复核（独立重算全部统计量，0 不一致；两口径疑点确认为设计）
- ✅ 已完成：日级无效天剔除（全天缺失/缺失率超阈值不参与训练与评估）+ 质量报告实际天数统计 + 全关天剔除全天缺失天
- ✅ 已完成：分路通道范围审计与修正——开机分析/质量门禁改为只针对配置目标分路（branch_target 子表报告）
- ✅ 已完成：质量报告切分级统计（train/val/test 各自总天数/全关天）+ 推理阶段质量报告（有分路数据时，与训练同构）
- ✅ 已完成：数据质量报告增加清洗后数据统计（总天数/全关天数量/全关天日期清单，按 on_thr_w 口径）
- ✅ 已完成：逐用户×逐模型日级指标详析 + F1 不达标日形态学归因（发现 proportional×Scaler 工程缺陷）
- ✅ 已完成：7 模型批量验证（transformer 临时注释）+ 日级指标达标分析（SAE<0.2 & F1>0.9，scripts/analyze_daily_metrics.py）
- ✅ 已完成：配置说明文档 docs/CONFIG_GUIDE.md（default.yaml 逐项详解 + 8 模型训练效率因素）
- ✅ 已完成：GPU 自动检测（device=auto：CUDA→MPS→CPU）+ 训练/推理前分路开机情况分析（branch_sessions.csv）
- ✅ 已完成：M2 多模型——新增 random_forest / xgboost / lstm / cnn1d / transformer 五个模型（8 模型对比全通）
- ✅ 已完成：训练三阶段（train/val/test）指标 + 每模型日级指标 CSV；推理结果增加状态真值/开态概率 + 日级指标 CSV
- ✅ 已完成：清洗后数据落盘 CSV 功能（cleaned/{bus,branch}_cleaned.csv，配置可关）
- ✅ 已完成：批量/单用户执行增加强制重新训练推理功能（--force，忽略 _DONE 重跑）
- ✅ 已完成：分类指标输出增加混淆矩阵计数 TP/FP/FN/TN（tp/fp/fn/tn 指标注册 + 配置默认开启）
- ✅ 已完成：全部 5 用户重新运行验证测试（train+infer 10/10 OK，96 项测试全过）
- ✅ 已完成：模型评估增加状态分类指标（F1/Accuracy/Precision/Recall，按 on_thr_w 二值化）
- ✅ 已完成：用户 800080252842 更新数据验证（ridge R² 0.835）
- ✅ 已完成：docs/ 技术方案文档更新至 v1.2 并存档
- ✅ 已完成：5 用户真实数据批量执行验证测试（train+infer 全通）

## 已完成
- [x] 日级无效天剔除（2026-08-17）：validator 新增 `invalid_data_days`（有效点按功率列判定；全天缺失或日缺失率> `quality.max_daily_missing_rate`〔新配置，默认 0.9〕即无效）；train 侧在时间过滤前对 bus_al 与 branch_al[target_cols] 取并集剔除整天（不参与训练与三阶段/日级评估）；infer 侧离线评估同口径剔除；两侧均落盘 excluded_days.json
- [x] 质量报告实际天数：cleaned_stats 扩展为 total_days/**actual_days**（=总天数−全天缺失天）/missing_days/missing_dates/all_off_days/all_off_dates；全关天只在实际天中判定（全天缺失天不再计入全关天）；HTML 统计表增「实际天数/全天缺失天」列 + 全天缺失日期清单段
- [x] 全天缺失天审计：branch_sessions（dropna 后分组，全 NaN 天不产生行✅）、evaluate_daily（上游 drop_invalid_rows/dropna 已剔 NaN 样本✅）、identifiability（内部 dropna✅）、质量四项指标（missing_rate/coverage 有意度量缺失，保留✅）——唯一漏洞是旧全关天统计把全 NaN 天当 0 功率计入（fillna(0)），已修
- [x] 验证（2026-08-17）：146 项测试全过（新增 4 项：missing 天不计全关/invalid_data_days 阈值/HTML 实际天数渲染/端到端剔除）；真实数据 5 户——842 train 剔 7 天（含此前 F1 归因发现的 6-20/6-22"全天仅 2 点"可疑天）、infer 剔 7 天且日级指标不再含这些天；778/789/800 infer 各剔 3 天（7-24/25/26）；844 剔 76 天后 INSUFFICIENT_TIME_RANGE——数据侧真实问题暴露（p2 有 49 个 0 点天 + 27 天 bus/branch 不重叠），非误判
- [x] 分路通道范围审计（2026-08-17）：核心建模链路（目标/训练/指标/推理评估）已严格限定 target_col；发现两处整表口径——branch_sessions 开机分析（全部 pN）与质量门禁 assert_quality（整表缺失率，非目标分路缺失可误杀任务）
- [x] 修正（经用户确认）：①开机分析限定目标分路（analyze_branch_sessions 传 columns=target_cols，train/infer 两侧）；②新增 branch_target 目标子表质量报告（kind=branch_target，含 cleaned_stats）——门禁改按目标子表判定，整表 branch 报告保留作全景参考；split_stats 迁移至 branch_target（口径归位）；infer 侧同构（target_quality + split_stats.infer 挂目标子表）
- [x] 验证（2026-08-17）：142 项测试全过（端到端断言更新：sessions 分路⊆target_cols、branch_target·train/test/infer HTML 渲染）；真实数据 789 户（4 分路配 p1+p2）——sessions 只含 p1/p2；整表全关 0 天 vs 目标子表全关 11 天，两口径差异直观证明区分价值
- [x] 质量报告切分级统计：validator 新增 `series_daily_stats`（单序列口径同 cleaned_daily_stats）+ 抽出 `_daily_stats_from_pmax` 共用内核；train 质量报告 branch 段附 `split_stats`（train/val/test 各自总天数/全关天/清单，目标功率口径，切分后重写 HTML）；HTML 渲染「数据集·切分」行与逐切分全关天清单
- [x] 推理阶段质量报告：有分路数据时产出 data_quality_report.html（bus15+branch 四项指标+清洗后统计，与训练同构；只报告不设门禁）；离线评估段附 split_stats.infer（评估交集口径）；infer meta.json 新增 quality 键
- [x] 验证（2026-08-17）：142 项测试全过（新增 2 项+批量端到端断言扩展）；真实数据 800 户——train 切分级 4/1/1 天全关 0；infer 评估段 28 天全关 3 天（7-29/30/31）**与此前 F1 归因的误报日完全吻合**（质量报告直接暴露该类问题）
- [x] 质量报告清洗后统计：validator 新增 `cleaned_daily_stats`（行级最大功率按天聚合，日峰值 < on_thr_w 判全关天）；quality_report 增加可选 on_thr_w 参数附加 cleaned_stats（不传保持旧行为向后兼容）；HTML 报告新增「清洗后数据统计」表 + 逐数据集全关天日期清单段；pipeline train 侧 bus/branch 质量报告均传入用户 on_thr_w（meta.json quality 键同步携带）
- [x] 验证（2026-08-17）：140 项测试全过（新增 6 项：计数与日期清单/阈值口径/pf 列不计功率/空表与无功率列/quality_report 嵌入与兼容/HTML 渲染与不渲染）；真实数据 800 户实测——bus 71 天全关 4 天、branch 71 天全关 18 天（与 branch_sessions 分析交叉吻合），HTML 统计段渲染正常
- [x] 需求文档获取：用户推送（c8cb164），docs/多数据源用户数据批量合并脚本-功能需求文档.pdf，全文解析
- [x] 核心实现 `nilm/data_io/merge.py`：两级串行合并（§3）——阶段一单源内逐用户逐通道迭代两两合并（§4.1）、阶段二跨源同用户同通道复用同规则（§4.2）
- [x] 约束全落地（§5）：合并唯一性（终端号+用户号+通道号，复用 contracts.RE_BUS 正则，不放宽）；时间闭区间重叠即终止该用户通道批次、告警跳过整组、不强制合并不覆盖；先内源后跨源串行；新文件沿用原命名仅更新起止时间；输出复刻「数据源/用户目录」层级
- [x] 输入输出契约（§6）：--sources 多源必填；--output-root/--log-dir/--no-keep-original 可选；产物含结构化合并 CSV、运行日志（区分内源/跨源）、告警日志（数据源路径/用户目录/文件名/冲突区间四要素）、merge_report.json
- [x] 原始数据只读保障（指南 §13）：合并全程只读源目录，有专项测试验证源文件清单不变
- [x] 测试：6 项合并专项（发现/重叠判定/两级端到端/跨源成功路径/只读/keep 选项）；全量 71 项通过（含解耦守卫）
- [x] 真实数据实测：data/trains+data/infers 两源 → 内源 10 组 OK；5 个跨源组全部检出时间完全重合 → SKIPPED_OVERLAP 告警跳过（重叠保护在真实数据上生效）

- [x] 严格文件名格式修订：新增 `contracts.RE_MERGE_FILE`（无后缀严格式）+ `parse_merge_filename`；discover 只认严格格式，带后缀文件告警跳过；与指南 RE_BUS（允许后缀）两个契约并存、互不放宽
- [x] 验证：73 项测试全过（新增严格格式契约测试 + 后缀文件排除端到端测试）；真实数据实测——现有 10 个总线文件均带 `-1` 后缀，全部被正确拒绝并告警，0 合并产物

- [x] 单源独有用户透传：阶段二中仅存在于单一数据源的用户（其余源无该用户目录），其文件原名原样直接进入合并后用户数据目录（action=copied_single_source）；--no-keep-original 下透传同样生效（阶段一跟踪原始文件路径供阶段二使用）
- [x] 验证：75 项测试全过（新增透传端到端 + no-keep 组合 2 项）；真实数据回归——严格格式行为不变（10 个带后缀文件仍全部拒绝）

- [x] 分路格式文件合并：新增严格契约 RE_MERGE_BRANCH（`<用户号>-<起>-<止>.csv`，YYmmdd，无后缀）；BusFile 扩展 kind（bus/branch）——bus 按「用户+通道」分组、branch 按「用户」分组（无通道维度）；两类共用同一套迭代合并/重叠跳过/跨源/透传逻辑与日志告警
- [x] 验证：79 项测试全过（新增 4 项：分路严格格式契约、分路两级合并端到端、分路单源透传、分路带后缀拒绝）；真实数据实测——5 用户分路文件均为严格格式成为合并目标（内源 single_kept），trains/infers 同名同区间跨源组正确判重叠 SKIPPED_OVERLAP×5；带后缀总线文件仍全部拒绝

- [x] 官方点位表落地：bus_field_map 更新为官方映射（ua→data9、ub→data45、uc→data81、ia→data1、ib→data37、ic→data73、pa→data7、pb→data43、pc→data79、pfa→data8、pfb→data44、pfc→data80），含单位与倍率
- [x] 缺列置 0 规则（用户规则）：文件中找不到映射列 → WARNING 日志 + 该列置 0 + 报告标记 MISSING_COLUMN_ZERO_FILLED（非致命，不阻塞任务）
- [x] PF 重算兜底：电压置 0 无法 P/(U·I) 重算时回退文件 PF 均值，仍无数据置 0（避免 NaN 吞样本）
- [x] 验证：83 项测试全过（新增 4 项：缺列置 0、全列无置 0、哨兵+缺列组合、PF 回退）；真实数据复验 10/10 OK——置 0 字段 ua,ub,uc,ib,pfb 正确告警；800 用户 ridge r2 0.716→0.762（官方 pa/pb/pc 优于旧 ptotal 猜测）

- [x] 倍率规则落地（官方确认）：bus_field_map 全部字段 multiplier 0.001（实际值 = 原始/1000），PF 原始 916→0.916 归一无量纲；加载器 multiplier 机制零代码改动（配置驱动生效）
- [x] 验证：84 项测试全过（新增倍率应用测试）；真实数据复验 10/10 OK——bus 质量分升至 98.7–100（缩放前 PF 原始值越界计为异常，缩放后消除）；模型指标不变（均匀缩放对 z-score 归一后的模型近似不变，符合预期）

- [x] GPU 自动检测：`seq_models.resolve_device`——device 默认 auto（CUDA 可用→cuda 并打日志显卡名；其次 Apple MPS；否则 cpu）；显式 cuda 但不可用时回退 cpu 并告警；predict 每次独立解析设备（GPU 机器训练的模型可在 CPU 机器加载推理，net.to(device) 迁移）
- [x] 分路开机情况分析：新增 `nilm/analysis/branch_sessions.py`（analyze_branch_sessions）——逐分路逐天按 on_thr_w 切开机段，每段输出起止时间/时长(min)/最小/平均/峰值功率(W)/电量(kWh)/state=1；整天无开机输出整天一行 state=0（统计整天数据）；采样间隔从时间戳中位差推断（5/15min 兼容）；train 与 infer（有分路文件时）均在建模前执行，落盘 branch_sessions.csv
- [x] 验证（2026-08-14）：134 项测试全过（新增 10 项：单段统计手算/多段切分/整天关机行/跨午夜按天切/5min 间隔推断/NaN 剔除/设备解析与回退/默认 auto 等）；真实数据 800 用户实测——train 3 分路×71 天（开机段 118、全关天 129）、infer 3 分路×53 天，统计量一致性校验通过；GPU 检测日志正常（本机无 GPU→CPU）
- [x] M2 多模型支持：`tree_models.py` 实现 random_forest（sklearn 原生 multioutput）/ xgboost（每分路一回归器，有 val 早停）；`seq_models.py` 实现 `_SeqTorchModel` 适配器基类（滑窗构造/训练循环/早停/批推理/权重级持久化）+ lstm / cnn1d / transformer 三个时序回归；全部惰性导入依赖并注册进 MODEL_REGISTRY，configs/default.yaml models 扩为 8 项；requirements-ml.txt 增补 xgboost
- [x] 验证（2026-08-14）：124 项测试全过（新增 19 项：注册/形状与学习能力/save-load 往返/DL 种子可复现/滑窗对齐）；真实数据 --force 全量重跑 10/10 OK——树模型显著提效：778 用户 xgboost r2 0.951（原 best history_profile 0.827）、800 用户 random_forest r2 0.768（原 ridge 0.666）；DL 模型在当前小数据量下未超越树模型（预期内，窗口模型需更多数据）
- [x] 训练三阶段评估：每个模型在 train/val/test 三个切分上分别评估（原先只评 test）——`metrics_by_split.csv`（model×split 行 × 指标列）；选型口径不变（仍按 test 指标挑 best_model，metrics.json/comparison 兼容）
- [x] 日级指标落盘：新增 `evaluation.metrics.evaluate_daily`（按自然日分组评估，date/n_points/各指标宏平均）——训练 `metrics_daily.csv`（model×split×date）、推理 `metrics_daily.csv`（model×date，有分路真值时产出）
- [x] 推理结果扩列：`inference_result.csv` 契约扩为 timestamp,user_id,target,**target_state**,pred,pred_state,**pred_prob**——target_state=分路真值按 on_thr_w 二值化（无真值为空，Int64 可空）；pred_prob=以 on_thr_w 为中心的 sigmoid 伪概率（阈值处 0.5，决策边界与 pred_state 判据一致），`postprocess.state.state_probability`
- [x] 验证（2026-08-14）：105 项测试全过（新增 5 项：三阶段+日级 CSV 端到端、推理状态/概率语义、evaluate_daily 分组一致性、index 不匹配报错、state_probability 语义）；真实数据 --force 全量重跑 10/10 OK——842 用户抽查：三阶段汇总 9 行（3 模型×3 阶段）、训练日级 306 行、推理日级决策边界/真值状态一致性全部通过
- [x] 清洗后数据落盘：user_task 新增 `_save_cleaned_csv`——train 保存 bus+branch、infer 保存 bus（+离线评估侧 branch）到运行目录 `cleaned/{bus,branch}_cleaned.csv`（时间索引列名 timestamp，UTF-8）；配置开关 `preprocess.save_cleaned_csv`（默认 true）；只写 outputs/ 不触碰原始数据（§13 只读）
- [x] 验证（2026-08-14）：100 项测试全过（新增 2 项：产物存在性+清洗语义抽查【功率非负/时间戳唯一】、配置关闭不产出）；真实数据 --force 全量重跑 10/10 OK——5 用户 train+infer 共 20 个 cleaned CSV 全部产出，单用户 train cleaned 约 4.3MB
- [x] 强制重跑功能（--force）：`run_batch(force=...)` + CLI `--force`——忽略已完成产物（_DONE）重新训练/推理，优先级高于断点续跑（resume）；产物写入新时间戳目录不覆盖历史；`_new_outdir` 同秒冲突追加序号保证唯一；可与 --user-key/--stage 任意组合
- [x] 验证（2026-08-14）：98 项测试全过（新增 2 项：force 忽略 _DONE 重跑且产物目录 +1、force 优先于 resume）；真实数据三轮验证——无 force 二跑 SKIPPED_RESUME×10、单用户 --force OK×2、全量 --force OK×10
- [x] 混淆矩阵计数指标（TP/FP/FN/TN）：`nilm/evaluation/metrics.py` 注册 tp/fp/fn/tn 四个计数指标（与 f1 等共用 `_confusion` + on_thr_w 二值化口径；per_branch=各分路计数，macro=跨分路总数）；`configs/default.yaml` metrics 列表默认追加；`compare.py` 新增 COUNT_METRICS——计数为诊断输出不参与最优模型排序（否则「FP 越小越好」会把全预测关态的退化模型评为最优）
- [x] 验证（2026-08-14）：96 项测试全过（新增 4 项：单分路手算计数、多分路求和与四类计数守恒 TP+FP+FN+TN=N、阈值透传、排序豁免）；全 5 用户 train+infer 重跑 10/10 OK——TP+FP+FN+TN 与 test 样本数逐户守恒（1622/2/670/96/96）；metrics.json、comparison.csv/md、offline_metrics.json 均带四类计数
- [x] docs/ 文档更新存档：TECH_DESIGN v1.0→v1.2（版本头+修订记录），新增 §12 实施存档五节——12.1 真实数据 M0 摸底（哨兵值/对齐与覆盖率与可辨识性口径修正）、12.2 官方点位映射+倍率+缺列置 0+PF 兜底链、12.3 合并脚本全功能（两类严格格式/两级合并/重叠保护/单源透传/输出契约）、12.4 当前验证基线（5 用户指标表+10/10 OK+84 项测试）、12.5 遗留事项转 TODO

## 进行中
- 无

## 下一步（TODO）
0. ~~【高优先】修复 proportional 基线缺陷~~ ✅ 已修复（2026-08-18，pbus 入 NON_SCALED_COLS）；遗留：全量 5 户重跑刷新 best_model：pbus 被 z-score 标准化后 clip(0) 致预测恒≈0（TP 全期=0）——倾向方案：pbus 移出 scale_cols（slot 先例）；修复后全量重跑并复核历史 best_model 结论（844/789 的 proportional "胜出"是退化假象）
1. ~~M2 多模型：GBDT/Seq2Point/LSTM~~ 已落地 8 模型对比；稀疏用户 789 仍无正 r2 模型（数据侧问题为主），844 test 切分过小——需数据扩充或切分配置调整
2. DL 模型调参/加速：大用户 transformer CPU 训练约 25 min，可下调 epochs/d_model 或引入 GPU；DL 当前未超越树模型，数据量增大后复评
3. 指南附件缺失项（日级指标字段清单/启动段契约）待补充
4. 总线真实文件均带 -1 后缀（非合并对象），何时切换严格格式取决于上游导出约定
5. 部分文件缺 data9/45/81/37/44（三相电压与 B 相电流/PF）：置 0 保证流程可用，但电压特征实际无信息；可评估是否向采集侧补齐这些点位

## 决策记录 / 踩坑
- 顶级全局 infer_model 的层级插位（2026-09-02）：放在硬编码默认之上、_default 之下——顶级键是"文件级全局"，_default 是"显式默认段"，语义上后者更具体应更优先；实现为 resolve_user_config 的 global 合并层（GLOBAL_CONFIG_KEYS 白名单）+ list_user_keys 跳过，非 _ 前缀顶级键从"必须是 user_key 否则报错"改为白名单豁免，其余非法键行为不变
- ridge 全关天误报机制（2026-09-01，权重级归因方法论）：线性模型解释顺序=①W 排名与 L2（发现千级抵消结构：pa −2100/pc +1457/ia +1399，L2=3324 vs 目标~700）→②场景分组贡献分解（日历族常数 366~419=未用季节补偿）→③对误报量回归解释变量（pred≈2.48×rise−582，R²=0.90，零点=4月背景）；结论：ridge 等效「常数背景差分」，背景漂移沿泄漏斜率放大。**锚定/off_weight 对 ridge 无效的原因是几何而非调参**：6月全关 rise(351/370)＞4月开机 rise 最低(237)，线性决策面无解；GBDT 样本内可记住但样本外误开 0.61——容量不解决信息不足
- 离线复现流水线训练链路的对账要点（2026-09-01）：user_task 的 apply_constraints 是 sum_consistency=False（只裁负值）——复现时误开总和一致性会得到系统性偏差（单分路时=把 pred 缩放成 pbus）；CSV 落盘精度 5e-4 属正常
- pred 与 pred_state 「不一致」排查顺序（2026-09-01）：① 确认 decision_thr_w；② 重放 postprocess_state 三步（阈值→enforce_min_on→fill_short_off）定位翻转步骤；③ 对照 pred_prob（点级证据，填充前）与 pred_state（段级判决，填充后）——7-27 14:30/14:45 即 fill_short_off=3 回填两侧被长开态段夹住的 2 点短关断，设计行为非缺陷；下游可用 `pred_state==1 且 pred<decision_thr_w` 识别回填点
- on_thr_w 10→50（2026-09-01，2842）：真值无 [10,50) 灰区（0 翻转，业务语义无损），变化实质=分类指标口径与判决阈值对齐——raw/判决链两口径从此几乎重合（0.9591/0.9493），口径分裂主源消除；注意历史 10W 口径指标不可直接对比
- 7-27 审查方法论（2026-09-01）：单日结果审查三步——误差逐点定位（FP/FN 时段分布）→总线-分路交叉（同步跳变）→变比换算物理核验（k̂·Δpbus vs Δp1）；据此撤销 08-18 的"真值漏记"怀疑（当时仅凭表象推断）——**对数据的怀疑必须做物理一致性核验后才能下结论**
- 三切分日级指标诊断框架（2026-09-01）：train=拟合诊断/val=泛化预警/test=能力结论；跨切分比较日级中位数必须先对齐天型构成（2842 train F1 0.786<test 0.970 是 train 含 11 全关天+4月漂移段的成分差异，非泄漏）
- 坏天五类归因模式沉淀：全关天 F1=0（口径内）、季节作息漂移 FP（4月日均开机 3.8h vs 夏季 12h，模型按夏季模式预测）、夜间小值 FP（判决链已消）、低档日（幅值外推差）、平坦日 R² 伪影（std<30W 时 R² 无意义，看 F1/MAE）
- 指标解读三因分解方法论（2026-09-01）：指标"变差"先分解=真问题（低档日零覆盖：设备 200W/720W 双档，6 低档日全在 test）+统计伪影（平坦日 SS_tot≈0 使日级 R²=-57，同日 F1 0.947 完好）+成分效应（全关天移出 test 后 R² 分母缩小，同口径重算无退化）——只修真问题，不被伪影带节奏
- 多档位设备切分原则：stratified_day 只按开/关分层不感知档位，档位覆盖需 splits 显式锚定（低档日清单来源=branch_sessions 日功率中位聚类）；test 保留部分低档日作外推检验
- 全关天数据集方案定型（2026-09-01）：splits.train.include 锚定不可辨识全关天入 train——比纯口径排除（on_days_only）更优：样本被利用（train 停机日 7→11 使推理 F1 0.934→0.980）且 test 聚焦可辨识场景；条件画像切点 Pareto 扫描确认无改进点（FP/FN 严格此消彼长），2842 不启用 pbus_bins
- 【踩坑】切分变更连带 best_model 被 proportional 带偏（test 无全关天后其退化特性 recall=1/sae 低胜出 3 项）——新增用户级 infer_model 字段（优先级用户级>全局>best_model）锁定；根因是 wins 计数选型对退化模型不鲁棒，待后续改进
- hp 深层原因定性（2026-08-20）：「条件缺失」结构性缺陷——无条件画像回答"这个时刻通常开吗"，白天槽位开态占比>0.5 时 mean/median 都必然输出开机值；FP 100% 集中于全关天。重要负结果：训练集排除全关天无效（画像更高 FP 微升）——hp 的问题不在训练数据脏，在模型没有"今天"的输入
- 条件画像设计（pbus_bins）：profile[slot, pbus分位桶] 把当天总线水平注入画像；适用性判据=总线可见性（778 生效 0.9851→0.9925；2842 受信息论边界，FP 降但 FN 大增不启用）；默认关闭保持原行为
- 全关天终版结论（2026-08-20，第三轮迭代后收敛）：①背景负荷与目标同一作息表（日曲线形状相关 0.999，全关天 9 点也有 47~110 假沿与真沿 92 同量级同时刻）——所有形态/边沿类特征失效的统一解释；②信息论边界：背景日间漂移 σ=49 vs 信号 92，跨天比较特征理论上限 ~50% 重叠；③五组方案（双沿匹配滤波/背景残差/自门控/三特征投票/电流通道）全部量化失败——算法侧正式宣告无解，不再投入
- 分析方法论沉淀：点级 SNR 与关键窗口 SNR 必须分开算（全天平稳段背景 vs 事件同时刻背景）——SNR 3.18 与日级不可分的表面矛盾由此调和
- 正比关系验证方法论（2026-08-20）：无 CT/PT 时可做①隐含变比多途径反推（沿跳变/停机沿/大幅变化点比值——必须避开全点回归的衰减偏差：小幅变化时 Δpbus 是背景噪声，斜率被拉向 0）②功率平衡校验（p1≤k·pbus，先排除总线缺数置 0 时段再验，否则假违反）③k̂ 时间稳定性（正比性必要条件）
- 关键洞察：k̂ 线性缩放经 z-score 后模型输入不变——变比确认对归一化模型族零增益；其价值边界=精确 k 才能做功率平衡硬约束特征。全关天误报根因最终定性：目标信号/背景波动的信噪比问题（重叠 53%），与变比无关
- 【重大教训 2026-08-20】CT/PT 变比误判：把 Δtarget/Δpbus≈8 解读为"目标未计入总线"（实为变比），连锁推出"B 相挂接"等错误结论——**总线与分路量纲不同源（变比未确认），任何跨源绝对幅值比较都无效**；涉及总线幅值的分析必须先做变比确认或改用尺度不变口径（比值/信噪比/相关性）
- 检测口径修正：TARGET_NOT_VISIBLE_ON_BUS（绝对比值，作废）→ TARGET_EDGE_BURIED_IN_BUS（边沿 SNR=沿跳变/背景跳变 P90，总线自身单位内比较）；附 implied_bus_scale 输出（2842≈7.5~8.0）供向采集侧核对 CT/PT
- 全关天问题回归"背景掩盖"定性：信号存在（SNR 3.18）但被背景负荷波动掩盖——变比确认后幅值特征恢复物理意义，算法侧方案（如换算后的功率平衡特征）可重评
- 全关天根因升级（2026-08-20）：从「总线无停机信号」升级为物理根因「目标设备功率未计入总线」——开机沿 723W 总线只跳 92W（比值 0.12），B 相计量全缺（data45/43）且目标疑挂 B 相；pearson 0.835 会掩盖此问题（白天形态相关≠幅值计入），必须用开机沿跳变比检测
- 三组缓解实验量化否决（日级门控/夜间基线差/早晨阶跃全不可分）：算法侧对此无解，不再投入；解决路径=数据侧补 B 相计量或停机日历特征
- 可辨识性新增 bus_visibility_ratio（开机沿≥10 时计算，<0.5 标记 TARGET_NOT_VISIBLE_ON_BUS）：把「训练后分析发现」前置为「训练前自动告警」
- 阈值自描述列设计（2026-08-19）：预测结果带 on_thr_w（真值判态）+decision_thr_w（预测判态）双列；日级/三阶段指标带 state_thr_w（分类指标判定阈值）——三份产物的阈值口径从「读文档才知道」变为「数据自带」；一致性关系：state_thr_w≡on_thr_w（分类指标真值与预测都按它判），与 decision_thr_w 不等属双口径设计（pred_state 判决链）
- FP 治理三层框架定型（2026-08-18）：模型层（加权岭 off_weight/中位画像 agg=median，压原始 FP）→判决层（decision_thr_w+post_min_on，压生产链 FP）→口径层（on_days_only，处理可辨识性下界）——alpha 正则被实验否定（不改变系统性高估方向）；proportional 保持朴素不调（sanity 基线定位）
- 【缺陷修复】history_profile unseen 槽位判定曾用 profile==0 代理：中位画像下合法 0 值被误回退 fallback（非零）——显式 _seen 数组修复；教训：布尔状态不得用数值 0 代理
- proportional 修复选方案 B（pbus 入 NON_SCALED_COLS）而非模型内反标准化：①slot 先例已存在，机制一致；②模型层保持「按列名取物理量」的简单契约，不感知 Scaler；③对缩放敏感的模型（ridge）实证无回归（f1/r2 差异 <0.001）。教训固化：基线模型按列名取物理量的列必须逐一确认不在 scale_cols
- 评估产物双口径设计定型（2026-08-18）：metrics_by_split/metrics_daily=模型能力口径（on_thr_w 判决、无后处理，选型用）；state_strategy_metrics=生产判决链口径（decision_thr_w+游程后处理，部署效果）——两者数字必然不同是设计而非缺陷；state_strategy 增加 raw_on_thr 对照行与 by_split 逐值对账（测试守卫）
- 【缺陷修复】state_strategy 的 precision 空真约定曾与 evaluation.metrics 不一致（tp+fp=0 有 FN 时记 1.0 而非 0）——教训：同一指标在多处实现时，空真/除零约定必须复用同一实现或测试对账
- decision_thr_w 与 on_thr_w 分离（2026-08-18）：决策阈值只影响预测→状态判决；真值判态/分类指标恒用 on_thr_w——保证「业务开机定义」与「模型判决点」解耦，调优判决点不改变考核口径
- 状态策略评估（state_strategy_metrics.csv）双口径并出（all_days/on_days_only）：把「口径协商」所需的两个数字固化为标准产物，需求方可直接对比选择
- min_score 10→70（2026-08-18）：低阈值形同虚设（25 分的天也放行）；70 恰好分开「半天以上有数据」与「大段缺失」两类天。2842 exclude 后指标绝对值略降（0.930→0.908）但可信度提升——原推理 F1=0 的天是半天数据的评估噪声
- F1>0.95 口径结论（2842 实证）：含全关天不可达（4 个全关天 FP 占 51%，总线无停机信号）；仅开机日口径+决策阈值 50W+min_on8 → F1 0.982 达标。口径协商是达标的必要条件，模型侧只能到 ~0.88
- 清洗后统计口径演进（2026-08-18）：bus/branch 各自统计 → 双达标天单一口径（总/全关/训练/验证/测试/推理/未使用天数）——只有双侧同时合格的天对建模有意义；旧 cleaned_stats/split_stats 保留在 JSON（write_quality_html 无 qualified_detail 时回退旧渲染，向后兼容）
- 「未使用」类别的价值：质量合格但未进任何数据集的天（时间过滤排除/特征剔除），2842 有 28 天——提示可放宽 include 范围扩样本；推理侧 115 天未使用=训练期数据在推理窗之外，语义正常
- 切分覆盖性建议自动化：全关天在 train/test 的覆盖检查（2842 训练集全关 7 天占 10%→自动提示加权 3~5 倍），把此前人工分析结论（stopped-day 学习不足）固化为规则
- 【语义修正 2026-08-18】业务确认非目标通道不在当前总线回路（无效数据）：三层口径简化为单一有效通道口径——branch_c 在清洗+目标解析后立即裁剪，branch_target 子表报告并入 branch；此前「整表全景参考」在新语义下是误导（p2/p3/p4 的"全关天"统计无业务意义）
- 语义修正不改模型结果（三阶段指标逐位一致）：目标构建本就只用 target_cols，本次修正的是产物与报告的口径一致性
- 2842 F1 分析方法论：F1 缺口先分解 P/R——R≈1 说明不是漏报问题，优化方向锁定 FP；FP 再按「全关天/边界/夜间」三源分解，逐源给对策；离线可行性实验（阈值扫描/min_on 扫描/分类器/门控）先量化收益上限再决定开发投入
- 全关天误报的本质：目标分路停机时总线（其他分路）水平与开机日重叠 62%——单用户单通道口径下不可辨识；后处理无法解决「整个白天连续误报」（游程很长，min_on 滤不掉）
- 【缺陷 2026-08-17】pandas interpolate(limit=N) 语义陷阱：limit 是「每缺口最多填 N 点」而非「只填 ≤N 的缺口」——长缺口被部分填充，全天缺失天泄漏出 max_gap_interp 个伪有效点（2842 户 6-13 被 6-12 尾部 0 值延伸出 2 个伪点）。修复：_interp_short_gaps 按缺口游程长度整段决策 + limit_area="inside" 禁首尾外推。教训：pandas 参数语义必须以实测为准，"limit" 类参数尤其易错
- 修复后 842 目标通道缺失天 1→6、全关天 16→11：此前部分被插值伪点掩盖的缺失天归位——统计变化是修正而非回归
- 质量统计复核方法论：用独立代码路径（逐天循环 vs groupby、正则匹配 vs is_power_column、手写公式）重算并逐项对比，避免「用实现验证实现」的自证循环；配合交叉一致性断言（HTML=JSON、剔除∩评估=空、全关∩缺失=空、split_stats=metrics_daily 天数）
- total_days 口径=「有数据行的天」：日历缺口天（整天无行）不计入 total/missing——缺口已由 n_days_approx（跨度）与 coverage_rate 反映，三个数字互相自洽（386−132=254 缺口天）；如需显式列出可加 gap_days（暂不加）
- excluded_days 只覆盖时间过滤窗内的无效天：窗外缺失天本就不参与训练，无需剔除（复核疑点确认为正确语义）
- invalid_data_days 有效点按功率列判定而非任意数值列：PF 兜底（回退文件均值）会把全缺失天的 pf 列填满，若按任意列判定则全缺失天被误判有效（首版实现踩坑，端到端测试暴露）——功率列口径与全关天判定一致
- 844 户剔除 76 天后训练不足属数据真实问题（p2 仅 84 个完整天、49 个全 0 点天，且 bus 仅 90 天与 branch 136 天错位）：不放宽阈值迁就，记录为数据侧待补齐；如需临时跑通可在配置中调高 max_daily_missing_rate 或缩小训练时间窗
- 全关天口径修订：旧实现 fillna(0) 把全 NaN 天当 0 功率计入全关天（虚增全关天数）；新口径全关只在实际天（有有效数据）中判定——「全关」与「无数据」是不同的业务事实
- 分路范围三层口径定型：①branch 整表报告=全景参考（该户有没有全停日）；②branch_target 子表=门禁与切分统计口径（训练标签视角）；③branch_sessions=只分析目标分路（与训练目标一致）。门禁从整表改为目标子表：消除非目标分路缺失误杀任务的隐患（审计发现，用户确认修正）
- 切分级统计用「目标功率序列」口径（splits y 值）而非整表 branch：切分是样本级概念，目标列才是训练标签；与整表 cleaned_stats（所有分路任一开机）口径不同属预期——前者回答"标签里有没有停机日"，后者回答"该户有没有全停日"
- 推理质量报告只报告不设门禁：推理数据不满足训练门禁时仍应出结果（生产语义），质量问题由报告呈现而非阻断
- infer 的 bus 质量报告用 bus15（15min 重采样后）：与训练阶段同一时间粒度，覆盖率/天数可比
- 全关天判定用「行级最大功率的日峰值 < on_thr_w」：任一相/分路瞬时开机即不算全关天（比逐列平均更严格且物理直观）；与状态判据/branch_sessions 同一二值化口径，三处结论可互相印证
- quality_report 的 on_thr_w 设为可选参数：不传不产生 cleaned_stats——保持旧调用（含测试）零破坏，向后兼容
- 【缺陷发现 2026-08-17】proportional 基线自 Scaler 引入后失效：模型用特征列 pbus×share，但 pbus 在 scale_cols 中被标准化（中位≈0），clip(0) 后预测恒≈0——F1 不达标形态学分析（A 全漏报 47 行全来自 proportional）暴露；教训：基线模型按列名取原始物理量时必须校验该列是否被縮放
- F1 不达标日六分类形态学（A 全漏报/B 全误报/C 错位/D TN=0 过度报开/E 漏报为主/F 误报为主）是高效归因工具：形态×模型交叉表 3 分钟定位三类根因（工程缺陷/阈值不匹配/停机样本缺失）
- transformer 临时注释而非删除（default.yaml 注释保留参数，恢复取消注释即可）：CPU 大户 ~25min 拖慢批跑 4 倍；恢复时机=GPU 环境或调参后
- 日级达标分析发现 SAE 口径缺陷：真值全关日 Σtrue=0，SAE=|Σpred|/(0+eps) 出现 1e13 天文数字——建议正式口径对全关日只考核 F1，或 SAE 分母加下限保护
- SAE 超标主因是训练/推理期负荷水平漂移（Σpred/Σtarget：800 低估 43%、778 低估 17%、842 低估 14%）而非开关误判（F1 多数完美）——改善方向是扩充训练时间范围，非换模型
- 设备解析放模型层（resolve_device）而非 pipeline：fit/predict 各自独立解析——模型对象可跨设备迁移（GPU 训练 CPU 推理）；auto 语义进 params 持久化，载入后仍按当前机器解析
- 采样间隔推断用 np.diff(idx.values)/timedelta64(1,'m')：新版 pandas DatetimeIndex 底层可能是 us 而非 ns，view(int64)/6e10 会算错 900 倍（踩坑：测试 duration 0.1min）——timedelta64 除法不依赖底层单位
- 开机段跨午夜按天切开（groupby normalize 后逐日游程）：口径为「每天开机情况」，跨日会话拆成两天各自的段；电量按段内 Σ(P×Δt)/1000 kWh
- 整天关机行 session_id=0 与开机段（≥1）区分；时间段取该日实际数据范围（非日历 00:00–24:00，避免数据缺失时虚报时长）
- DL 序列模型的窗口语义选 Seq2Point 逐点版（输入 [t-L+1,t] 特征、输出 t 时刻功率，头部复制首行填充）：保证 predict 输出行数=输入行数，与扁平模型对齐，评估/推理零改动；未复用 build_windows（其窗口不含头部填充，输出少 L-1 行）
- torch 局部类 Net 不可 pickle：__getstate__/__setstate__ 只序列化 state_dict，load 时经 _build_net 重建结构再载权重——BaseModel.save/load 接口不变
- DL 训练打乱用独立 np.random.default_rng(seed) 而非全局 np.random：保证同种子两次训练逐位一致（测试守卫）；float(loss) 改 float(loss.detach()) 消 torch 警告
- xgboost 早停仅在有验证集时启用（early_stopping_rounds=None 关闭）：无 val 场景（如单元测试）不炸
- 大用户 transformer CPU 训练约 25 分钟（5717 样本×L96）：当前可接受；后续可按需下调 epochs/d_model 或加 GPU
- 8 模型真实数据结论：树模型（RF/XGB）在中等数据量用户上显著优于线性/画像基线；DL 三兄弟未超越树模型——样本量不足（数千级）+ 特征已含 lag/rolling（时序信息已被扁平特征吸收），M2 后续调参或数据扩充后再评估
- 开态概率的实现选择：回归模型无原生概率输出，用以 on_thr_w 为中心的 sigmoid 伪概率（p=1/(1+exp(-4·(P−thr)/thr))）——单调、阈值处恰 0.5、与 pred_state 决策边界一致；不假称是校准概率（文档写明"伪概率"），M2 引入分类头模型后可替换为真概率
- target_state 用 pandas Int64 可空整型：无分路真值处为空而非 0（避免把"未知"当"关态"误导下游）；真值二值化与 pred 同一 on_thr_w 口径
- 三阶段评估不改选型口径：best_model 仍按 test 挑选（train 指标必然乐观、val 已用于早停/调参），train/val 指标用于过拟合诊断（如 ridge train r2 0.954 vs test 0.616 提示过拟合幅度）
- 日级指标定位为诊断而非选型：单日 96 点上 r2/sae 波动大（如某天全关时 r2=0），metrics_daily.csv 用于定位"哪几天预测差"，选型仍看整段指标
- evaluate_daily 放 evaluation 模块（不放 pipeline）：与 evaluate_all 同域复用注册表，pipeline 只负责组装与落盘
- 清洗数据落盘位置选运行时间戳目录内（cleaned/ 子目录）而非独立顶层目录：与该次运行的配置快照/质量报告同域，可追溯「这份清洗数据是哪次运行、哪套参数产出的」；每次 force 重跑各有一份，不互相覆盖
- 落盘时机选清洗后、重采样前（bus 保留原始 5min 粒度）：保证保存的是「清洗」这一步的产物本身；15min 聚合结果可由 agg_strategy.json + cleaned CSV 复现
- 默认开启（save_cleaned_csv: true）：单用户产物约 4–5MB 可接受；数据量大时配置关闭即可，测试覆盖关闭路径
- force 与 no-resume 语义区分：--no-resume 只是「本次不查 _DONE 跳过逻辑」的开关（历史语义保留），--force 是明确的「强制重跑」意图入口且优先级最高（`resume and not force`）；两者行为等价但 --force 意图清晰，README 推荐用 --force
- force 重跑不删除历史产物：写入新时间戳目录（审计可追溯）；同秒内重复运行（测试/快速重跑场景）曾因目录同名 exist_ok=True 复用旧目录——`_new_outdir` 改为冲突时追加 `_1/_2` 序号并 mkdir(exist_ok=False)，保证每次运行目录唯一
- TP/FP/FN/TN 作为「指标」注册进 METRIC_REGISTRY 而非改 evaluate_all 返回结构：零侵入——user_task/metrics.json/comparison 全链路自动带上，配置可开关；代价是 macro 语义对计数取「总和」而非平均（已在 docstring 说明）
- 计数指标必须豁免 summarize 排序：LOWER_IS_BETTER 若给 fp/fn 设 True，会把「全预测关态」的退化模型（FP=0）评为最优——用 COUNT_METRICS 集合在 summarize 中直接跳过
- 环境重置后 .venv 丢失（快照排除目录），按开局仪式重建 venv + requirements + pytest + pypdf
- 本地分支指针曾被外部操作重置到初始提交（工作区文件仍在），用 `git reset --hard FETCH_HEAD` 恢复（远程含完整历史）——教训：合并异常先 `git rev-parse HEAD` 诊断，别盲目 stash/重提交
- 合并脚本落位 `nilm/data_io/merge.py`：属于数据接入域，只依赖 common（解耦守卫通过）；CLI 在 scripts/，不入包
- 文件名解析复用指南 RE_BUS（含可选 suffix），兼容真实数据的 `-1` 后缀文件；单文件组保留原名（含后缀），多文件合并产物用标准格式（仅更新起止时间，§5 命名约束）
- 重叠即跳过「整组」（需求原文：跳过该组所有数据，不生成合并文件）——已入组的部分合并中间产物一并丢弃，输出中不出现该用户通道目录
- 合并内容时保留原始时间列名（event_time/time），不改列结构；重复时间戳去重保留先出现者并计数告警
- 跨源产物放 `cross_source/<user_key>/`，与单源结果分区，溯源清晰；单源仅一组时不做跨源动作
- 需求文档命名格式无 suffix 而真实文件带 `-1`：用户明确要求合并脚本**严格遵守无后缀格式**（如 `-260604-260611-1.csv` 不符合），已改为独立严格契约 RE_MERGE_FILE；指南 RE_BUS（流水线数据接入用）保持不变——两个契约并存。**影响**：当前 data/ 下真实总线文件全部带 `-1` 后缀，均不是合并对象；如需合并须将源文件命名为严格格式（不改动原始数据，由上游导出方或拷贝重命名解决）
- 单源独有用户透传语义：在 cross_source/<user>/ 中原名保留（不重命名、不重写内容）；报告 action=copied_single_source 与真正跨源合并 action=merged 区分；用户级缺失在通道粒度上等价实现（该用户所有通道组均为单源组）
- 分路文件合并设计：分组键 (user_key, "branch")（无通道维度，与 bus 的 (user_key, ch) 并存不冲突）；分路文件身份校验 = 文件名用户号 vs 用户目录用户号部分；合并产物命名 `<用户号>-<最早>-<最晚>.csv`（仅更新起止时间，§5 命名约束）
- 真实数据现状：分路文件恰好为严格格式（可合并），总线文件全部带 -1 后缀（不可合并）——两类行为在同一数据上同时验证通过
- venv 在单轮内也可能丢失（沙箱重启），执行测试前需检查 .venv 存在性
- 官方点位表与此前数据驱动猜测的差异：data7 官方为 pa（此前猜测为 ptotal）；pa+pb+pc 入特征后 800 用户 r2 0.716→0.762，验证官方映射更优
- 缺列置 0 的告警文案刻意避开「不存在/缺少列」字样（user_task 致命判定关键词），以 MISSING_COLUMN_ZERO_FILLED 标记区分非致命 issue
- 置 0 列的连锁影响处理：PF 重算分母 U·I=0 → NaN → 回退文件 PF 均值再置 0；Scaler 对常量列 std<eps 自动置 1，模型不受害
- 实测文件列集合差异：5 用户均缺 data9/45/81/37/44（三相电压 + ib/pfb），置 0 后电压类特征无信息量——记录为数据侧待补齐项
- /1000 倍率验证（778 用户）：ia 756→0.756A、pa 56573→56.6W、pfa 692→0.692，量级全部合理；PF 重算因电压缺失仍走「回退文件 PF」路径，缩放后文件 PF 本身已合法（0.69/0.88）
- 缩放对模型指标的影响分析：输入均匀缩放经 z-score 归一后对 ridge/树模型近似不变，842/778/800 指标与缩放前一致（r2 0.624/0.827/0.762），证明改动无回归；PF 从越界异常变为合法特征，质量分显著提升

## 关键文件路径
- `nilm/data_io/merge.py`：两级合并核心；`scripts/merge_user_data.py`：CLI 入口
- `tests/test_merge.py`：6 项合并专项测试；`docs/多数据源用户数据批量合并脚本-功能需求文档.pdf`：需求来源
- `outputs/merged/`（不入库）：实测产物——`{trains,infers}/<user_key>/` 单源结果 + `logs/{merge_run.log,merge_warnings.log,merge_report.json}`
