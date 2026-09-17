# 0800 B1 池内划分失衡诊断 — train 15/23 65%关 vs val 1/8 12% vs test 0/7 0%（2026-09-17）

> **2026-09-17 更新（自动化已实现）**：新增 `split_strategy=stratified_by_state`（`contracts.py/splits.py/user_task.py` + `tests/test_splits.py` 6/6 绿，B1 池 38天实证 `43%/37%/42%` 极差<6pct），配置 `configs/time_filters_0800_B1b.json`（`split_strategy=stratified_by_state`）已生成待双路验证，判据 `infer F1+0.02`，详见 `STATUS.md` 进行中。

> **用户现况**：池级 训练 38天16关（42.1%关）/ 推理 19天5关26.3% 池级差已收至 15.8pct；**池内 `train 15/23 65.2%关 / val 1/8 12.5% / test 0/7 0%`**  
> **对照**：B 原池 40天17开23关 42.5%开时 手锚 `train 10/14 41.6%开 / val 3/5 37.5% / test 4/4 50%` 三者 8pct 内一致；B1 切为 **自动 `stratified_day`（按星期）** 后失衡  
> **审计**：`outputs_0800_B1_* audit` 全绿但 `test F1 0.93` 虚高（0关）、`val F1 0.84` 乐观、`train 65%关` 过关

---

## 一、结论先行

**不合理，且是 B1 `test 0.93 大幅赢 / infer 0.69 反降` 的直接原因之一：**

- **train 65%关** 让模型 **关先验过重**（`proportional train F1 0.44` 低，`ridge 0.24` 坍缩前兆），学到“多报关”
- **val 12%关 / test 0%关** 让 **早停与评测失真**（`val F1 0.74→0.84` 乐观，`test F1 0.93` 全开段虚高），`test` 的 `0关` 与 `infer 26%关` 错位 `26pct`，比池级 16pct 更重
- **理想应为 42%关均摊**：`train 9-10关/23（39-43%） / val 3关/8（37%） / test 3关/7（42%）` 或至少 `test 2关/7 28%` 贴近推理 26%

**处置**：**B1 池级对齐保留，池内改回手锚均衡**（如 `train 10/23 43% / val 3/8 37% / test 3/7 42%`），或新增 `split_strategy=stratified_by_state` 自动化；否则 `test` 评测不可信，`infer` 的 5关 26% 将持续被低估。

---

## 二、定量审计

### 2.1 现状 vs 理想

| 层 | 天数 | 关 | 开 | 关占比 | 与池均 42.1% 差 | 与推理 26.3% 差 |
|---|---|---|---|---|---|---|
| **池均** | 38 | 16 | 22 | **42.1%** | — | +15.8pct |
| **train** | 23 | **15** | 8 | **65.2%** | **+23.1pct** | **+38.9pct** |
| **val** | 8 | 1 | 7 | 12.5% | -29.6pct | -13.8pct |
| **test** | 7 | 0 | 7 | **0%** | **-42.1pct** | **-26.3pct** |
| **infer** | 19 | 5 | 14 | 26.3% | -15.8pct | — |

*方差：65.2-0=65.2pct 极差，B 原池手锚极差仅 58-37=21pct。*

### 2.2 对指标的传导

| 现象 | 数据 | 机理 |
|---|---|---|
| `train 65%关` → `train F1 0.44` 低 | `B proportional train 0.44` vs `B1 train 0.44` 持平，但 `B1 transformer train 0.71 r²0.66` 仍可学 | 关多但特征弱（`ub/ib/pfb 置0`），`65%关` 未提 `train` 精度 |
| `val 12%关` → `val F1 0.74-0.84` 乐观 | `B1 val 0.748→0.844` 高于 `train 0.44`，早停点 `ep38 val_loss 1.42` 可能偏晚 | `val` 关少则 `FP` 少，`F1` 虚高，早停偏向“敢开”模型 |
| `test 0%关` → `test F1 0.93` 虚高 | `B1 test 0.93` vs `B1 infer 0.69` 差 0.24 | `test` 无关天则 `F1=2PR/(P+R)` 分母无 `全关天 F1=0` 拖累，且 `SAE 0.03` 无除零伪值 |

> **B1 `test 0.93` 是“全开段内测”，`infer 0.69` 才是“含关段外推”**；`B` 的 `test 4关50%` 虽拉低均值 `0.60`，但更接近推理 26% 的含关形态，故 `B infer 0.77` 反高于 `B1 infer 0.69`。

### 2.3 与 `stratified_day` 的关联

