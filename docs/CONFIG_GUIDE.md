# 配置文件详细说明（default.yaml + base_optimal.yaml + 用户 JSON，含模型与训练效率指南）

> 版本：**v3.0（2026-09-18，对齐 `REPORT.md v1.1 + NILM_DATA_DICT v0.2.10 + TUNING_GUIDE.md v1.1 详细版 + REPORT_TEST.md 18专题 + base_optimal lag5[5,1,2,3,4]`）**
> 适用代码：`nilm/` 流水线（对齐《工商业负荷辨识算法开发指南 V2.1》）｜协议：`BOOTSTRAP.md v2.3`｜分支：`arena/01a0896c-nilm-new`
> 优先级：`--time-filter-config` 用户 JSON（§12）> `--base-config` 指定 yaml（`default.yaml` 或 `base_optimal.yaml`）> 代码硬编码默认。
> 本文档描述各配置项在**当前代码实现**中的真实语义（非仅注释复述）；配置结构变化时须同步更新本文档（BOOTSTRAP 收尾仪式条件触发项）。
>
> 全文三部分：**第一部分 `configs/default.yaml`（§1–§10，流水线级基线）**；**第二部分 `configs/base_optimal.yaml`（§10.1，生产最优合一）**；**第三部分 用户 JSON（§11–§14，`--time-filter-config` 指定，用户级）**。
> 人话版操作说明见 `TUNING_GUIDE.md v1.1`（8 步 SOP/两阈解耦/4选1/7项放行）；本文为**精确技术字典**。

### v3.0 变更摘要（相对 v2.0 2026-08-18）

| 变更 | 旧（v2.0） | 新（v3.0） | 原因/证据 |
|---|---|---|---|
| `quality.min_score` | 10 | **70** | 2026-08-18 上调，门禁与逐天质量表/双达标口径共用；2844 `bus 69.63 FAIL` 实证 |
| `quality.day_gate` | 默认关闭/未提 | **全局默认 `true`** | 2026-09-14 任务⑮全局默认，用户拍板；优先级 `day > train_range > full`；2844 池 29→45天 +55% |
| `quality.min_on_day_ratio` | 未文档化 | **新增 0.2** | 2844 开机日占比门禁（71%达标）；可选 0~1 |
| `features.lags` | `[1,2,3,4]` | **`[5,1,2,3,4]`（lag5 75min首位）** | P0 任务 2026-09-17实录：`transformer R² 0.39→0.49 F1 0.69→0.75 SAE 0.22→0.03` 已合入 `default/base_optimal` |
| `models` | 注释仅 3基线，其余注释，epochs 60 | **`default 3基线` + `base_optimal 4模型（+transformer epochs150/patience20）`** | 2842/2844/778/789 最优实录 `ridge/transformer/history` 分别最优，单押必错；2842 `epochs 60→150 F1 0.984→0.988` |
| `ridge` | 无 off_weight | **`off_weight 5.0 / off_thr_w 10.0`** | 关态加权 `FP 481→342` |
| `transformer` | 无或 epochs60 | **`window96 d_model64 nhead4 layers2 epochs150 patience20`** | 4户择优之一，需 GPU；加宽 `128/3` 反而过拟合 |
| 用户 JSON | 无 `decision_thr_w` | **新增 `decision_thr_w`（10→500 扫阈） + `post_min_on/post_fill_short_off`** | 两阈解耦：`on_thr 真值` vs `decision 判决`；2844 `30→400 F1 0.887→0.955 R≥0.95` |
| 新增 §10.1 | — | **`base_optimal.yaml 对照表`** | 生产推荐路径：`--base-config base_optimal.yaml` |
| 对齐文档 | — | **对齐 `TUNING_GUIDE v1.1/REPORT v1.1/NILM_DATA_DICT v0.2.10`** | 18专题一致 |
| 800 状态 | 未提 | **OQ-16 PAUSED（p1/p2/p3均非目标，待p4）** | 12篇p1结论降级 |

---

# 第一部分：configs/default.yaml（流水线级基线）

## 1. 全局

| 配置项 | 当前值 | 意义 |
| --- | --- | --- |
| `experiment_name` | `guide_v2_1` | 实验名称标签，仅标识用，不影响计算；`base_optimal` 为 `guide_v2_1_optimal` |
| `seed` | `42` | 全局随机种子。批量入口统一设置 `random.seed` / `np.random.seed` / `torch.manual_seed`，保证切分与模型初始化可复现（跨 GPU 逐位复现实证） |
| `output_dir` | `outputs` | 产物根目录默认标注（实际以 CLI `--output-root` 为准） |

