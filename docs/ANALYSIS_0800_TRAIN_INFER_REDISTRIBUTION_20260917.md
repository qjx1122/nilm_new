# 0800 训练/推理时段重切 — 全关天分布错位治理（2026-09-17 续问二）

> **用户新观察**：0800 整体数据 `训练 2026-05-21~06-30` 全关天多，`推理 2026-07-01~08-02` **0 全关天**，时段分布严重错位；是否应 **重划 train/infer 时段**（剔部分全关天、把训练往 7 月延等）  
> **关联**：前篇 `ANALYSIS_0800_SPLIT_REBALANCE` 只动了 **同一 40 天池内的 splits 手锚**（train/val/test 关占比 58%/62%/50%），未动 **40 天 vs 30 天的池级错位**；本篇补池级视角  
> **口径**：全关天 = 该日 `p1 日峰值 < on_thr_w 50W`（与配置一致）；数据以用户陈述为准（训练 23/40 关 vs 推理 0/≈27 关），沙盒无 `data/` 无法逐点复核，下列计数按用户陈述 + 前次 `outputs_0800_B_*` 产物交叉校核

---

## 一、结论先行

1. **错位属实且是比“训练内失衡”更重的错位**：池级 `57.5% 关 vs 0% 关` 比 `train 58% vs test 50%` 的层内 8pct 差大一个量级。**训练让模型学会“57% 的日子该报关”，推理却考“100% 的日子都开机”**，直接解释 `infer 低召回（R 0.40@50）/ test 4关 F1=0 拉垮均值 0.60` 的一半。

2. **该不该重切？该，但不是“把训练的关天全删光”**：若训练 0 关、推理也 0 关，模型将 **丧失对未来全关天（周末/停产/检修）的识别能力**，且 `ridge/history 坍缩` 前科说明“无反例”会让阈值与 `post_min_on` 失锚。**目标应是“分布对齐而非归零”**：训练关占比从 57% 压到 **20–30%**（与推理 0% 的差距从 57pct 收敛到 20–30pct），同时 **把训练往 7 月平移 10 天** 以吃进更近的负荷形态（缓解 L1 的 75min 时滞与 6→7 月季节漂移）。

3. **推荐小步快跑**：**方案 B1（剔 11 关 + 延 10 天）= 训练 05-21~07-10 去关后 29 天（17开12关 29%关），推理 07-11~08-02（≈20 天 0 关，排除 07-23~26）**。样本从 40→29（-27%）但窗口仍 `≈800`，`transformer steps≈7500` 仍可训；预期 `R↑/F1 +0.02~0.05、off_fp 大降、SAE 不恶化` 为合入判据；不达标一键回退。此方案 **零泄漏**（`train 07-10 < infer 07-11`）、**零代码**、30 秒可验（见 §5）。

---

## 二、现状池级审计

| 池 | 时段（当前 `time_filters.json:0800`） | 天数 | 开机 | 全关 | 关占比 | 窗数 | 备注 |
|---|---|---|---|---|---|---|---|
| **训练总池** | `2026-05-21~06-29` | 40 | 17 | **23** | **57.5%** | 1088 | `splits hand-anchor: train 10/14 58%关 / val 3/5 62% / test 4/4 50%` |
| **推理** | `2026-07-01~08-03 \ 07-23~26` | ≈27 | ≈27 | **0** | **0%** | 2822 点 | 用户陈述“0 全关天”；前次产物 `infer 6关 at 07-29~08-03` 与此矛盾，待 `branch_sessions` 复核（§5.1） |

**错位度**：`57.5% - 0% = 57.5pct` 关占比差。`day_gate both 40` 未筛，说明关天非质量筛出，是 **业务真实分布随时间迁移**（5–6 月停产/周末多，7 月生产满开）。

**三问**：
- **是否 train 比 infer 更旧？** 是，`train 05-21 起` 比 `infer 07-01 起` 早 40 天，`R² -1.02 / lag5 75min` 的时滞与 6→7 月功率漂移未被训练吃到。
- **是否 test 能代表 infer？** 否，`test 4关/50%关` 反比训练更关、更不像 infer 的 0 关；`test F1 0.60` 被 4 个 F1=0 天拖垮，对推理性评价失真。
- **是否 train 需保留关样本？** 是，保留 **20–30% 关**作反例底座，否则未来一旦出现全关天（国庆/检修）将如 2844 全关天虚报（fp 139）重演。

