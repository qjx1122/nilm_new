# 0800 OFF-Filter 执行包 — 池内删全关天对齐推理 26%（模式B，2026-09-17）

> **B相更正**：`B相置0` 为设计，`P0_UBFIX` 作废。本包回归 `B1b 38天42%` 池，在 `train.exclude` 再删池内最旧 `N` 关天（`lag5` 已合入 `default/base_t5`，`stratified_by_state` 自动均摊）。

## 一、交付物

| 文件 | 说明 |
|---|---|
| `scripts/make_OFFFILTER_config.py` | 池内删关生成器（`--drop-off-n 0/4/8/12`，`fallback 16关`，真实数据时自动用 `branch p1` 校准） |
| `configs/time_filters_0800_OFF8.json` | **推荐** `38→30天 26.7%`（剔 `05-24,25,06-01,12,13,14,15,16` 8关） |
| `configs/time_filters_0800_OFF4.json` | 保守 `34天35.3%`（剔4关） |
| `configs/time_filters_0800_OFF12.json` | 激进 `26天15.4%`（剔12关） |
| 本文档 | 命令+判据 |

## 二、双路执行（以 OFF8 为例，OFF4/12 同理）

```powershell
# 预检（剔关天数）
python -c "import json,pathlib; c=json.loads(pathlib.Path('configs/time_filters_0800_OFF8.json').read_text(encoding='utf-8')); k='800080270800_4200904302272'; print(len(c[k]['train']['exclude']), c[k]['_note_OFFFILTER'])"
# 期望 20 (=12+8) + 30天26.7%

# default 三模型
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters_0800_OFF8.json --data-root data --output-root outputs_0800_OFF8_default --user-key 800080270800_4200904302272

# t5 单模型
python scripts/run_batch_users.py --base-config configs/base_t5.yaml --time-filter-config configs/time_filters_0800_OFF8.json --data-root data --output-root outputs_0800_OFF8_t5 --user-key 800080270800_4200904302272

# 审计与阈值（看 test 含关是否仍可信，off_fp 是否降）
python scripts/audit_user_run.py --run-root outputs_0800_OFF8_default --user-key 800080270800_4200904302272
python scripts/audit_user_run.py --run-root outputs_0800_OFF8_t5 --user-key 800080270800_4200904302272
python scripts/threshold_sweep.py --csv (Get-ChildItem outputs_0800_OFF8_default\800080270800_4200904302272\train\*\predictions\train_predictions.csv).FullName --pred-col pred_proportional --state-col pred_state_proportional --split test --thresholds 10,30,50,80,100,150
```

## 三、判据

| 指标 | B1b 基准 | **OFF8 达标** |
|---|---|---|
| `infer链 F1@30` | 0.676/0.691 | **≥0.70** (+0.02) |
| `infer R²` | 0.496 (lag5) | **≥0.55** |
| `off_fp` (5关) | 301 / 301 | **≤200** |
| `on_fp` | 99 | **≤70** |
| `audit` | 全绿 | 全绿 |

**回传**：`console`（`切分` + `模型指标` + `infer 19天`）贴聊天；`OFF8` 若 `F1` 未赢则试 `OFF4/12` 再定最优关占比。

*产物*：`outputs_0800_OFF{N}_*` 双路，`train 2201→~1800` 点随剔关递减，`splits` 仍 `stratified_by_state` 均摊。