## 2. `data` — 数据接入

| 配置项 | 意义 |
| --- | --- |
| `trains_root` / `infers_root` | 训练/推理数据根目录，按 `<终端号>_<用户号>` 一级用户目录扫描（§3.1）；原始数据全程只读 |
| `sentinel_values` | 哨兵值清单（`[-2147483648, 2147483647]` INT32_MIN/MAX）。加载时替换为 NaN——实测数据用它们表示无效采样，不处理会被当成天文数字功率毁掉训练 |
| `derive_phase_from_ptotal` | `true`：文件只有总功率无分相时按 `ptotal/3` 均分出 pa/pb/pc（临时假设，schema 报告显式标记，非静默处理） |

**文件命名正则（硬校验）**：总线 `RE_BUS ^e241_(device)_(user)-Ch\d+-\d{6}-\d{6}(-1|-infer)?\.csv$`；分路 `RE_BR ^(user)-\d{6}-\d{6}(-1|-infer)?\.csv$`。带 `-1/-infer` 不参与合并；`ChN` 仅通道号。

## 3. `quality` — 质量门禁

不达标判 `DATA_QUALITY_FAILED`，该用户任务终止（失败隔离，不影响其他用户）。**2026-09-18 现值（已与 `base_optimal` 同步）：**

| 配置项 | 当前值 | 意义 |
| --- | --- | --- |
| `max_missing_rate` | 0.9 | 最大缺失率，超过拒绝建模 |
| `min_coverage` | 0.15 | 时间覆盖率下限（实际点数 / 按 15min 应有点数） |
| `min_score` | **70** | 综合质量分下限（缺失/异常/覆盖合成）；**2026-08-18 由 10 上调**，同时是逐天质量表 `daily_quality.csv` 的「得分阈值」：单日总线与目标分路得分均 ≥70 才算当天合格（双达标） |
| `gate_scope` | `full` | 门禁范围：`full`=全范围（默认）；`train_range`=门禁后移——改判「时间过滤后的训练范围」，需配合 `time_filters.json` 缩窗（任务⑬ 2844 放行 B） |
| `day_gate` | **true** | **日级放行（2026-09-14 起全局默认）**：训练/评估天 = 总线与分路同时达标天（逐天得分≥min_score）；启用后跳过 `full/train_range` 门禁；优先级 `day > train_range > full`；可用户级 `quality.day_gate=false` 显式关闭 |
| `min_on_day_ratio` | 0.2（可选） | 可选开机日占比门禁（0~1，默认不启用）：双达标天中开机日占比低于此值不放行；2844 实测 31.1%→日级池 71% 已验 |
| `min_days` | 3 | 时间过滤后最少有效天数（`len(bus) < 96×min_days` 判 `INSUFFICIENT_TIME_RANGE`） |
| `max_daily_missing_rate` | 0.9 | **日级无效天阈值**：总线或分路（有效通道）全天数据缺失、或当日缺失率超过该值的天，整天剔除——不参与模型训练，也不参与训练/推理阶段的评估；剔除清单落盘 `excluded_days.json` |

`quality.min_score` 双达标后产出 `qualified_days_detail.csv`（是否全关日/全关阈值/所属数据集）与 `quality_advice.json`；HTML 报告呈现「同时达标统计」「双达标口径清洗后统计」。

> v2.0 阈值“0.3/0.5/50 放宽版”已收紧为 **70**；数据质量改善后可再收紧覆盖率。

## 4. `preprocess` — 清洗与聚合

| 配置项 | 当前值 | 意义 |
| --- | --- | --- |
| `clip_negative` / `allow_negative_power` | `true/false` | 有功负值裁剪为 0（§2.3 原则非负）。业务存在反送电必须显式 `allow_negative_power: true`，禁止静默处理 |
| `max_gap_interp` | 2 | 线性插值最长连续缺口（点数）。`2` = 只补 ≤30min 短缺口；长缺口保留 NaN 由样本构建剔除（防插值编造假数据） |
| `save_cleaned_csv` | true | 清洗后数据落盘 `cleaned/{bus,branch}_cleaned.csv` 开关（默认 true，供抽查） |
| `min_overlap` | 0.3 | 母线/分路时间对齐重叠率门禁，低于此值拒绝训练 |
| `agg_strategy` | `{u:mean,i:mean,p:mean,pf:recompute}` | 5min→15min 聚合策略逐物理量：u/i/p 取 `mean`；**pf 用 `recompute`**（由聚合后 P/S 重算—功率因数直接平均数学错误）。策略落盘 `agg_strategy.json` |

