# 4户最优配置梳理（0800 OQ-16 PAUSED）（2026-09-17，`arena/01a0896c-nilm-new` HEAD → `29b3927`）

> **OQ-16 更新（2026-09-17）**：`800080270800` 分路不在 `p1/p2/p3`，原 `p1` 全量历史结论降级为错误目标参考，**暂时不对0800做分析**；本文为 `5户→4户（2842/2844/778/789）` 最优快照，800 行见 `CORRECTION_0800_TARGET_20260917.md`。

> **目的**：收敛 4 户 `target/on_thr/decision/train/splits/quality/base/lags` 的最优版本，定 `batch 5户全量` 的回归基准。**数据口径**：`day_gate=true` 全局默认（⑮）、`W-1 分段构窗` 已修复、`target 2842/2844` 已按 OQ-13 正名为 `p1/p2`。

## 一、全局最优（全户共享，`configs/default.yaml` `configs/base_t5.yaml` `29b3927`）

| 键 | 最优值 | 证据 | 影响 |
|---|---|---|---|
| `features.lags` | **`[5,1,2,3,4]` (首滞75min)** | 0800 `transformer R² 0.39→0.49 +0.105 F1 0.69→0.75 +0.059 on_day_fp -56%`（`docs/ANALYSIS_0800_LAG5_RESULTS`），`ridge/history` 持平 `±0` 零风险 | 已合入 `default.yaml:44` `base_t5.yaml:33`（`edf0e28`），全户零改动担忧 |
| `quality.day_gate` | **`true` 全局** | 2844 `45天 vs ⑬手窗29天 +55% 开机15≈14`（`REPORT.md#8`），2842 `107/112≈无感`，`base_t5` 已补 `day_gate` 免回退 | `configs/default.yaml` `base_t5.yaml` 同步；`_DONE` 通过 |
| `dataset.window` | `96 (24h)` | W-1 修复后 `cross_gap 0` 守卫 | — |
| `models` 选型 | **分户**（见下表） | 0800 `proportional≈transformer`；2842/2844/789 `transformer` 优；778 `history/ridge` 稳 | 见 `base_optimal` 合并方案 |

> **落版**：`experiment_name guide_v2_1 (default) / t5_transformer_retrain (base_t5)`，`seed 42`，其余 `bus_field_map` 12列未变（`ub/ib/pfb` 置0 已确认无需映射，`CORRECTION_20260917`）。

## 二、分户最优（`configs/time_filters.json` HEAD 40天-07-31/08-03 窗）

