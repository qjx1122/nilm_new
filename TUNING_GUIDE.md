# TUNING_GUIDE.md — 工商业负荷辨识调参运维人话版

> **给谁看**：无算法背景的工程师、运维、交付同学。会用 `PowerShell/cmd` 粘贴命令、会看 CSV，就能按本手册把一个新设备从 0 接到生产。  
> **版本**：v1.0（2026-09-18，对齐 `REPORT.md v1.1 + NILM_DATA_DICT v0.2.10 + REPORT_TEST 18专题 + base_optimal lag5`）｜协议：`BOOTSTRAP.md v2.3`｜代码：`arena/01a0896c-nilm-new` `b3b1226`  
> **一句话定位**：总线（5分钟电表，含电压电流功率）猜分路（15分钟电表，某一路有功）现在是开还是关、功率是多少。模型不看谐波，只看 24 小时窗口内的功率走势。

---

## 1. 新设备接入前，先回答 3 个问题

| 问题 | 人话 | 错了会怎样 |
|---|---|---|
| **目标是哪一路？** | 分路文件里 `p1/p2/p3/p4` 哪一列是你要猜的？必须人工去现场/台账核实，不能猜 | 2842 把 `p1+p2` 当目标训了半个月，后来发现真目标是 `p1`，旧结论全作废（OQ-13） |
| **开机的电功率是多少算开？** | `on_thr_w`：功率超过多少瓦算“开”。默认 10W，工商业常用 50W（2842 p1 稳态 700W） | 10W 太低会把小抖动算成开，P 值虚低；400W 太高会把低档开机算成关 |
| **有没有 30 天同时有数的数据？** | 总线和分路**同一天**都有数才算一天有效 | 2844 总线断录 294 天，分路却在那 39 天有数，59 天“各玩各的”直接被门禁拦（DATA_QUALITY_FAILED 69.63<70） |

> **800 的教训**：`800080270800` 分路数据不在 p1/p2/p3，原来 12 篇 p1 分析全降级暂停（OQ-16）。**新设备第一步一定先定 target_col，别先跑。**

---

## 2. 数据：最少要什么、要不要处理

### 2.1 放哪、叫什么（不改名就失败）

```
data/trains/900080270900_4200000000001/
  e241_900080270900_4200000000001-Ch1-250710-260630.csv   ← 总线
  4200000000001-250710-260630.csv                         ← 分路
data/infers/900080270900_4200000000001/
  e241_900080270900_4200000000001-Ch1-260701-260731.csv   ← 推理总线（分路有就放，没有也不拦推理）
```

* 总线文件名必须 `e241_设备_用户-Ch1-起-止.csv`，分路 `用户-起-止.csv`，起止是 `YYMMDD`。带 `-1/-infer` 的不参与合并。
* 总线里必须有 `event_time`，分路里 `time + p1..pN`（W）。列名大小写不限。
* 不同设备 `load_iden_data1/7/8...` 对应的物理量不同，靠 `configs/default.yaml` 的 `bus_field_map` 翻译（已按官方点位表配好 `ua/ub/uc/ia/ib/ic/pa/pb/pc/pfa/pfb/pfc ×0.001`），缺的列（如 284x 的 `ub/ib/pfb`）会自动置 0 并告警，不用你补。

### 2.2 多久才够

* **硬门槛**：覆盖率 ≥15%、缺失 ≤90%、质量分 ≥70（代码算）。推理时不设门禁。
* **经验下限**：**双达标天 ≥30 天**（总线与分路同一天都 ≥70 分）。2842 是 107 天，2844 是 45 天。少于 10 天别训，`off_day_weight` 加权也救不了（已证伪：3 个全关天加权后 fp 258→299 更差）。
* **时间**：训练 `2025-07-10~2026-06-30`，推理 `2026-07-01~07-31`，训练必须在推理之前（防泄漏，`_default` 已配 `train exclude 07-01~12-31`）。
* **总量**：总线 5 分钟（288点/天）+ 分路 15 分钟（96点/天），流水线自动把 5 分钟聚成 15 分钟（`mean/pF重算`），你不用插值。

### 2.3 要不要自己洗数据

**不用。** 哨兵值 `-2147483648`、负功率、2 个点以内的缺口、时间对齐，代码全自动。洗完会落 `cleaned/{bus,branch}_cleaned.csv` 给你抽查。B 相为 0 是设计（人为删 B 相），不用修。

---

## 3. 从 0 到上线的 8 步（粘贴即跑）

> 以新用户 `900080270900_4200000000001` 猜 `p2` 为例，`on_thr 10W`，用最稳的 `base_optimal`（lag5 + 4 模型择优）。

**Step 0 拉代码自检**