清洗链：哨兵 `-2147483648/2147483647→NaN` → `clip_negative` → `max_gap_interp 2` → 5→15min 聚合 → 对齐去重。分路 15min 不插值。

## 5. `features` — 特征工程（§8；FFT/THD 明确禁用 §8.5）

| 配置项 | 当前值 | 意义 |
| --- | --- | --- |
| `lags` | **`[5, 1, 2, 3, 4]`** | 总线功率滞后特征：首位 **lag5=75min（5×15min）** + 15/30/45/60min。2026-09-17实录：`transformer R² 0.39→0.49 / F1 0.69→0.75 / SAE 0.22→0.03`，`ridge` 持平，已全局合入 `default/base_t5/base_optimal`，对 4 户零污染已验 |
| `rolling_windows` | `["1h","6h","24h"]` | 对 pbus/ia/ua/pfa 各生成滚动均值+标准差；每加一窗口=每基础量多 2 列特征 |

代码固定生成（不可配置）：差分、三相不平衡度（i/u/p）、相电流占比、slot(0–95) 与时刻 sin/cos 周期编码。**特征列数直接影响所有模型训练耗时**（见 §10）。

**时滞彩蛋**：`W-1` 修复后 `lags` 按时间连续段构造，非跨洞旧语义污染已清（`common.schema.segment_bounds` 原语，间隔≠15min 即分段）。

## 6. `dataset` — 样本构建（§10，W-1 已修复）

| 配置项 | 当前值 | 意义 |
| --- | --- | --- |
| `window` | 96 | 滑窗长度 L=96 = 过去 24h（15min 粒度）；也是训练样本量下限判据（样本 < 2L 拒绝训练） |
| `mode` | `seq2seq` | 窗口标签模式（seq2seq=整窗标签 / seq2point=窗末点），当前用于 `train_window_index.csv` 索引落盘；深度模型适配器内部固定 Seq2Point 逐点语义，窗口长度由各模型 `params.window` 控制 |

**W-1 修复**：按 `index.diff() != 15min` 分段，段头用段内首行填充，段尾不足一窗不产窗，`cross_gap=0`（max 23.75h）为金标准；13项守卫 `tests/test_window_continuity.py`。

## 7. `bus_field_map` — 总线字段映射（§3.2/§4）

ChN 只是通道标识，**物理含义必须由本配置确认**。每字段四要素：

```yaml
ua: {ch: 1, column: load_iden_data9, multiplier: 0.001, unit: V}
```

- `ch`：通道号；`column`：原始 CSV 列名（官方点位表 2026-08-14 确认）
- `multiplier: 0.001`：**实际物理量 = 原始值 / 1000**（官方倍率规则，如 PF 916 → 0.916）；CT/PT 倍率同样经此配置化；`P/(U·I·PF)≈1.065` 待核对（OQ-11）不阻断
- 缺列规则：文件中找不到 `column` → WARNING + 该列置 0 + 报告标记 `MISSING_COLUMN_ZERO_FILLED`（非致命，容忍设备间列集合差异；实测 5 户均缺 `ub(data45)/ib(data37)/pfb(data44)` 3列，B相人为删，置0为设计）

官方映射：`ua/ub/uc→data9/45/81`，`ia/ib/ic→data1/37/73`，`pa/pb/pc→data7/43/79`，`pfa/pfb/pfc→data8/44/80`。

## 8. `metrics` — 评估指标（双口径）

| 组 | 指标 | 口径 | 说明 |
| --- | --- | --- | --- |
| 回归 | `mae / rmse / r2 / sae` | **能力口径 `@on_thr`**（`pred ≥ on_thr` 直判，无游程） | sae=信号聚合误差（整段电量偏差占比）；全关日 `Σy=0` 时 SAE 置 NaN（D-7 伪值 `1e12` 已修） |
| 状态分类 | `f1 / accuracy / precision / recall` | 同上 | 按 `on_thr_w` 二值化后计算 |
| 混淆计数 | `tp / fp / fn / tn` | 同上 | **诊断输出，不参与最优模型排序**（否则全预测关机被选优） |

**双口径（`TUNING_GUIDE §6` 详述）：**

| 表 | 口径 | 产出 | 看什么 |
|---|---|---|---|
| `metrics_daily.csv` + `offline_metrics.json` + `metrics_by_split.csv` | 能力口径 `@on_thr` | `state_thr_w=on_thr` 自描述 | 模型本身准不准 |
| `metrics_daily_chain.csv` + `state_strategy_metrics.csv` + `inference_result.csv:pred_state` | **交付口径 `@decision_thr_w + 游程`** | `decision_thr_w` 自描述，`target_state vs pred_state` 逐日，全关 `fp` 在此 | 用户收到准不准 |

