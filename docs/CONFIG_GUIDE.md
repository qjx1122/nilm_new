# configs/default.yaml 配置说明（含模型与训练效率指南）

> 版本：v1.0（2026-08-14）
> 适用代码：`nilm/` 流水线（对齐《工商业负荷辨识算法开发指南 V2.1》）
> 优先级：`--time-filter-config` 用户 JSON（§12）> 本文件默认值 > 代码硬编码默认。
> 本文档描述各配置项在**当前代码实现**中的真实语义（非仅注释复述）；
> 配置结构变化时须同步更新本文档（BOOTSTRAP 收尾仪式条件触发项）。

---

## 1. 全局

| 配置项 | 当前值 | 意义 |
| --- | --- | --- |
| `experiment_name` | `guide_v2_1` | 实验名称标签，仅标识用，不影响计算 |
| `seed` | `42` | 全局随机种子。批量入口统一设置 `random.seed` / `np.random.seed`，保证切分与模型初始化可复现 |
| `output_dir` | `outputs` | 产物根目录默认标注（实际以 CLI `--output-root` 为准） |

## 2. `data` — 数据接入

| 配置项 | 意义 |
| --- | --- |
| `trains_root` / `infers_root` | 训练/推理数据根目录，按 `<终端号>_<用户号>` 一级用户目录扫描（§3.1）；原始数据全程只读 |
| `sentinel_values` | 哨兵值清单（INT32_MIN/MAX）。加载时替换为 NaN——实测数据用它们表示无效采样，不处理会被当成天文数字功率毁掉训练 |
| `derive_phase_from_ptotal` | `true`：文件只有总功率无分相时按 `ptotal/3` 均分出 pa/pb/pc（临时假设，schema 报告显式标记，非静默处理） |

## 3. `quality` — 质量门禁

不达标判 `DATA_QUALITY_FAILED`，该用户任务终止（失败隔离，不影响其他用户）。

| 配置项 | 当前值 | 意义 |
| --- | --- | --- |
| `max_missing_rate` | 0.9 | 最大缺失率，超过拒绝建模 |
| `min_coverage` | 0.15 | 时间覆盖率下限（实际点数 / 按 15min 应有点数） |
| `min_score` | 10 | 综合质量分下限（缺失/异常/覆盖合成） |
| `min_days` | 3 | 时间过滤后最少有效天数（`len(bus) < 96×min_days` 判 `INSUFFICIENT_TIME_RANGE`） |
| `max_daily_missing_rate` | 0.9 | **日级无效天阈值**：总线或分路（目标列）全天数据缺失、或当日缺失率超过该值的天，整天剔除——不参与模型训练，也不参与训练/推理阶段的评估指标计算；剔除清单落盘 `excluded_days.json`。有效点按功率列判定（与全关天口径一致） |

> 当前阈值按 5 户真实数据校准过的「放宽版」；数据质量改善后建议收紧（如 0.3 / 0.5 / 50）。

## 4. `preprocess` — 清洗与聚合

| 配置项 | 意义 |
| --- | --- |
| `clip_negative` / `allow_negative_power` | 有功负值裁剪为 0（§2.3 原则非负）。业务存在反送电必须显式 `allow_negative_power: true`，禁止静默处理 |
| `max_gap_interp` | 线性插值最长连续缺口（点数）。`2` = 只补 ≤30min 短缺口；长缺口保留 NaN 由样本构建剔除（防插值编造假数据） |
| `save_cleaned_csv` | 清洗后数据落盘 `cleaned/{bus,branch}_cleaned.csv` 开关（默认 true） |
| `min_overlap` | 母线/分路时间对齐重叠率门禁，低于此值拒绝训练（标签与特征对不上） |
| `agg_strategy` | 5min→15min 聚合策略逐物理量配置：u/i/p 取 `mean`；**pf 用 `recompute`**（由聚合后 P/S 重算——功率因数直接平均数学上不正确）。策略落盘 `agg_strategy.json` |

## 5. `features` — 特征工程（§8；FFT/THD 明确禁用 §8.5）

| 配置项 | 意义 |
| --- | --- |
| `lags: [1,2,3,4]` | 总线功率滞后特征（前 15/30/45/60min 的 pbus）。加大可看更远历史，但前 max(lag) 行因 NaN 被丢弃 |
| `rolling_windows: ["1h","6h","24h"]` | 对 pbus/ia/ua/pfa 各生成滚动均值+标准差；每加一个窗口 = 每基础量多 2 列特征 |

