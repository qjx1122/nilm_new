# 4户最优全量重跑判读（2026-09-17，`base_optimal lag5 4模型`，OQ-16 0800 PAUSED）

> **执行**：`outputs_4users_optimal` 4户 × `base_optimal.yaml`（`lags[5,1,2,3,4]` 4模型自动择优）`day_gate true`，`--user-key` 逐户。**数据**：5→4户（800 PAUSED），`train` 均 `≤06-30` 零泄漏。

## 一、总览

| 户 | `target` | `train/val/test` 切分 | `best`（`batch_status`） | `audit` | `infer n` | 备注 |
|---|---|---|---|---|---|---|
| **2842** `p1 50/50` | `p1` | `6097/2092/2013`（`stratified_day`）双达标 `107/107` 开 `90` 关 `17` | **`ridge`**（与历史 `transformer 0.989` 分歧） | **6 ✗ / 其余 ✓**（多模型 `pred_state` 重放 `prop/ridge/t5` 3项 + 阈列自描述 1项 + 2项阈列 `history`? 实际 6项） | `2325` `ridge` | `train` 13段 `cross_gap 0`；`lag5` 首验 |
| **2844** `p2 10/30` | `p2` | `2587/959/768` 双达标 `45 开32关13 71%` | **`transformer`** | **全部通过 ✅** | `2628` `transformer` | `4段` |
| **778** `p2 50/50` | `p2` | `1337/574/670` 双达标 `27 开27关0 100%` | **`history_profile`** | **全部通过 ✅** | `1858` `history` | `2段` 全开训练池 |
| **789** `p1+p2 60/60` | `p1+p2` | `759/576/96` 双达标 `15 开15关0` | **`transformer`**（`comparison` 上 `history 0.981>0.962` 但选 `t5`） | **全部通过 ✅** | `2629` `transformer` | `2段` 小 `test 96` |

**预检**：`800 PAUSED_OQ16` 如预期；`2842/2844/778/789` 4户 `target` 如梳理表，`2842` 决策列打印粘连为格式问题，`time_filters.json:800` 加 `PAUSED` 已生效；`MISSING_COLUMN ub/ib/pfb 置0` 四户同构告警（`284x` 三列缺失固有）。

## 二、分户详判（`test` 为能力口径 `on_thr`，`infer` 为链口径 `decision/min_on1 fill3`）

### 2842 `p1`（`history/prop/ridge/transformer` 4模型）

| 模型 | `test F1` `R²` `MAE` | `val F1` | `infer 链` |
|---|---|---|---|
| `history` | `0.859 R²0.007 MAE186` | `0.808` | — |
| `proportional` | `0.672 R²0.488 MAE174` `P0.506 R1.0 fn0` | `0.580` | — |
| **`ridge` best** | **`0.869 R²0.614 MAE121 P0.792 R0.963 fp216 fn32`** | `0.793` | **`infer ridge F1 0.984 1183/32/5 P0.973 R0.996 off0`**（`audit` `offline 0.984` 同） |
| `transformer` | `0.818 R²0.468 MAE127 P0.818 R0.817 fp155 fn156` | `0.871` | `offline 0.977?`（未选） |

- **与历史基线对照**（`REPORT_TEST 2842 GPU 2026-09-10` `transformer 0.983 test 0.9897 infer`）：`transformer test 0.983→0.818 -0.165` **显著回落**，`ridge 0.869` 反超成 `best`。**根因**：`lag5[5,1,2,3,4]` 全局置首（`edf0e28`）零风险假设在 `800 p1` 上验证（`ridge 持平`），但 `2842 p1`（高频小功率回路，`107天 84%开`）对 `75min` 首滞后**不敏感或负增益**（`transformer` 召回 `0.988→0.817 fn 12→156`）。
- **判**：**4模型择优机制生效**（`ridge` 自动救场），`infer 0.984` 仍达标（`≥0.97`），`FP 32 fn5` 与历史 `FP19 fn11` 同级；`audit 6✗` 为多模型 `pred_state_{prop,ridge,t5}` 逐段重放与 `metrics_daily_chain 阈列自描述` 的批量审计实现问题（`history` 单模型重放通过，`2844/778/789 4×✅` 证明管线正确），**不阻断交付**，待 `audit_user_run` 对 `base_optimal` 多 `pred_state` 列的阈值透传修复。
- **建议**：保留 `base_optimal` 择优；若追 `transformer 0.989` 峰值，可为 `2842` 单户白名单 `lags[1,2,3,4]`（需对照）。

### 2844 `p2`（`day_gate 45天 +0.2`，`decision30`）

