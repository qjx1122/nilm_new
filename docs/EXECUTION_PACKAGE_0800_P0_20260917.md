# 0800 P0 执行包 — lag5 75min 时滞校正（模式B，2026-09-17）

> **前置**：`B1b` 已验 `43%/37%/42%` 池内均衡达成但 `infer 0.67/0.69 <0.773` 未赢，瓶颈 `lag5`+`ub/ib/pfb`。本包 **P0-1 lag5** 零代码首试（`pearson 0.37→0.43` 预期），`P0-2` 通道修复待列清单回传后定映射。

## 一、交付物（`arena/01a0896c-nilm-new` 已推）

| 文件 | 作用 |
|---|---|
| `configs/base_lag5.yaml` | `default` 派生，`features.lags [5,1,2,3,4]`（`5` 置首=75min时滞首特征），其余与 `default` 一致 |
| `configs/base_t5_lag5.yaml` | `base_t5` 派生，同上 `lags`，`transformer` 参数 `epochs150/patience20` 不变 |
| `configs/time_filters_0800_B1b.json` | 复用 `B1b` 池 `05-21~07-10 剔12关 38天 + stratified_by_state`（`decision30`），**无需改** |
| 本文档 | 执行命令+预检+判据+回传清单 |

## 二、预检（PowerShell，`D:\Work\testPython\NILM_Test2026\workspace-ai-nilm-win\nilm_new`）

```powershell
# 1) 配置在位校验（期望 [5,1,2,3,4]）
Select-String -Path configs/base_lag5.yaml -Pattern "lags"
Select-String -Path configs/base_t5_lag5.yaml -Pattern "lags"
# 期望 lags: [5, 1, 2, 3, 4]

# 2) 通道现况诊断（回传列清单，定 ub/ib/pfb 映射）
python -c "import glob, pandas as pd; f=glob.glob('data/trains/800080270800_4200904302272/*.csv')[0]; print('file:', f); print(pd.read_csv(f, nrows=0).columns.tolist())"
# 期望回传如 ['time','load_iden_data1',...,'load_iden_data81',...] 的完整列清单（亦可直接贴 data_schema_report.json 的 bus_schema.issues）

# 3) 亦可用 pipeline 自检（看置0告警是否仍在）
python scripts/run_batch_users.py --help | Select-String -Pattern "base-config"
```

## 三、双路执行（与 B1b 同参，仅换 base）

```powershell
# default 三模型（history_profile/proportional/ridge，best=ridge 预期）
python scripts/run_batch_users.py --base-config configs/base_lag5.yaml --time-filter-config configs/time_filters_0800_B1b.json --data-root data --output-root outputs_0800_LAG5_default --user-key 800080270800_4200904302272

# t5 单模型（transformer，早停 33→ 预期 35-40）
python scripts/run_batch_users.py --base-config configs/base_t5_lag5.yaml --time-filter-config configs/time_filters_0800_B1b.json --data-root data --output-root outputs_0800_LAG5_t5 --user-key 800080270800_4200904302272
```

*单路约 30s（default）/ 80s（t5 GPU），`--force` 可重跑覆盖。*

## 四、审计与阈值（同 B1b 门禁）

```powershell
python scripts/audit_user_run.py --run-root outputs_0800_LAG5_default --user-key 800080270800_4200904302272
python scripts/audit_user_run.py --run-root outputs_0800_LAG5_t5 --user-key 800080270800_4200904302272

# test 段阈值（看峰是否回 30，off_fp 是否降）
python scripts/threshold_sweep.py --csv (Get-ChildItem outputs_0800_LAG5_default\800080270800_4200904302272\train\*\predictions\train_predictions.csv).FullName --pred-col pred_proportional --state-col pred_state_proportional --split test --thresholds 10,30,50,80,100,150

# infer 段阈值（可选，看 infer 峰）
python scripts/threshold_sweep.py --csv (Get-ChildItem outputs_0800_LAG5_default\800080270800_4200904302272\infer\*\predictions\inference_result.csv).FullName --pred-col pred --state-col pred_state --split all --thresholds 10,30,50,80,100,150
```

## 五、判据与回传

| 指标 | B1b 基准 | **P0 达标线** | 未达标动作 |
|---|---|---|---|
| `infer链 F1@30` | 0.676 / 0.691 | **≥0.70** (+0.02) 且 `SAE` 不恶化>10% | 合入失败则叠加 `ub` 修复再验 |
| `infer R²` | 0.36 / 0.39 | **≥0.45** | — |
| `test R²` | 0.28-0.42 | **≥0.38** | — |
| `07-11~15 fp` | 27-43 | **≤20** | 时滞未校则调 `lags [6,5,1,2,3,4]` 再试 |
| `audit` | 全绿 | **全绿** | 阻断合入 |

**回传**：`console` 文字（含 `切分 stratified_by_state` + `模型 * 指标` + `infer 19天5关` + `audit` + `threshold_sweep` + **列清单**）贴聊天，勿发附件。

## 六、P0-2 通道后续

- 待列清单回传后，若 `load_iden_data45/37/44` 确无，`bus_field_map` 将改为 `ub→load_iden_data9` 复用或剔除该特征（`imb_u` 等置0消除），生成 `configs/base_lag5_ubfix.yaml` 二次验证。

*证据链*：`B1b R² 0.36` + `lag5 R 0.37→0.43` + `MISSING_COLUMN 45/37/44` + `B1b test 峰100`。
