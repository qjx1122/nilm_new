# 0800 分类+回归 进一步优化方案（2026-09-17，基于 B/B1/B1b 实录）

> **现状锚点**（`B_default` 生产口径 40天17开23关 + `hand-anchor` 10/14-3/5-4/4，`decision30`）：
> - **分类**：`proportional infer链@30 F1 0.773`（`B1b 0.629-0.689, B1 0.739`），`transformer 0.783→B1b 0.691`；`5关 fp 253/318 43-58%` 全关虚开，`14天开段 fp 27-43` 抖动，`P~0.51` 精度瓶颈，`R 0.99` 召回余量大
> - **回归**：`infer offline R² 0.36/0.39`（`train R² 0.21-0.74`），`MAE 65-77` `SAE 0.22-0.25`（`B1b ridge/transformer`），`train R²<0.35` 欠拟合，`test R² 0.28-0.42` 不泛化，`pearson 0.37` 低相关
> - **根因已定量**：`lag5 75min` 时滞（`R 0.37→0.43` 预期）+ `ub/ib/pfb` 三通道 `MISSING_COLUMN_ZERO_FILLED` 置0（特征天花板）+ `TARGET_EDGE_BURIED_IN_BUS` 可辨识度低（`p1` 边沿埋于总线）+ 静态阈 `30` 跨池漂移（`B1b test` 峰已移至 `100`）

---

## 一、分级目标

| 指标 | 现状（B_default/B1b） | 阶段一目标（P0 1周） | 阶段二目标（P1 2-3周） | 终局目标（工业可用） |
|---|---|---|---|---|
| **infer链 F1@30** | 0.773 / 0.67 | **≥0.80** (+0.03) | **≥0.85** | ≥0.90 |
| **infer R²** | 0.36 / 0.39 | **≥0.50** | **≥0.65** | ≥0.70 |
| **SAE** | 0.25 / 0.22 | **≤0.20** | ≤0.15 | ≤0.10 |
| **5关 fp** | 253/318 (43-58%) | **≤180** (-30%) | ≤120 | ≤60 |
| **off_day_fp @test** | 114@30 | ≤60 | ≤30 | — |

*判据*：分类 `F1+0.02 且 SAE不恶化>10%`（`B1/B1b` 沿用），回归 `R²+0.10 且 MAE不恶化`。

---

## 二、P0 零成本首试（预期 +0.05-0.10，1次双路复跑可验）

### P0-1 时滞校正 `lag5 75min`（优先级 ★★★）

- **证据**：`pearson 0.37→0.43`（`+0.06`），`B1/B1b 07-11~15 fp 27-43` 且 `07-16/17 fp 9-11` 骤降，`branch_sessions` 日内开机段与 `pbus` 峰错位 5 窗
- **实施**：`nilm/preprocess/features.py` `build_features(bus, lags=[5,1,2,3,4])` 首位改为 `5`（或 `nilm/common/schema.py` `MODEL_STEP=15min` 对齐），**零代码改配置**：`configs/time_filters_0800_LAG5.json` 在 `800080270800` 块增 `"feature_lags": [5,1,2,3,4]`（需 `features.lags` 透传，`~30min` 开发）
- **预期**：`R² 0.36→0.45`, `F1 0.67→0.72`（`fp 41→20`），`test R² 0.28→0.35`（`B` 0.60→0.68 曾因 `lag5` 改善）
- **验证**：`audit` `train R²` 抬升 + `infer 07-11~15 fp` 收敛 + `threshold_sweep` 峰回 `30`

### P0-2 三通道修复 `ub/ib/pfb`（★★★，与 P0-1 可叠加）

- **证据**：`MISSING_COLUMN_ZERO_FILLED load_iden_data45/37/44` 置0 全程告警，`ub/ib` 缺失致无功判关乏力（`pfb` 亦0），`IDENTIFIABILITY_LOW` 直接关联
- **实施**：
  1. `data/trains/800080270800_4200904302272/*.csv` 列头核对（`load_iden_data45` 是否应为 `load_iden_data5` 等笔误；`field_map` 在 `bus_field_map` 中 `ub` 映射错误待查）
  2. 若确无此列，改 `bus_field_map` 剔除 `ub/ib/pfb` 或映射至 `ure/ire/pfe`（`CsvBusLoader` 已有 `derive_phase_from_ptotal` 兜底）
  3. `cleaned/bus_cleaned.csv` 12列中 `ub/ib/pfb` 非0 验证