指标输出：`metrics.json`（test）、`metrics_by_split.csv`（train/val/test）、`metrics_daily.csv`（逐天）、`comparison.csv/md`（模型对比与选优）。**`decision_thr_w` 契约上不进 offline 指标**（看错表教训 §9-5）。

## 9. `infer_model`（默认注释）

推理模型选择：不配置 = 用该用户训练对比综合最优（`best_model`）；配置则强制指定（必须在该用户训练清单内；§13 禁止借用他人模型）。
注意：用户 JSON 侧的 `infer_model`（用户级 / `_default` / 顶级全局，见 §11）优先级均高于此处 yaml 全局项。

---

## 10. 模型清单详解（`models:`，8 模型，含训练效率因素）

所有模型经 `MODEL_REGISTRY` 注册、配置驱动实例化，统一 `(n,f)→(n,k)` 矩阵接口。

> **当前启用状态（2026-09-18，生产最优）：**
> - `configs/default.yaml`：启用 3 基线 `history_profile / proportional / ridge`（其余 `random_forest/xgboost/lstm/cnn1d/transformer` 注释保留，取消注释即恢复）
> - `configs/base_optimal.yaml`：**启用 4 模型 `history_profile / proportional / ridge / transformer`（`lags[5,1,2,3,4] + 150epochs`）** —— 生产推荐 `base_optimal`，对 4 户零污染已验
> 恢复树/深度模型需已安装 `requirements-ml.txt`。

### A. 基线组（sanity 下界；毫秒级；零 ML 依赖）

| 模型 | 原理 | 参数 | 效率因素 | 定位 |
| --- | --- | --- | --- | --- |
| `history_profile` | 按一天 96 槽位取训练均值，预测=查表 | `agg: median`（默认中位，抗异常） | O(n)，与配置无关 | 捕捉固定作息；778 最优 `R² 0.985 F1 0.968` |
| `proportional` | 目标分路占总功率固定比例 `p_target = pbus × 占比` | 无 | O(n) | 验收最低 sanity；分路与总线同步波动时有效 |
| `ridge` | 多输出岭回归闭式解 `W=(XᵀX+αI)⁻¹XᵀY` | `alpha:1.0`（正则强度）、`off_weight:5.0/off_thr_w:10.0` 关态加权 | **特征数 f**（`O(nf²+f³)`）—lags/rolling 加多直接变慢 | 线性关系强时最优；2842 最优 `R² 0.869 F1 0.984`（`transformer -0.165` 负效被 4选1救回） |

### B. 树模型组（秒级；`base_optimal` 未启用，按需开）

**`random_forest`**（`n_estimators:200, min_samples_leaf:2`）—sklearn 原生 multioutput

- 参数：`n_estimators` 树数（耗时线性）、`max_depth`（深→慢+过拟合）、`min_samples_leaf`（大→快且平滑）、`n_jobs:-1` 并行
- 效率：`n·log(n)·f`，CPU 核数线性加速

**`xgboost`**（`n_estimators:400, max_depth:6, learning_rate:0.05`）—每分路一回归器，有验证集自动早停 `early_stopping_rounds:30`

- 三角：`learning_rate` 小→需更多树（更慢更稳）；`max_depth` 每 +1 耗时翻倍；`subsample/colsample_bytree 0.8` 抗过拟合兼提速
- 效率：①树数×深度（串行）；②早停实际决定耗时；③分路数 k 线性
- 实测旧：778 曾 `R² 0.951` 最优（基线 0.827）

### C. 深度时序组（分钟级；`device: auto` 自动检测 GPU：CUDA→MPS→CPU）

共用适配器：`L=96` 滑窗 Seq2Point 逐点输出（头部复制填充，输出行数=输入行数）、Adam+MSE、验证早停 `patience` 回滚最优权重、种子可复现、state_dict 持久化。

**训练稳健性护栏（2026-09-02，0800 均值坍缩修复）：**
- 标签标准化：y 内部 z-score 后训练、predict 反标准化还原瓦数（修复 0800 `F1 0→0.92`）；
- batch 自适应：`min(配置值, max(16, n_train//8))` 保每 epoch ≥8 步；
- `UNDER_TRAINED` 告警：总步数 <500 时 WARNING；
- 坍缩检测：test 预测带宽 < `on_thr_w` 记 `meta.json.collapsed_models` + `PRED_COLLAPSED`。

