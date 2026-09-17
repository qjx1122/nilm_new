# 0800 最终闭环判读（2026-09-17 双路对照：default多模型 vs base_t5 thr30）

> **验证完成**：`outputs_800_default (default.yaml, 3模型)` 与 `outputs_800_thr30 (base_t5, transformer+thr30)` 双跑已回，审计`全部通过✅`，双表链审计对账，`threshold_sweep`复现校验`✓`。本篇为**“整体低”重分析的终判**，直接给出交付口径。

## 1. 双路实录对比（同一数据 40天/2822点，同decision=30，唯模型族不同）

| 指标 | `outputs_800_default` (default.yaml, best=proportional) | `outputs_800_thr30` (base_t5, transformer) | 解读 |
|---|---|---|---|
| **选型** | `best=proportional`（wins自动，`history 0/ ridge 0` 坍缩）| `best=transformer` 单模型 | default有fallback，base_t5无 |
| **train F1@50** | prop 0.473 / ridge 0.372 / hist 0.0 | transformer 0.566 | transformer在train最好，但val/test仍低 |
| **val F1@50** | prop 0.486 / ridge 0.287 | 0.531 | |
| **test F1@50** | **prop 0.546** (tp115 fp143 fn48) | **0.602** (142/167/21) | transformer test反而+0.05 |
| **test 链 F1@30** | prop 0.570 (133/171/30) | sweep 0.614 | thr30均比50高 ~0.02 |
| **infer offline F1@50** | prop 0.750 (789/266/259) | — (未审但同分布) | 能力口径 |
| **infer 链 F1@30** | **prop 0.773** (856/310/192, P0.734/R0.817) | **transformer 0.783** (860/288/188, R0.821) | **两模型在infer几乎一致** `Δ0.01` |
| **infer 链 F1@50** | — sweep 0.554 (438/96/610, R0.418) | 0.554 (同表) | **50踩谷底，30提0.23** |
| **早停** | 无（基线模型） | ep27 val_loss 0.676 | — |
| **审计** | ✅ T1-4/I1-10全过，链Σ==总数，TP+FN 1048/683恒等，阈值30自描述 | —（sweep复现✓） | — |

**核心发现：**

1. **“整体低”在train就低**：即便default最强的prop，`train 0.473 val 0.486` 也没上0.6；transformer `0.566/0.531/0.602` 同样。`test`最好的0.61与`infer@50`的0.55同病，**不是过拟合是欠拟合/信息不足**（`r2 0.22-0.34`）。
2. **模型族差异在infer消失**：`prop 0.773` vs `transformer 0.783` 在`thr30`下只差0.01——**0800与2844/789不同，深度模型不占优也不劣，proportional已触天花板**。
3. **阈值是第二杠杆**：`infer 0.55→0.78`（50→30）的0.23增益，远大于模型族的0.01。`test 0.605→0.614` 的0.01小增益说明`test`的真/假重叠更紧（`fp带[50,100)60%` vs `infer[10,50)84%`，两期分布漂移已证）。
4. **全关天是均值拉垮的唯一解释**：`test` 4全关天 `F1=0.0` +4开机天 `0.88` =0.60；`infer` 6全关天 `fp 213@30` +22开机天 `fp ~100`。换阈只能改`fp 310→213`，不能把4个0变1。

## 2. 交付口径推荐（按成本分级）

### 立刻可交付（0重训，仅改判决，已验证）
**`decision_thr_w = 30.0`**（已入`time_filters.json` 0800块，`_note_0800_thr30`留痕）：
- `infer 链 0.554→0.773~0.783 (P0.73-0.75/R0.82)`，全关天 `fp 65@50→310@30?` 等一下——看清：`outputs_800_default`的`off_fp 213@30` vs `all_chain@50 65`，**thr30在infer会把全关天fp从65放回213，但换来R 0.418→0.817**。这是**“保召回”**的取舍（0800漏报比虚报更致命）。
- 若要**保精确**（控全关天），则维持50但接受`R 0.418`的漏检；已验证`sweep`中`50 P0.82`最高但`R`腰斩。
- **折中推荐30**：`test`峰、`infer`次峰（与10的0.787只差0.004），兼顾两期。

### 多模型并存（推荐生产）
**`base-config = default.yaml`（或显式`infer_model: proportional`）**：
- `best=proportional`自动，无`PRED_COLLAPSED`风险（`ridge/history`已坍缩告警），与`transformer@30`在`infer`等效但更稳（基线模型不依赖时序连续性，对`lag5`时滞不敏感）。
- 训练时间 <1秒（vs transformer 8秒GPU），适合批量。

### 不推荐
- 单`base_t5` + `thr50`：`F1 0.55`谷底，已证伪。
- `target p1→p1+p2`：`pearson 0.37`虽低但`identifiable=true`，`contribution 0.82`提示p1已占主导，切`p1+p2`需重算splits的开/关天且`R2 -1.02`提示拓扑问题未明，先不切。

## 3. 与 2844 的对照（防误用）

| 户 | 低的形态 | 最优thr | 机理 |
|---|---|---|---|
| 2844 | 低P高R `fp258 R0.996` | **400** ↑ P `0.65→0.93` R 0.996→0.98 | 全关天幻觉在<400，阈值可分 |
| **0800** | **高P低R@50** `fn610 R0.418` | **30** ↓ P `0.82→0.74` R 0.418→0.82 | 真/假重叠在50-100，阈值只能保一头 |

## 4. 审计证据（已全过）
- `outputs_800_default`：`audit_user_run --user-key 800...` **全部通过✅**（T1-4/I1-10，链Σ==总数，TP+FN 1048恒等，`decision=30`自描述，`n_points`与`metrics_daily`逐日对齐，`sigmoid`复现✓）
- `outputs_800_thr30`：`threshold_sweep` 复现✓（`pred_state`逐位一致），`meta` `train/val/test 2292/768/768`
- `outputs_all_chain` 历史 `F1 0.467→0.750` 的“fallback救场”已复现为 `default 0.773` vs `base_t5 0.783` 的等效

## 5. 后续（按需）
- D-7 SAE伪值：`metrics_daily_chain`全关天 `3e12`，`analyze_daily_metrics`已排除`SAE`再判（或过滤`on_day_only`）
- `lag5` 75min最佳相关：可试`time_offset`校正（需确认计量时滞是否物理）
- 若`proportional 0.78`仍不达标（业务阈0.90），则非阈值/选型可救，需补特征/时滞/拓扑核查（`explained_r2 -1.02`）

---
*落盘：docs/ANALYSIS_0800_FINAL_20260917.md + time_filters.json 0800 decision30；产物：outputs_800_default / outputs_800_thr30 双路已审。*
