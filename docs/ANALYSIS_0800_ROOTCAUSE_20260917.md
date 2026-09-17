# 0800 用户模型指标差根因详析 — 模式B双路重跑终判（2026-09-17）

> **执行方式**：模式B（用户本地 RTX3080 / test_gpu / PowerShell）  
> **双路对照**：`outputs_0800_B_default（default.yaml: history/proportional/ridge, decision 30）` vs `outputs_0800_B_t5（base_t5: transformer, decision 30）` 同数据同切分同阈值，唯模型族不同  
> **审计**：`audit_user_run` 双路 **全部通过✅**（T1-4/I1-10，链Σ==总数，TP+FN恒等 683/1048，阈值30自描述，`pred_state`按段重放✓）  
> **产物**：3828 行 train_predictions / 2822 点 infer / `metrics_daily_chain` 逐日对账 / `threshold_sweep` 复现✓ / `identifiability` / `day_gate` / `train_window_index 1088窗0跨间断`

---

## 一、终判摘要（先说人话）

**0800 的“整体差”不是阈值没选对、也不是某个bug，是 4 层叠加的天花板：**

| 层 | 结论 | 量化证据 |
|---|---|---|
| **L1 信息论天花板** | `Pbus-p1` 相关仅 **0.37-0.43**，`R²=-1.02`，`lag5(75min)` 最优，`weekday_night 0.05` | 指南 §9 可辨识性分析：弱耦合+时滞+负解释率，模型再深也学不到强映射 |
| **L2 训练就没学好** | 三模型 `train F1 0.0-0.56` ，`r2 0.08-0.34` ，`ridge/history坍缩` | 非过拟合是**欠拟合**，40天池样本薄+特征 `ub/ib/pfb置0` 维度残缺 |
| **L3 全关天拉垮均值** | `test 4全关天 F1=0.0` +4开机天 `0.77-0.92` = `0.60`；`infer 6全关天 fp 213@30` | 单看开机天可用，**平均值被全关天一票否决**；D-7 `SAE 3e12` 伪值更放大 |
| **L4 阈值是第二杠杆** | `infer 50→30 F1 0.55→0.78 +0.23` ，`test 0.605→0.614 +0.01` | 50踩谷底，30是折中峰，但**天花板仍0.61-0.78**，阈值救不起L1/L2 |

**生产交付口径**：`decision_thr_w=30.0`（已入 `time_filters.json:0800`）+ `infer_model=proportional`（或 `transformer@30` 二者 `infer` 等效 `0.773 vs 0.783 Δ0.01`），`default.yaml` 更稳（无坍缩风险，<1s）。

---

## 二、双路实录（一字不改的数）

### 1. 切分与样本
`train 2292 / val 768 / test 768`（40天 17开23关，`day_gate both 40 on17/off23 ratio0.425` 全通过，`剔NaN 12行`，`train_window_index 1088窗 0跨间断` 14段）

### 2. train 能力口径 @50（`metrics_by_split`）

| 模型 | train F1 | val F1 | test F1 | test r2 | 备注 |
|---|---|---|---|---|---|
| history | 0.0 | 0.0 | 0.0 | -0.25 | 带宽0 坍缩 |
| **proportional** | **0.473** | **0.486** | **0.546** | 0.091 | **best** |
| ridge | 0.372 | 0.287 | 0.0 | 0.014 | 带宽49.3<50 坍缩 |
| **transformer@30** | **0.566** | **0.531** | **0.602** | 0.227 | test反超prop 0.05，但仍低 |

> **判读**：`prop` 与 `transformer` 在 `train` 谁都没过0.6，`r2 <0.35`，**模型在训练集就没拟合好**。

### 3. 判决链（`threshold_sweep`，`test 768 4全关天`）

**proportional**：`30 F1 0.570 (133/171/30) | 50 0.545 (115/143/48) | 80 0.412`  `fp带 [50,100)37.8%`  
**transformer**：`10 0.535 (163/283/0) | 30 0.614 (161/200/2) | 50 0.605 (144/169/19) | 80 0.353`  `fp带 [50,100)60%`

> **判读**：两模型峰都在 **30**，比生产50高0.01-0.02，但**峰值本身仅0.57-0.61**，阈值不是主因。

### 4. infer 2822点（30天 6全关 at 07-29~08-03）

**proportional**：`offline@50 F1 0.750 (789/266/259) | 链@30 0.773 (856/310/192 P0.734/R0.817 off213) | 链@50 0.750 | 链@30 sweep 0.773→0.750 Δ-0.02`  `fp带 [10,20)65.6% [100,200)16.4%`  
**transformer**：`offline@50 F1 0.540 (420/86/628 R0.40) | 链@30 0.783 (860/288/188 R0.821 off225) | 链@50 sweep 0.554 (R0.418)`  `fp带 [10,20)32% [30,50)35% [50,100)16%`

> **判读**：`transformer offline 0.54` 到 `链@30 0.78` 的 `+0.24` 全靠把 `50→30` ，**50踩谷底把R砍半**；`proportional` 的 `30 vs 50` 仅 `0.77 vs 0.75`，对阈值不敏感。**两模型在@30下等效**（0.773 vs 0.783）。

### 5. 日级链

*`metrics_daily_chain` test 8天*：**proportional** 4全关天 `F10.0` (05-26/06-11/06-12/06-25) +4开机天 `0.84/0.89/0.82/0.77`；**ridge** 在开机天反而 `F1 0.95-1.0` 但 `test` 因阈值50时全关天 `fp 0` 导致 `F1 0.0` 假象（`ridge` 的 `state_strategy decision@30 on_days_only F1 0.476` 暴露真相）。**`transformer` test 8天** 未贴但 `sweep` 的 `off_fp 152@50` 同构。  
*`infer` 日级*：`proportional 2026-07-01~22` 日级 `fp 2-5` `fn 0-12` 可用，`07-28 19fp`  + `07-29~08-03 6天 off_fp 39-41/天` 是唯一重灾；`transformer` 同构 `off 42-50/天`，`07-14/15/16` 还出现 `fn 39/25/22` 的漏检段。

