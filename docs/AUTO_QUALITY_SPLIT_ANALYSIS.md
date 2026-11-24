# 基于每日数据质量的数据集自动划分专题分析与设计报告（`split_strategy="auto"`）

- 版本：v1.0（2026-09-10）
- 状态：专题分析与实施方案落盘（对应源码 `nilm/preprocess/splits.py`、`nilm/data_io/validator.py`、`nilm/pipeline/user_task.py`）
- 关联模块：`nilm/common/contracts.py`、`nilm/preprocess/splits.py`、`nilm/data_io/validator.py`、`nilm/pipeline/user_task.py`、`scripts/auto_run_steps2to5.py`

---

## 1. 背景与核心问题定义

在工商业负荷辨识（NILM）单户建模流水线中，原始采集数据普遍存在**设备短时掉线、单侧缺数（母线在而分路缺或反之）、季节性停产/长假全关机、投运初期残日数据**等非理想现象。

当前代码体系中已具备两块基础能力：
1. **前置逐日质量评估与放行（`validator.py` + `day_gate`）**：按 15 分钟网格（96 点/天）独立计算母线得分 `bus_score` 与目标分路得分 `branch_score`，剔除 `< min_score`（默认 70 分）的非双达标天；
2. **事后切分覆盖性体检（`quality_advice` + `split_coverage_advice`）**：在切分完成后检查合格天数规模、连续离线缺口、`train / val / test` 三折全关天覆盖情况并输出告警文本。

然而，处于中间核心位置的**数据集划分器 `build_split_masks`（`nilm/preprocess/splits.py`）对每日数据质量仍是“盲切”的**——默认策略 `stratified_day`（按星期几分层切 `6:2:2`）与其余既有策略（`time`、`stratified`、`global_stratified`、`stratified_by_state`）均未消费 `daily_quality.csv` 的逐日质量信息，导致在真实用户上频繁需要人工查阅报告后手写 `time_filters.json` 的 `splits` 日期锚点（典型如 `800080270800_4200904302272` 手工编写 23 段日期区间及 `scripts/make_B1_config.py` 离线删关脚本）。

本专题回答两个核心工程问题：
1. **能否根据用户每天的数据质量实现全自动数据集划分？** —— **完全可行（100% 零外部依赖、向后兼容）**。
2. **如何设计自动划分准则与算法，才能从数学和物理上保证用于训练、验证与测试的数据集最合理？**

---

## 2. 既有划分策略在真实工商业数据上的五大结构性短板

即便前置 `day_gate` 已剔除 `< 70` 分的不合格天，既有自动切分策略仍存在以下 5 个痛点：

| 序号 | 短板维度 | 既有代码实际行为 | 对模型训练、自动定参与评估的具体危害 |
| :---: | :--- | :--- | :--- |
| **1** | **打碎连续自然日，损耗序列模型真实满窗率** | 默认 `stratified_day` 将周一~周日分 7 桶各自按 `6:2:2` 切分，相当于每周挖走 1~2 天划入 `val/test` | 原本连续 30 天的双达标数据被切成十余个 2~4 天碎段；深度序列模型（`Transformer / LSTM / CNN1D`，`window=96`）遇时间断点强制重起（`seq_models.py:34`），每个碎段前 95 点均依赖首行重复左填充（`_padded_windows`），大幅稀释真实 24h 上下文窗口占比 |
| **2** | **跨折滚动特征渗透与时序内插（审计 L-3）** | 切分前对全序列计算 `lags=[5,1..4]` 与 `rolling=["1h","6h","24h"]`，且 `train` 日与 `val/test` 日按周交错相邻 | 当 `val/test` 日次日紧邻 `train` 日时，次日 `train` 整天（96 点）的 `24h` 滚动均值/标准差直接包含前一日 `val/test` 的母线功率；同时产生用未来周训练模型内插预测过去周的时序乐观偏差 |
| **3** | **三折开/关机日分布失衡（最影响泛化）** | `time` 与 `stratified_day` 不感知日开/关状态；`stratified_by_state` 虽按开关分层但会进一步切碎连续段且不看日质量分 | - **`train` 缺全关日**：模型无法学习背景母线停机基荷，推理遇节假日/停产日整天虚报；<br>- **`val` 缺全关日**：`decision_thr_w="auto"` 与 `post_fill_short_off="auto"` 失去关态负样本护栏，无法抑制关态短毛刺虚报；<br>- **`test` 缺全关日**：离线验收未覆盖停机工况，`F1/Precision` 虚高 |
| **4** | **边缘合格残缺天（70~79 分）与满点精品天（95~100 分）无差别混分** | `day_gate` 仅做 `score >= 70` 二值放行；71 分（当日掉线约 7 小时）与 100 分（96 点完整无异常）在切分时完全等价 | 若稀缺的 `val` / `test` 恰好分到低分残缺天或两侧均断连的 1 天孤岛（1-day island），早停监控 `val_loss`、自动定参寻优标尺和最终测试集指标会被断连边缘噪声主导 |
| **5** | **训练池全关日极端过剩未自动治理 + 非锚定 `on_thr_w` 看 `test`（审计 L-4）** | 春节/淡季长假导致全关日占比 `> 65%` 时全量喂入；非锚定切分用户在 `masks["test"]` 生成前用全池真值算 `on_thr_w="auto"` | 过量全关零值主导 MSE 回归损失压低预测幅值；`test` 折分路真值在切分前参与了 `on_thr_w` 寻谷 |