---

## 三、为何池级错位比层内失衡更伤

| 失效链 | 机制 | 在 0800 的证据 |
|---|---|---|
| **先验错配** | 训练关先验 57% → 模型阈值与 `proportional` 基准偏关 | `infer@50 R 0.40→@30 R 0.82`（关阈太高，敢开不足）；`proportional ridge band 0/49.3W 坍缩`（学到“不开”） |
| **评测失真** | test 50% 关 vs infer 0% 关，`test F1 0.614` 的“关 F1=0”分量在 infer 不存在 | `test 4关 F1=0 +4开 0.77-0.89 =0.60`，`infer 链 0.78` 反高 0.18 |
| **时序外推** | 6 月关天学的“关特征”在 7 月全开时段为噪声（L1 `weekday_night 0.05`） | `identifiability` 日型分层夜间近零、`contribution 0.82 vs explained -1.02` 矛盾 |

> 层内手锚只能保证 `train/val/test` 彼此 8pct 内一致，救不了 57pct 的池级鸿沟。

---

## 四、重切方案（均满足 `train.end < infer.start` 零泄漏）

### 方案对比总表

| 方案 | 训练时段 | 推理时段 | 训练构成 | 关占比 | 样本/窗 | 治哪段 | 风险 |
|---|---|---|---|---|---|---|---|
| **现状** | 05-21~06-29 | 07-01~08-03\23-26 | 17开23关 40天 | 57.5% | 40天/1088窗 | — | 关先验过高、旧、与 infer 脱节 |
| **A 仅剔关（不延）** | 05-21~06-29 剔 11 关 | 同现状 | **17开12关 29天** | **41→29%** | 29天/≈790窗 | 关占比 | 丢 27% 样本，`r²` 可能再降 |
| **B1 剔关+延 10 天（推荐）** | **05-21~07-10 剔 11 关** | **07-11~08-02\23-26** | **≈22开12关 34天** | **≈35%→29%** | 34天/≈925窗 | 关占比+时效 | 需确认 07-01~10 开关清单（预期全开） |
| **B2 仅延不剔** | 05-21~07-10 不剔 | 07-11~08-02 | ≈27开23关 50天 | 46% | 50天/1360窗 | 时效 | 关占比仍 46%，治标不治本 |
| **C 滑窗对齐** | 06-01~07-10 剔 8 关 | 07-11~08-02 | ≈20开12关 32天 | 37% | 32天/870窗 | 时效+占比 | 训练更近但丢 5 月早期多样性 |

> **“剔哪 11 关”**：从 `05-21~06-29` 的 23 关里，**优先剔质量分最低或孤立的关天**（与 `day_gate` 质量分联动），保留周一/月末等分散关天作反例锚。**“延哪 10 天”**：`07-01~07-10` 用户称全开，若属实则 10 天全开直接把池关占比从 57% 压到 35%（不剔）或 29%（再剔 11）。

### B1 具体锚定（占位，需 `branch_sessions` 定剔哪 11 天）

```json
// configs/time_filters.json:800080270800_4200904302272  B1 示例
{
  "target_col": "p1",
  "on_thr_w": 50.0,
  "decision_thr_w": 30.0,
  "_note_B1": "2026-09-17 B1：训练 05-21~07-10 剔 11 关（23→12，关占比 57%→29%），推理 07-11~08-02，零泄漏，对齐 0 关推理分布但保留 29% 关作反例",
  "train": {"include": [["2026-05-21","2026-07-10"]]},
  "infer": {"include": [["2026-07-11","2026-08-02"]], "exclude": [["2026-07-23","2026-07-26"]]},
  "splits": {
    "train": {"include": [
      ["2026-05-21","2026-05-23"], ["2026-05-27","2026-05-30"],
      ["2026-06-02","2026-06-08"], ["2026-06-13","2026-06-14"],
      ["2026-06-16","2026-06-17"], ["2026-06-19","2026-06-19"],
      ["2026-06-21","2026-06-22"], ["2026-06-24","2026-06-24"],
      ["2026-06-26","2026-06-26"], ["2026-06-28","2026-06-28"],
      ["2026-07-01","2026-07-10"]
    ]}, // 剔 05-?? 11 关天（占位：与 REBALANCE 同法，需日清单定）
    "val": {"include": [
      ["2026-05-24","2026-05-25"], ["2026-05-31","2026-05-31"],
      ["2026-06-09","2026-06-10"], ["2026-06-15","2026-06-15"],
      ["2026-06-20","2026-06-20"], ["2026-06-27","2026-06-27"]
    ]},
    "test": {"include": [
      ["2026-05-26","2026-05-26"], ["2026-06-01","2026-06-01"],
      ["2026-06-11","2026-06-12"], ["2026-06-18","2026-06-18"],
      ["2026-06-23","2026-06-23"], ["2026-06-25","2026-06-25"],
      ["2026-06-29","2026-06-29"]
    ]}
  }
}
```