代码固定生成（不可配置）：差分、三相不平衡度（i/u/p）、相电流占比、slot(0–95) 与时刻 sin/cos 周期编码。
**特征列数直接影响所有模型的训练耗时**（见 §10 各模型效率因素）。

## 6. `dataset` — 样本构建（§10）

| 配置项 | 意义 |
| --- | --- |
| `window: 96` | 滑窗长度 L=96 = 过去 24h（15min 粒度）；也是训练样本量下限判据（样本 < 2L 拒绝训练） |
| `mode: seq2seq` | 窗口标签模式（seq2seq=整窗标签 / seq2point=窗末点），当前用于 `train_window_index.csv` 索引落盘；深度模型适配器内部固定 Seq2Point 逐点语义，窗口长度由各模型自己的 `params.window` 控制 |

## 7. `bus_field_map` — 总线字段映射（§3.2/§4）

ChN 只是通道标识，**物理含义必须由本配置确认**。每字段四要素：

```yaml
ua: {ch: 1, column: load_iden_data9, multiplier: 0.001, unit: V}
```

- `ch`：通道号；`column`：原始 CSV 列名（官方点位表 2026-08-14 确认）
- `multiplier: 0.001`：**实际物理量 = 原始值 / 1000**（官方倍率规则，如 PF 916 → 0.916）；CT/PT 倍率同样经此配置化
- 缺列规则：文件中找不到 `column` → WARNING + 该列置 0 + 报告标记 `MISSING_COLUMN_ZERO_FILLED`（非致命，容忍设备间列集合差异；实测 5 户均缺 data9/45/81/37/44）

官方映射：ua/ub/uc→data9/45/81，ia/ib/ic→data1/37/73，pa/pb/pc→data7/43/79，pfa/pfb/pfc→data8/44/80。

## 8. `metrics` — 评估指标

| 组 | 指标 | 说明 |
| --- | --- | --- |
| 回归 | `mae / rmse / r2 / sae` | sae=信号聚合误差（NILM 惯例，整段电量偏差占比） |
| 状态分类 | `f1 / accuracy / precision / recall` | 按用户 `on_thr_w` 把功率二值化后计算 |
| 混淆计数 | `tp / fp / fn / tn` | 同一二值化口径的原始计数；**诊断输出，不参与最优模型排序**（否则「FP 越小越好」会把全预测关机的退化模型选成最优） |

指标输出位置：`metrics.json`（test）、`metrics_by_split.csv`（train/val/test 三阶段）、`metrics_daily.csv`（逐天）、`comparison.csv/md`（模型对比与选优）。

## 9. `infer_model`（默认注释）

推理模型选择：不配置 = 用该用户训练对比综合最优（`best_model`）；配置则强制指定（必须在该用户训练清单内；§13 禁止借用他人模型）。

---

## 10. 模型清单详解（`models:`，8 模型，含训练效率因素）

所有模型经 `MODEL_REGISTRY` 注册、配置驱动实例化，统一 `(n,f)→(n,k)` 矩阵接口。

### A. 基线组（sanity 下界；毫秒级；零 ML 依赖）

| 模型 | 原理 | 参数 | 效率因素 | 定位 |
| --- | --- | --- | --- | --- |
| `history_profile` | 按一天 96 槽位取训练均值，预测=查表 | 无 | O(n)，与配置无关 | 捕捉固定作息；作息不规律即失效 |
| `proportional` | 目标分路占总功率固定比例，预测=pbus×占比 | 无 | O(n) | 验收最低 sanity；分路与总线不同步波动即失效 |
| `ridge` | 多输出岭回归闭式解 W=(XᵀX+αI)⁻¹XᵀY | `alpha`（正则强度：大→平滑抗过拟合，小→反之） | **特征数 f**（O(nf²+f³)）——lags/rolling 加多直接变慢；样本量线性 | 线性关系强的用户（842 R² 0.616） |

### B. 树模型组（当前真实数据主力；秒级）

**`random_forest`**（`n_estimators: 200, min_samples_leaf: 2`）——sklearn 原生 multioutput