**共同效率因素（重要性排序）：**

1. **`window`（第一旋钮）**：每样本 `(window,f)` 张量，计算量 ∝window（transformer ∝window²）；96→48 提速 2倍（transformer 4倍）
2. `epochs` 上限 × 早停 `patience`：实际耗时由早停决定（`2842 ep 47`）
3. 样本量 n：2842 4666 窗 vs 800 数百窗
4. `batch_size`（256）：大 batch 提吞吐
5. 设备：CUDA 5–20倍加速（transformer 收益最大）

| 模型 | 结构 | 特有参数 | 特有效率因素 | 实测（4户最优） |
| --- | --- | --- | --- | --- |
| `lstm` | LSTM 末隐→FC | `hidden_size:64, num_layers` | 时间步串行（96步循环最慢） | 未跑赢树/transformer |
| `cnn1d` | 3×Conv1d+池化→FC | `channels:32, kernel:5, num_blocks:3` | 时间维全并行（DL 最快） | 旧 789 全场相对最优 |
| `transformer` | 投影+位置编码→Encoder→末 token→FC | **`d_model:64 nhead:4 num_layers:2 dim_feedforward:128 window:96 epochs:150 patience:20`** | **自注意力 O(window²·d_model)**—三者最慢（大户 CPU ~25min）；砍 window 最有效；GPU 收益最大 | **2844/789 最优 `R² 0.678/0.962 F1 0.887/0.984`**；**lag5 前 0.39→0.49** |

### 实测结论速览（2026-09-18，4户 `base_optimal lag5 4模型`）

| 用户 | 最优模型 | test R² / infer F1 | 备注 |
| --- | --- | --- | --- |
| 2842 p1 | **ridge** 0.869 / 0.984 | `transformer 0.818` 被 lag5 负 0.165，但 4选1救场 |
| 2844 p2 | **transformer** 0.678 / 0.887 | `decision400→0.955` 可阈值救；日级全关 `fp 140→18` |
| 778 p2 | **history** 0.985 / 0.968 | `ridge 0.575` 崩，history最稳 |
| 789 p1+p2 | **transformer** 0.962 / 0.984 | `history 0.981` 但 R² -5.3 幅值弱 |
| 800 | **PAUSED**（OQ-16，p4待查） | 12篇p1结论降级 |

**调优建议：**
- 必须 4选1，单押必错；数据少时 `history/proportional` 先顶，30天后 `transformer` 自然赢
- 控制批跑时长：首旋钮砍 DL `window`/`epochs`；或 `models` 临时注释 transformer
- `d_model 128/layers3` 过拟合，别加宽
- GPU 无需改配置（`device: auto`），仅需 CUDA 版 torch

---

## 10.1 `configs/base_optimal.yaml` — 生产最优合一（推荐）

```yaml
# 5户最优合一（2026-09-17，lag5[5,1,2,3,4] + 4模型择优）— 生产推荐
experiment_name: guide_v2_1_optimal
quality: {max_missing_rate:0.9, min_coverage:0.15, min_score:70, gate_scope:full, day_gate:true, min_days:3, max_daily_missing_rate:0.9}
preprocess: {clip_negative:true, allow_negative_power:false, max_gap_interp:2, save_cleaned_csv:true, min_overlap:0.3, agg_strategy:{u:mean,i:mean,p:mean,pf:recompute}}
features: {lags:[5,1,2,3,4], rolling_windows:["1h","6h","24h"]}  # lag5 75min
dataset: {window:96, mode:seq2seq}
models:
  - {name: history_profile, params: {agg: median}}
  - {name: proportional}
  - {name: ridge, params: {alpha:1.0, off_weight:5.0, off_thr_w:10.0}}
  - {name: transformer, params: {window:96, d_model:64, nhead:4, num_layers:2, epochs:150, patience:20}}
```

| 维度 | `default.yaml` | `base_optimal.yaml` | 用哪 |
|---|---|---|---|
| `lags` | `[5,1,2,3,4]`（已同步） | `[5,1,2,3,4]` | 一致 |
| `models` | 3基线（transformer注释） | **4模型（含 transformer 150ep）** | **生产用 base_optimal** |
| `quality` | `day_gate true`（注释 min_on_day_ratio） | 同 | 一致 |
| 用法 | `... --base-config configs/default.yaml` | `... --base-config configs/base_optimal.yaml` | **全量/单户均推荐 base_optimal** |