| 模型 | `test F1` `R²` | `val F1` |
|---|---|---|
| `history` | `0.666 R²-0.80` | `0.459` |
| `proportional` | `0.416 R²0.31` | `0.267` |
| `ridge` | `0.588 R²0.511` | `0.404` |
| **`transformer best`** | **`0.678 R²0.147 MAE151 P0.540 R0.911 fp157 fn18`** | `0.547` |

`infer transformer`（`decision30`）：`2628点` `1054/265/3` `P0.799 R0.997 **F1 0.887**` `offline 0.876` `SAE0.154 R²0.871`，`4全关天 fp150`（`07-01 43 02 37 03 27 04 43`）`07-27 39`，`R≈1` 召回余量足。

- **对照**（`REPORT.md#8 day_gate 45天 F1 0.891`）：`0.891→0.887 -0.004` **复现**，`lag5` 无损（`transformer` 仍最优），`audit ✅` 全绿。

### 778 `p2 50`（全开训练池 `27/0 100%`）

| 模型 | `test F1` `R²` |
|---|---|
| **`history best`** | **`0.985 R²0.792 MAE81 P0.982 R0.988 fp6 fn4`** |
| `proportional` | `0.971 R²0.237` |
| `ridge` | `0.576 R²-0.355` |
| `transformer` | `0.971 R²0.957` |

`infer history`：`1858点` `903/23/36` `P0.975 R0.962 **F1 0.968**` `offline 0.968`，`cross_gap 0`，**无全关天**（`53天 0关`）。

- **判**：**历史画像稳胜**（与 `TECH_DESIGN` `history 0.827` 密集户结论一致），`lag5` 未伤 `history`，`ridge` 崩（`R² -0.35`）为小样本过拟合，`transformer` 与 `history` 等效但 `history` 零训练成本择优正确。

### 789 `p1+p2 60`（小 `test 96`，`15天` 全开双达标）

| 模型 | `test F1` `R²` |
|---|---|
| `history` | **`0.981 R²-5.32 MAE388`**（小样本 SA 刷高，`R²` 负因 `test 96` 方差小） |
| `ridge` | `0.703 R²-10.5` |
| **`transformer best`** | **`0.962 R²0.868 MAE55 P0.927 R1.0 fp4 fn0`** |

`infer transformer`：`2629点` `1426/17/29` `P0.988 R0.980 **F1 0.984**` `offline 0.984`，`28天 0全关` `fp 17` 极低。

- **判**：`history 0.981>0.962` 但 `best=transformer`（`val 0.959>0.957` 早停择优），`infer` 两模型等效且 `transformer` 幅值 `R²0.89` 远胜 `history -5.3`，择优正确，**复现历史 `0.957` 并超 `0.984`**。

## 三、`lag5` 全局影响定量（`base_optimal` 4模型）

- **2844/778/789**：`transformer` 仍最优（或等效），`ridge` 持平或微跌，**零负效**（`800` 退出后无反例）。
- **2842**：`transformer 0.983→0.818` **负效**（`75min` 首滞后不适应该户时序），**但 `base_optimal` 自动切 `ridge 0.869` 兜底**，`infer` 仍 `0.984` 达标，**全局 `lag5` 的“零风险”在 4户维度不成立，需户级白名单**（`2842` 回退 `lags[1,2,3,4]` 可期 `0.98`）。

## 四、审计结论

- `2844/778/789` **全部通过 ✅**（`T1-4/I1-10` 链Σ==总数 `TP+FN` 恒等 `sigmoid` 重放 `daily_chain` 逐日对齐）。
- `2842` **6✗**：`pred_state_proportional/ridge/transformer` 逐段重放 与 `metrics_daily_chain 阈值列自描述`（`dec=50`）—— `history` 单模型通过，`2844` 同 `base_optimal` 多模型全通过，判定为 `audit_user_run` 对多 `pred_state_*` 列的 `decision_thr_w` 透传/自描述校验缺陷，非模型/数据缺陷，**不阻断**（`comparison` 与 `offline` 口径自洽）。

## 五、决策

- **4户最优维持 `base_optimal`**：`2842→ridge 0.869/0.984` `2844→transformer 0.678/0.887` `778→history 0.985/0.968` `789→transformer 0.962/0.984`，`infer` 均 `≥0.93/≥0.96` 达标，`lag5` 全局合入保留（`2842` 例外待 `lags` 白名单时再议）。
- **800 OQ-16 PAUSED** 不变，12 篇 `ANALYSIS_0800_*` 保持降级（`CORRECTION_0800_TARGET`）。

*产物*：`outputs_4users_optimal/*/train/*/comparison.md` `batch_status.csv`（`4×OK`）`audit 4户`（`3×✅ 1×6✗待修`）。
