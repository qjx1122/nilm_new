# 0800 用户整体模型评估指标偏低 — 重新分析（2026-09-17）

> **类型**：专题重分析（用户指令驱动）  
> **对象**：`800080270800_4200904302272`（target_col=p1, on_thr=50W, splits 分层锚定）  
> **基线**：W-1 修复后对照（2026-09-10）+ `outputs_all_chain` 5户全量最新实录（2026-09-16 09:19 batch）  
> **方法**：配置 /  splits / 窗口 / 评估口径 / 模型族 / 跨用户横比 六维重审；以实录为准，缺口用审计工具 `audit_user_run / threshold_sweep / analyze_daily_metrics` 定位  
> **结论先行**：**0800 的“整体低”不是数据污染（W-1已清）也不是门禁误拦，而是三要素叠加——① transformer 在该户已失能（长期依赖 best=proportional 的 fallback），② 5户全量用 `base_t5 (transformer-only)`/`day_gate` 后把 fallback 路径掐掉，③ p1 回路的“开机可变功率带窄 + 关断段负荷噪声大”导致 recall 塌陷（与 2844 的 precision 塌陷对偶）**。短期无代码可救，需换模型或改回多模型对比；中期做目标与阈值校准。

---

## 1. 现状事实（以仓库配置与用户实录为准）

### 1.1 生产配置（`configs/time_filters.json:0800`，当前 HEAD）

```json
{
  "target_col": "p1",
  "on_thr_w": 50.0,
  "train": {"include": [["2026-05-21","2026-06-29"]]},
  "splits": {
    "train": {"include": [[ "05-21..05-23"], [05-27..05-30], [06-02..06-08], [06-13..06-14], [06-16..06-17], [06-19], [06-21..06-22], [06-24], [06-26], [06-28 ]]}, // 24天 开10/关14
    "val":   {"include": [[ "05-24..05-25"], [05-31], [06-09..06-10], [06-15], [06-20], [06-27 ]]},             //  8天 开3/关5
    "test":  {"include": [[ "05-26"], [06-01], [06-11..06-12], [06-18], [06-23], [06-25], [06-29 ]]},             //  8天 开4/关4
  },
  "infer": {"include": [["2026-07-01","2026-08-03"]], "exclude": [["2026-07-23","2026-07-26"]]},
  "_note_splits": "2026-09-02 重划分：训练窗 40有效天(开机17/全关23)… 旧窗6天全开机致条件缺失(transformer全关天全误报)"
}
```

- **训练池**：`05-21~06-29` 共 40 天有效（17开/23关），splits 硬锚定保证各子集开/关均衡（train 24/ val 8/ test 8）。`_note` 明示旧窗 6 天全开机已作废。
- **推理窗**：`07-01~08-03` 除 `07-23~26` 缺数段，约 30 天 → 2822 点（15min，链实录）。
- **阈值**：`on_thr=50W`（与 2842=50, 789=60 同级；2844=10），`decision_thr` 未显式配 → 默认沿用 50（无 10/400 解耦）。
- **质量门**：用户级未配 `quality`，走全局 `day_gate=true`（⑮起默认）。若 `data_quality_report` 双达标 <70，day 级会再筛——需看 `train/*/data_quality_report.html` 与 `quality.qualified_days_by_side`。

### 1.2 最近实录（用户粘贴 + 记忆重建）

| 来源 | 段 | n | MAE | R² | SAE | F1 | P / R | FP / FN | 解读 |
|---|---|---|---|---|---|---|---|---|---|
| **W-1 修复后对照（同条件 before/after，`_default` 口径）** | 800 train/val | 2197→1088 窗 | 15.1→7.6 / val 30.3→19.9 | 0.79→0.92 / 0.35→0.61 | — / F1 val 0.56→0.79 | 改善 | 幅值大幅改善 |
| 同表 | 800 test | — | 44.9→32.5 | **-0.110→+0.061** | — | **0.622→0.437 (P .502/.816→.551/.362)** | **recall -0.45 下滑** |
| 同表 | 800 infer | 2822 | — | — | 0.748→0.425 | **0.467→0.750 (proportional)** | best-model 切至 proportional 才救回 |
| **outputs_all_chain（2026-09-16 09:19, `audit` 见记忆）** | 800 train(总) | 3828≈3840-12缺 | — | — | — | — | — | 40天×96≈3840 自洽 |
|  | 800 infer | **2822** | — | — | — | **0.554** | **P 0.820 / R 0.418** | **tp438 fp96 fn610 tn1678** |
| （推导） | infer 准确率 | — | — | — | — | — | — | **0.75** | 正例率 37% (1048/2822) |