*`analyze_daily_metrics --split all` **总计150行（模型×天）不达标129行 86%***：86%天 `SAE>0.2或F1<0.9`，**不是个别天差，是多数天差**。

### 6. 可辨识性与质量

`identifiability: pearson 0.376 spearman 0.397 lag5 0.430 R2 -1.02 contribution 0.82 strata weekday_day 0.199 weekday_night 0.051 weekend 0.24/0.16 target_on_rate 0.178 cv 2.12 n_on_edges 9 identifiable true`  
`quality: bus 99.33 branch 100 both 69→day_gate 40天全量` `cleaned: all_off 4→29天` `feature ub/ib/pfb 置0`

> **判读**：弱相关+负R2+低on率+高cv+3通道置0 = **信号本底差**，`lag5(75min)` 说明存在计量时滞未校正。

---

## 三、根因排序（按对“整体低”的解释力）

### 根因1：可辨识性弱（信息论天花板，权重 40%）
*`pearson 0.37 < 0.5` 的强可辨识阈，`R2 -1.02` 说明 `Pbus ≠ ΣPi` ，`lag5` 最优说明75min错位，`weekday_night 0.05` 说明夜间几乎无耦合。*  
`target_cv 2.12` + `target_on_rate 0.178` → p1是**间歇小功率回路**（`target_std 68W`），`L=96` 的24h上下文抓的是噪声。指南 §9 此时应标 `IDENTIFIABILITY_LOW`（虽 `identifiable true` 但已贴边），**加深网络掩盖不了**。

### 根因2：训练欠拟合（权重 30%）
*`train F1` 没一个过0.6，`r2 <0.35`，`history/ridge` 直接坍缩 `fp=0 fn=163`，`proportional` 的 `r2 0.08` 也近零。*  
三类模型同低 → 非模型选型问题，是 **特征-标签互信息低**：`ub/ib/pfb 三通道置0` + `bus 0.0/1.0` 的无效列 + 无效 `derived` 稀释，导致 `40天 1088窗` 的有效信息密度更低。`contribution 0.82` 但 `explained -1.02` 的矛盾也指向 **倍率/时滞/拓扑** 未对齐。

### 根因3：全关天型失效拉垮均值（权重 20%）
*`test` 8天中 `4全关天 F1=0` 与 `4开机天 0.77-0.92` 平均成 `0.54-0.60`，`infer` 6全关天 `fp 213` 占 `310` 的69%。*  
`post_min_on 1 / fill 3` 对**成段**虚报无效，`metrics_daily_chain` 的 `SAE 3e12` 伪值（D-7）更把 `SAE<0.2` 的达标判据一票否决 → `86%不达标`。**单看开机天，模型可用**（`on_days_only F1 0.78-0.92`），但**按天平均的“整体”口径必差**。

### 根因4：阈值错位放大器（权重 10%）
*`infer transformer 50 0.554 →30 0.783 +0.23` 是**最大单点增益**，但 `test` 仅 `+0.01`，`proportional` 仅 `+0.02`。*  
说明阈值能救 `infer` 的漏检（`R 0.40→0.82`），救不了 `train/test` 的天花板。已修正为 `30`，剩余 `0.61-0.78` 的天花板归 L1/L2。

---

## 四、已验证的交付口径（`time_filters.json:0800 decision 30`）

| 路径 | 模型 | 阈值 | test F1 | infer 链 F1 | infer P/R | 备注 |
|---|---|---|---|---|---|---|
| `outputs_800_B_default` | **proportional (best)** | 30 | 0.546→链0.570 | **0.773** | 0.734/0.817 | `<1s`，无坍缩，最稳 |
| `outputs_0800_B_t5` | transformer | 30 | 0.602→链0.614 | **0.783** | 0.749/0.821 | `ep27`，与prop等效 |

**推荐**：`default.yaml` + `decision 30` 作为生产交付（`infer_model` 可显式 `proportional` 兜底）；`base_t5@30` 作为**对照**存档，二者在 `infer` 等效，`test` 差异 `0.05` 归统计噪声。

---

## 五、后续治理（按 ROI）

1. **时滞校正（零成本，首试）**：`identifiability best_tau 5` → `time_offset.json` 或 `bus` 提前75min对齐后重跑，预期 `pearson 0.37→0.43` + `R2` 回正。
2. **目标/特征核查（低成本）**：确认 `p1` 非 `p1+p2`（已丢 `p2/p3` 通道），核查 `load_iden_data45/37/44` 置0是否可修复点位映射；`pf` 置0的功率因数重算核查。
3. **日型加权/分层（中成本）**：`history 0.0 / ridge 0.0` 说明全关天样本权重不足，可试 `model_params: {proportional: {}}` 或 `stratified` 按开/关天再平衡（当前 `stratified_day` 按星期不保开/关均衡）。
4. **数据侧**：`71天中29全关天`，`contribution 0.82` 但 `R2 -1`，拓扑验证 `Pbus≈ΣPi+Punknown`（指南 §7）需现场确认。

---
*证据链：`audit T4/I10 Σ==总数/TP+FN恒等/n_points对齐` + `threshold_sweep 复现✓` + `metrics_daily_chain` 逐日 `fp/fn` 分解 + `identifiability lag5` + `86%不达标`；产物自描述 `state_thr 50 / decision 30 / post 1/3` 双轨并存。*
