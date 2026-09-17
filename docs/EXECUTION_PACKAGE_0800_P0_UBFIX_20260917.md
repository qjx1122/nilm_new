# 0800 P0-2 执行包 — ub/ib/pfb 通道修复（单相复用，叠加 lag5，模式B，2026-09-17）

> **诊断**：`bus` 列清单 `['time', ... 'load_iden_data1'..36,43,49-51,73-108]` 确缺 `37/44/45`，故 `ub→45 / ib→37 / pfb→44` 持续 `MISSING_COLUMN_ZERO_FILLED` 置0。本包 **复用 A 相** `ub→ua(9) / ib→ia(1) / pfb→pfa(8)` 消除置0，叠加 `P0-1 lag5 [5,1,2,3,4]`，预期 `R² +0.05` 再叠。

## 一、交付物（`f401243→` 本次 `UBFIX`）

| 文件 | 变更 |
|---|---|
| `nilm/pipeline/user_config.py` | 透传 `bus_field_map` / `features` per-user（`time_filters` 可覆盖 `base`） |
| `nilm/pipeline/user_task.py` | `field_map` 改 `user>base` 优先级 + `features` 合并 `base+user`（`user` 覆盖 `lags`） |
| `configs/time_filters_0800_P0_UBFIX.json` | 基于 `B1b` 叠加 `features.lags [5,1,2,3,4]` + `bus_field_map` 全量 12 字段（`ub→9 / ib→1 / pfb→8`）+ 注释 |
| `configs/base_lag5.yaml` / `base_t5_lag5.yaml` | 已合入 `default/base_t5`（`P0-1`），本包 `UBFIX` 用 `default` 亦可（`per-user` 已含 `lag5`） |

## 二、预检

```powershell
# 1) per-user 配置命中
python -c "import json,pathlib; c=json.loads(pathlib.Path('configs/time_filters_0800_P0_UBFIX.json').read_text(encoding='utf-8')); k='800080270800_4200904302272'; print(c[k]['features']); print(c[k]['bus_field_map']['ub'], c[k]['bus_field_map']['ib'], c[k]['bus_field_map']['pfb'])"
# 期望 {'lags':[5,1,2,3,4]} + ub 9 / ib 1 / pfb 8

# 2) 列清单已在上一包回传，此包无需再探
```

## 三、双路执行（任选其一，`per-user` 已含 `lag5` 故用 `default` 即可）

```powershell
# 推荐：default 三模型（ridge/proportional/history，best=ridge）
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters_0800_P0_UBFIX.json --data-root data --output-root outputs_0800_P0_UBFIX_default --user-key 800080270800_4200904302272

# t5 单模型（transformer）
python scripts/run_batch_users.py --base-config configs/base_t5.yaml --time-filter-config configs/time_filters_0800_P0_UBFIX.json --data-root data --output-root outputs_0800_P0_UBFIX_t5 --user-key 800080270800_4200904302272
```

*单路 ~30s/80s，`MISSING_COLUMN` 告警应消失（`schema 警告` 行不再出现 `45/37/44`）。*

## 四、审计与阈值

```powershell
python scripts/audit_user_run.py --run-root outputs_0800_P0_UBFIX_default --user-key 800080270800_4200904302272
python scripts/audit_user_run.py --run-root outputs_0800_P0_UBFIX_t5 --user-key 800080270800_4200904302272

python scripts/threshold_sweep.py --csv (Get-ChildItem outputs_0800_P0_UBFIX_default\800080270800_4200904302272\train\*\predictions\train_predictions.csv).FullName --pred-col pred_proportional --state-col pred_state_proportional --split test --thresholds 10,30,50,80,100,150
```

## 五、判据

| 指标 | LAG5 基准（`t5`） | **UBFIX 达标** | 说明 |
|---|---|---|---|
| `infer R²` | 0.496 | **≥0.55** (+0.05) | `ub/ib` 无功辅助判关 |
| `infer F1` | 0.755 | **≥0.78** (+0.02) | `P 0.60→0.65` |
| `MISSING_COLUMN` | 3 行告警 | **0 行** | 置0消除 |
| `on_fp` | 99 | **≤80** | `imb_u` 等生效 |

**回传**：`console`（`切分` + `模型指标` + `infer 19天` + `audit`）贴聊天；`ub` 若仍无感则回退至“剔除 `imb_u` 特征”方案。

*产物*：`outputs_0800_P0_UBFIX_*` 双路，`audit` 全绿。