> **计算**：`P=438/(438+96)=0.820`, `R=438/(438+610)=0.418`, `F1=2PR/(P+R)=0.554`, `Acc=(438+1678)/2822=0.75`。
> **形态**：**高 P 低 R = 保守型**（预测偏关，漏报为主），与 2844 的低 P 高 R（`R 0.999 fp258`）正好对偶。阈值往上只会更低 R，故 0800 的治理方向与 2844 相反。

### 1.3 模型族历史

- 评审 + W-1 复核：**800 的 transformer 一直弱**；W-1 修复后 test `R 0.437` 下滑被判“信息论可见性边界 + 召回虚高修正”，`infer` 靠 `best=proportional`（0.750）才有可交付数字（`REPORT_TEST §2` “800 依赖 best-model 机制”已被 `REPORT.md#4` 收编）。
- `seq_models.py` 的 **y 标准化 + batch 自适应 + UNDER_TRAINED 护栏**（0800 均值坍缩修复，2026-09-02）把 `test F1 0→0.889` 救回过一次；但 0800 的 窗数/样本数少（1088 窗）仍踩 `total_steps<500` 告警边界。
- `base_t5.yaml` 是 **transformer-only**；`default.yaml` 是 `history_profile / proportional / ridge` 三基线。`outputs_all_chain` 若走 `default.yaml` 应有 fallback，若走 `base_t5` 则无。

---

## 2. “整体低”的逐项分解

### 2.1 点级分类（infer 2822 为主战场）

- **正例规模**：`TP+FN=1048` 天型中开机占比 37%，不是极稀疏；但 `FN 610` 占正例 58% → **过半开机点被判关**。
- **误差主因**：`FN ≫ FP`（610 vs 96，6.3倍），F1 瓶颈在 **R**。P 0.82 尚可，说明“敢判开的基本都对”。
- **日级推断**：若 30 天 × 平均 35 开机点/天 ≈ 1048，`FN 610` 均摊约 **20/天**；而 `FP 96` 仅 3/天。最差天可能是连续漏报（整段未检出），而非零星误报。

### 2.2 回归侧（需用户补 `metrics_by_split.csv` 的 MAE/R²/SAE，但可由历史外推）

- W-1 after：train 7.6/0.92, val 19.9/0.61, test 32.5/0.061 → **训练可学、跨天泛化弱**（R² 0.92→0.06 陡降）。
- 推断：`test R² 0.06` → 模型对幅度几乎零解释；`SAE 0.425`（proportional 口径）→ 能量误差 42%，日级漂移大。
- 0800 的 `metrics_daily.csv` 若按 `analyze_daily_metrics.py` 扫 `SAE<0.2 & F1>0.9`，预期 **不达标率 >60%**（训练侧拉低整体）。

### 2.3 训练侧（三段对比）

- train 比 test 好得多（W-1 已证），说明**不是欠训练/坍缩到常数**（坍缩时 train 也差），而是**过拟合或分布外推失败**（5-6 月训练天 → 7-8 月推理天 负荷形态迁移）。
- `day_gate` 后有效天若 <40（bus/branch 双达标过滤），样本更少，transformer（64d/4头/2层）样本效率低于 ridge。

### 2.4 评估口径

- `on_thr=50W`：若 p1 真开机功率带与 2842 类似 ~700W，则 50W 线本身不离谱；但若 p1 存在 50-200W 低风档/待机带，则 50W 会把大量低功率真开点计入正例，模型用高功率特征去拟会天然低 R。
- **链口径**（`metrics_daily_chain` 需回收）：`FP 96` 含游程 `post_min_on=1/fill_off=3` 的回填效应；`FN 610` 中的“>3 窗关断”不可回填，故链与能力口径差异小。

---

## 3. 根因假说（按证据权重排序）

### H1. 模型-数据失配：transformer 在 0800 已失能，best 应为基线模型（**首因，强证据**）

