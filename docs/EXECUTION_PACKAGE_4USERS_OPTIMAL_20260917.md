# 4户最优配置全量重跑执行包（模式B，2026-09-17，OQ-16 0800 PAUSED）

> **最优快照**：`configs/time_filters.json` HEAD（`0800 _OQ16_PAUSED`）+ `configs/base_optimal.yaml`（`lags[5,1,2,3,4]` 4模型自动择优）+ `day_gate true`。**梳理**见 `docs/ANALYSIS_4USERS_OPTIMAL_CONFIG_20260917.md` + `CORRECTION_0800_TARGET_20260917.md`。
> **范围**：`2842/2844/778/789` 4户（**0800 暂停，不参与 batch**）。

## 一、预检（PowerShell，`D:\Work\testPython\NILM_Test2026\workspace-ai-nilm-win\nilm_new`，运行前门禁）

```powershell
# 1) 最优 base 在位（期望 [5, 1, 2, 3, 4] + 4模型）
Select-String -Path configs/base_optimal.yaml -Pattern "lags"
Select-String -Path configs/base_optimal.yaml -Pattern "name: transformer"
# 期望 lags: [5, 1, 2, 3, 4] 且 4 个 - name:

# 2) time_filters 4户 + 0800 PAUSED（期望 4户 target/decision 如梳理表，800 为 PAUSED）
python -c "import json; d=json.load(open('configs/time_filters.json',encoding='utf-8')); [print(k, d[k].get('target_col'), d[k].get('decision_thr_w'), d[k].get('_status')) for k in ['800080252842_4206894986488','800080252844_4206894986488','800080270778_4200903422131','800080270789_4206680982373','800080270800_4200904302272']]"

# 3) 数据在位（期望 4户目录存在，800 可不检）
dir data\trains | Select-String "800080"
dir data\infers | Select-String "800080"
```

## 二、执行（4户单批最优，`~30s~80s/户` GPU，`~4min`）

### A. 推荐：单批 `base_optimal` 4模型择优（覆盖 4户最优，显式列户免误跑 800）

```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs_4users_optimal --user-key 800080252842_4206894986488
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs_4users_optimal --user-key 800080252844_4206894986488
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs_4users_optimal --user-key 800080270778_4200903422131
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs_4users_optimal --user-key 800080270789_4206680982373
# 或一键（若 batch.py 已支持多户过滤，仍建议逐户贴回更易定位）
# python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs_4users_optimal
```

*产物*：`outputs_4users_optimal/<user_key>/train/*/comparison.csv` 将显示 `overall_best`（2842/789/2844→transformer，778→history/ridge）。

### B. 兼容：分两批（无 `base_optimal` 时等价）

```powershell
# default 3模型（778）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/default.yaml --data-root data --output-root outputs_4users_optimal_default --user-key 800080270778_4200903422131
# t5 单模型（2842+2844+789）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_4users_optimal_t5 --user-key 800080252842_4206894986488
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_4users_optimal_t5 --user-key 800080252844_4206894986488
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_4users_optimal_t5 --user-key 800080270789_4206680982373
```

## 三、审计与择优验证（每户必过）

```powershell
python scripts/audit_user_run.py --run-root outputs_4users_optimal --user-key 800080252842_4206894986488
python scripts/audit_user_run.py --run-root outputs_4users_optimal --user-key 800080252844_4206894986488
python scripts/audit_user_run.py --run-root outputs_4users_optimal --user-key 800080270778_4200903422131
python scripts/audit_user_run.py --run-root outputs_4users_optimal --user-key 800080270789_4206680982373
type outputs_4users_optimal\batch_status.csv
Get-ChildItem outputs_4users_optimal\*\train\*\comparison.csv | ForEach-Object { Write-Host "=== $($_.FullName) ==="; Import-Csv $_.FullName | Format-Table -AutoSize }
```

## 四、判据与回传

| 户 | 最优 `best` 预期 | `audit` | 达标线 |
|---|---|---|---|
| 2842 p1 | `transformer` | ✅ | `infer F1 ≥0.97`（历史 `0.989`） |
| 2844 p2 | `transformer` | ✅ | `infer F1 ≥0.87`（`day_gate 0.891`），`400@ P0.93` 另计 |
| 778 p2 | `history/ridge` 自适应 | ✅ | `F1` 不跌 >0.02 |
| 789 p1+p2 | `transformer` | ✅ | `infer F1 ≥0.93`（历史 `0.957`） |

**回传（贴聊天文字）**：`预检2条 + run_batch 4户 console（含切分/模型×指标/infer天数） + audit 4户 + batch_status.csv + comparison 4表（含 best_model）`。

> **通过** → `REPORT_TEST` 落盘 4户最优基线快照；**0800 保持 PAUSED**，待 `p4` 确认后另立项重定 `target` + `splits` + `lag5` 重验。
