# 执行包 0800 B1 — 训练 05-21~07-10 剔12关(29%关) + 推理 07-11~08-02（池级对齐推理22%）

> **版本**：2026-09-17 B1 更正版（推理 6关22.2% 更正后）  
> **前置**：`configs/time_filters_0800_B1.json` 已由 `scripts/make_B1_config.py` 生成（fallback 12关清单，沙盒无 pandas 时亦可用；有真实 `data/` 请重跑校准）  
> **模式**：**模式B（用户本地 Windows PowerShell + conda test_gpu + RTX3080）**  
> **产出**：`outputs_0800_B1_default`（`default.yaml` 三模型 <1s）与 `outputs_0800_B1_t5`（`base_t5` transformer 30s）同数据同切分同阈值 30

---

## 一、环境与目录钉死

```powershell
# 1) 工作目录钉死（v5 的多工作副本教训：本包强制校验）
$wd = "D:\Work\testPython\NILM_Test2026\workspace-ai-nilm-win\nilm_new"
Set-Location $wd
if ((Get-Location).Path -ne $wd) { throw "工作目录未钉死：$(Get-Location)" }

# 2) 环境
conda activate test_gpu
python --version
nvidia-smi  # 确认 RTX3080
git status --porcelain  # 应干净；若有未提交改动先处理
git branch --show-current  # 应为 arena/01a0896c-nilm-new
git pull origin arena/01a0896c-nilm-new
```

---

## 二、配置预检（运行前门禁，★★必做，v5 教训）

```powershell
# 2.1 检查 B1 配置存在且键齐全
Test-Path configs/time_filters_0800_B1.json
python -c "import json; c=json.load(open('configs/time_filters_0800_B1.json',encoding='utf-8')); k='800080270800_4200904302272'; print('train',c[k]['train']); print('infer',c[k]['infer']); print('splits empty?', c[k]['splits']); print('decision_thr_w', c[k].get('decision_thr_w')); print('drop_days', c[k].get('_note_drop_days'))"
# 期望：
#   train include=[['2026-05-21','2026-07-10']] exclude 12 天（05-26..06-11 最旧12关）
#   infer include=[['2026-07-11','2026-08-02']] exclude=[['2026-07-23','2026-07-26']]
#   splits train/val/test 均为空（走自动分层，池级 29%关均匀分布）
#   decision_thr_w 30.0，_note_B1 含“剔12关 23→11 关占比57%→29%”

# 2.2 零泄漏校验（train 07-10 < infer 07-11）
python -c "import json; c=json.load(open('configs/time_filters_0800_B1.json')); import pandas as pd; tr=c['800080270800_4200904302272']['train']['include'][0]; inf=c['800080270800_4200904302272']['infer']['include'][0]; print('train',tr,'infer',inf, 'leak?', pd.Timestamp(tr[1]) >= pd.Timestamp(inf[0]))"
# 期望 leak? False

# 2.3 若本地有 data/，建议重校准真实关天（覆盖 fallback）
# python scripts/make_B1_config.py --time-filter-config configs/time_filters.json --output configs/time_filters_0800_B1.json
# 产出会回显真实 off 天数与剔除清单，若与上方 fallback 不一致，以重校准为准
```

---

## 三、训练与推理（同参双路，与 09-17 ROOTCAUSE 双路同阈值 30）

```powershell
# 3.1 default.yaml 三基线（proportional/ridge/history，<1s，best 应为 proportional）
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters_0800_B1.json --data-root data --output-root outputs_0800_B1_default --user-key 800080270800_4200904302272

# 3.2 base_t5 transformer（30s，ep≈27）
python scripts/run_batch_users.py --base-config configs/base_t5.yaml --time-filter-config configs/time_filters_0800_B1.json --data-root data --output-root outputs_0800_B1_t5 --user-key 800080270800_4200904302272
```

**预期控制台**（与 09-17 B_default/B_t5 同形态）：

```
[800080270800_4200904302272] 分路开机分析：... 全关天 11 天（原 23→11）
[800080270800_4200904302272] 日级放行 both 38 天 on 27/off 11 ratio 0.289 全通过
[800080270800_4200904302272] 切分（stratified_day）: {'train': 3648, 'val': 1216, 'test': 1216}  # 38天≈3648点，按 0.6/0.2/0.2
train OK（best=proportional 或 transformer，视互信息而定）
infer n≈2112（≈22天×96，07-11~08-02 排除 07-23~26）
```

---

## 四、一键审计（T1-4/I1-10 必须全部 ✅，含链Σ==总数、TP+FN恒等、阈值30自描述、按段重放）