- **证据**：W-1 后 `infer best=proportional 0.750 ≫ transformer 0.467`；`test F1 0.437` 的 P/R 倒挂（.551/.362）符合 “序列模型学不到该回路时序依赖” 的典型信号。
- **机制**：① `seq_models._padded_windows` 依赖连续段，0800 即便 `cross_gap=0`，段长仍短（train 10段，最长可能仅 7 天），L=96 的上下文多样性不足；② 标签方差小（p1 若为小功率回路，`y_std` 小，梯度信噪比低）；③ 总步数 `≈ (1088/16)*150≈10200` 刚过线，但 `val` 早停可能在 20-30 轮即停，实际有效步数少。
- **后果**：`base_t5`（单模型）强行用 transformer 推理 → 整体数字即 `0.55`；若切 `default.yaml`（含 proportional），`overall_best` 会自动选 proportional，infer 可回 0.70+。
- **检验**：回收 `outputs_all_chain/800.../train/*/comparison.csv` 看三基线 vs transformer 的 `best_per_metric` / `overall_best`；若 `overall_best=proportional`，则 H1 闭环。

### H2. 样本量/多样性瓶颈（**次因，强证据**）

- 40 天有效天，train 24 天（仅 10 开机天），val/test 各 4 开机天 → **每个子集的开机多�性极薄**。`stratified_day` 按星期分层，不按功率分层，test 抽中低负荷/边界天时 recall 天塌（2842 test `R 0.762` 同病）。
- `day_gate` 后若再筛（bus/branch 各自达标），`24→？` 可能再少 2-5 天；`min_days` 仅 3，无保护。
- 0800 的 `train_window_index.csv` 窗数 1088（修复后）≈ 每有效天 27 窗，低于 2842 的 4666 窗 → 每个 epoch 梯度步数少，泛化差。
- **检验**：`train/*/data_quality_report.html` + `quality.qualified_days_by_side`（双达标/单侧达标），以及 `train_window_index.csv` 的 `len & cross_gap`。

### H3. 目标分路归属未复核（**待查，中证据**）

- 2842/2844 已于 2026-09-11 核查为 p1/p2 并入库；**789/778/800 仍待复核**（`STATUS TODO 2` 显式留痕：`789=p1+p2, 778=p2, 800=p1` 未确权）。
- 若 800 真目标是 `p1+p2`（如 789），单取 p1 会 **丢半边信号** → `y_true` 与 `X` 的互信息被人为砍半，R 低是信息论上限，非模型问题。
- **检验**：`identifiability_report`（`analysis/identifiability.py`）的 `Pbus-P1 / Pbus-P2 / Pbus-(P1+P2)` 三相关系数 + 滞后 τ；以及 `branch_sessions` 按 50W 的开/关天分布在 p1 vs p2 vs p1+p2 下的对照。

### H4. 推理期分布漂移（**中证据**）

- 训练 `05-21~06-29` vs 推理 `07-01~08-03` 跨梅雨/高温季；`REPORT_TEST 789` 已证 7 月负荷水平高于训练窗（MAE 482W 漂移）。
- 若 800 的 7-8 月开机时长/功率均值上移（p1 夏季制冷/生产加班），而模型均值锚在 5-6 月，则 `pred` 系统性偏低 → `FN` 高、`R` 低。`R²≈0` 的同时 `SAE` 大，正是均值偏移信号。
- **检验**：`metrics_daily.csv` / `metrics_daily_chain.csv` 的 `mae/r2/sae` 按日排序，看 7-8 月是否系统性差于 5-6 月 test；`threshold_sweep` 看 `pred` 分布的 `tp@[thr]` 功率带是否整体左偏。

### H5. 阈值-功率带错配（**弱证据，需数据**）

- `on_thr=50W` vs `decision_thr=50W` 同值，未做 10/400 解耦。若 p1 实测开机带在 100-300W，50W 线会把大量 50-100W 过渡带算正例，模型在边界处抖动。
- 单调阈值上移会 **同时降 R**（0800 已低 R），故不能照搬 2844 的 400 策略；反向**下探至 10-30W** 可能提 R（代价 P 降）。
- **检验**：`threshold_sweep --split test --thresholds 10,30,50,80,100` 看 `F1/P/R` 曲线是否在 30W 处尖峰；`fp 幅值带` 看关断段幻觉是否集中在 [30,50)。

### H6. 特征/总线相关性弱（**弱证据**）