验证：`4户 lags零污染` + `default/base_t5/base_optimal 三路 --user-key 批量一致`；`W-1 cross_gap 0`。

---

# 第二部分：用户 JSON 配置（--time-filter-config）

指南 §12 规定入口：`python scripts/run_batch_users.py --time-filter-config <path.json> --base-config configs/base_optimal.yaml`。
仓库示例：`configs/time_filters.json`（生产在用）、`configs/time_filter.example.json`（全字段示例）。

## 11. 顶级结构与键规则（§12.1）

```json
{
  "<device>_<user>": { ...单用户配置... },
  "_default":        { ...全局默认... },
  "_user_id_map":    { "<user_id>": "<device>_<user>" },
  "infer_model":     "ridge"
}
```

| 顶级键 | 意义 |
| --- | --- |
| `<device>_<user>` | 用户配置键，必须严格匹配 `RE <device>_<user>`（如 `800080252842_4206894986488`）；`discovery` 按此扫描 `data/trains|infers` 目录 |
| `_default` | 全局默认层：所有用户共享，被具体 `user_key` 配置覆盖（字段级合并；`train/infer` 为整节合并，见 §13） |
| `_user_id_map` | 单独 user_id 键的显式映射层（`{user_id: user_key}`）。§12.1 禁止隐式猜测—不在映射中的非法键直接报错 |
| `infer_model` | **全局推理模型**（`contracts.GLOBAL_CONFIG_KEYS`）：配置后所有用户默认用该模型推理；未配置走 `best_model`。可被 `_default` 与用户级同名字段覆盖 |
| 其他 `_` 前缀键 | 保留键（如 `_note_train_window` / `_OQ16_PAUSED`），不作为用户数据加载 |
| `_OQ16_PAUSED` | 800 专用：`800080270800_4200904302272: {"_OQ16_PAUSED": true}` 表示分路不在 p1/p2/p3，待 p4，当前 PAUSED |

**优先级**：具体 `user_key` 配置 > `_default` > 顶级全局键（如 `infer_model`）> `--base-config` yaml > 代码硬编码默认（`contracts.CONFIG_RULES`）。
合并来源记录在运行时配置 `_provenance`（日志可溯源每个值来自哪层）。

**`infer_model` 完整回退链**（`user_task.run_user_infer`）：用户级 `infer_model` → `_default.infer_model` → JSON 顶级全局 `infer_model` → `default.yaml` 的 `infer_model` → 该用户训练综合最优 `best_model`（`comparison.csv:overall_best`）。指定的模型必须在该用户训练清单内，否则 `MODEL_NOT_FOUND`。

## 12. 用户级标量字段（校验规则见 `CONFIG_RULES` + `user_config.py`）

| 字段 | 默认 | 取值/范围 | 意义 | 关联 |
| --- | --- | --- | --- | --- |
| `target_col` | `None`→回退链 | 字符串，如 `"p1"`、`"p1+p2"` | **目标分路（有效通道）**。复合目标按行相加（任一分量 NaN→复合 NaN，`skipna=False`）。缺省回退 p1→首个 pN（WARNING）。非目标通道视为无效数据丢弃 | §1-①必先定；错=2842 `SAE 0.372→0.0039`、800 PAUSED |
| `on_thr_w` | **10.0** | 0.001~5000 (W) | **真值阈（考卷答案）**：`target ≥ on_thr` 判真开。统一口径：状态判据、F1二值化、branch_sessions、全关判定、可辨识性、`pred_prob(sigmoid中心)` | 三档 10/50/60：p2取10，p1取50，p1+p2取60 |
| `decision_thr_w` | **30.0** | 0.001~5000 (W) | **判决阈（判卷标准）**：`pred ≥ decision` 才报开，叠加 `post_min_on/post_fill_short_off` 游程。**只进交付口径**（`inference_result.csv:pred_state/decision_thr_w` + `metrics_daily_chain/state_strategy`），**不进能力口径** `offline/metrics_daily`；可离线 `threshold_sweep 10-500` 零成本扫，不改权重 | 2844 `30→400 F1 0.887→0.955 (+0.068)`，`30→150 0.887→0.929` |
| `post_min_on` | 1 | ≥0 整数 | 状态后处理：开机段最短持续点数，短于此的开段视为噪声置关（15min/点）。当前 `1` 恒空（长度<1不存在），防闪断 | 压 FP 收益实证 `0.75→0.87` |
| `post_fill_short_off` | 3 | ≥0 整数 | 状态后处理：两开机段之间 ≤N 点的短关断填充为开（≤45min） | 全关日 `fp 150→152` 仅回填短关断 |
| `split_ratios` | [0.6,0.2,0.2] | 3非负和=1 | train/val/test 切分比例 | `metrics_by_split.csv` |
| `split_strategy` | `stratified_day` | `stratified_day / stratified / time / global_stratified / stratified_by_state` | 切分策略：`stratified_day`=按天分层打散（推荐）；`time`=时间顺序；`stratified_by_state` 按开/关分层自动均摊（800 B1b 新增） | 800 池内 `65%/12%/0%` →42%均摊 |
| `quality.day_gate` | true（全局） | bool | 覆盖 base 配置；用户级 `true` 免疫漂移（2844 显式 true） | 45天池 |
| `quality.min_on_day_ratio` | 0.2（可选） | 0~1 | 开机日占比门禁 | 31%→71% |
| `quality.gate_scope` | full | `full/train_range` | 覆盖 base 配置 | 缩窗时用 train_range |
| `weather_latitude` / `weather_longitude` | 30.59/114.31 | 经纬度 | 天气特征坐标（预留，校验已实现；未接入不参与训练） | — |
| `use_weather_features` / `use_temp_based_season` | true | bool | 天气/温度季节开关（预留） | — |

