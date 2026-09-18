# TUNING_GUIDE.md — 工商业负荷辨识调参运维人话版（详细版）

> **给谁看**：完全不懂算法的工程师、运维、交付、现场实施同学。只要会 `复制粘贴 PowerShell 命令`、`用 Excel 打开 CSV`、`看懂“开/关”`，就能按本手册把一个从未见过的新设备从 0 接到生产，并判断能不能上线。  
> **版本**：v1.1 详细版（2026-09-18，对齐 `REPORT.md v1.1 + NILM_DATA_DICT v0.2.10 + REPORT_TEST 18专题 + base_optimal lag5[5,1,2,3,4]`）｜协议：`BOOTSTRAP.md v2.3`｜分支：`arena/01a0896c-nilm-new 7376764` → 本版  
> **一句话定位**：用总线侧 5 分钟电表（电压电流有功功率因数）去猜分路侧 15 分钟电表（某一路 `p1/p2/p3...` 的有功）此刻是“开 1”还是“关 0”、功率是多少瓦。模型只看过去 24 小时（96 点）的功率走势，不看高频谐波。

**怎么用本手册**：按顺序读，`Step 0-7` 是必做流水线；`§4-6` 是解释为什么；`§7-9` 是上线门禁与排障；`附录` 是可直接复制的模板。遇到报错先查 `§9 战史`，再查 `§10 FAQ`。

---

## 目录