- 总线侧 `ua/ub/uc/ia/ib/ic/pa/pb/pc/pfa/pfb/pfc` 经 `bus_field_map` 进模型；若 800 的分路非主回路（如照明/插座回路），与总线相关性天然低，`identifiability` 会亮 `IDENTIFIABILITY_LOW`（警告不阻断）。
- `clean.py` 的 `MISSING_COLUMN_ZERO_FILLED`（ub/ib/pfb 置 0）在该户若大面积发生，特征有效维度降。
- **检验**：`identifiability_report` + `train/*/identifiability.json`。

---

## 4. 诊断清单（用户侧，Windows PowerShell，批量一条龙）

> **前提**：已同步本分支（`git pull origin arena/01a0896c-nilm-new`），工作目录钉在 `workspace-ai-nilm-win\nilm_new`（`base_t5` 时 v5 的多副本教训），`conda activate test_gpu`。

### 4.1 定位 0800 产物（一键审计 + 逐日 + 阈值曲线）

```powershell
# 1) 一键审计（期望：T1-T4/I1-I10 全过；若有 ✗ 即为配置/产物硬问题）
python scripts/audit_user_run.py --run-root outputs_all_chain --user-key 800080270800_4200904302272
# 预期产物：
#   TRAIN  T1 metrics_by_split 三段计数和==train_predictions 段行数
#   T4 metrics_daily_chain Σ==train_predictions 总混淆 & TP+FN恒等
#   INFER  I7 总混淆=2822 & I10 链日级 Σ==I7

# 2) 日级达标（快速看多少天 SAE<0.2 & F1>0.9）
python scripts/analyze_daily_metrics.py --output-root outputs_all_chain --sae-max 0.20 --f1-min 0.90 --split all
# 产出：outputs_all_chain/analysis/daily_metrics_compliance.csv & summary.csv
# 重点看：800 的 train/val/test/infer 各自不达标行 reason 列（全漏报/全误报/全关日）

# 3) 阈值曲线（同一份 pred，扫 5 个阈值看 F1/P/R 何处峰，判断是 50 过高还是过低）
#    用最新 infer 产物 pred 列（power，恒与 decision_thr 无关）扫
$inf = Get-ChildItem outputs_all_chain/800080270800_4200904302272/infer/*/inference_result.csv | Sort-Object LastWriteTime | Select-Object -Last 1
python scripts/threshold_sweep.py --csv $inf.FullName --pred-col pred --state-col pred_state --split all --thresholds 10,30,50,80,100,150
# 看：F1 峰在 10/30 还是 80/150；fp 幅值带 [10,20)/[20,30)/[30,50) 集中度

# 4) 训练预测侧扫（看 test 段是否同样低 R，排除仅推理漂移）
$tpred = Get-ChildItem outputs_all_chain/800080270800_4200904302272/train/*/predictions/train_predictions.csv | Sort-Object LastWriteTime | Select-Object -Last 1
python scripts/threshold_sweep.py --csv $tpred.FullName --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,80,100,150
```

### 4.2 配置与质量对账

```powershell
# 5) 看 0800 实际生效配置（on_thr/decision_thr/target/splits/note）
python -c "import json; cfg=json.load(open('configs/time_filters.json',encoding='utf-8')); import pprint, json as j; pprint.pprint(cfg['800080270800_4200904302272'])"

# 6) 看比较表（谁是 overall_best；若 transformer 非 best，则“整体低”=选型不用 best）
$comp = Get-ChildItem outputs_all_chain/800080270800_4200904302272/train/*/comparison.csv | Sort-Object LastWriteTime | Select-Object -Last 1
Import-Csv $comp.FullName | Format-Table
Get-Content (Join-Path (Split-Path $comp.FullName) "summary.json") | ConvertFrom-Json | Format-List best_model, wins, models

# 7) 看窗口与数据质量（样本量 & 是否被 day_gate 再筛）
$fwin = Get-ChildItem outputs_all_chain/800080270800_4200904302272/train/*/train_window_index.csv | Sort-Object LastWriteTime | Select-Object -Last 1
python -c "import pandas as pd, glob; f=r'$($fwin.FullName)'; w=pd.read_csv(f); s=pd.to_datetime(w.win_end)-pd.to_datetime(w.win_start); print('windows',len(w),'cross_gap',(s>pd.Timedelta('23h45m')).sum(),'max',s.max())"
Get-ChildItem outputs_all_chain/800080270800_4200904302272/train/*/data_quality_report.html | Select-Object -Last 1 | % FullName
Get-ChildItem outputs_all_chain/800080270800_4200904302272/train/*/quality_advice.json | Select-Object -Last 1 | % { Get-Content $_.FullName | ConvertFrom-Json | ConvertTo-Json -Depth 6 }
```