> **两阈解耦（`TUNING_GUIDE §4` 图解）：** `target --on_thr(10恒定)--> target_state` vs `pred --decision(30→400可调)+游程--> pred_state`。改 `on_thr` =改答案需重算审计；改 `decision` =改判卷标准可离线扫万次。能力口径看模型准不准，交付口径看用户收到准不准。

## 13. 时间过滤与切分锚定（§12.4，闭区间语义）

`train` / `infer` / `splits` 三个结构字段，元素均为 `[start, end]` **闭区间**字符串（支持日期 `"2026-06-30"` 或精确到秒 `"2026-04-02 17:45"`；日期右端点含全天）。

| 字段 | 意义 |
| --- | --- |
| `train.include` / `train.exclude` | 训练数据时间窗：先取 include 并集（**空/缺省=全部**），再剔除 exclude。质量门禁之后、切分之前执行；`_default.train.exclude [[2026-07-01,12-31]]` 防泄漏 |
| `infer.include` / `infer.exclude` | 推理数据时间窗，语义同上；`_default.infer.include [[2026-07-01,12-31]]` |
| `splits.train/val/test` 各自 `include`/`exclude` | **切分锚定**：include=硬锚定（这些天强制归入该集）；exclude=从该集精确排除。在 `split_strategy` 初始切分后应用，最后自动做空集修复（repair）。用于复现实验/指定验收日 |

示例（`configs/time_filters.json` 生产实况，2026-09-18）：

```json
"800080252842_4206894986488": {
  "target_col": "p1",
  "on_thr_w": 50.0,
  "decision_thr_w": 50.0,
  "post_min_on": 8,
  "splits": { "train": { "include": [] } },
  "train": { "include": [["2025-07-10","2026-06-30"]] },
  "infer": { "include": [["2026-07-01","2026-07-31"]] }
},
"800080252844_4206894986488": {
  "target_col": "p2",
  "on_thr_w": 10.0,
  "decision_thr_w": 30.0,
  "quality": { "day_gate": true, "min_on_day_ratio": 0.2 },
  "train": { "include": [["2025-07-10","2026-06-30"]] },
  "infer": { "include": [["2026-07-01","2026-07-31"]] }
},
"_default": {
  "train": { "exclude": [["2026-07-01","2026-12-31"]] },
  "infer": { "include": [["2026-07-01","2026-12-31"]] }
},
"800080270800_4200904302272": {
  "target_col": "p1",
  "on_thr_w": 10.0,
  "_OQ16_PAUSED": true
}
```

**注意**：`--base-config` 只加载单文件（`default.yaml` 不自动参与），`day_gate/lags` 必须在各 base 文件同步（`tests/test_config_defaults.py` 守卫）。

## 14. 字段生效位置速查（哪个字段影响哪个产物）