---

## 3. “最合理数据集”的五维量化评价准则

一套面向 NILM 生产流水线的最优自动划分器，必须在双达标天集合上按优先级同时满足以下五条准则：

### 准则 1：整日原子性与连续块最大化（Block Continuity & Natural Gap Alignment）
- **日内不打散**：同一自然日的所有 15 分钟采样点必须 100% 归属同一数据集（`train`、`val` 或 `test`），与 `qualified_days_detail.csv` 逐日对账契约严格对齐。
- **借用天然离线断口、最小化人为切口**：设备历史离线/坏天剔除已经在时间轴上留下了天然断口（相邻合格日差 $\Delta d > 1\text{ day}$）。切分边界优先落在天然断口上（新增断窗代价 $= 0$），避免将连续完好运行周人为腰斩。

### 准则 2：三折开/关工况双覆盖铁律（On/Off Dual Representation）
记池内开机日总数为 $N_{\text{on}}$、全关日总数为 $N_{\text{off}}$：
- **当 $N_{\text{off}} \ge 3$ 且三折配额均 $\ge 1$ 天**：强制保证 `train >= 1` 全关日、`val >= 1` 全关日、`test >= 1` 全关日，同时最小化各折全关日占比与池均值 $\rho_{\text{off}}$ 的偏差 $\sum_{s} |\rho_s - \rho_{\text{off}}|$；
- **当 $N_{\text{off}} = 2$（稀缺双关日）**：强制分配 **`train` 1 天 + `val` 1 天**（保障模型学会停机模式 + 验证集具备防虚报校准标尺）；
- **当 $N_{\text{off}} = 1$（孤本单关日）**：**唯一 1 天强制保留在 `train`**（防止把唯一负样本日划去 `val/test` 导致模型 100% 只见过开机日而全天恒定虚报）；
- **当 $N_{\text{off}} = 0$（全开机池，如 `901` 户 24h/全勤空调）**：标记 `zero_off_days`，交由 `auto_thresholds.py` 日内关态时段分支兜底；
- 对稀缺开机日（$N_{\text{on}} \in \{1, 2, 3\}$）执行完全对称的覆盖保护。

### 准则 3：质量分级优选——精品完整天优先作 `val / test` 标尺，轻度残缺天留 `train`
依据单日短板侧得分 $s_d = \min(\text{bus\_score}_d, \text{branch\_score}_d)$ 与当日有效点数 $n_d$，将双达标天分为三级：
- **A 级精品天**：$s_d \ge 95$ 且 $n_d \ge 88$（覆盖 $\ge 22$ 小时，近乎满点无异常）；
- **B 级标准天**：$s_d \ge 80$ 且 $n_d \ge 64$（覆盖 $\ge 16$ 小时）；
- **C 级边缘合格天**：$70 \le s_d < 80$ 或 $n_d < 64$，以及两侧均不相邻的 **1 天孤岛（`is_island=True`）**。
- **优选方向**：`val`（早停与自动阈值标尺）和 `test`（离线验收标尺）优先选取连续 A 级精品天；C 级天与 1 天孤岛优先留在 `train` 充当补充训练样本，而当日有效点数 $< 16$ 点（不足 4 小时）的极短残片天在样本充足时直接从训练池剥离（标为 `未使用`）。

### 准则 4：极端全关天过剩自动裁剪（Pool Balancing）
- 当双达标总天数 $N_{\text{qual}} \ge 25$、开机日 $\ge 10$ 天，但全关日占比 $> 65\%$（如含整月停产/春节长假）时，自动从**最早（最远期）的连续全关段**起裁剪冗余全关日，将池内全关日占比收敛至约 $45\%$（保留近期全关日与至少 6 个全关日），避免远期停产零值淹没回归梯度。被裁剪天在 `qualified_days_detail.csv` 中透明记录为 `未使用`。

### 准则 5：时序近端对齐与严格零泄漏（Recency & Zero Leakage）
- **近端优先**：工商业母线基荷随季节缓慢漂移，在满足工况覆盖与高分连续性的前提下，`val` 与 `test` 优先布置在时间轴靠后的近期区段，使 `val` 校准出的 `decision_thr_w` 与 `pbus_mode` 紧贴下游 `infer` 月份。
- **顺手根治 L-3 / L-4 泄漏**：
  1. **L-4 闭环**：当 `on_thr_w="auto"` 且未手工锚定 `splits.test` 时，在切分锁定 `test` 折后，严格仅在 `train + val` 子集复核/重刷 `on_thr_w`，确保 `test` 分路真值零参与；
  2. **L-3 闭环**：在确定日级 `train / val / test` 掩码后，对三折独立调用 `build_features` 构造滞后与 `24h` 滚动特征，彻底阻断跨折母线功率渗透。