### 4.3 目标复核（identifiability，金标准）

```powershell
# 8) 批量任务已落 identifiability.json（若无，用沙盒脚本单户跑）
Get-ChildItem outputs_all_chain/800080270800_4200904302272/train/*/identifiability.json | Select-Object -Last 1 | % { Get-Content $_.FullName | ConvertFrom-Json | ConvertTo-Json -Depth 8 }
# 要点：pearson/spearman 的 Pbus-p1 / Pbus-p2 / Pbus-(p1+p2) 三值；lag τ 最优值；IDENTIFIABILITY_LOW 标记
```

---

## 5. 需要回收的产物（粘贴即判，无需截图）

1. `outputs_all_chain/800080270800_4200904302272/train/<ts>/metrics_by_split.csv`（3 行）
2. `.../comparison.csv` + `summary.json`（best 归属）
3. `.../metrics_daily.csv`（能力口径）与 `metrics_daily_chain.csv`（链口径，含 state_thr/decision_thr 列）
4. `.../state_strategy_metrics.csv`（test 段 6 行：raw/decision × all/on）
5. `outputs_all_chain/800080270800_4200904302272/infer/<ts>/offline_metrics.json` + `metrics_daily.csv` + `metrics_daily_chain.csv`（首行 decision_thr 自描述）
6. `audit_user_run --user-key 800...` 控制台全文（含 ✗/✓）
7. `threshold_sweep` 的两份 6 行曲线表（test + infer）+ fp 幅值带表

> 预填期望（与 2844 五次复现不同，0800 尚无复现锚，故不设硬期望；仅给“低”的分解式期望）：
> - 若 `comparison.overall_best = proportional/ridge` 且 `transformer P 0.55 / R 0.36`，则 H1 成立；
> - 若 `quality.qualified_days_by_side: 双达标 <35` 或 `windows <900`，则 H2 成立；
> - 若 `identifiability: p1 相关 0.3 / p2 0.6 / p1+p2 0.75`，则 H3 成立（应切 p1+p2）；
> - 若 `metrics_daily_chain: 7-8月 MAE ≫ 5-6月 test` 且 `threshold_sweep` 曲线随 thr 单调降 R，则 H4/H5 强。

---

## 6. 治理路径（按成本与风险分级）

### A. 零成本 / 立即可做（不改模型权重）

| 选项 | 动作 | 适用场景 | 代价 |
|---|---|---|---|
| **A1 切回多模型对比** | `--base-config configs/default.yaml` 重跑 0800 单户（`--user-key 800... --output-root outputs_800_default`），让 `overall_best` 自动选 proportional/ridge；`infer_model` 锁定为该 best | 若 `comparison` 证明 transformer 非 best | 30秒-2分钟（GPU），与 `outputs_all_chain` 并存可对比 |
| **A2 阈值校准（与 2844 反向）** | `threshold_sweep` 定峰后，用户级加 `"decision_thr_w": 30.0`（提 R）或 `10.0`（极致提 R），`--output-root outputs_800_thr30` 确认 | 若曲线峰在 30/10 | 只改 `time_filters.json` 一键，确定性门=早停不变（不重训也可用 sweep 离线评估，无需重跑）|
| **A3 推理模型锁定** | 若 `default.yaml` 已得 best=proportional，`time_filters.json` 0800 块加 `"infer_model": "proportional"` | 防止后台误用 transformer 推理 | 单键，无副作用 |

> **推荐 A1 优先**：W-1 专题已背书“800 依赖 best-model 机制”；`base_t5` 单模型是 0800 整体低的**直接放大器**。切回 `default` 可立即把 `0.55→0.75` 拉回（W-1 after 数字）。

### B. 低成本 / 小改配置（需重训一户，分钟级）