| 户（`user_key`） | `target` `on_thr` `decision` | `train` 窗 | `splits`（池=`train`） | `quality` | `base` 最优 | 关键指标（合法口径） | 版本/审计 | 备注 |
|---|---|---|---|---|---|---|---:|---|
| **2842** `800080252842_4206894986488` | `p1` `50` `50` (沿用 p1非零中位710W/峰848W 可容，暂不调) | `2025-07-10~06-30` 全窗 | 无（走全局 `stratified_day` 随机分层） | —（走全局 `day_gate`，预检双达标 `131/146`） | **`base_t5.yaml` transformer**（`default` 的 `ridge` 次之） | `test F1 0.9833 MAE91.5 R²0.81 SAE0.074` `infer F1 0.9897 MAE250.9 R²0.65 P0.984 FP19`（`REPORT_TEST 2842 GPU实录 2026-09-10`）`cross_gap 0 窗4666` | `W-1修复后+OQ-13` `audit✅` | `on_thr 50` 为 `p1+p2→p1` 沿用（`STATUS D-1` 观察）；`day_gate` 后重跑池 `107天`（OQ-14）待下轮重建但当前已为最优 |
| **2844** `800080252844_4206894986488` | `p2` `10*(缺省)` `**30**` (`on_thr 10` 硬编码默认) | 同上全窗 | 无 | **`day_gate true + min_on_day_ratio 0.2`** | **`base_t5.yaml` transformer** | `day_gate 45天池 开32关13 infer F1 0.891 P0.804 R0.999 SAE0.09 R²0.88 MAE49.5`（`REPORT.md#8` 新数据已补数）；`放行B` 时 `F1 0.852 R²-0.26`(旧)作废 | `2026-09-14 day_gate试点` `audit✅` `2629点` | **阈值双轨**：生产 `30`（`v5方案A` 2026-09-15），**7月最优 `400` F1 0.955 R0.98 fp -85%**（`STATUS ⑯ thr400` 待拍板），月度 sweep 定阈；`off_weight 3.0` 已证伪回退 `1.0` |
| **778** `800080270778_4200903422131` | `p2` `50` `— (=50)` | `05-21~06-08` + `07-01~07-08` | `train 06-24~06-25` (1段) | — | **`default.yaml` (history/prop/ridge)** 最优待 `t5抽检` | 历史 `ridge` 稳（W-1后 `test F1 0.36→0.35 FP314` 基线级），`TECH_DESIGN` 预研 `history 0.827` 密集户可用 | `2026-09-10 W-1对照` `cross_gap 0` | `_default.train.exclude 07-01~12-31` 被 `778 train.include 07-01~07-08` 显式顶掉（`W-3` 整节替换语义，符合“该户7月上旬参与训练”意图，已记录） |
| **789** `800080270789_4206680982373` | `p1+p2` `60` `— (=60)` | `05-21~06-04` (14天连续) | 无 | — | **`base_t5.yaml` transformer** | `train R²0.939 F1 0.966` `test F1 0.962 R²0.43` `infer F1 0.957 P0.974 R0.94 开机天28/28 中位0.967`（`REPORT_TEST 789 2026-09-10`） `窗570 cross_gap0` | `W-1后合法基线` `audit✅` | `p1+p2` 未复核但该窗 `14天连续` 受 `W-1` 影响 `0.9709→0.9566 -0.014` 可忽略；待 `800/778` 一并复核 `target` |
| **800** `800080270800_4200904302272` | ⚠️ **PAUSED OQ-16** (`p1/p2/p3` 均排除) | — | — | — | — | **全量 p1 历史结论降级为错误目标参考**（同 OQ-13），`target` 待重定（如 `p4`），**暂时无最优**（见 `CORRECTION_0800_TARGET_20260917.md`） | — | — |

**统一 infer 窗**：5户均为 `2026-07-01~07-31`（800 延至 `08-03` 除 `07-23~26` 缺数段）；`train` 均 `≤06-30` 零泄漏（`789 06-04` `778 06-08/07-08` `800 06-29`）。

## 三、候选 vs 稳定

- **2844 `thr 30 → 400`**：`30 R0.999 fp258` 保召回 vs `400 R0.98 fp18 -85%` 保精确；`报告已建议“维持400交付7月+月度sweep校准”`（`STATUS ⑯`），重跑时同时回收 `sweep@30/400` 再定量。
- **800 `B_default → OFF8`**：`0.773→0.783 +0.01` 但 `R` 降 `0.13`；若产线 `fp` 成本> `fn` 则切 `OFF8`，否则维持 `B`。
- **778 `default → t5`**：尚无 `t5` 实录，`default` 为稳妥；`base_optimal` 可一并试 `transformer` 自动择优。

## 四、batch 最优执行口径

- **分户最优 `base`** 不同 → 推荐新增 `configs/base_optimal.yaml`（`lag5` + 4模型 `history/proportional/ridge/transformer(150/20)`），单次 `run_batch` 自动按 `comparison.csv` 选 `overall_best`（800→prop、2842/2844/789→transformer、778→自适应）。
- **fallback**：无 `base_optimal` 时，分两批 `default`（800/778）+ `base_t5`（2842/2844/789）亦等价（详见 `EXECUTION_PACKAGE_5USERS_OPTIMAL`）。

## 五、证据链

`REPORT.md#4/#8` `REPORT_TEST 2842/789/2844/800` `ANALYSIS_0800_FINAL/LAG5/OFF8/T5_FROM_SCRATCH` `CORRECTION_B_PHASE_AND_OFFFILTER` `EXECUTION_PACKAGE_5USERS_REGRESSION` 全量 `审计✅`（`audit_user_run T1-4/I1-10` `链Σ==总数` `sigmoid逐位`）。

*本篇落盘即定 `5户全量回归` 的“最优基线”快照；执行包按此快照一键重跑。*