| 字段 | 影响的流程/产物 | 口径 |
| --- | --- | --- |
| `target_col` | 目标构建、无效通道丢弃、`branch_sessions.csv`、质量报告 branch 段、无效天判定、全部评估指标 | 全链 |
| `on_thr_w` | `pred_state/target_state`、`f1/accuracy/precision/recall/tp/fp/fn/tn`、`branch_sessions`、全关统计、`pred_prob` | 能力口径 |
| `decision_thr_w` + `post_min_on/post_fill_short_off` | `inference_result.csv:pred_state/pred_prob/decision_thr_w`、`metrics_daily_chain.csv`、`state_strategy_metrics.csv`（test）、`threshold_sweep` 曲线 | **交付口径**（offline 不进） |
| `split_ratios`/`split_strategy`/`splits` | 切分掩码 → `metrics_by_split.csv`、`metrics_daily.csv:split`、`qualified_days_detail.csv` | 切分 |
| `quality.day_gate/min_on_day_ratio/gate_scope` | 质量门禁 → `day_gate.json`、`quality_report`、`excluded_days.json` | 门禁 |
| `train`/`infer` 时间过滤 | 参与训练/推理的数据范围 → 质量报告「未使用」天数 | 范围 |
| `features.lags/rolling` | 特征矩阵列数 → 训练耗时、模型能力 | 特征 |
| `models` | 参与对比的模型清单 → `comparison.csv:overall_best` | 选型 |

---

## 15. 与 TUNING_GUIDE 联动与生产推荐

| 读本 | 定位 | 什么时候看 |
|---|---|---|
| `TUNING_GUIDE.md v1.1` | 人话版 8步 SOP（含输出怎么验、7项放行、战史、FAQ、月历） | 新设备 0→1 上线、运维巡检、报错先查 §9 战史 |
| `docs/CONFIG_GUIDE.md v3.0`（本文） | 精确技术字典（每项真实语义、默认值、取值范围、生效位置） | 改配置前查“这个键到底干什么”、审计对参 |
| `NILM_DATA_DICT.md v0.2.10` | 数据定义库（字段字典/粒度/命名/质量规则） | 加新数据源、核对 `RE_BUS/RE_BR`、倍率 |

**生产推荐流程（4户 Conditional Ready，800除外）：**

```powershell
# 1 训练（base_optimal 4模型择优，lag5）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs --user-key 900080270900_4200000000001
# 2 扫阈（不动模型，R≥0.95 护栏）
python scripts/threshold_sweep.py --csv outputs/900080270900_4200000000001/infer/*/predictions/inference_result.csv --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500
# 3 审计（T1-T4/I1-I10；形式 A 用户目录，形式 B 父目录+--user-key 均可，v2026-09-18 兼容）
# 新用户第1次不带期望看实测：
python scripts/audit_user_run.py --run-root outputs/900080270900_4200000000001
# 固化期望（模板 1036,77,21,1495/18 仅对 2844 有效，新用户如 800080270856 请用实测 580,19,9,2021/0）：
python scripts/audit_user_run.py --run-root outputs/900080270900_4200000000001 --expect-n 2629 --expect-confusion 1036,77,21,1495 --expect-off-day-fp 18
# 审计形式 B（批量 outputs 含多用户）：
# python scripts/audit_user_run.py --run-root outputs --user-key 900080270900_4200000000001 --expect-n 2629 --expect-confusion 1036,77,21,1495 --expect-off-day-fp 18
# 注：I8 期望为可选回归门禁，可省略；换用户/换阈值需重记，不可抄模板（见 TUNING_GUIDE Step6 黄框与 Q14）
# 4 全量批量（失败隔离，_DONE断点）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs
```

月历：**每月1号**全量重扫 `infer_result.csv 10-500`，漂移>20W 改 `decision_thr_w` 并 `git commit`；**每季度**训练窗延至近30天 `--force` 重训。

---

## 修订记录

- **v3.0（2026-09-18）**：对齐 `TUNING_GUIDE v1.1 + base_optimal lag5[5,1,2,3,4] + 4模型择优 + decision_thr_w 两阈解耦 + quality day_gate全局默认 + min_score70 + min_on_day_ratio0.2`；新增 §10.1 base_optimal对照、§12 decision_thr_w/quality新字段、§15 联动；同步 `REPORT v1.1/REPORT_TEST 18专题/800 PAUSED OQ-16`；`TUNING_GUIDE→CONFIG_GUIDE` 阈值/模型/门禁全量对账
- **v2.0（2026-08-18）**：新增第二部分「用户 JSON 配置」全字段说明（顶级结构/标量字段/时间过滤与切分锚定/字段生效位置速查）；yaml 部分同步近期变更—quality.max_daily_missing_rate、min_score 兼作逐天质量表阈值与双达标口径、preprocess.save_cleaned_csv、模型清单当前启用状态（仅3基线，其余注释）、质量报告新产物（daily_quality/qualified_days_detail/quality_advice）
- **v1.0（2026-08-14）**：初版—覆盖 default.yaml 全部配置项、8 模型说明与训练效率因素、5 户实测结论
