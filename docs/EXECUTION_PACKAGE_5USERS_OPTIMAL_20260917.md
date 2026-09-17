# 5户最优配置全量重跑执行包（模式B，2026-09-17）

> **最优快照**：`configs/time_filters.json` HEAD + `configs/base_optimal.yaml`（`lags[5,1,2,3,4]` 4模型自动择优）+ `day_gate true` 全局。**梳理**见 `docs/ANALYSIS_5USERS_OPTIMAL_CONFIG_20260917.md`。
> **目标**：`5/5 _DONE` `audit 全绿` `best_model` 自动择优（2842/2844/789→transformer，800→proportional，778→自适应）。

## 一、预检（PowerShell，`D:\Work\testPython\NILM_Test2026\workspace-ai-nilm-win\nilm_new`，运行前门禁）

```powershell
# 1) 最优 base 在位（期望 [5, 1, 2, 3, 4] + 4模型）
Select-String -Path configs/base_optimal.yaml -Pattern "lags"
Select-String -Path configs/base_optimal.yaml -Pattern "name: transformer"
# 期望 lags: [5, 1, 2, 3, 4] 且 4 个 - name:

# 2) time_filters 5户快照（期望 5行 target/decision 如梳理表）
python -c "import json; d=json.load(open('configs/time_filters.json',encoding='utf-8')); [print(k, d[k].get('target_col'), d[k].get('on_thr_w'), d[k].get('decision_thr_w')) for k in ['800080252842_4206894986488','800080252844_4206894986488','800080270778_4200903422131','800080270789_4206680982373','800080270800_4200904302272']]"

# 3) 数据在位
dir data\trains | Select-String "800080"
dir data\infers | Select-String "800080"
# 期望 5目录
```

## 二、执行（单批最优，`~30s~80s/户` GPU，批量 `~6min`）

### A. 推荐：单批 `base_optimal` 4模型择优（覆盖 5户最优）

```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs_5users_optimal
# 重跑加 --force；断点续跑默认 resume（_DONE 跳过）
```

*产物*：`outputs_5users_optimal/<user_key>/train/*/comparison.csv` 将显示每户 `overall_best`（800→proportional 预期，2842/789→transformer，2844→transformer，778→history/ridge）。

### B. 兼容：分两批（无 `base_optimal` 时等价，逐户隔离更易定位）

```powershell
# default 3模型批（800 + 778 稳定）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/default.yaml --data-root data --output-root outputs_5users_optimal_default --user-key 800080270800_4200904302272
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/default.yaml --data-root data --output-root outputs_5users_optimal_default --user-key 800080270778_4200903422131
# t5 单模型批（2842 + 2844 + 789 最优）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_5users_optimal_t5 --user-key 800080252842_4206894986488
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_5users_optimal_t5 --user-key 800080252844_4206894986488
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_5users_optimal_t5 --user-key 800080270789_4206680982373
```

## 三、审计与择优验证（每户必过）

```powershell
# 审计（期望 5× 全绿 ✅；2844 为 day_gate 45天池）
python scripts/audit_user_run.py --run-root outputs_5users_optimal --user-key 800080252842_4206894986488
python scripts/audit_user_run.py --run-root outputs_5users_optimal --user-key 800080252844_4206894986488
python scripts/audit_user_run.py --run-root outputs_5users_optimal --user-key 800080270778_4200903422131
python scripts/audit_user_run.py --run-root outputs_5users_optimal --user-key 800080270789_4206680982373
python scripts/audit_user_run.py --run-root outputs_5users_optimal --user-key 800080270800_4200904302272

# 批量状态 + 择优
type outputs_5users_optimal\batch_status.csv
Get-ChildItem outputs_5users_optimal\*\train\*\comparison.csv | ForEach-Object { Write-Host "=== $($_.FullName) ==="; Import-Csv $_.FullName | Format-Table -AutoSize }

# 2844 阈值双轨（可选，7月最优 400 vs 生产 30）
python scripts/threshold_sweep.py --csv (Get-ChildItem outputs_5users_optimal\800080252844_4206894986488\train\*\predictions\train_predictions.csv).FullName --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500
```

## 四、判据与回传

| 户 | 最优 `best` 预期 | `audit` | 达标线（`F1`） |
|---|---|---|---|
| 2842 p1 | `transformer` | ✅ | `infer ≥0.97`（历史 `0.989`） |
| 2844 p2 | `transformer` | ✅ | `infer ≥0.87`（`day_gate` `0.891`），`400@ P0.93` 另计 |
| 778 p2 | `history/ridge` 自适应 | ✅ | `F1` 不跌 >0.02 |
| 789 p1+p2 | `transformer` | ✅ | `infer ≥0.93`（历史 `0.957`） |
| 800 p1 | `proportional`（`base_optimal`） | ✅ | `infer 0.773±0.02`（`OFF8 history 0.783` 候选） |

**回传（贴聊天文字）**：`预检3条 + run_batch 全量 console（含切分/模型×指标/infer天数） + audit 5户 + batch_status.csv + comparison 5表（含 best_model）`。

> **通过** → `REPORT_TEST` 落盘 5户最优基线快照；**任一户超阈** → 贴 `comparison/offline_metrics` 供归因，决定是否回退 `lag5` 或切 `OFF8/ thr400`。
