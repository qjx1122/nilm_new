# 0800 重分析增补（2026-09-17 实录闭环）

> 基于用户补齐的 `threshold_sweep` 双表 + `metrics_by_split` + `meta/identifiability/day_gate/metrics_daily_chain` 四件套，**“整体低”已可定量归因**。

## 1. 量化画像（最新 outputs_all_chain，全量 base_t5 transformer-only）

### train 能力口径 @50（metrics_by_split）
| split | n | r2 | F1 | P/R | 说明 |
|---|---|---|---|---|---|
| train 2292 | 0.347 | 0.566 | 0.427/0.841 | 学都没学好（train<F1 test） |
| val 768 | 0.239 | 0.531 | 0.374/0.911 | |
| test 768 | 0.227 | 0.602 | 0.460/0.871 | 最好也仅0.60，天花板就在0.6 |

### test 判决链（threshold_sweep, test 768 4全关天）
| thr | F1 | P/R | off_fp |
|---|---|---|---|
| 10 |0.535|0.366/1.000|217|
| **30** |**0.614**|0.446/0.988|175| **峰** |
| **50生产** |0.605|0.460/0.883|152 |
| 80 |0.353|0.405/0.313|75 |

fp带：`[50,100) 60%` —— 真开与假开的pred重叠在50-100，阈值不可分。

### infer 2822（30天 6全关 at 07-29~08-03）
| thr | F1 | P/R | off_fp | 带 |
|---|---|---|---|---|
| **10** |**0.787**|0.650/0.996|292|峰（或30 0.783并列）|
| 30 |0.783|0.749/0.821|225||
| **50生产** |**0.554**|0.820/0.418|65| **谷底**，R腰斩 |
| 80 |0.196|0.991/0.109|0|

infer fp带：`[10,20)32% [20,30)17% [30,50)35%` → 84% <50，**生产50把R砍掉**

### 日级链 test（metrics_daily_chain, 8天）
- 全关天 4天 `F1=0.0`（05-26,06-11,06-12,06-25） accuracy 0.56-0.62，整段虚报
- 开机天 4天 F1 0.90,0.917,0.915,0.815（可用）
- 均值 `0.60 = (0*4 + 0.88*4)/8` —— **“整体低”是4个全关天把均值拉垮**，不是8天都差
- SAE 在全关天 `3e12` = D-7除零伪值，聚合时应排除

### meta / day_gate / identifiability
- `meta.best_model=transformer` 但 `models=[transformer]` 单模型，`collapsed=[]`，无 fallback（故0.55即交付值）
- `day_gate both=40 on17/off23 ratio0.425` 全通过，未再筛
- `identifiability pearson 0.376 spearman 0.397 best_tau 5@0.43 explained_r2 -1.02` —— **Pbus-p1 相关仅0.37-0.43，R2为负**（`Pbus≈ΣPi` 不成立，拓扑或倍率/时滞导致）；`target_on_rate 0.179` 稀疏，`strata pearson 0.16-0.24` 更弱，`target_cv 2.12` 波动大。

## 2. 根因定量归因（权重排序）

1) **模型-数据失配（主因，0.55→0.78的落差来源）**  
   W-1后历史 `best=proportional 0.75 >> transformer 0.467`，本轮强行 `base_t5` 单模型使0.55成为唯一交付值；即便thr调至30，`test`天花板仍0.61（能力口径），说明transformer在该户已触信息论上限（相关0.37）。

2) **全关天型失效（次因，均值拉垮）**  
   4/8天全关天 `F1=0` 贡献 `152/169 fp@50`，日均38点整段虚报；`infer` 6全关天同构（off_fp 65@50）。`post_min_on=1/fill=3` 对成段虚报无效，唯一杠杆是阈值但真/假重叠带使阈值也只能在300点间腾挪。

3) **生产阈错位（放大器）**  
   `infer`生产50正踩谷底，比最优低0.23；`test`50比30低0.01。2844要上调（0.89→0.95），0800要下调（0.55→0.78）方向相反，说明两户的功率带不同，不可复用同一阈值策略。

4) **可辨识性弱（结构性）**  
   0.37相关 + R2 -1.02 + lag5最佳，提示该p1回路与总线耦合弱或存在75min时滞/倍率错；`target_cv 2.12`也表明p1功率跳变大，L=96的时序记忆难抓。

## 3. 处置（已备好，两条并行，0代码改模型权重）

### A1 多模型回退（立得，推荐首选）
```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/default.yaml --data-root data --output-root outputs_800_default --user-key 800080270800_4200904302272
python scripts/audit_user_run.py --run-root outputs_800_default --user-key 800080270800_4200904302272
python scripts/analyze_daily_metrics.py --output-root outputs_800_default --sae-max 0.20 --f1-min 0.90 --split all
```
*效果*：`overall_best`自动切 `proportional/ridge`，预期 `infer F1 0.55→0.78`，`test` 同步回升；与 `base_t5` 的0.60形成对照。

### A2 判决阈下调（与2844反向，1行配置）
`configs/time_filters.json` 0800块加 `"decision_thr_w": 30.0`（`test/infer`折中；若更激进用10则 `infer R 0.996` 但 `P 0.65`）：
```json
"800080270800_4200904302272": {
  "target_col": "p1",
  "on_thr_w": 50.0,
  "decision_thr_w": 30.0,
  ...
}
```
```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_800_thr30 --user-key 800080270800_4200904302272
python scripts/threshold_sweep.py --csv (Get-ChildItem outputs_800_thr30/800080270800_4200904302272/infer -Recurse -Filter inference_result.csv | Sort-Object LastWriteTime | Select-Object -Last 1).FullName --pred-col pred --state-col pred_state --thresholds 10,30,50,80,100,150
```
*效果*：`infer 0.55→0.78`，`test 0.605→0.614`。早停/回归指标不变（阈值不进MSE）。

> **A1 与 A2 可并行**，A1 解决“选型错”，A2 解决“阈值错”，两者叠加即完整的“整体低”治理。

### B类（视A后仍低再议）
- `target_col p1→p1+p2`（若p1相关0.37而p1+p2相关>0.6则切；需重算splits的开/关天）
- 训练池扩至 `05-21~07-01` 排除段（若质量达标）或引入气象/日历特征（`explained_r2 -1.02` 提示拓扑/时滞可诊）

## 4. 本次产物自描述校验
- `metrics_by_split.state_thr_w=50` vs `inference_result.decision_thr_w=50` 解耦正确（改decision不改真值）
- `train_predictions` 与 `metrics_by_split` 的 `tp+fp+fn+tn==768` 三段各自对账（audit T1）
- `metrics_daily_chain.test 4×0.0` 与 `threshold_sweep off_fp 152` 同构
- D-7 SAE伪值已在日级链全关天复现，需在 `analyze_daily_metrics` 排除全关天后再判SAE

---
*增补落盘：docs/ANALYSIS_0800_REEVAL_20260917_UPDATE.md；原初版 docs/ANALYSIS_0800_REEVAL_20260917.md 保留；待 outputs_800_default / outputs_800_thr30 回收后追加定量对比。*