`build_split_masks` 的 `stratified_day` 按 **星期几** 分层（`by_dow: {0:[Mon..], ...}`），不感知 `on/off`，`B1` 的 `train` 恰抽中 **周一/二关天扎堆**（`05-26 Mon 关` 等），`val/test` 抽中周末开天扎堆，致 `65% vs 0%`。B 原池用 **手锚 `splits.include` 硬锚定**（`train 10/14 / val 3/5 / test 4/4`）才保 8pct 内一致。

---

## 三、治理路径

### 3.1 手锚均衡（零代码，推荐，30秒可验）

保留 B1 池 `05-21~07-10 剔12关 38天 22开16关`，仅改 `splits` 为 **关占比 42% 均摊**：

```json
// 在 configs/time_filters_0800_B1.json 上覆盖 splits（示例，需按真实关天清单校准关/开归属）
"splits": {
  "train": {"include": [
    ["2026-05-21","2026-05-23"], ["2026-05-27","2026-05-28"],
    ["2026-06-02","2026-06-04"], ["2026-06-07","2026-06-08"],
    ["2026-06-12","2026-06-14"], ["2026-06-16","2026-06-17"],
    ["2026-06-19","2026-06-19"], ["2026-06-21","2026-06-22"],
    ["2026-06-24","2026-06-24"], ["2026-07-01","2026-07-03"]
  ]}, // 23天 13开10关 43%关（示例：需以真实关天为准，控制 9-10关）
  "val": {"include": [
    ["2026-05-24","2026-05-25"], ["2026-06-09","2026-06-10"],
    ["2026-06-15","2026-06-15"], ["2026-06-27","2026-06-29"]
  ]}, // 8天 5开3关 37%关
  "test": {"include": [
    ["2026-05-31","2026-05-31"], ["2026-06-18","2026-06-18"],
    ["2026-06-20","2026-06-20"], ["2026-06-25","2026-06-25"],
    ["2026-07-04","2026-07-10"]
  ]}  // 7天 4开3关 42%关（与池均 42% 一致，贴近推理 26%+16pct）
}
```

*原则*：每 split 关占比 **37-43%**（池均 42%±5pct），`test` 至少 **2-3关**（28-42%）以贴近推理 26%，`train` 不超 **45%** 关。

**更简**：复用 B 原池手锚模板，仅把 `07-01~10` 的 7天（5开2关，需以真实为准）按 2/2/3 分给 train/val/test，使三者关占比同步抬至 42%。

### 3.2 自动化（治本，P2）

新增 `split_strategy=stratified_by_state`：按 `on_thr_w` 的日开/关标签分层，再按 `split_ratios 0.6/0.2/0.2` 切分，自动保关占比≈池均，无需手锚维护。B1 池上 `train 9-10关/23` 自动达成。

---

## 四、验证（与 B1 同命令，仅换 splits）

```powershell
# 1) 生成手锚 B1b 配置（在 B1 基础上覆盖 splits）
Copy-Item configs/time_filters_0800_B1.json configs/time_filters_0800_B1b.json
# 手动粘贴 §3.1 splits 到 B1b 的 0800 块

# 2) 双路重跑
python scripts/run_batch_users.py --base-config configs/default.yaml --time-filter-config configs/time_filters_0800_B1b.json --data-root data --output-root outputs_0800_B1b_default --user-key 800080270800_4200904302272
python scripts/run_batch_users.py --base-config configs/base_t5.yaml --time-filter-config configs/time_filters_0800_B1b.json --data-root data --output-root outputs_0800_B1b_t5 --user-key 800080270800_4200904302272

# 3) 审计与阈值
python scripts/audit_user_run.py --run-root outputs_0800_B1b_default --user-key 800080270800_4200904302272
python scripts/audit_user_run.py --run-root outputs_0800_B1b_t5 --user-key 800080270800_4200904302272
python scripts/threshold_sweep.py --csv (Get-ChildItem outputs_0800_B1b_default/.../inference_result.csv).FullName --pred-col pred --state-col pred_state --split all --thresholds 10,30,50,80,100,150
```

**判据**：`train/val/test 关占比 43%/37%/42%` 极差 <10pct；`val F1` 回落至 `0.75` 附近（不虚高），`test F1 0.93→0.85` 回落但更可信，`infer 0.69→0.75+` 为合入。

---

## 五、风险

- **手锚维护成本**：`splits` 与 `train 05-21~07-10 剔12关` 强耦合，未来再调剔关数需同步改锚；`stratified_by_state` 可根治。
- **样本量**：B1 38天已薄，手锚不改变样本量，`r²` 预期持平 `0.79`，`infer off_fp 60%` 仍需 `lag75` 治理。

*证据链：`branch_sessions 71天29关→双达标38天22/16` + `B1 切分 2200/768/670` + `train 15/23 65% vs test 0/7 0%` + `B1 test 0.93 vs infer 0.69` + `B手锚 10/14 3/5 4/4`；复现命令与 B1 同参。*
