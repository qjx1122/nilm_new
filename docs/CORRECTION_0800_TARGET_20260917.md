# 0800 目标分路二次修正 — OQ-16 PAUSED（2026-09-17）

> **上位事实**：用户核实 `800080270800_4200904302272` 分路数据**不在 `p1/p2/p3`**（2026-09-17）。此为继 `OQ-13（2842→p1, 2844→p2）` 后的**二次目标归属修正**，触发“重大输入变化须以上位约束重新审视既有交付物”。

## 一、历史结论处置

| 交付物 | 原口径 `p1 40天 hand-anchor / decision30 / lag5[5,1,2,3,4] / OFF8` | 处置 |
|---|---|---|
| `ANALYSIS_0800_B1_RESULTS` / `B1b` / `LAG5` / `OFF8` / `T5_FROM_SCRATCH` / `FINAL` / `REEVAL*` / `ROOTCAUSE` / `SPLIT_REBALANCE` / `TRAIN_INFER_REDISTRIBUTION` / `INNER_SPLIT`（12篇） | `F1 0.773-0.783 R²0.36-0.49 SAE0.22` 等 p1 结论 | **降级为「错误目标参考」**（同 OQ-13），保留审计痕迹，**不作为现行基线/最优依据**，仅可作 `p4` 待查时的相对比较 |
| `configs/time_filters.json:0800` `target p1` `splits hand-anchor` `decision30` | 生产 `p1` 配置 | 加 `_OQ16_PAUSED` `_status PAUSED_OQ16`，**暂停使用**，`target` 待重定（如 `p4` 待查） |
| `REPORT.md#4` 800 条 | `infer proportional 0.750` | 改为 `OQ-16 PAUSED` 注记 |
| `docs/ANALYSIS_5USERS_OPTIMAL_CONFIG` / `EXECUTION_PACKAGE_5USERS_*` 中 800 行 | 800 最优按 p1 归纳 | **本次修正为 4户最优**（2842/2844/778/789），800 行为 PAUSED 注记，见 `ANALYSIS_4USERS_OPTIMAL_CONFIG_20260917.md` |

**PPD**：`lag5[5,1,2,3,4]` 虽在 800 p1 上实证 `+0.10 R²`，但因目标错配，其对 `p4` 是否有效需重验；对 `2842/2844/789` 的 `lag5` 零风险合入（`ridge` 持平）**不受影响**（已在 `default/base_t5/base_optimal` 合入）。

## 二、现行 4户最优（`base_optimal` 4模型择优仍有效）

| 户 | 最优 `base` | 要点 |
|---|---|---|
| 2842 `p1 50/50` | `base_t5` transformer | `2025-07-10~06-30` `day_gate 107天` `F1 0.989` |
| 2844 `p2 10/30` | `base_t5` transformer | `day_gate 45天 +0.2` `F1 0.891`（`400` 7月最优候选） |
| 778 `p2 50` | `default` | `05-21~06-08+07-01~07-08` |
| 789 `p1+p2 60` | `base_t5` transformer | `05-21~06-04 14天 570窗` `F1 0.957` |

`base_optimal.yaml`（`lag5` 4模型）对4户仍为最优合一口径，单批 `run_batch` 自动择优。

## 三、暂停期间执行

- **不再对 0800 做分析/重跑**，`EXECUTION_PACKAGE_4USERS_OPTIMAL_20260917.md` 为 4户 B 模式全量包（单批 `base_optimal`）。
- 待 0800 真实分路（如 `p4`）确认后，再走 `identifiability` 重定 `target` + `splits` 重算 + `lag5` 重验，另立项。

*落盘：`NILM_DATA_DICT v0.2.10 OQ-16` `configs/time_filters.json:0800 _OQ16_PAUSED` `REPORT.md#4` `STATUS 决策记录` 本篇。*
