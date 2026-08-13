# STATUS.md

## 当前目标
- ✅ 已完成：5 用户真实数据批量执行验证测试（train+infer 全通，断点续跑/单用户模式验证通过）
- ✅ 已完成：按《工商业负荷辨识算法开发指南 V2.1》重构代码（模块解耦 + 单用户/多用户批量执行）

## 已完成
- [x] 用户推送测试数据（commit f2ccca9）：data/trains|infers 各 5 个 `<device>_<user>` 目录 + configs/time_filters.json
- [x] M0 数据摸底：双哨兵值（INT32_MIN/MAX）、稀疏用户（842: 88/288 点/天，844: 57/288）、密集用户（778/789/800: 282/288）、分路百瓦级功率、总线量纲未确认
- [x] 数据驱动字段辨识：load_iden_data7/79=总有功（与分路和相关性 0.80+）、1/2/73/74=电压类、3..6=电流类、8/80=PF 类；临时映射入 configs/default.yaml 并显式标记待点位表确认
- [x] 代码适配：哨兵值配置化处理、ptotal→pa/pb/pc 均分派生（DERIVED_EQUAL_SPLIT 显式标记）、覆盖率按真实日历跨度、质量门禁阈值校准（min_coverage 0.15/min_days 3/min_overlap 0.3）
- [x] 对齐口径修正：重叠率改为「分路标签点被总线覆盖率」（实测分支索引 100% 落在总线网格，对称 Jaccard 对稀疏总线系统性偏低）；844 用户由 FAILED 转 OK
- [x] 可辨识性误报修正：低方差判据改为目标自身 CV<5%（原绝对阈值受总线未确认量纲污染）；5 用户全部 identifiable=True（pearson 0.74–0.90）
- [x] 批量验证结果：10/10 OK；best 模型分布 ridge×2 / proportional×2 / history_profile×1；密集用户 r2 0.63–0.83（842/778/800），稀疏用户 844/789 基线表现差 → M2 需 GBDT/序列模型
- [x] 断点续跑验证：复跑 10/10 SKIPPED_RESUME；单用户模式验证通过；65 项回归测试全过

## 进行中
- 无

## 下一步（TODO）
1. **点位表确认**：向设备方索取 load_iden_dataN → 物理量映射表与 CT/PT 倍率，修订 bus_field_map（当前为临时映射，DATA_UNIT_UNKNOWN 未消）
2. **M2 多模型**：GBDT/Seq2Point/LSTM 接入（重点改善稀疏用户 844/789 与复合目标 789 的表现）
3. **稀疏数据增强**：844（57 点/天）可考虑放宽窗口连续性约束或按天聚合特征；842/844 分路覆盖率低，评估是否调整采集策略
4. 指南附件缺失项（日级指标字段清单/启动段契约）待用户补充
5. M3 对比选型：超参扫描 + 跨季节测试集 → REPORT.md

## 决策记录 / 踩坑
- 哨兵值不止 INT32_MIN：data15/26/27/30 含 INT32_MAX（2147483647），两者都须置 NaN；data88 全列为哨兵
- 总线时间戳为区间尾减 1 秒（00:04:59），分路为区间首（00:00:00）；5min→15min floor 聚合后网格天然对齐（实测交集=分路全集）
- 重叠率口径教训：对称 Jaccard |∩|/|∪| 在总线远稀疏于分路桶时系统性偏低（844 仅 16%），误杀合法数据；改用分路标签覆盖率后既通过真实数据，又保留对时钟错位的检出力（错位时覆盖率趋 0，单测覆盖）
- 覆盖率口径教训：原按行数推算天数，有缺口时恒为 1.0；改为按索引真实日历跨度计算
- 可辨识性低方差判据教训：绝对阈值（相对总线均值）在总线量纲未确认（DATA_UNIT_UNKNOWN）时失真，改为目标自身 CV；间歇负荷（多零值）CV 天然高，不误报
- 质量阈值按实测校准：max_missing_rate 0.9 / min_coverage 0.15 / min_score 10 / min_days 3（与 time_filters.json 最短训练窗 6 天相容）；原始默认（0.3/0.5/50/14）会误杀全部稀疏用户
- 842/844 的 user_id 相同（4206894986488）但 device 不同 → user_key 复合键设计正确区分了两套数据
- 844 train 窗口内分路 p2 非零点仅 2096/5760，基线模型 r2<0：数据稀疏+标签间歇是主因，非流水线缺陷（指标如实记录了这一点）
- 天气特征字段在 time_filters.json 未出现，走 _default 校验默认值，未影响流程

## 关键文件路径
- `data/trains|infers/<device>_<user>/`：5 用户真实数据（只读）；`configs/time_filters.json`：用户配置（用户维护）
- `configs/default.yaml`：基础配置（含临时 bus_field_map、哨兵值、质量阈值，决策均注释在案）
- `outputs/batch/<ts>/batch_status.csv`：批量状态表；`outputs/<user_key>/train|infer/<ts>/`：各用户产物
- `nilm/preprocess/align.py`：对齐口径修订；`nilm/analysis/identifiability.py`：CV 判据修订
- 验证报告沉淀：`REPORT_TEST.md`（[2026-08-13] 验证专题：5 用户真实数据批量执行）