*精细版*：若要把训练关压到 **20%**（更贴 0% 推理），则剔 15 关留 8 关（17开8关 25天），但反例过薄，**不推荐**。

**Test 的同步治理**：`test 4关/50%` 亦应向推理看齐，改为 **1 关 7 开**（留 1 关作底线），否则 `test F1` 继续被关天拖垮，无法预警推理。实现：`test` 剔 3 关（选 06-11/12/25 中 3 天），保留 06-25 作唯一关锚。

---

## 五、复现与验证（PowerShell / test_gpu，与 09-17 双路同命令）

### 5.1 先验“0 关”与“23 关”清单（不猜，直接产）

```powershell
conda activate test_gpu
# 1) 分别对训练池与推理池取 branch_sessions（或直接读产物）
#    训练池：outputs_0800_B_default/.../branch_sessions.json  已含 05-21~06-29 40天 17/23
#    推理池：需单跑一次轻量离线统计（不训模型）
python - << 'PY'
import json, pathlib, pandas as pd
from nilm.data_io.csv_source import CsvBranchLoader
from nilm.analysis.branch_sessions import analyze_branch_sessions
# 按 time_filters.json 的 train/infer include 切 branch 原始 CSV，复用 user_task 的 filter_dataframe 逻辑
# 简易：直接读已产物 inference_result.csv 的 target 列按日峰值<50W 判关
import glob
f = glob.glob("outputs_0800_B_default/800080270800_4200904302272/infer/*/inference_result.csv")[0]
df = pd.read_csv(f, parse_dates=["timestamp"])
df["date"] = df["timestamp"].dt.normalize()
daily_max = df.groupby("date")["target"].max()
off_days = daily_max[daily_max < 50].index.tolist()
print(f"infer off days {len(off_days)}/{len(daily_max)}:", [d.date() for d in off_days[:10]])
# 同理 train：读 train/*/branch_cleaned.csv 或 train_predictions.csv
PY
# 2) 日级链复核（看 F1=0 天是否为真关）
python -c "import pandas as pd, glob; f=glob.glob('outputs_0800_B_default/800080270800_4200904302272/train/*/metrics_daily_chain.csv')[0]; df=pd.read_csv(f); print(df[df['f1']==0][['date','tp','fp','fn','tn']].to_string())"
```

*期望*：若推理真 0 关，则 `off_days` 应 0；若仍 6，则用户“0 关”指清洗后或 `on_thr` 口径差异，需对齐口径再定剔数。

### 5.2 B1 一键重跑（零泄漏检验 + 审计）

