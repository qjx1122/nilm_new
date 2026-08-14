# STATUS.md

## 当前目标
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
1. ~~M2 多模型：GBDT/Seq2Point/LSTM~~ 已落地 8 模型对比；稀疏用户 789 仍无正 r2 模型（数据侧问题为主），844 test 切分过小——需数据扩充或切分配置调整
2. DL 模型调参/加速：大用户 transformer CPU 训练约 25 min，可下调 epochs/d_model 或引入 GPU；DL 当前未超越树模型，数据量增大后复评
3. 指南附件缺失项（日级指标字段清单/启动段契约）待补充
4. 总线真实文件均带 -1 后缀（非合并对象），何时切换严格格式取决于上游导出约定
5. 部分文件缺 data9/45/81/37/44（三相电压与 B 相电流/PF）：置 0 保证流程可用，但电压特征实际无信息；可评估是否向采集侧补齐这些点位

## 决策记录 / 踩坑
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
