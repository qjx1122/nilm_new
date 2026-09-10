# session/NILM_AC_session_complete.md — 会话纪要（只追加，每 session 一条）

## [2026-09-10] 会话纪要（Session 2：数据定义库）
- 目标：新增 NILM 数据定义库 md 文档（模板版，后续用户根据实际项目数据情况修改）
- 本会话角色：资深电力算法专家（默认角色）
- 完成项：
  - 创建 `NILM_DATA_DICT.md` v0.1：9 节模板（标注约定 / 数据源清单 / 粒度与口径 / 字段字典 / 文件组织与命名 / 数据质量规则 / 预处理与特征约定含 NILM_DUAL_SCALER 登记项 / 设备标签库 / 数据集划分与 KPI 口径），全部条目 🔶待确认 + 示例值
  - 登记 BOOTSTRAP.md 台账并升级协议 v2.3（新增台账行 + 修订记录）
  - STATUS.md 任务立项与收尾更新
- 关键决策：
  - 文件命名 `NILM_DATA_DICT.md` 并入台账治理（避免游离于协议外的文件爆炸）
  - 初版全部取值标 🔶待确认——项目尚无真实数据，具体数值无数据支撑，不得作为实验/汇报依据
  - 修改纪律：不删字段行、只翻状态（✅/🔶/❌）、变更记入文内 §9 变更记录
- 未决问题：
  - NILM_DUAL_SCALER 双标定的确切定义（文档按命名给出两种推测，待项目确认）
  - 时间戳语义（周期起始 vs 结束）、功率单位 kW 口径、质量阈值、设备清单——均待用户按实际数据修订
  - `README.md` 仍为 UTF-16 占位，待建项目骨架时重写
- 相关文件/分支：`NILM_DATA_DICT.md`、`BOOTSTRAP.md`、`STATUS.md`、`session/NILM_AC_session_complete.md`｜分支 `arena/01a0896c-nilm-new`（commit 32e3a8e 起）

## [2026-09-10] 会话纪要（Session 3：依据开发指南重构数据定义库）
- 目标：拉取最新代码；根据《工商业负荷辨识算法开发指南》（V2.1）内容重新修改 NILM 数据定义库
- 本会话角色：资深电力算法专家（默认角色）
- 完成项：
  - 拉取远端更新（afb2328→aa62f69，新增 docs/工商业负荷辨识算法开发指南.pdf V2.1，8 页，全文已提取解读）
  - `NILM_DATA_DICT.md` v0.1→v0.2 整体重构（commit a5ceee2）：工商业总线→分路监督辨识契约版，15 节；契约条目标 ✅ 附指南条款号，待核对项标 🔶 并汇总为 §14 OQ-01~10；§15 含 v0.1→v0.2 废弃对照表
- 关键决策：
  - 重构而非修补：指南确立的场景/接口（总线 5min 288 点→分路 15min 96 点 W、user_key 复合键、RE_BUS/RE_BR、run_batch_users.py JSON 配置）与 v0.1 户用假设全面冲突；v0.1 全为 🔶 从未作为事实，故不保留废弃行尸体，以废弃对照表保追溯
  - 上位约束登记：指南及其引用的《数据输入输出及配置要求.md》为最高优先级；后者仓库缺失 → OQ-01，需用户提供
  - 踩坑固化：并行 edit_file 同一文件互相覆盖（last-writer-wins），导致 Session 2 收尾「当前目标/已完成」更新丢失；处置=整文件 write_file 或串行编辑，本轮已修复
- 未决问题：
  - OQ-01《数据输入输出及配置要求.md》缺文件；OQ-02 日级指标 23 vs 25 字段（附件缺）；OQ-03 总线 CSV 实际字段待 schema；OQ-04 时区/时间戳语义；OQ-05 being_time 拼写张力；OQ-06 target_col 回退链；OQ-07 质量阈值定标；OQ-08 PF 符号/Ptotal；OQ-09 气象数据源；OQ-10 NILM_DUAL_SCALER
- 相关文件/分支：`NILM_DATA_DICT.md`、`STATUS.md`、`session/NILM_AC_session_complete.md`、`docs/工商业负荷辨识算法开发指南.pdf`｜分支 `arena/01a0896c-nilm-new`（commit a5ceee2 起）