```powershell
Copy-Item configs/time_filters.json configs/time_filters.json.bak_20260917_B0
# 按 §4 粘贴 B1 到 configs/time_filters.json:0800（建议另存 configs/time_filters_0800_B1.json 再 --time-filter-config 指向它，以免污染 2842/789 的生产配置）
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters_0800_B1.json --output-root outputs_0800_B1_default --user-key 800080270800_4200904302272
python scripts/run_batch_users.py --base-config configs/base_t5.yaml   --time-filter-config configs/time_filters_0800_B1.json --output-root outputs_0800_B1_t5       --user-key 800080270800_4200904302272

python scripts/audit_user_run.py --run-root outputs_0800_B1_default --user-key 800080270800_4200904302272
python scripts/audit_user_run.py --run-root outputs_0800_B1_t5 --user-key 800080270800_4200904302272
$inf = Get-ChildItem outputs_0800_B1_default/800080270800_4200904302272/infer/*/inference_result.csv | Sort-Object LastWriteTime | Select-Object -Last 1
python scripts/threshold_sweep.py --csv "$($inf.FullName)" --pred-col pred --state-col pred_state --split all --thresholds 10,30,50,80,100,150
python scripts/analyze_daily_metrics.py --output-root outputs_0800_B1_default --sae-max 0.20 --f1-min 0.90 --split all
# 额外：泄漏检查（train 最大日期 < infer 最小日期）
python -c "import pandas as pd, glob; tr=glob.glob('outputs_0800_B1_default/800080270800_4200904302272/train/*/train_window_index.csv')[0]; inf=glob.glob('outputs_0800_B1_default/800080270800_4200904302272/infer/*/inference_result.csv')[0]; print(pd.read_csv(tr)['timestamp'].max(), pd.read_csv(inf)['timestamp'].min())"
```

### 5.3 判据（与 ROOTCAUSE 同口径）

- **主判据**：`infer 链 F1@30` 与 `test 链 F1@30` 净增 **≥0.02**（`0.783→0.80+` 视为有效）；
- **约束**：`SAE` 不恶化 >10%，`R` 不跌破 0.95，`train F1` 不再降（L2 已欠拟合，再降即过拟合）；
- **分布对齐度**：`identifiability pearson 0.37→0.43`（若同步做 `lag5 75min` 校正）与 `train off% 29% vs infer 0%` 差从 57pct → 29pct；
- 未达标 **回退** `Copy-Item configs/time_filters.json.bak_20260917_B0 configs/time_filters.json`。

---

## 六、与前篇及既有治理的衔接

| 治理 | 关系 | 结论 |
|---|---|---|
| 前篇 `SPLIT_REBALANCE 50/50`（仅层内） | 被本篇包含：B1 的 `splits hand-anchor 10/14→10/12` 即其子集 | 二选一，**本篇 B1 优先**（池级错位更重） |
| `decision 30`（已交付，+0.23） | 正交，保留 | `B1` 仍用 30，`threshold_sweep` 二次确认峰是否漂至 20/50 |
| `lag5 75min` 时滞校正 | 正交，可叠加，**P0 首试** | 建议 **先 B1 再 lag** 或 **B1+lag 一并**（预期 `pearson 0.37→0.43` 叠加 `off 57→29` 双增益） |
| `ub/ib/pfb 置0` 通道修复 | 正交，补 `r²` | 无冲突 |
| `2844 off_day_weight 3.0` 证伪 | 警示：关样本过薄时加权无效 | 本篇 **剔关而非加权**，避同坑 |
| `D-7 SAE 3e12` 伪值 | 推理 0 关后 `SAE` 分母不再零，伪值自解 | `B1` 后 `86%不达标` 中的 `SAE` 分量预期大降 |

---

## 七、风险与回退

- **样本损失**：40→29 天，窗 1088→790（-27%），`transformer steps 10200→7400` 仍可训，但 `r²` 可能再降 0.05；若 `test R² 0.22→<0.10` 则回退。
- **关反例过薄**：12 关 vs 现状 23 关，`off_fp` 可能反增（模型更敢开）；若 `infer off_fp` 不降反增且 `P↓` 则回退或改 17开12关→17开15关（32%关）折中。
- **时段外推**：07-01~10 本是推理段，挪入训练后推理缩至 20 天，评估天数少、日级方差大；需 `metrics_daily_chain` 逐日看 `F1` 分布而非只看均值。
- **口径对齐**：用户“0 关”与产物“6 关”矛盾，**先以 §5.1 命令定真关清单**，再定剔数；勿按猜测一次剔 23 关至 0 关。

---

*证据链：`configs/time_filters.json:0800 train 40天 17/23 vs infer 07-01~08-02 0关（用户陈述）` + `outputs_0800_B_* 横切 1088窗/2822点` + `ROOTCAUSE pearson 0.37/R² -1.02/lag5 75min` + `day_gate 40天未筛` + `2844 off_weight 证伪`；复现命令与 09-17 双路同参同阈值，零泄漏可验。*