- 参数：`n_estimators` 树数（**耗时线性正比**，精度边际递减）；`max_depth`（默认不限，深→慢+易过拟合）；`min_samples_leaf`（大→树浅→快且平滑）；`n_jobs: -1` 已并行
- 效率因素：树数 × n·log(n) × 特征数（每分裂扫描 √f 特征）；**CPU 核数**（树间并行，几乎线性加速）
- 实测：800 户 R² 0.768（该户最优），约 2s

**`xgboost`**（`n_estimators: 400, max_depth: 6, learning_rate: 0.05`）——每分路一个回归器，有验证集自动早停（`early_stopping_rounds: 30`）

- 参数三角：`learning_rate` 小→需更多树（更慢更稳）；`max_depth` 每 +1 层耗时约翻倍；`subsample`/`colsample_bytree`(0.8) 行列采样抗过拟合兼提速
- 效率因素：①树数×深度（boosting **串行**，不能树间并行）；②**早停实际决定耗时**（val 30 轮不提升即停，400 棵常只训一百多棵）；③分路数 k 线性倍增
- 实测：778 户 R² **0.951**（全场最佳，较原基线 +0.124），约 1–3s

### C. 深度时序组（分钟级；`device: auto` 自动检测 GPU：CUDA→MPS→CPU）

共用适配器：L=96 滑窗 Seq2Point 逐点输出（头部复制填充，输出行数=输入行数）、Adam+MSE、验证早停（`patience` 轮不提升回滚最优权重）、种子可复现、state_dict 级持久化（跨设备加载）。

**共同效率因素**（重要性排序）：

1. **`window`（第一旋钮）**：每样本 (window, f) 张量，计算量 ∝ window（transformer 为 window²）；96→48 提速约 2 倍（transformer 约 4 倍）
2. `epochs` 上限 × 早停 `patience`：实际耗时由早停决定
3. **样本量 n**：842 户 5717 样本 vs 800 户数百样本，耗时差一个量级
4. `batch_size`(256)：大 batch 提吞吐（内存换速度）
5. **设备**：有 CUDA GPU 可加速 5–20 倍（transformer 收益最大）；显式 `{device: cpu|cuda}` 可覆盖 auto

| 模型 | 结构 | 特有参数 | 特有效率因素 | 实测 |
| --- | --- | --- | --- | --- |
| `lstm` | LSTM 末隐状态→FC 头 | `hidden_size: 64`, `num_layers` | **时间步串行**（96 步循环不可并行，单位参数量最慢）；hidden² 影响每步计算 | 分钟级；当前数据量未跑赢树模型 |
| `cnn1d` | 3×Conv1d+ReLU→全局平均池化→FC 头 | `channels: 32`, `kernel_size: 5`, `num_blocks: 3` | 时间维**全并行**（DL 三者最快）；耗时 ∝ channels²×blocks×kernel×window | ~2min/大户；789 强噪声户全场相对最优（平滑归纳偏置） |
| `transformer` | 投影+位置编码→Encoder→末 token→FC 头 | `d_model: 64, nhead: 4, num_layers: 2, dim_feedforward: 128` | **自注意力 O(window²·d_model)**——三者最慢（大户 CPU ~25min）；砍 window 提速最有效；GPU 收益最大 | 842 户 DL 最好（R² 0.523），CPU 性价比低 |

### 实测结论速览（2026-08-14，5 户真实数据）

| 用户 | 最优模型 | test R² | 备注 |
| --- | --- | --- | --- |
| 842 | ridge | 0.616 | 线性关系强 |
| 778 | **xgboost** | **0.951** | 树模型大幅领先（原基线 0.827） |
| 800 | **random_forest** | 0.768 | 原 ridge 0.666 |
| 789 | cnn1d | -0.767 | 全员负（数据侧问题），DL 相对最不差 |
| 844 | proportional | — | test 仅 2 点，无统计意义 |

**调优建议**：

- 树模型是当前数据量（数千样本）下的性价比之王
- DL 组在样本量上万、且 lag/rolling 特征覆盖不了的长程模式出现时才值得调优
- 控制全量批跑时长：第一旋钮砍 DL 的 `window`/`epochs`；或在 `models:` 列表临时注释 transformer
- GPU 环境无需改配置（`device: auto` 自动启用），仅需 CUDA 版 torch

---

## 修订记录

- **v1.0（2026-08-14）**：初版——覆盖 default.yaml 全部配置项、8 模型说明与训练效率因素、5 户实测结论
