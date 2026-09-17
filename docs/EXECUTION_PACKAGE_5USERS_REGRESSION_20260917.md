# 5户回归执行包 — 验证0800改动对其它4户零污染（模式B，2026-09-17）

> **目的**：0800 近期 `P0 lag5 ([5,1,2,3,4])` 为全局改动，`decision_thr 30 / splits  hand-anchor` 为 0800 专用，`OFF8/4/12` 为可选剔关文件——本包验证**除 0800 外其余 4 户（2842/2844/778/789）指标与门禁不受牵连**。
> **判据**：`audit 全绿 + _DONE 5/5 + F1 Δ<0.02 / R² Δ<0.05 / fp 增幅<15%`（`transformer` 不在 `default` 故不计）。

## 一、改动隔离清单（已推 `edf0e28`/`204a235`，`arena/01a0896c-nilm-new`）

| 文件 | 改动 | 影响面 | 预期对其它4户 |
|---|---|---|---|
| `configs/default.yaml` `features.lags [5,1,2,3,4]` | `lag5 75min` 全局置首 | 全用户 `default` 链路 | `history/proportional` 理论零影响（不吃 `lags`）；`ridge` 线性≈0（0800实测 `F1 0.000 / R² 0.000`），`±0.01` 属噪声 |
| `configs/base_t5.yaml` 同上 `lags` | `transformer` 时序 | 仅 `base_t5` 链路 | `default` 不跑 `transformer`→零影响；`t5` 链路 `0800` 已证 `+0.10`，其它户待本轮抽检 |
| `configs/time_filters.json` | 仅 `800080270800` 加 `decision_thr 30 + splits hand-anchor(40天)` | 仅 0800 | 其它4户 `target/on_thr/splits/train.include` 均**未改**（见下表） |
| `configs/time_filters_0800_OFF*.json` | 池内剔关 `OFF4/8/12` | 仅显式 `--time-filter-config` 指向时生效 | 本回归用 `time_filters.json`→**零影响** |
| `configs/time_filters_0800_B1b.json` 等 | `stratified_by_state` 自动化 | 仅该文件 | 同上零影响 |

**其它4户当前配置快照（本包预检门禁，必须逐位一致）**

```
2842 800080252842_4206894986488  target p1  on_thr 50  decision 50  train 2025-07-10~06-30  splits 无（走全局）
2844 800080252844_4206894986488  target p2  on_thr —   decision 30  train 同上               splits 无
778  800080270778_4200903422131  target p2  on_thr 50  decision —   train 05-21~06-08+07-01~07-08   splits 有(train 06-24~06-25)
789  800080270789_4206680982373  target p1+p2 on_thr 60 decision —  train 05-21~06-04          splits 无
800  800080270800_4200904302272  target p1  on_thr 50  decision 30  train 05-21~06-29          splits 有(10/14 3/5 4/4 hand-anchor)
```

## 二、预检（PowerShell，`D:\Work\testPython\NILM_Test2026\workspace-ai-nilm-win\nilm_new`，**运行前门禁**）

```powershell
# 1) 全局 lag 已合入（期望 [5, 1, 2, 3, 4] 三处一致）
Select-String -Path configs/default.yaml -Pattern "lags"
Select-String -Path configs/base_t5.yaml -Pattern "lags"
Select-String -Path configs/base_lag5.yaml -Pattern "lags"
# 期望 lags: [5, 1, 2, 3, 4]

# 2) time_filters 其它4户未被污染（期望 4户 decision/on_thr/target 如上表）
python -c "import json; d=json.load(open('configs/time_filters.json',encoding='utf-8')); [print(k, d[k].get('target_col'), d[k].get('on_thr_w'), d[k].get('decision_thr_w'), bool(d[k].get('splits',{}).get('train',{}).get('include'))) for k in ['800080252842_4206894986488','800080252844_4206894986488','800080270778_4200903422131','800080270789_4206680982373']]"

# 3) 数据在位（期望 5 目录均存在）
dir data\trains | Select-String "800080"
dir data\infers | Select-String "800080"
# 期望 5 行：2842 2844 778 789 800
```

> 任一项不符**立即停**并贴回 `console`，勿继续批量。

## 三、执行（单批 5户，~150s GPU；建议先 `default` 必跑，`t5` 抽检可选）

### A. 必跑：`default` 三模型（`history/proportional/ridge`，生产口径）

```powershell
# 全量5户一键（推荐，自动跳过已 _DONE 的户；重跑加 --force）
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_default

# ——若需逐户隔离排障（与上等价，逐条贴回更易定位）——
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_default --user-key 800080252842_4206894986488
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_default --user-key 800080252844_4206894986488
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_default --user-key 800080270778_4200903422131
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_default --user-key 800080270789_4206680982373
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_default --user-key 800080270800_4200904302272
```

