# REPORT_TEST.md — 专题报告（只追加，按专题分节）

## [2026-09-10] 专题：arena/019ffeb6-nilm-new 分支代码与数据一致性评审
- 类型：验证专题（代码评审 + 数据审计 + 测试复跑 + 端到端冒烟）
- 目标与假设：
  - 用户指令「拉取新版本，根据代码和数据检查代码是否存在问题」
  - 待评审对象：`origin/arena/019ffeb6-nilm-new`（87 提交，tip 6e3d1aa；含 nilm/ 流水线、scripts、configs、5 户真实数据 79.8 MB）。该分支是 `arena/019ffa35-nilm-new`（30 提交）的严格超集，故评审以其为准
  - 假设：其自称「180 项测试全过」属实、指南 V2.1 为最高开发规范
- 方法 / 数据 / 参数：
  - 静态评审：contracts/schema/csv_source/discovery/align/clean/scaling/splits/target/dataset/features/seq_models/baselines/metrics/constraints/state/validator/identifiability/user_config/user_task/batch/merge + default.yaml + time_filters.json 逐文件核对指南条款
  - 数据审计：pandas 全量扫描 5 户 buses/branches（时间节奏、覆盖率、哨兵值、字段映射自洽性 P/(U·I·PF)、单位量级、网格对齐、NaN 结构）
  - 动态验证：venv 全新安装依赖跑全套 pytest；用 800 户真实数据端到端 train+infer 冒烟
- 用户执行命令（实录路径：本文件本节 + STATUS.md 决策记录；评审工作区为临时 worktree `/tmp/review_ffeb6`，重建命令如下）：
  - `git fetch origin '+refs/heads/*:refs/remotes/origin/*'`
  - `git worktree add /tmp/review_ffeb6 origin/arena/019ffeb6-nilm-new`
  - `python3 -m venv /tmp/review_venv && pip install numpy pandas pyyaml tabulate scikit-learn xgboost lightgbm matplotlib pytest torch`
  - `python -m pytest tests/ -q`（在 /tmp/review_ffeb6 下）
  - `python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --user-key 800080270800_4200904302272 --data-root data --output-root /tmp/audit/outputs`
- 结果 / 结论：

### 总体结论
**代码工程质量和契约纪律显著高于平均水平**（180/180 测试独立复跑通过；RE_BUS/RE_BR/skipna=False/Scaler 仅 Train 拟合/非负约束/失败隔离等关键契约全部忠实落地；端到端冒烟 train+infer OK）。但存在 **1 个高危实证缺陷、2 个中危缺陷、4 个低危问题**，以及一组数据侧口径疑点。

### 🔴 高危（建议尽快修复）
**W-1 Seq 模型滑窗跨越时间间断，违反指南 §10「窗口必须连续」，实证污染率 50.5%**
- 证据：`nilm/models/seq_models.py::_padded_windows` 纯按数组位置滑窗；`nilm/preprocess/dataset.py::build_windows` 同样纯位置滑动（docstring 把「不跨 split」责任推给调用方，但**无人负责时间连续性**）。上游三处会制造时间洞：`drop_invalid_rows` 剔除 NaN 行、`train.include/exclude` 过滤、`splits.*.include` 按日锚定
- 实证：800 户按其生产配置（time_filters.json 的按日锚定切分）实际运行，`train_window_index.csv` 2197 窗中 **1109 窗（50.5%）实际跨度 > 标称 23.8h**，中位额外跨度 24h、最大 **4.99 天**。lstm/cnn1d/transformer 的序列邻接假设与位置编码被系统性破坏（扁平模型 ridge/proportional 不受影响）
- 影响面：STATUS 里 778「FP 208 待查」、800 transformer 系列指标等深度模型结论均在含污染窗口的数据上测得——相对对比仍有效，绝对数值可信度需打折扣
- 修复建议：按 `index.diff() > 15min` 把切分内数据切成连续 run，窗口只在 run 内构造（run 首部可用 run 内复制填充替代跨 run 取数）；补「间断处窗口重置」单测（当前 tests/ 对窗口连续性零覆盖，grep 证实）

### 🟡 中危
**W-2 SAE 指标全关段分母 0（已知未修，应修未修三现）**
- `nilm/evaluation/metrics.py`：`denom + 1e-9`，全关段 Σtrue=0 → SAE≈1e12 天文数字。REPORT_TEST.md L845 自记「第三次出现，建议正式修复排期」但仍带病运行。污染 metrics_daily.csv（全关天行 SAE 失真）。建议：全关段 SAE 置 NaN 或按其建议「全关日只考核 F1」