```powershell
git fetch origin; git checkout arena/01a0896c-nilm-new; git pull origin arena/01a0896c-nilm-new
python -c "import json,pathlib;print(json.loads(pathlib.Path('configs/time_filters.json').read_text(encoding='utf-8'))['_default']['train'])"
# 看到 exclude 07-01~12-31 即正常
```

**Step 1 写配置**（唯一可信源 `configs/time_filters.json`）

```json
"900080270900_4200000000001": {
  "target_col": "p2",
  "on_thr_w": 10.0,
  "decision_thr_w": 30.0,
  "post_min_on": 1,
  "post_fill_short_off": 3,
  "quality": {"day_gate": true, "min_on_day_ratio": 0.2},
  "train": {"include": [["2025-07-10","2026-06-30"]]},
  "infer": {"include": [["2026-07-01","2026-07-31"]]}
}
```
* `target_col` 必填；`on_thr` 看电器额定（10/50/60 三档）；`decision` 先写 30，后面会扫。

**Step 2 训练**

```powershell
conda activate test_gpu
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs --user-key 900080270900_4200000000001
# 期望：批量[train] -> OK，日志“滑窗按时间连续段构造：N段 M窗”，best 是 ridge/history/transformer 之一
```

**Step 3 自检**

```powershell
python -c "import pandas,glob;f=sorted(glob.glob('outputs/900080270900_4200000000001/train/*/train_window_index.csv'))[-1];w=pandas.read_csv(f);s=pandas.to_datetime(w.win_end)-pandas.to_datetime(w.win_start);print('cross_gap',(s>pandas.Timedelta('23h45m')).sum())"
# 期望 0（W-1 修复：不再跨天拼窗）
```

**Step 4 选阈值**（不动模型，零成本扫）

```powershell
python scripts/threshold_sweep.py --csv outputs/900080270900_4200000000001/train/*/predictions/train_predictions.csv --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500 --min-on 1 --fill-off 3
python scripts/threshold_sweep.py --csv outputs/900080270900_4200000000001/infer/*/predictions/inference_result.csv --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500
# 看 F1-R-off_fp 曲线：R≥0.95 且 test链不净降的那档就是稳健点（2844 7月稳健点 400，峰 500 贴 R 线不取）
```

**Step 5 推理**

```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs --user-key 900080270900_4200000000001 --stage infer
```

**Step 6 审计**（一键门禁）

```powershell
python scripts/audit_user_run.py --run-root outputs/900080270900_4200000000001 --expect-n 2629
# 期望：T1(计数和==段行数) T2(tp+fn恒等) T3(按split重放) I1-9 全✓；2842那类多pred_state 6✗是工具待修，单pred_state绿即放行
```

**Step 7 上线**：`outputs/<key>/infer/<ts>/predictions/inference_result.csv` 的 `pred_state`（0/1）就是交付列，`decision_thr_w` 列是阈值自描述。

**全量批量**：去掉 `--user-key` 即扫 `data/trains|infers` 全目录，`outputs/batch/<ts>/batch_status.csv` 按 `user_id` 出状态，失败隔离，`_DONE` 支持断点续跑，`--force` 刷写新时间戳。

---

## 4. 两个阈值，人话版

* **`on_thr_w`（真值阈）**：**什么算真的开**。`target ≥ on_thr` 才算开。改它 = 改考卷答案，需重算审计。按电器最小功率定，10W 是“有没有人用”，50W 是“是不是在干活”。
* **`decision_thr_w`（预测阈）**：**模型猜多少才报开**。`pred ≥ decision` 才报开，完事还做 `去短开1窗+填短关3窗(45分)`。改它 = 改判卷标准，不改模型，可离线扫 thousands 次。
* **为什么分开**：2844 真开机 500-1000W，全关幻觉 <500W，10W 真值线与 400W 预测线差 50 倍，混一起就没法既评模型能力又调交付。能力口径（`raw≥on_thr`）看模型准不准，交付口径（`pred_state≥decision`）看用户收到准不准，两口径之差就是你调阈值的战场。

---

## 5. 模型怎么选（不用你选，自动的）

`base_optimal.yaml` 配了 4 个：`history_profile(中位画像)` / `proportional(按比例)` / `ridge(线性)` / `transformer(时序)`。跑完 `comparison.csv` 按 `mae/rmse/r2/sae/f1...` 投票，`overall_best` 就是生产用的。

* **谁赢过**：2842 `ridge 0.869/0.984`（transformer 0.818被lag5拖累但被择优救回）、2844 `transformer 0.678/0.887`、778 `history 0.985/0.968`（ridge 0.575崩）、789 `transformer 0.962/0.984`。所以必须4选1，单押一个必错。
* **新用户**：数据少时 `history/proportional` 先顶上，`transformer` 影子跑；数据够 30 天后自然让 `transformer` 赢。

---

## 6. 推理后看哪两个表

