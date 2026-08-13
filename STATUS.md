# STATUS.md

## 当前目标
- ✅ 已完成：按《工商业负荷辨识算法开发指南 V2.1》重构代码（模块解耦 + 单用户/多用户批量执行）
- ✅ 已完成：负荷辨识算法开发流程技术方案 + 流水线代码骨架（M1 MVP）

## 已完成
- [x] 指南获取：PDF 由用户推送至远程分支（commit a5915e3），已合入并全文解析（8 页，§0–§13）
- [x] 接口契约层 `nilm/common/contracts.py`：RE_BUS/RE_BR/RE_USER_DIR 原文照抄、§13 状态码、§12.3 配置规则
- [x] 数据接入重构：`data/trains|infers/<device>_<user>/` 扫描（discovery）→ 多 Ch CSV 关联 + 字段映射（bus_field_map，倍率配置化）→ schema/质量报告（JSON+HTML）
- [x] 预处理对齐指南：内部字段名 ua/…/pfc、5min→15min 可配置聚合（策略落盘）、τ 时滞只报告不改戳、§8 特征集（FFT/THD 禁用）、L=96 窗口（默认 Seq2Seq）、四种切分策略 + splits 锚定（train→val→test 优先）、Scaler 仅 Train 拟合
- [x] §9 可辨识性分析模块（训练前强制执行，IDENTIFIABILITY_LOW 标记）
- [x] §12 用户 JSON 配置：user_key 优先级/保留键/字段校验/user_id 显式映射层；时间过滤 include/exclude 闭区间语义
- [x] §13 批量执行：状态码全覆盖、单用户失败隔离、_DONE 断点续跑、原始数据只读、状态表含 user_id
- [x] 单用户/多用户同路径：`run_batch_users.py --time-filter-config <json> [--user-key]`（指南原文入口）
- [x] 输出契约：predictions/inference_result.csv（timestamp/user_id/target/pred/pred_state）+ 开态后处理（on_thr_w/post_min_on/post_fill_short_off）
- [x] 解耦守卫：tests/test_decoupling.py 静态审计依赖方向；65 项测试全过；CLI 实测多用户/单用户/续跑/失败隔离全部通过

## 进行中
- 无

## 下一步（TODO）
1. **接口待确认项**：指南附件《数据输入输出及配置要求.md》未随 PDF 提供——日级指标字段清单（25 vs 23 差异）、启动段字段（begin_time 全量契约）、target_col 缺省回退链，需用户补充后对齐
2. **M0 真实数据摸底**：真实 CSV 放入 data/trains/<device>_<user>/，按 schema 检查结果修订 bus_field_map；验证时间同步与重叠率
3. **M2 多模型**：tree_models.py / seq_models.py（GBDT、Seq2Point/LSTM），装 requirements-ml.txt
4. **M3 对比选型**：批量实验矩阵 + 超参扫描 → REPORT_TEST.md → REPORT.md

## 决策记录 / 踩坑
- 指南接口契约 > 原技术方案：字段名 u_a→ua、p_total→pa/pb/pc+pbus、branch_<id>→pN；target 为单目标（target_col 可复合 p1+p2，skipna=False），模型输出统一 (n,1) 矩阵接口
- 单用户 = users=[一个 key] 的批量执行（同一代码路径），避免两套流程漂移
- 断点续跑用 outputs/<user_key>/<mode>/<ts>/_DONE 标记实现（无需集中状态库，天然分布式）
- 日历/槽位列（slot/hour/dow…）不参与 z-score 缩放，否则破坏基线按列名取用语义
- splits include 冲突优先级：必须用 claimed 集合按 train→val→test 顺序主张，低优先级不得抢占（首版实现被 val 抢占 train，测试抓出并修复）
- 测试期望值教训：宏平均 RMSE = 各分路 RMSE 均值（非全元素展平）；CSV 读回 user_id 为 int64 需 astype(str) 比较
- `idx.dayofyear`（无下划线）；pandas 3.0 频率串用 "5min"/"15min"
- PDF 附件通道两次未投递成功，最终由用户直接 git push 到分支解决（commit a5915e3）——附件不达标时改走 git 通道更可靠
- 指南状态码之外新增扩展码（SCHEMA_UNCONFIRMED/DATA_UNIT_UNKNOWN/SKIPPED_RESUME/FAILED/IDENTIFIABILITY_LOW），已在 contracts.py 注释标明出处
- 天气特征（weather_*）字段已校验并透传，但天气数据源未接入，当前按「保留字段」处理（不生成天气特征列）

## 关键文件路径
- `docs/工商业负荷辨识算法开发指南.pdf`：开发规范（最高优先级）；`docs/TECH_DESIGN_LOAD_ID_PIPELINE.md` §11：条款→实现映射
- `nilm/common/contracts.py`：全部接口契约；`nilm/pipeline/batch.py`+`user_task.py`+`user_config.py`：编排层
- `scripts/run_batch_users.py`：指南 §12 入口；`configs/default.yaml` + `configs/time_filter.example.json`：配置示例
- `tests/test_decoupling.py`：解耦守卫；`tests/conftest.py`：契约合规的合成数据工厂
- `outputs/`（不入库）：batch 状态表 + 各用户 train/infer 产物