1. [三个前置灵魂问题](#1-三个前置灵魂问题)
2. [数据：最少要什么、放哪、叫什么、要不要洗](#2-数据最少要什么放哪叫什么要不要洗)
3. [从 0 到上线的 8 步（粘贴即跑，含输出怎么验）](#3-从-0-到上线的-8-步粘贴即跑含输出怎么验)
4. [两个阈值到底是什么（on_thr vs decision，人话+图）](#4-两个阈值到底是什么on_thr-vs-decision人话图)
5. [模型怎么选（4 选 1 自动，为什么不能单押）](#5-模型怎么选4-选-1-自动为什么不能单押)
6. [推理后看哪两个表（能力 vs 交付）](#6-推理后看哪两个表能力-vs-交付)
7. [生产 7 项放行（缺一不可，含不合格长什么样）](#7-生产-7-项放行缺一不可含不合格长什么样)
8. [部署架构与日常运维（批量、断点、月历）](#8-部署架构与日常运维批量断点月历)
9. [战史：我们犯过的 7 个错（别再踩）](#9-战史我们犯过的-7-个错别再踩)
10. [常见问题 12 问](#10-常见问题-12-问)
11. [附录：模板与速查卡](#11-附录模板与速查卡)

---

## 1. 三个前置灵魂问题

| 问题 | 人话 | 怎么定 | 定错会怎样 |
|---|---|---|---|
| **① 目标是哪一路？`target_col`** | 分路 CSV 里 `p1/p2/p3/p4` 哪一列是你要猜的？必须去现场台账或点位表核实，不能看文件名猜、不能“先 p1 不行再换 p2 试” | 到现场看该设备下挂的分路编号，或让采集方给“终端-用户-分路”对照表；用 `pandas` 看 `pN` 的 `NaN 占比/非零中位` 交叉验证 | 2842 把 `p1+p2` 当目标训半个月，`infer SAE 0.372` 漂移全是 `p2` 带来的，目标改 `p1` 后 `SAE 0.0039` 消失，旧结论全降级（OQ-13） |
| **② 多少瓦算开？`on_thr_w`** | 功率超过多少瓦算“开 1”。默认 10W，工商业常用 50W | 看电器铭牌最小档功率或历史 `pN` 直方图：`p2` 稳态 709W 取 10W，`p1` 稳态 700W 取 50W，`p1+p2` 取 60W | 10W 太低→小抖动算开，`P` 虚低 0.44；400W 太高→低档开机算关，`R` 暴跌 0.43（2844 6月） |
| **③ 有没有 30 天“同一天都有数”的数据？** | 总线和分路**同一天**都质量达标才算一天有效 | 先各跑 `branch_sessions` 看 `pN` 有效天，再看质量报告双达标天 | 2844 总线断录 294 天（2025-07-30~2026-05-21 空 7056h），分路那 39 天却有数，59 天“各玩各的”被 `DATA_QUALITY_FAILED 69.63<70` 直接拦 |

> **800 的血泪**：`800080270800` 分路不在 `p1/p2/p3`，12 篇 `p1` 分析（`F1 0.77/0.78 lag5 0.75`）全部降级暂停（OQ-16）。**新设备第一步一定先定 target_col，别先跑，后面全返工。**

---

## 2. 数据：最少要什么、放哪、叫什么、要不要洗

### 2.1 目录与命名（写错一个字符就 `INVALID_FILENAME` 失败隔离）

```
data/
├── trains/900080270900_4200000000001/          ← 训练用（必须同时有总线+分路）
│   ├── e241_900080270900_4200000000001-Ch1-250710-260630.csv   ← 总线，RE_BUS
│   └── 4200000000001-250710-260630.csv                         ← 分路，RE_BR
└── infers/900080270900_4200000000001/          ← 推理用（总线必有，分路有则仅评估）
    ├── e241_900080270900_4200000000001-Ch1-260701-260731.csv
    └── 4200000000001-260701-260731.csv  ← 可选，有就离线算分
```

**正则（代码硬校验，勿改）：**
* 总线 `RE_BUS: ^e241_(?P<device>[^_]+)_(?P<user>[^-]+)-Ch\d+-\d{6}-\d{6}(-1|-infer)?\.csv$` 示例 `e241_800080252842_4206894986488-Ch1-260604-260611.csv`
* 分路 `RE_BR:  ^(?P<user>[^-]+)-\d{6}-\d{6}(-1|-infer)?\.csv$` 示例 `4206894986488-260604-260611.csv`
* 带 `-1/-infer` 后缀的不参与合并，会告警；`ChN` 只是通道号，不代表物理量

**时间列：** 总线 `event_time`（5min，288点/天，常见 00:04:59 随机秒偏移，代码自动归桶），分路 `time`（15min 整点 00:00/00:15… 96点/天，naive 无时区，内部一致即可）

### 2.2 字段最小集（缺了自动置 0，不用你手工补）

| 侧 | 文件里叫什么 | 翻译后叫什么 | 单位 | 必有 | 缺了会怎样 |
|---|---|---|---|---|---|
| 总线 | `load_iden_data9` | `ua` | V | 尽量有 | 缺 `45(ub)/37(ib)/44(pfb)` 时日志 `MISSING_COLUMN_ZERO_FILLED` 并置 0（284x 实测 3 列缺失，B相人为删，置0为设计） |
|  | `load_iden_data1` | `ia` | A |  |  |
|  | `load_iden_data7` | `pa` | W |  |  |
|  | `load_iden_data8` | `pfa` | - |  |  |
|  | 同理 `45/81`→`ub/uc`，`37/73`→`ib/ic`，`43/79`→`pb/pc`，`44/80`→`pfb/pfc` |  |  | `multiplier 0.001` 已配：原始 916 → 0.916 |
| 分路 | `p1..pN` | `branch_p` | W | 是 | 复合目标 `p1+p2` 按 `skipna=False` 行累加（任一 NaN→目标 NaN） |

> **倍率**：`实际物理量 = 文件原始值 /1000`（官方点位表确认）。`P/(U·I·PF) ≈1.065` 为 OQ-11 待核对，不阻断。

### 2.3 多久才够（硬门槛 vs 经验值）

* **硬门槛（代码算，不过就 `DATA_QUALITY_FAILED`）**：覆盖率 ≥0.15、缺失率 ≤0.9、质量分 ≥70（`min_score 70`，2026-08-18 由 10 上调，逐天质量表与门禁共用）。推理不设门禁。
* **经验下限（能出可信推理）**：**双达标天 ≥30 天**（总线与分路同一天都 ≥70 分）。2842 107天（84%开）、2844 45天（71%开）、778 27天（100%开）、789 15天（100%开）均达标。少于 10 天别训，`off_day_weight 3.0` 加权已证伪（fp 258→299 更差，3 个全关天加权不产生新信息，还把开机日权重挤到 0.898 伤幅值 `MAE+16%`）。
* **时间窗口**：训练 `2025-07-10~2026-06-30`，推理 `2026-07-01~07-31`，训练必须 `<` 推理起点（防泄漏，`_default` 已配 `train exclude 07-01~12-31` / `infer include 07-01~12-31`）。
* **粒度**：总线 5min→15min `mean(pf重算)` 聚合，`agg_strategy.json` 留痕；分路保持 15min 不插值（硬禁令）。

### 2.4 要不要自己洗数据

**完全不用。** 流水线自动：哨兵 `-2147483648/2147483647→NaN` → `clip_negative` 裁负功率 → `max_gap_interp 2` 补 2 点以内缺口 → 5→15min 聚合 → 对齐去重。洗完落 `cleaned/{bus,branch}_cleaned.csv` 给你抽查，`save_cleaned_csv true` 可关。

---

## 3. 从 0 到上线的 8 步（粘贴即跑，含输出怎么验）

> 以新用户 `900080270900_4200000000001` 猜 `p2`、`on_thr 10W`、初始 `decision 30W`，用最稳的 `base_optimal.yaml`（`lags[5,1,2,3,4] + 4模型择优`，已合入 `default.yaml` 全局，对 4 户零污染已验）为例。Windows 用 PowerShell，Linux/macOS 把 `Select-String` 换 `grep`。

### Step 0 拉代码与自检（每次开工必做，BOOTSTRAP 开局仪式）

```powershell
git fetch origin; git checkout arena/01a0896c-nilm-new; git pull origin arena/01a0896c-nilm-new
git status; git log --oneline -3
# 期望分支 arena/01a0896c-nilm-new，tip 含 TUNING_GUIDE v1.x
python -c "import json,pathlib;cfg=json.loads(pathlib.Path('configs/time_filters.json').read_text(encoding='utf-8'));print('default train',cfg['_default']['train']);print('800 PAUSED',cfg['800080270800_4200904302272'].get('_OQ16_PAUSED'))"
# 期望 default train exclude 07-01~12-31，800 PAUSED True
Test-Path configs/base_optimal.yaml; (Select-String -Path configs/base_optimal.yaml -Pattern "lags").Line
# 期望 True，lags: [5,1,2,3,4]
```

### Step 1 写配置（唯一可信源 `configs/time_filters.json`，优先级 `user_key > _default`）

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

* `target_col` 必填，`on_thr` 看铭牌（10/50/60 三档：`p2`低频10，`p1`高功率50），`decision` 先 30。
* `post_min_on 1` 去 <1 窗的抖动（恒空操作，防短闪），`post_fill_short_off 3` 填 ≤45min 的短关断。
* `day_gate true` 全局默认（优先级 `day > train_range > full`），`min_on_day_ratio 0.2` 防“无可学开机模式”（2844旧池 31%→71%已验）。
* `train/include` 为闭区间，`YYYY-MM-DD` 自动扩到 00:00-23:59:59。

### Step 2 训练（`--user-key` 即单用户批量，同代码路径）

```powershell
conda activate test_gpu   # 或你的 env，有 CUDA 自动用 GPU，CPU 亦可（6→22min）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs --user-key 900080270900_4200000000001
```

**期望日志（对照 2842/2844 实录）：**
* `批量[train] 900080270900_4200000000001 -> OK`（`DATA_QUALITY_FAILED` 即门禁拦，看 `quality_report`）
* `滑窗按时间连续段构造：N段 M窗`（W-1 修复，2842 是 12/7/11/2 段，4666 窗）
* `best=ridge/history/transformer` 之一，无 `PRED_COLLAPSED(带宽<on_thr)` / `UNDER_TRAINED(<500步)` 告警，早停 `ep 40-79` 正常。

**落盘**：`outputs/900080270900_4200000000001/train/<timestamp>/` 含 `metrics_by_split.csv`/`metrics_daily.csv`/`metrics_daily_chain.csv`/`train_predictions.csv`/`branch_sessions.csv`/`train_window_index.csv`/`day_gate.json`/`_DONE`。

### Step 3 窗口连续性自检（金标准，W-1 是否生效）

```powershell
python -c "import pandas,glob;f=sorted(glob.glob('outputs/900080270900_4200000000001/train/*/train_window_index.csv'))[-1];w=pandas.read_csv(f);s=pandas.to_datetime(w.win_end)-pandas.to_datetime(w.win_start);print('windows',len(w),'cross_gap',(s>pandas.Timedelta('23h45m')).sum(),'max',s.max())"
# 期望 cross_gap 0，max 23.75h（L96）。2842 修复前 1045 跨洞最长 231 天，修复后 0。
```

### Step 4 选阈值（不动模型，零成本离线扫，核心）

```powershell
# test 链（train_predictions.csv，按 split 分块重放，防段边界跨块回填假错）
python scripts/threshold_sweep.py --csv outputs/900080270900_4200000000001/train/*/predictions/train_predictions.csv --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500 --min-on 1 --fill-off 3
# infer 链（inference_result.csv）
python scripts/threshold_sweep.py --csv outputs/900080270900_4200000000001/infer/*/predictions/inference_result.csv --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500
```

**怎么选（以 2844 p2 为例，人话）：**
* 扫完看 `F1 - R - off_fp` 曲线：`10→150 F1 0.8904→0.9292` 单调升，`off_fp 140→56 (-60%)`，`R≥0.993` 全程不破 `0.95`。
* 峰在 500（`F1 0.9585`）但贴 `R 0.95` 且边界贴真值密集带 `[500,700) 205点`，季节一漂就破。稳健点 `400`（`F1 0.9548 让0.0037，R余量0.03，fn 21 vs 41减半，off 18`）更抗漂。
* **结论**：`R≥0.95` 且 `test链不净降` 的最大 `F1` 档即稳健点。定后改 `time_filters.json` 该户 `decision_thr_w` 并 `git commit`，下月自动用新阈（月扫 SOP）。

### Step 5 推理

```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs --user-key 900080270900_4200000000001 --stage infer
# 或 --stage all 一键 train→infer；有分路真值则自动产 offline_metrics.json + 双日级表
```

**交付物**：`infer/<ts>/predictions/inference_result.csv` 12 列契约 `timestamp,user_id,target,target_state,on_thr_w,pred,pred_state,decision_thr_w,pred_prob + infer_model`，`pred_state 0/1` 即交付列，`decision_thr_w` 自描述。

### Step 6 审计（一键门禁，T1-T4 / I1-I10）

```powershell
# 形式 A：--run-root 已是用户目录（含 train/infer，单用户最常用；v2026-09-18 已兼容）
python scripts/audit_user_run.py --run-root outputs/900080270900_4200000000001 --expect-n 2629 --expect-confusion 1036,77,21,1495 --expect-off-day-fp 18 --baseline-run outputs/900080270900_4200000000001
# 形式 B：--run-root 为父目录（批量 outputs 含多用户）则需 --user-key 指名
python scripts/audit_user_run.py --run-root outputs --user-key 900080270900_4200000000001 --expect-n 2629 --expect-confusion 1036,77,21,1495 --expect-off-day-fp 18 --baseline-run outputs --user-key 900080270900_4200000000001
# 若报“期望唯一用户目录 实际:['infer','train']”：说明把用户目录当成了父目录传入，已在 v2026-09-18 修复兼容——形式 A/B 均可；旧版请改形式 B 或升级代码
# 模板：T1计数和==段行数 T2 tp+fn恒等/ raw==metrics_by_split T3按split重放 I1行数==meta I2列契约 I3值域 I4时序15min I5重放 I6 sigmoid I7总混淆+全关fp I8期望断言 I9 offline与baseline逐键一致（模型逐位复现）
# 期望全✓；2842那类多pred_state 6✗是审计工具多列透传待修，单pred_state绿即放行
```

### Step 7 上线判断（见 §7，7项全绿）

### Step 8 全量批量（上线后）

```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs
# 扫 data/trains|infers 全目录，outputs/batch/<ts>/batch_status.csv 按 user_id 出状态，失败隔离，_DONE 断点续跑，--force 刷写新时间戳
```

---

## 4. 两个阈值到底是什么（on_thr vs decision，人话+图）

```
真值侧（考卷答案）              预测侧（判卷标准）
target --on_thr--> target_state   pred --decision_thr--> pred_state --min_on/fill_off--> 最终 0/1
  10W 恒定                       30→400 可调，月扫重定
```

* **`on_thr_w`（真值阈）**：什么算真的开。`target ≥ on_thr` 才算开 1。**改它 = 改答案**，需重算审计与历史对比。按电器最小档定：10W是“有没有人用”，50W是“是不是在干活”（2842 p1 稳态 700W 取 50）。
* **`decision_thr_w`（预测阈）**：模型猜多少才报开。`pred ≥ decision` 才报开，完事 `去短开1窗（<1不存在，恒空）+填短关3窗（≤45min）`。**改它 = 改判卷标准**，不改权重，可离线扫上千次。
* **为什么分开**：2844 真开机 500-1000W，全关幻觉 <500W，10W 真值线与 400W 预测线差 50 倍。能力口径（`raw≥on_thr`）看模型准不准，交付口径（`pred_state≥decision`）看用户收到准不准，两口径之差就是你调阈值的战场，混一起就没法既评模型又调交付。
* **游程**：`enforce_min_on 1` 恒空（防闪断），`fill_short_off 3` 仅填两侧皆开的 ≤3 窗短关断（全关日 `fp 150→152 +2` 即此）。

---

## 5. 模型怎么选（4 选 1 自动，为什么不能单押）

`base_optimal.yaml` 配 4 个：`history_profile(中位画像)` / `proportional(按总线比例)` / `ridge(线性)` / `transformer(时序，L96滑窗Seq2Point，epochs150/patience20)`。跑完 `comparison.csv` 按 `mae/rmse/r2/sae/f1/acc/prec/rec` 投票，`overall_best` 就是生产用的。

| 用户 | 赢家 | 经验 |
|---|---|---|
| 2842 p1 | `ridge 0.869/0.984` | transformer 0.818被 lag5 拖 -0.165，但被 4选1救回 |
| 2844 p2 | `transformer 0.678/0.887` | 日级全关虚报 139 是阈值可救，模型能力未崩 |
| 778 p2 | `history 0.985/0.968` | `ridge 0.575` 崩，history最稳 |
| 789 p1+p2 | `transformer 0.962/0.984` | `history 0.981`但R² -5.3 幅值弱 |

> **单押必错**，必须 4 选1。数据少时 `history/proportional` 先顶上，`transformer` 影子跑；30 天后自然让 `transformer` 赢。`d_model 128/layers3` 反而过拟合，别加宽。

**时滞彩蛋 `lags[5,1,2,3,4]`**：首滞后 75min（5×15），2026-09-17实录 `transformer R² 0.39→0.49 F1 0.69→0.75 SAE 0.22→0.03`，`ridge` 持平，已全局合入 `default/base_t5/base_optimal`，对 4 户零污染。

---

## 6. 推理后看哪两个表（能力 vs 交付）

| 表 | 口径 | 怎么算 | 看什么 | 典型坑 |
|---|---|---|---|---|
| `metrics_daily.csv` | 能力口径 `@on_thr` | `pred ≥ on_thr` 直接判，无游程，`state_thr_w=10` 自描述 | 模型本身准不准。`TP/FP/FN` 由 `pred` 列直接判 | 别拿它判交付，会说“无效”（v5曾把offline F1当交付判） |
| `metrics_daily_chain.csv` | 交付口径 `@decision+游程` | `target_state 1/0 vs pred_state 1/0` 逐日，`decision_thr_w` 自描述 | 用户收到准不准。全关日 `fp` 在此看（2844 7月 `140→18`） | 与上表行集一致、差阈值，`Σ=有效行数`、`TP+FN=真值开点数` 恒等是对账锚 |

* `offline_metrics.json` 是能力口径汇总版（`r2` 全关日 `NaN` 排除后均值，`SAE 1e12` 是 D-7 全关 `Σy=0` 除零伪值，看 `F1` 即可）。
* `branch_sessions.csv` 看开机段起止/时长/峰均/电量，`train_window_index.csv` 看窗口 `win_start/win_end` 是否跨天。
* `state_strategy_metrics.csv`（test）是 `raw@on_thr` vs `decision+runs` 的对照表，跨月护栏在此看（6月 `R 0.43` vs 7月 `0.98` 即漂移实证）。

---

## 7. 生产 7 项放行（缺一不可，含不合格长什么样）

| # | 门 | 合格 | 不合格长什么样 | 查哪 |
|---|---|---|---|---|
| 1 | 目录与命名 | `scan 5→5` 无 `INVALID_FILENAME` | 1个 `-infer` 带错后缀或 `RE_BR` 用户号与目录不一致 → 整目录 FAIL | `batch_status.csv` |
| 2 | 质量门禁 | `day_gate` 双达标天 ≥`min_days(3)` 且 `min_on_day_ratio` PASS，无 `DATA_QUALITY_FAILED` | `bus 69.63<70`（2844旧）或开机占比 <0.2 → 整训拦；全局 `day_gate true` 已验 45天 | `quality_report` / `day_gate.json` |
| 3 | 窗口连续性 | `cross_gap=0`（max 23.75h） | 跨天窗 `>23.75h` 最长 4.99天（W-1 前 50.5%污染）→ `transformer` 位置编码全错 | `train_window_index.csv` |
| 4 | 审计 | 全绿（`I7 Σ=2629 TS1=1057` 等） | `T3` 整列重放 vs 按split重放不一致 → 段边界跨块回填假错（工具教训） | `audit_user_run.py` |
| 5 | 交付指标 | `infer链 F1` 低频 ≥0.87 / 高功率 ≥0.95，`R²>0`，`SAE<0.2`，全关日 `fp<20%` | `proportional 0.773→B1b 0.691` 推理未赢、或 `R² -0.26` 幅值不达标 → 转 A补数 | `metrics_daily_chain` |
| 6 | 阈值校准 | `threshold_sweep` 已扫且 `decision` 不破 `R0.95`，`test链不净降` | 7月 `400` 在 6月 `R 0.43` 净降 -0.17 → 静态阈不可跨月，需月扫 | `state_strategy` / `sweep` 曲线 |
| 7 | 模型择优 | `comparison` best 稳定，`offline` 与 `metrics_by_split test` 逐位一致 | `best` 随阈抖动 → 误把交付口径当选型依据 | `comparison.csv` |

> 7项全绿即可与 4 户同口径并入 `outputs/batch` 调度。任一项红→按 `§9 战史` 定向排障，不盲调参。

---

## 8. 部署架构与日常运维（批量、断点、月历）

```
[数据源采集] → data/trains|infers/<device>_<user>/ (只读)
      ↓
run_batch_users.py --time-filter-config --base-config base_optimal.yaml
      ├─ discovery(扫目录) → 失败隔离(1户坏不拦全批)
      ├─ user_task(train): 聚合→清洗→对齐→质量(day_gate)→可辨识性(pearson/τ)→splits→分段构窗L96→4模型→双口径指标→_DONE
      └─ user_task(infer): 最新_DONE 模型 → inference_result.csv(交付)
      ↓
outputs/<key>/{train,infer}/<ts>/ + outputs/batch/<ts>/batch_status.csv → 下游/监控
```

* **断点**：`_DONE` 标记，`SKIPPED_RESUME` 重复触发无害；`--force` 刷写新时间戳目录（配置+权重全留痕）。
* **资源**：`device auto`，3080上 2842全流程 30s（CPU 23min），`batch_size` 自适应保 ≥8步/epoch，`<500步 UNDER_TRAINED` 自动告警。
* **月历**：
  * **每月1号**：全量用户重扫 `infer_result.csv` `10-500`，`decision` 漂移 >20W 就改 `time_filters.json` 并 `git commit`，下月自动用新阈（无需重训）。
  * **每季度**：训练窗延至近 30 天，`--force` 重训（季节漂移 6月`≥400W 43%` vs 7月 `98%`）。
  * **触发**：`metrics_daily_chain` 连续3天全关 `fp>10` 或 `offline r2<0` 立即扫阈。

---

## 9. 战史：我们犯过的 7 个错（别再踩）

1. **W-1 跨天拼窗（高危）**：按数组位置滑窗，把切分锚定造成的时间洞拼进 24h 窗，800有 50.5% 窗被污染最长 4.99 天，`transformer` 位置编码全错。现按 `间隔≠15min` 即分段，段头用段内首行填充，`common.schema.segment_bounds` 全局原语，守卫 13 项。
2. **目标错（OQ-13）**：2842 `p1+p2`、2844 `p3+p4` 训完才发现真目标 `p1/p2`，旧结论 `SAE 0.372→0.0039` 全降级。**先定 target_col 再跑。**
3. **800 暂停（OQ-16）**：分路不在 `p1/p2/p3`，12 篇 `p1` 分析全作废待 `p4`。**新用户第一天就定 pN。**
4. **加权证伪**：训练池 3 个全关天加 `off_day_weight 3.0`，`fp 258→299` 更差（不产生新信息，还把开机日权重挤到 0.89 伤幅值 `+16%`）。数据少就补数，别加权。
5. **阈值看错表**：`decision 10` 看 `offline F1` 说无效，实则 `offline` 契约上就不进 `decision`。**看能力调模型，看交付调阈值。**
6. **单文件加载**：`--base-config` 只读一个文件，`default.yaml` 不参与（曾致 2844 试点被全范围门禁拦）。`day_gate` 必须各 base 文件同步，`test_config_defaults` 守卫。
7. **多工作副本**：`nilm_model_tune` 与 `.../nilm_new` 并存致运行时缺键，以产物 `decision_thr_w` 列为准，执行包必含 `Select-String` 预检（`仓库预检≠运行时生效`，`audit` 以产物自描述为准）。

---

## 10. 常见问题 12 问

**Q1: 我只有 7 天数据能上线吗？** 能跑但不建议当生产。`history/proportional` 可先当影子，`transformer` 等 30 天再切。  
**Q2: `on_thr` 和 `decision` 能否统一成 400？** 别。会把 10-400W 真开点重定义为关，报表好看但漏报被洗掉（`target [10,400)` 约 21 点）。  
**Q3: `SAE 1e12` 是不是崩了？** 不是，全关日 `Σy=0` 除零伪值（D-7），聚合排除，看 `F1`。  
**Q4: 800 为什么不让分析？** `p1/p2/p3` 都不是真目标，`p4` 待查。你的新用户定准 `pN` 就不踩。  
**Q5: `B相 0` 要修吗？** 不修，人为删 B 相，置 0 为设计（`CORRECTION_B_PHASE` 已废弃单相复用）。  
**Q6: `P/(U·I·PF)=1.065` 正常吗？** 待核对倍率（OQ-11），`multiplier 0.001` 已配，不阻断。  
**Q7: `val/test P 0.44` 是模型差吗？** 不是，阈值 10W 量高功率回路自然低 `P`，`decision 400` 后 `P 0.93` 即阈值可救，`R 0.96` 守线即排序能力在。  
**Q8: 训练池全关天太多要重划分吗？** `B 40天 hand-anchor 10/14` 已均衡，50/50 需丢 6 天换 7.5pct，`P0 lag5` 收益 `+0.10` 远大于重划分 `+0.01`。  
**Q9: 推理无分路真值怎么评？** 正常，`inference_result.csv` 仍产，`metrics_daily_chain` 跳过，无真值不阻断推理。  
**Q10: 改 `decision` 要重训吗？** 完全不用，`threshold_sweep` 离线扫即可，`offline` 不变即改阈不改权重。  
**Q11: `batch` 扫到 `INVALID_FILENAME` 目录？** 1 个错文件致整目录 FAIL，`infers/trains` 各余 1 非法目录全量前需删或重命名。  
**Q12: 怎么证明 GPU 和 CPU 结果一致？** 同种子 `2844` `val_loss 0.349628` 第5次逐位复现即金标准，`audit I9` 与 `baseline` 逐键一致为放行门。  
**Q13: 审计报“期望唯一用户目录 实际:['infer','train']”怎么办？** 这是 `--run-root` 指向歧义：`outputs/<user>` 本身已含 `train/infer`，旧版脚本误判为父目录。v2026-09-18 已兼容——`--run-root outputs/<user>`（形式 A）与 `--run-root outputs --user-key <user>`（形式 B）均可；旧版请升级 `scripts/audit_user_run.py` 或改用形式 B。你的 `800080270856_4206810972139` 即此例：`python scripts/audit_user_run.py --run-root outputs/800080270856_4206810972139 --expect-n 2629 --expect-confusion 1036,77,21,1495 --expect-off-day-fp 18 --baseline-run outputs/800080270856_4206810972139` 已可直接过。

---

## 11. 附录：模板与速查卡

### A. 新用户 `time_filters.json` 模板

```json
{
  "900080270900_4200000000001": {
    "target_col": "p2",
    "on_thr_w": 10.0,
    "decision_thr_w": 30.0,
    "post_min_on": 1,
    "post_fill_short_off": 3,
    "quality": {"day_gate": true, "min_on_day_ratio": 0.2},
    "train": {"include": [["2025-07-10","2026-06-30"]], "exclude": []},
    "infer": {"include": [["2026-07-01","2026-07-31"]], "exclude": []},
    "splits": {"train": {"include": []}, "val": {"include": []}, "test": {"include": []}}
  },
  "_default": {
    "train": {"exclude": [["2026-07-01","2026-12-31"]]},
    "infer": {"include": [["2026-07-01","2026-12-31"]]}
  }
}
```

### B. 命令速查（PowerShell / bash 通用）

```powershell
# 单户训练+推理
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs --user-key 900080270900_4200000000001
# 阈值扫
python scripts/threshold_sweep.py --csv outputs/900080270900_4200000000001/infer/*/predictions/inference_result.csv --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500
# 审计（形式 A 用户目录；批量多用户用 --run-root outputs --user-key <user>）
python scripts/audit_user_run.py --run-root outputs/900080270900_4200000000001 --expect-n 2629
# 审计（形式 B 父目录+指名）
# python scripts/audit_user_run.py --run-root outputs --user-key 900080270900_4200000000001 --expect-n 2629
# 全量
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs
```

### C. 输出速查

| 路径 | 人话 |
|---|---|
| `outputs/<key>/train/<ts>/metrics_by_split.csv` | 能力口径总表（选型用） |
| `outputs/<key>/train/<ts>/metrics_daily_chain.csv` | 交付口径日级（全关fp在此） |
| `outputs/<key>/infer/<ts>/predictions/inference_result.csv` | 交付列 `pred_state` |
| `outputs/batch/<ts>/batch_status.csv` | 批量状态表（user_id） |

---

*维护：稳定结论/方法论/SOP 变时整篇重写对账（BOOTSTRAP 收尾§2），不追加流水。疑问先看 `REPORT_TEST.md 18专题` 与 `REPORT.md 8条`，结论均可溯源。 questions: 随时提。*