- **预期**：`R² +0.05-0.08`, `P 0.51→0.60`（无功辅助判关），`5关 fp -20%`
- **验证**：`data_schema_report.json` 无 `MISSING_COLUMN` + `feature importance` `ub/ib` 非0

### P0-3 阈值月度校准（★☆，零训练）

- **证据**：`B_default` 峰 `30` vs `B1b` 峰 `100`（`F1 0.629→0.689 off_fp 114→28`），静态 `30` 跨池/跨月漂移已实证（` thr400` 案 `500` vs `30`）
- **实施**：`scripts/threshold_sweep.py --csv .../train_predictions.csv --pred-col pred_proportional --split test --thresholds 10,30,50,80,100,150` 每月 `--infer` 前扫一次，`decision_thr_w` 按 `F1` 峰动态写入 `time_filters`（`30→100` 试点）
- **预期**：`F1 +0.02-0.06`（`B1b 0.629→0.689` 已证），`fp` 可控
- **验证**：`state_strategy_metrics.csv` `decision+runs` 行 `F1` 峰选

> **P0 合计预期**：`F1 0.67→0.78`（回 `B_default` 并超），`R² 0.36→0.50`，`fp 253→150`，**1 周内双路 `LAG5+UB` 叠加可验，`audit` 全绿即合入**。

---

## 三、P1 低成本增强（1-2周，1-2次训练）

### P1-1 特征增强（★★）

- **滞后+差分+无功**：`lags` 扩至 `[5,1,2,3,4,6,8]` + `rolling 1h/6h/24h` 已有，增 `diff1 = pbus.diff(1)`, `reactive = sqrt(s²-p²)`（若 `ub/ib` 修复后），`hour` 分箱交互
- **预期**：`R² +0.05`, `F1 +0.02`

### P1-2 模型侧（★）

- **ridge 超参**：`alpha` 网格 `0.1/1/10`（现 `ridge test 0.722` 已最优，可微调）
- **transformer 早停 `33` 已浅**：`epochs 100→150, patience 10→15` 试深训（`B1b train 0.91 / test 0.65` 泛化隙大，防过拟），或 `proportional+ridge` 集成（`proportional` 稳 `ridge` 准）
- **预期**：`R² +0.03-0.05`

### P1-3 样本侧（★）

- `day_gate 38天` 已全量，若 `lag5` 后仍 `IDENTIFIABILITY_LOW`，试 `train include` 扩至 `05-10~07-10`（`+10天` 全关稀释）或 `off_day_weight=1.5` 小权重（`2844 3.0` 已证大权重失效，小权重可试）

---

## 四、P2 架构级（按需立项）

- **序列建模升级**：`base_t5` `transformer` 已试，`lstm`/`tcn` 对照（`789` 曾 `lstm` 优）
- **天气/日历**：`use_weather_features true` 已开，核对 `lat/lon 30.59/114.31` 与 `temp_based_season` 对 `0800` 7-8月高温期是否有效
- **可辨识性重构**：`p1` 单分路边沿埋没，试 `target_col p1+p2`（`789` 曾 `p1+p2` 达 `R² 0.96`）或 `p1` 差分目标

---

## 五、实施路线与验证

```
W0（本周）：P0-1 LAG5 + P0-2 UB 修复 → 生成 configs/time_filters_0800_LAG5_UB.json
           → 双路 run_batch (default/t5) → audit + threshold_sweep test/infer → 判 `F1+0.02`
W1：若 P0 达标合入 → P0-3 月度阈值自动化脚本化；否则 P1-1 特征增量
W2：P1-2 模型网格 → 选型 `comparison.md`  `overall_best`
全程 `audit_user_run` 门禁 + `train_predictions.csv` 逐位重放 + `metrics_daily_chain` 链口径日级
```

**回滚**：任一步 `SAE恶化>10%` 或 `R²` 跌即回退 `B_default`，`B1/B1b` 已作反例。

---

*证据链*：`B 40天 hand-anchor 10/14 vs B1 65%vs0% vs B1b 43%/37%/42%` + `B1b audit 2202/764/672` + `infer 5关 253/318` + `R² 0.36` + `lag5 75min` + `MISSING_COLUMN 45/37/44` + `thr100 0.689` 峰移。

