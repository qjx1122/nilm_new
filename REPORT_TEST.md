# REPORT_TEST.md — 专题报告（只追加，按专题分节，不新建文件）

## [2026-08-13] 专题：负荷辨识算法开发流程技术方案与流水线骨架
- 类型：用户专题
- 目标与假设：在「母线 288 点/天（三相 U/I/P/PF）+ 分路 96 点/天（三相总有功）」数据条件下，产出模块化、解耦、支持多模型对比的负荷辨识开发流水线。假设：分路标签粒度决定辨识上限为分路级有功分解；训练主分辨率取 15min 可行且信息无损
- 方法 / 数据 / 参数：
  - 架构：common（契约/注册表/日志）→ data_io / preprocess / events / models / evaluation / reporting → pipeline 编排，单向依赖、接口隔离、配置驱动、注册表装配
  - 对齐：母线 5min→15min 均值聚合（PF 用 P/(U·I) 重算），时间重叠率门禁；防泄漏时序划分 70/15/15，滑窗不跨 split
  - 模型（已实现 3 个）：history_profile（槽位均值画像）、proportional（占比分摊）、ridge（numpy 闭式解）；GBDT/Seq2Point/LSTM 列入 M2
  - 约束后处理：非负 + 总和一致性投影（模型无关）
  - 验证：15 项单测（含端到端 smoke）+ 7 天合成数据 CLI 全流程演示
- 结果 / 结论：
  - 流水线端到端跑通：train→evaluate→compare 一键完成，产物（配置快照/模型/metrics/对比表/报告）自动归档 outputs/<exp>/<ts>/
  - 合成数据演示：三模型 R²≥0.996，proportional 综合最优（符合合成设定为固定占比的预期，验证对比机制正确性）
  - 新增模型成本 = 实现 BaseModel 接口 + 注册 + 配置加一行，编排/评估代码零改动（解耦达标）
- 是否进入 REPORT.md（稳定结论）：否（尚无真实数据实验结论）
- 遗留问题：真实数据接入与时间同步验证（M0）；GBDT/深度模型实现（M2）；超参扫描与选型报告（M3）；事件检测扩展（M3+）

## [2026-08-13] 专题：按《工商业负荷辨识算法开发指南 V2.1》代码重构
- 类型：用户专题
- 目标与假设：以指南为最高优先级接口契约重构 M1 骨架；假设：指南 §0–§13 条款可逐条映射为模块职责；单用户与多用户可统一为同一批量代码路径
- 方法 / 数据 / 参数：
  - 契约层集中管理（common/contracts.py）：RE_BUS/RE_BR/RE_USER_DIR 原文照抄、§13 状态码、§12.3 字段规则；其余模块仅引用常量，不改写正则
  - 数据接入：discovery 扫描（状态码判定）→ CsvBusLoader 多 Ch 关联 + bus_field_map 字段映射（CT/PT 倍率配置化）→ schema/质量报告（data_schema_report.json + data_quality_report.html）
  - 预处理：5min→15min 聚合策略可配置且落盘（agg_strategy.json）；τ 时滞只报告证据；§8 特征集（FFT/THD 禁用）；L=96 窗口默认 Seq2Seq；四种切分策略 + splits include/exclude 锚定（train→val→test 优先）；Scaler 仅 Train 拟合
  - 新增 analysis（§9 可辨识性，训练前强制）与 postprocess（on_thr_w/post_min_on/post_fill_short_off）模块
  - 编排：user_config（§12 键优先级/校验/user_id 显式映射层）→ user_task（单用户端到端 + 状态码异常）→ batch（失败隔离/_DONE 续跑/状态表 user_id 字段）
  - 验证：65 项测试（含 AST 解耦守卫、契约正则、时间过滤语义、复合目标 skipna=False、配置校验、批量状态码/隔离/续跑）；CLI 实测 2 合法用户 + 2 异常目录全流程
- 结果 / 结论：
  - 指南 §3–§13 全部可落地条款已实现并在测试中固化；依赖方向静态审计通过（业务模块仅依赖 common）
  - 多用户批量：OK 4 / DATA_MISSING_BUS 1 / INVALID_USER_DIR 1，失败隔离生效；续跑 SKIPPED_RESUME 生效；单用户模式同路径生效
  - 推理输出符合 predictions/inference_result.csv 契约（timestamp/user_id/target/pred/pred_state）
  - 识别 3 项接口待确认项（日级指标字段清单 25vs23、启动段字段契约、target_col 回退链），按指南 §0 口径记录不擅自补充
- 是否进入 REPORT.md（稳定结论）：否（工程重构专题，尚无真实数据实验结论）
- 遗留问题：指南附件缺失（字段清单/启动段契约）；真实数据 M0；M2 多模型；天气特征数据源未接入