**W-3 用户 JSON 配置「整节替换」合并语义静默失效 `_default`**
- `nilm/pipeline/user_config.py::resolve_user_config`：`user.train 存在 → 完全顶掉 _default.train`（无字段级合并）。实例：778 配置了自己的 `train.include`（含 2026-07-01~07-08），`_default.train.exclude`（排除 7 月起）被静默丢弃 → 7 月上旬进入训练集。是否符合意图无法从配置自证；至少需在 docs/CONFIG_GUIDE.md 显式声明该语义，或改为 include/exclude 字段级合并
### 🟢 低危 / 文档
- **W-4 文件名时间弱校验**：`csv_source.py` 只查「CSV 内存在 ≥start 的天」——2842 总线文件名写 260803、实际数据止于 2026-07-30 21:13，校验照常通过
- **W-5 质量边界过宽**：`validator.py BOUNDS ua∈(0,1000)`，而实测电压中位 ~102（×0.001 后）、2842/2844 存在 651.8/736.3 的尖峰（6-7 倍中位）全部放行，outlier_rate 对这类异常钝感
- **W-6 README 过时**：README 写「98 项测试」，实际 180 项（独立复跑 180/180 通过）
- **W-7 推理特征 lags 跨洞陈旧**：`build_features` 在时间过滤后的带洞时间轴上 shift/rolling，洞边界行的 lag_1 实为 >15min 前（行数占比小，影响轻微）

### 📊 数据侧发现（供与采集方核对，非代码缺陷）
- **D-1 倍率/口径系统性偏差**：5 户 A/C 两相 `P/(U·I·PF)` 中位 **1.065~1.069**（IQR ±3%，高度一致）——若点位表与 0.001 倍率完全正确应≈1.0；建议与采集方核对电压倍率或 P 口径定义（指南 §5.2「必须确认原始 P 物理定义」正属此类）
- **D-2 284x 设备三列缺失**：ub(data45)/ib(data37)/pfb(data44) 在 2842/2844 文件中不存在 → 置 0（MISSING_COLUMN_ZERO_FILLED，代码有告警与报告 ✓）；但 0 填后 imb_u/imb_i 等三相结构特征失真，模型仍消费该特征
- **D-3 284x 覆盖率极低**：2842 总线均值间隔 834s（覆盖≈36%）、2844 达 1323s（≈23%）；分路名义 390 天仅 150 天有数据（38.5%）。质量门禁已按实测放宽（min_score 70 拦截 2844 ✓ 与其 STATUS 记载一致）
- **D-4 PF 寄存器封顶**：pfa 最大值恰为 0.70（284x）/0.80（778/789/800），疑似原始数据封顶，PF 特征顶部失真
- **D-5 无效分路通道**：2842 p3（91% NaN）/p4、2844 p4 大面积缺失 → 按契约被识别为无效通道丢弃 ✓ 处理正确
- **D-6 时间戳形态**：总线 5min 节奏（中位 300s）但带随机秒偏移（00:04:59 式），resample 分桶处理正确；分路 100% 落在 15min 整点网格 ✓；全部 naive 无时区（内部一致，建议在契约中显式声明「无时区本地时间」）

### 测试与冒烟记录
- `pytest tests/ -q`：**180 passed**（含 torch 全新安装后 test_ml_models 31 项）——与其 STATUS 声称一致
- 端到端冒烟（800 户，3 基线模型）：train OK（best=proportional，ridge 触发 PRED_COLLAPSED 护栏 band 49.3<50 ✓ 行为符合设计）、infer OK（2822 点，inference_result.csv 9 列契约齐全）
- 无阻塞项；评审未发现安全漏洞层面问题（pickle 加载模型为常见注意事项）

- 是否进入 REPORT.md（稳定结论）：**是**——建议登记两条：①「W-1 窗口连续性缺陷修复并重训前，lstm/cnn1d/transformer 的既有绝对指标仅可作相对比较用」；②「284x 用户数据覆盖率与三列缺失事实，任何涉及 2842/2844 的结论须注明数据覆盖口径」
- 遗留问题：
  - W-1/W-2 修复与重训后指标复核（建议下一专题）
  - `_default` 合并语义需产品拍板（W-3）
  - D-1 倍率口径待与采集方核对（可回填 NILM_DATA_DICT.md §14 OQ）
  - 本评审为静态+冒烟级，未逐户重训深度模型复核指标（算力/时间约束，已声明口径）