### B. 抽检（可选）：`t5 transformer` 单模型（验证 `lag5` 对深度模型无回归，`~80s/户`）

```powershell
python scripts/run_batch_users.py --base-config configs/base_t5.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_t5 --user-key 800080270778_4200903422131
python scripts/run_batch_users.py --base-config configs/base_t5.yaml --time-filter-config configs/time_filters.json --data-root data --output-root outputs_regression_5users_t5 --user-key 800080270789_4206680982373
# 0800 t5 已有 OFF8/LAG5 基准，可不重跑；2842/2844 数据稀疏 t5 可跳过
```

*产物*：`outputs_regression_5users_default/<user_key>/train/*/metrics.json + infer/*/metrics.json + batch_status.csv`；`--force` 覆盖旧时间戳目录，`resume` 默认跳过 `_DONE`。

## 四、审计与指标提取（每户必过）

```powershell
# 审计（期望 5× 全绿 ✅，含 2844 day_gate 45天池）
python scripts/audit_user_run.py --run-root outputs_regression_5users_default --user-key 800080252842_4206894986488
python scripts/audit_user_run.py --run-root outputs_regression_5users_default --user-key 800080252844_4206894986488
python scripts/audit_user_run.py --run-root outputs_regression_5users_default --user-key 800080270778_4200903422131
python scripts/audit_user_run.py --run-root outputs_regression_5users_default --user-key 800080270789_4206680982373
python scripts/audit_user_run.py --run-root outputs_regression_5users_default --user-key 800080270800_4200904302272

# 批量汇总（贴回 batch_status.csv 全表）
type outputs_regression_5users_default\batch_status.csv

# 关键指标一键提取（贴回即可，Δ 由我方对照基线计算）
python -c "import pathlib, json, csv; root=pathlib.Path('outputs_regression_5users_default'); rows=[]; [rows.extend(list((root/k).rglob('metrics.json'))) or True for k in []]; import glob; files=glob.glob('outputs_regression_5users_default/*/train/*/metrics.json'); print('train metrics files', len(files)); [print(f, open(f,encoding='utf-8').read()[:600]) for f in files[:5]]"
# 更稳：直接贴各户 train/infer 的 console 段（切分 + 模型 * 指标 + infer 天数）
```

**若 `audit` 报 `DATA_QUALITY_FAILED`**：仅 `2844` 允许（旧数据 `bus 69.63<70`）；`day_gate` 新数据应 `PASS 45天`，否则贴 `quality_report.html` 的 `双达标天` 行。

## 五、判据与回传

| 户 | 基线参照（`default`，`REPORT.md`/`STATUS`） | 回归达标线 |
|---|---|---|
| **2842 p1** | `infer F1 0.9895 R²0.9427 SAE0.0039`（day_gate前；现池 107天Δ<0.02 可接受） | `F1 ≥0.97` 且 `audit✅` |
| **2844 p2** | `infer F1 0.891 R²0.885`（day_gate 45天新数据）**或** `F1 0.852`（旧）不可直比——以 `audit✅+池45天` 为主，`F1≥0.87` | `audit✅` 且 `F1` 不跌 >0.02 |
| **778 p2** | 历史 `ridge` 稳定（`TECH_DESIGN` 最佳 `history 0.827` 级） | `F1` 不跌 >0.02，`fp` 不增 >15% |
| **789 p1+p2** | `infer F1 0.957 R²~0.96`（待确权前作参考） | `F1≥0.93` 且 `t5抽检` 不崩（`R²>-0.2`） |
| **800 p1** | `infer best=proportional F1 0.773 offline 0.750`（`B_default`） | **复现 `0.773±0.02`**（`lag5` 对 `ridge` 0800实测 `±0.00`） |

**回传（贴聊天文字，勿附件）**

1. `预检` 3 条 `console`（`lags` + `time_filters 4户` + `dir`）
2. `run_batch` 全量 `console`（含 `切分` 行 + `模型 * 指标 train/val/test/infer` + `infer 天数`）
3. `audit` 5 户结果（`✅/✗`）
4. `batch_status.csv` 全表 + 各户 `metrics.json` 关键行（`F1/R²/SAE/fp/fn`）

> **通过**=5户 `audit✅` 且 `F1/R²` 在达标线内 → 结论「0800 改动对其它4户零污染」落盘 `REPORT_TEST`；**任一户超阈**→贴 `train_predictions.csv` 的 `sweep`（`--thresholds 10,30,50,80,100`）供定量归因，再决定是否回退 `lags` 或加用户级白名单。

*备注*：`OFF8/4/12` 与 `B1b/B1` 均为 `800` 专用文件，本回归**不加载**，已与 `time_filters.json` 物理隔离；`base_lag5.yaml` 仅作 0800 专用对照，本回归用 `default.yaml` 即可验证全局 `lag5`。