---

## 4. `split_strategy="auto"` 四步确定性算法设计

算法实现于 `nilm/preprocess/splits.py` 的 `resolve_auto_quality_split`，时间复杂度 $O(N^2)$（前缀和加速，100~365 天耗时 $< 2\text{ ms}$），全程确定性可复现（无随机种子抖动）：

```text
输入: index (15min 时间索引), ratios ([r_tr, r_va, r_te]), daily_quality, target, on_thr_w, splits_spec
  │
  ├─ Step 0 [池级净水, user_task 侧可选开启]:
  │    ├─ 剔除 <16 点（<4h）极短残片天（未被 splits_spec 锚定且余量充足时）
  │    └─ 若 N_qual >= 25 且 off_ratio > 0.65: 从最远期连续全关段裁剪冗余全关天至 off_ratio ≈ 0.45
  │
  ├─ Step 1 [构建逐日质量-工况画像表 build_day_quality_profiles]:
  │    对每个自然日计算: n_points, bus_score, branch_score, min_side_score,
  │    tier (A/B/C), is_on, gap_before/gap_after, block_id, is_island
  │
  ├─ Step 2 [按样本规模与工况分布全局搜索最优连续块]:
  │    ├─ 模式 A: 单段连续块精确搜索 (auto_contiguous_quality_blocks)
  │    │    基于前缀和遍历非重叠连续区间 val=[v0, v0+N_va), test=[t0, t0+N_te), 其余归 train
  │    │    按字典序+综合效用最大化评分:
  │    │      (1) 硬满足准则 2 开/关双覆盖铁律
  │    │      (2) 三折开关占比偏差 Δ_off <= 0.25
  │    │      (3) 最大化 val/test 的 A 级占比与平均 min_side_score，惩罚 C 级/孤岛天进入 val/test
  │    │      (4) 最小化人为新增断口数（切在 gap_before 天然断口上零惩罚）
  │    │      (5) 偏好时序顺序 train → val → test 且靠近时间轴近端
  │    │
  │    └─ 模式 B: 极化季节双段分层连续块 (auto_polarized_two_block)
  │         当全池呈“前半程全关 + 后半程全开”等极化分布，单段连续区间无法兼顾开关均衡时自动切换：
  │         在全关日序列与开机日序列内部分别按质量分、天然断口与近端性选取连续子块组合，
  │         使每折最多仅由 2 个长连续块（1 关块 + 1 开块）组成且开关比与全池对齐
  │
  └─ Step 3 [人工锚点覆盖与空折兜底]:
       调用 anchor_splits(index, masks, splits_spec) → repair_empty_splits(index, masks, ratios)
       人工配置的 splits.{train,val,test}.{include,exclude} 始终保有 100% 最高优先级
```

---

## 5. 可观测性、对账契约与能力边界

### 5.1 落盘留痕与 `qualified_days_detail.csv` 对账不变量
1. **`meta.json` 新增 `split_decision` 审计字典**：完整记录 `configured_strategy`、`resolved_mode`（`auto_small_sample_contiguous` / `auto_contiguous_quality_blocks` / `auto_polarized_two_block`）、各折天数/点数、各折开/关日数、各折平均短板质量分与 A 级精品天数、天然断口复用数与人为切口数、残片天与冗余全关天裁剪清单，以及 `per_split_feature_isolation` 与 `zero_test_leakage_on_thr` 状态。
2. **`qualified_days_detail.csv` 严格守恒对账**：
   - 任意双达标天（`qualified == 1`）在 `dataset` 列有且仅有一个明确归属：`训练集`、`验证集`、`测试集` 或 `未使用`（絕不出现同一自然日跨多个数据集的混切）；
   - 恒等式：`total_days == train_days + val_days + test_days + unused_days`；
   - 当池内 `all_off_days >= 3` 且无冲突人工锚点时，`qualified_days_detail.csv` 中 `训练集`、`验证集`、`测试集` 各自包含的 `all_off == 1` 天数均 $\ge 1$，`split_coverage_advice` 不再触发“训练集/测试集不含全关天”告警。

### 5.2 客观能力边界与配套兜底
1. **全池物理零全关天（$N_{\text{off}} = 0$）**：自动划分器标记 `off_day_scarcity="zero_off_days"` 并最大化连续高质量窗口，阈值校准自动交由 `auto_thresholds.py` 的日内关态时段机制处理。
2. **全池普遍低质量（所有合格天均在 `70~78` 分、每日固定长掉线）**：自动划分器仍能选出相对最完整的区段作 `val/test`，但单日内部缺口会限制 `window=96` 深度模型的有效上下文，由 4 模型自动对比机制自然偏向对缺口鲁棒的 `ridge` / 树模型。