| 表 | 口径 | 看什么 |
|---|---|---|
| `metrics_daily.csv` | 能力口径 `@on_thr` | 模型本身准不准（`raw≥10`）。`TP/FP/FN` 由 `pred` 列直接判。 |
| `metrics_daily_chain.csv` | 交付口径 `@decision+游程` | 用户收到准不准（`pred_state`）。`tp/fp/fn` 按 `target_state 1/0 vs pred_state 1/0` 逐日，全关日 `fp` 在此看。 |
| 两表行集一致、差阈值，`Σ=有效行数`、`TP+FN=真值开点数` 恒等是对账锚。|

* `offline_metrics.json` 是能力口径的汇总版（`r2` 全关日 `NaN` 排除后均值，`SAE` 全关日 `1e12` 是 D-7 已知伪值不用管）。
* `branch_sessions.csv` 看开机段起止/电量，`train_window_index.csv` 看窗口是否跨天。

---

## 7. 生产 7 项放行（缺一不可）

1. `scan` 合法 `5→5` 无 `INVALID_FILENAME`
2. `day_gate` 双达标天 ≥`min_days(3)` 且 `min_on_day_ratio` PASS，无 `DATA_QUALITY_FAILED`（全局 `day_gate true`，已验 2844 70+3→45天）
3. `cross_gap=0`（窗口最大 23.75h）
4. `audit` 全绿（2842多`pred_state_*` 6✗除外）
5. `infer链 F1` 低频 ≥0.87 / 高功率 ≥0.95，`R²>0`，`SAE<0.2`，`metrics_daily_chain` 全关日 `fp<20%`
6. `threshold_sweep` 已扫且 `decision` 不破 `R0.95`
7. `comparison` best 稳定、`offline` 与 `metrics_by_split test` 逐位一致

全绿即可与 2842/2844/778/789 同口径并入 `outputs/batch` 调度。

---

## 8. 战史（犯过的错，别再犯）

* **W-1 跨天拼窗**：按位置滑窗把切分造成的时间洞拼进 24h 窗，800有 50.5% 窗被污染最长 4.99 天。现按 `15min间隔≠15` 即分段，段头用段内首行填充，已修复。
* **目标错**：2842 `p1+p2`、2844 `p3+p4` 训完才发现真目标是 `p1/p2`，旧结论全降级。**先定 target_col 再跑。**
* **800 暂停**：分路不在 p1/p2/p3，12 篇 p1 分析全作废待 p4。**新用户第一天就定 pN。**
* **加权证伪**：训练池 3 个全关天加 `off_day_weight 3.0`，fp 258→299 更差（加权不产生新信息，还把开机日权重挤到 0.89）。数据少就补数，别加权。
* **阈值踩坑**：`decision 10` 看 `offline F1` 说无效，实则 `offline` 契约上就不进 `decision`。看能力口径调模型，看交付口径调阈值。
* **单文件加载**：`--base-config` 只读一个文件，`default.yaml` 不参与（曾致 2844 试点被全范围门禁拦）。`day_gate` 必须各 base 文件同步，`test_config_defaults` 守卫。
* **多工作副本**：`nilm_model_tune` 与 `.../nilm_new` 并存致运行时缺键，以产物 `decision_thr_w` 列为准，执行包必含 `Select-String` 预检。

---

## 9. 每月要做什么

* **每月1号**：全量用户重扫 `infer_result.csv`（`10-500`），`decision` 漂移 >20W 就改 `time_filters.json` 并 `git commit`，推理下月自动用新阈（无需重训）。
* **每季度**：训练窗延至近 30 天，`--force` 重训（季节漂移 6月`≥400W 43%` vs 7月 `98%`，静态阈不可跨月）。
* **触发**：`metrics_daily_chain` 连续3天全关 `fp>10` 或 `offline r2<0` 立即扫阈。

---

## 10. 常见问题

**Q: 我只有 7 天数据能上线吗？** A: 能跑但不建议当生产。`history/proportional` 可先当影子，`transformer` 等 30 天再切。

**Q: `on_thr` 和 `decision` 能否统一成 400？** A: 别。10W 是“真开定义”，400W 是“报开阈值”，统一会把 10-400W 真开点重定义为关，报表好看但漏报被洗掉。

**Q: `SAE 1e12` 是不是模型崩了？** A: 不是，是全关日 `Σy=0` 除零伪值（D-7），聚合时排除，看 `F1` 即可。

**Q: 800 为什么不让分析？** A: `p1/p2/p3` 都不是真目标，p4 待查。你的新用户只要 `pN` 定准就不踩。

---

*维护：稳定结论/方法论/SOP 变时整篇重写对账（BOOTSTRAP 收尾§2），不追加流水。疑问先看 `REPORT_TEST.md 18专题` 与 `REPORT.md 8条`，结论均可溯源。*