| 选项 | 动作 | 论据 |
|---|---|---|
| **B1 目标复核后改 p1→p1+p2** | 若 identifiability 指向 `p1+p2` 高相关，则 `target_col` 改 `p1+p2`，`on_thr` 改 60（与 789 同），splits 复用现锚定（开/关天数按新 p1+p2 重算） | 2842/2844 的 p1/p2 修正已实证“旧漂移归因 p2”；0800 若同为混合回路，单取 p1 会丢信息 |
| **B2 扩大训练池** | `train.include` 从 `05-21~06-29` 扩至 `05-21~07-01` 排除前（含 07-01 前的 7-8 月天若质量达标），或把 `infer exclude 07-23~26` 的两天挪回训练池（若质量达标） | 24 天 train 中仅 10 开机天，样本多样性是硬瓶颈；day_gate 后的双达标天池 45 天经验（2844）可参考 |
| **B3 min_on/fill 调参** | 若 `FN` 以段漏报为主，`post_fill_short_off` 3→6 可回填短关断；若 `FP` 以碎片开为主，`post_min_on` 1→3 可削短开 | 与阈值互补，需 sweep 后看 `fn` 的游程分布 |

### C. 中成本 / 数据与特征（需跨月数据或特征工程）

- **C1 补数/特征**：若 `identifiability` 报 `LOW`，加气象/日历/节假日特征（`derived_features`），或补总线 05-21 前的离线坑数据（若存在）。
- **C2 评价口径分层**：`SAE` 在全关天奇异，`metrics_daily_chain` 按 `全关/开机日` 分组看；`offline_metrics` 聚合时排除全关天（D-7 已登记，待修复）。

---

## 7. 与 2844/2842 的横比（防误判）

| 户 | 失败形态 | 阈值策略 | 数据侧 |
|---|---|---|---|
| **2844** | 低 P 高 R（fp258） | **上调** 10→400（-85.9% fp，R 0.999→0.98） | day_gate 池 45天，改阈立得 |
| **0800** | **高 P 低 R（fn610）** | **下调或换模型**（提 R） | 40天池，transformer 失能，需切基线模型 |
| 2842 | p1 修正后健康（F1 0.989） | 50/50 已稳 | 112天池，样本足 |
| 789 | 健康（0.957） | 60 | 14天连续池，小但稳 |
| 778 | 未复核 | 50 | 待查 |

> 0800 的“整体低”是**全栈性弱**（train/val/test/infer 同低），与 2844 的“交付口径低、能力口径好”不同；故 2844 的 400 经验不可平移。

---

## 8. 立即执行建议（两步走）

1. **现在**（5 分钟，必做）：执行 §4.1-4.3 的 8 条命令，回收 §5 的 7 件产物 → 本分析的 H1-H6 可闭环到**唯一主因**。
2. **下一步**（按诊断结果二选一）：
   - 若 `best=proportional`：走 **A1+A3**（切 `default.yaml` + 锁定 `infer_model`）并产 `outputs_800_default` 作为 0800 的交付口径，发 `audit_user_run` 与 `analyze_daily_metrics` 双表存档。
   - 若 `best=transformer` 但 `R<0.5`：走 **A2**（thr30）+ **B1**（目标复核 p1+p2）二选一，各做一次 `outputs_800_thr30` / `outputs_800_p12` 对照，发 `threshold_sweep` 曲线对比。

---

## 9. 附：常见误读澄清

- “W-1 已修为何还低？”—W-1 修的是**跨间断污染**（50.5% 窗），修后 800 的幅值已好（R² -0.11→+0.06），分类的低是**可见性/样本/模型失配**，与污染无关。
- “用 transformer 大模型一定好？”—否。`comparison.wins` 在 800 上长期是 `proportional > ridge > transformer`（`REPORT_TEST` 复核表）；强行 `base_t5` 单模型=拔掉安全网。
- “SAE 0.425 高是否模型崩？”—SAE 在全关天奇异（Σy≈0），`metrics_daily_chain` 按开/关分组后看 `on_days_only SAE` 才可比（D-7 缺陷）。
- “改 on_thr 能救 R 吗？”—能，但须以 **物理铭牌**为据（10→50 已做），再往下到 10 需确认 p1 无 10-50W 合法开机；否则会把噪声正例再拉进来。

---

*本重分析落盘位置：`docs/ANALYSIS_0800_REEVAL_20260917.md`；关联代码：`nilm/evaluation/compare.py`（选型）/ `seq_models.py`（y标准化）/ `pipeline/user_task.py`（splits/day_gate/identifiability）/ `scripts/threshold_sweep.py`（阈值曲线）；待用户回收 §5 产物后追加定量判读。*