```powershell
python scripts/audit_user_run.py --run-root outputs_0800_B1_default --user-key 800080270800_4200904302272
python scripts/audit_user_run.py --run-root outputs_0800_B1_t5 --user-key 800080270800_4200904302272
# 期望：TRAIN T1 metrics_by_split 三段计数和==train_predictions 段行数
#       T4 metrics_daily_chain Σ==总数/TP+FN恒等
#       INFER I7 混淆Σ==2112 / I10 链日级Σ==I7 / 阈值列 30 自描述
```

---

## 五、阈值与日级复核（与 ROOTCAUSE 同口径，对比 09-17 基线 0.614/0.783）

```powershell
# 5.1 阈值曲线（test 与 infer 各扫 6 阈值，峰应在 30 附近；若漂至 20/50 需记录）
$tr = Get-ChildItem outputs_0800_B1_default/800080270800_4200904302272/train/*/train_predictions.csv | Sort-Object LastWriteTime | Select-Object -Last 1
python scripts/threshold_sweep.py --csv "$($tr.FullName)" --pred-col pred --state-col pred_state --split test --thresholds 10,30,50,80,100,150

$inf = Get-ChildItem outputs_0800_B1_default/800080270800_4200904302272/infer/*/inference_result.csv | Sort-Object LastWriteTime | Select-Object -Last 1
python scripts/threshold_sweep.py --csv "$($inf.FullName)" --pred-col pred --state-col pred_state --split all --thresholds 10,30,50,80,100,150

$infT5 = Get-ChildItem outputs_0800_B1_t5/800080270800_4200904302272/infer/*/inference_result.csv | Sort-Object LastWriteTime | Select-Object -Last 1
python scripts/threshold_sweep.py --csv "$($infT5.FullName)" --pred-col pred --state-col pred_state --split all --thresholds 10,30,50,80,100,150

# 5.2 日级达标（86%不达标是否改善）
python scripts/analyze_daily_metrics.py --output-root outputs_0800_B1_default --sae-max 0.20 --f1-min 0.90 --split all
python scripts/analyze_daily_metrics.py --output-root outputs_0800_B1_t5 --sae-max 0.20 --f1-min 0.90 --split all

# 5.3 可辨识性（pearson 0.37→0.43 是否随池级对齐改善；R2 -1.02 是否回正）
Get-Content outputs_0800_B1_default/800080270800_4200904302272/train/*/identifiability.json | ConvertFrom-Json | Format-List
```

---

## 六、回收清单（粘贴到聊天，按文字粘贴，勿用附件）

```
# 控制台关键行（各 2 路）：
# - 分路开机分析：... 全关天 11 天
# - 切分：train/val/test 行数
# - best 模型名

# 审计：
python scripts/audit_user_run.py --run-root outputs_0800_B1_default --user-key ...  # 全量粘贴
python scripts/audit_user_run.py --run-root outputs_0800_B1_t5 --user-key ...      # 全量粘贴

# 指标：
# outputs_0800_B1_default/.../metrics_by_split.csv  三段 F1/r2
# outputs_0800_B1_default/.../state_strategy_metrics.csv  test 链@30
# outputs_0800_B1_default/.../metrics_daily_chain.csv  前 10 行 + 全关天 F1=0 行
# outputs_0800_B1_default/.../identifiability.json
# threshold_sweep test/infer 各 6 行（30/50 的 F1/P/R/off_fp）
# analyze_daily_metrics summary.csv
```

**判据**（与 ROOTCAUSE/REDISTRIBUTION 同口径）：

- **主判据**：`infer 链F1@30` 与 `test 链F1@30` 较 09-17 基线 `0.783 / 0.614` 净增 **≥0.02**（`0.80+ / 0.63+` 视为有效）
- **约束**：`SAE` 不恶化 >10%，`R` 不跌破 0.95，`train F1` 不再降（已欠拟合 0.47-0.56，再降即伤）
- **分布对齐度**：`train off% 57%→29%` 与 `infer 22%` 差 35→7pct，`off_fp 213→?` 预期大降
- 未达标 **一键回退**：`Copy-Item configs/time_filters.json.bak_20260917 configs/time_filters.json`（执行前已备份）

---

## 七、风险与备注

- **样本 -27%**：40→38 天（实为 50→38，窗 1088→≈925），`transformer steps 10200→8600` 仍可训；若 `test R2 0.22→<0.10` 则回退
- **关反例薄**：11 关 vs 23 关，若 `infer P↓` 且 `off_fp` 反增，说明反例不足，可改剔 8 关（15关38%关）折中
- **Fallback 清单**：本包 fallback 剔最旧 12 关（05-26..06-11），有 `data/` 时务必重跑 `make_B1_config.py` 以真实关天校准
- **B1 与层内 50/50 的关系**：B1 已含池级 29%关，层内自动 `stratified_day` 会使 `train/val/test` 各 29%关，无需另做层内手锚