## [2026-09-10] 会话纪要（Session 4：019ffeb6 分支代码与数据评审）
- 目标：拉取新版本，根据代码和数据检查代码是否存在问题
- 本会话角色：资深电力算法专家（默认角色，兼工程实现工程师探查/验证方式）
- 完成项：
  - 厘清仓库分支拓扑：main 为孤立基点；019ffa35(30 提交) ⊂ 019ffeb6(87 提交)；本 session 分支远端无新提交；本地工作区曾遭快照回退（b93f2a2+陈旧文件），已核实后 reset --hard 对齐远端 576fc90
  - 独立评审 019ffeb6：静态走读 22 模块 + pandas 全量数据审计 5 户 + 全新 venv 复跑测试 180/180 通过 + 800 户端到端冒烟 train+infer OK
  - 发现并实证：🔴W-1 滑窗跨间断（800 户 50.5% 窗口污染、最大 4.99 天、tests 零覆盖）；🟡W-2 SAE 分母 0（应修未修三现）；🟡W-3 配置整节替换致 _default 失效（778 实例）；🟢W-4~7；📊D-1~6（P/(U·I·PF)≈1.065、284x 三列缺失/低覆盖率、PF 封顶等）
  - 专题报告落盘 REPORT_TEST.md；数据侧疑点回填 NILM_DATA_DICT.md（OQ-11/12，v0.2.1）
- 关键决策：
  - 跨分支只评审不合入（无共同历史+用户未要求合并）
  - 评审工作区放 /tmp worktree（79.8MB 树，护快照配额，可重建）
  - W-1 定高危的依据=实证污染率+违反指南§10+影响深度模型指标口径
  - REPORT.md 暂不创建（候选稳定结论两条待用户确认）
- 未决问题：W-1/W-2 修复与重训复核；W-3 合并语义拍板；OQ-01/02 附件缺文件；OQ-11 倍率口径待采集方核对
- 相关文件/分支：REPORT_TEST.md、NILM_DATA_DICT.md、STATUS.md、session/NILM_AC_session_complete.md｜评审对象 origin/arena/019ffeb6-nilm-new｜本分支 commit 见 git log

## [2026-09-10] 会话纪要（Session 5：W-1 修复复核 + 快照回退重建 + ROLE v1.3 执行分级）
- 目标：修复 W-1（分段构窗+单测）后重训深度模型复核指标；执行推送；分析并落盘 ROLE.md 执行环境分级
- 本会话角色：资深电力算法专家 + 工程实现工程师（交付验证优先）
- 完成项：
  - W-1 修复与对照实验（首次完成于本 session 前段）：segment_bounds 原语+分段构窗+接口扩展+13 项守卫测试（193/193）；5 户真实数据前后对照——跨间断窗 1045/1109→0（2842 曾跨 231 天）、2842 幅值四段全线改善（test MAE -15.6%/SAE -30.5%/R² 0.765→0.808、infer 不降）、800 train/val/test MAE·R² 改善+infer F1 0.467→0.750、分类 F1 行为修正（precision 升/recall 回落）如实归因；基线模型前后一致（回归锚）
  - **快照回退灾后重建**（第 2 次回退，恰逢 GitHub token 失效）：未推送 5 commit+`/tmp` 实验现场灭失；按远端 34670d5+会话上下文重建（C1 基线 stat 80/8626 对账一致、C2 重打后 193/193 复验、C4 报告补实录灭失注记），推送 7a2c02f 并 ls-remote 核验
  - ROLE.md v1.3：新增全角色「执行环境分级与协作模式」（A 沙盒自执行默认 / B 用户本地执行）+ 三个技术角色挂接条款；决策依据=本 session 沙盒瓶颈实证与调参教练既有模式
- 关键决策：双模式分级而非一刀切（≤15min 轻量留沙盒）；未推送=随时不存在→推送失败必须下回合优先重试；重要结论当场写 REPORT_TEST.md（/tmp 日志不可依赖）
- 未决问题：W-1 修复是否回推 019ffeb6 上游；800 recall 回落业务拍板；W-2/W-3 未修；778/789 生产口径复核（模式 B 候选）；OQ-01/02/11 外部依赖
- 相关文件/分支：nilm/{common/schema,preprocess/dataset,models/*,pipeline/user_task}.py、tests/test_window_continuity.py、REPORT_TEST.md、ROLE.md、STATUS.md｜分支 tip 7a2c02f 后续提交见 git log
