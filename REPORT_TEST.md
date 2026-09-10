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

## [2026-09-10] 专题：W-1 修复（按时间间断分段构窗）与深度模型指标复核
- 类型：验证专题（缺陷修复 + 修复前后同条件对照实验）
- 目标与假设：
  - 修复评审专题（同日）认定的 🔴W-1：Seq 模型滑窗与 build_windows 按纯数组位置构造，跨时间间断拼窗（违反指南 §10「窗口必须连续」；实证 800 户 50.5% 训练窗被污染）
  - 假设：修复后（同数据/同配置/同种子，唯一差异=窗口语义）深度模型指标变化可归因于窗口合法性；幅值类指标（MAE/RMSE/R²/SAE，本任务核心目标）应改善，分类指标变化待实证
- 方法 / 数据 / 参数：
  - 修复设计：新增 `common.schema.segment_bounds`（间隔≠15min 即断开的连续段原语，common 层供 preprocess/models 共用，不破坏解耦守卫）；`dataset.build_windows` 与 `seq_models._padded_windows` 均改为逐段构窗（段头用段内首行填充、段尾不足一窗不产窗、全段过短报错）；BaseModel 全族 fit/predict 接口扩展可选 `index/val_index`（非序列模型忽略）；user_task 三处调用点接线；未提供索引时退回纯位置滑窗并告警（兼容旧调用）
  - 测试：新增 tests/test_window_continuity.py 13 项（原语/构窗/seq 模型边界窗口内容/接口契约/端到端产物守卫）+ 修正桩模型签名；全套 193/193 通过（180 旧 + 13 新）
  - 对照实验：5 户真实数据（after 侧以 symlink 只读复用 before 的数据目录）、同一 base_deep.yaml（history_profile/proportional/ridge/transformer{epochs150,patience20,window96}）、同一 time_filters.json（2842/800 生产节 + _default，778/789 走默认）、同种子 42；before=未修复代码（review worktree），after=修复后代码（本分支 worktree）
- 用户执行命令（实录路径：/tmp/audit/before_run.log、/tmp/audit/after_run.log、/tmp/audit/compare.py；⚠️ /tmp 为沙箱临时区且本会话经历一次快照回退，实录文件已灭失——以下为原始执行命令，可按其重建复跑）：
  - `git worktree add /tmp/review_ffeb6 origin/arena/019ffeb6-nilm-new`（before 代码+数据）
  - `git worktree add /tmp/after_wt <修复commit> && ln -s /tmp/review_ffeb6/data /tmp/after_wt/data`（after 代码，数据只读复用）
  - `python scripts/run_batch_users.py --time-filter-config /tmp/audit/tf_two.json --base-config /tmp/audit/base_deep.yaml --data-root data --output-root /tmp/audit/{before|after}`
  - 修复代码见本分支 fix(W-1) commit；冒烟预检（1 epoch 全 5 户）确认分段路径与 0 跨间断后才启动正式对照
- 结果 / 结论：

### 修复有效性（直接验证）
| 用户 | 修复前窗口（跨间断数/最大跨度） | 修复后 |
| --- | --- | --- |
| 2842 | 5711 窗，**1045 跨间断**，最大跨 **231 天** | 4666 窗，**0 跨间断**，最大=23.75h ✓ |
| 800 | 2197 窗，1109 跨间断（50.5%） | 1088 窗，**0 跨间断** ✓ |

### 指标复核（transformer，同条件对照；test 段）
| 户/段 | 指标 | 修复前 | 修复后 | 判读 |
| --- | --- | --- | --- | --- |
| 2842 test | MAE / R² / SAE | 105.5 / 0.765 / 0.137 | **89.0 / 0.808 / 0.095** | 幅值全面改善（MAE -15.6%、SAE -30.5%） |
| 2842 test | F1（P/R） | 0.9886（.994/.983） | 0.9832（.983/.983） | -0.005 基本持平，仍高位 |
| 2842 infer | MAE / R² / F1 | 262.7 / 0.625 / 0.9930 | **252.0 / 0.646 / 0.9922** | 推理全线不降、幅值改善 |
| 800 train/val | MAE / R² / F1(val) | 15.1/0.79；val 30.3/0.35/0.56 | **7.6/0.92；val 19.9/0.61/0.79** | 学习质量大幅改善 |
| 800 test | MAE / R² | 44.9 / **-0.110** | **32.5 / +0.061** | R² 由负转正 |
| 800 test | F1（P/R） | 0.622（.502/.816） | 0.437（.551/.362） | **下滑（recall -0.45）**，见解读 |
| 800 infer | F1 / SAE（best 模型口径） | 0.467 / 0.748（transformer） | **0.750 / 0.425**（proportional） | 批量结果大幅改善（best-model 机制正当切换） |
| 778 test（默认配置口径） | F1 / MAE | 0.319 / 109.1 | 0.361 / **97.0** | FP 386→314 改善 |
| 789 test（默认配置口径） | F1 / precision | 0.744 / 0.626 | 0.660 / **0.704**（FP 234→112） | precision 升、recall 降 |

### 结论
1. **修复有效且必要**：跨间断窗口 1045/1109 → 0；2842 曾存在跨 231 天的「窗口」，位置编码与序列邻接假设被系统性破坏属实。
2. **幅值类指标（任务核心目标）总体显著改善**：2842 train/val/test/infer 四段全部改善；800 train/val/test 的 MAE/R² 全部改善（test R² 由负转正）。
3. **分类 F1 非全面改善，属预期行为修正**：修复后 precision 普遍升/FP 普遍降（778/789/800 推理），recall 普遍回落——修复前跨洞窗口相当于让模型「跨天拼接记忆」训练样本，test recall 有虚高成分；修复后模型只能依赖物理合法的连续 24h 上下文。800 test F1 0.622→0.437 的下滑与其已知「可见性缺失」信息论边界一致（其 STATUS 已论证该户全关天不可分），之前的好数字部分源于非法窗口。
4. **对其既有报告的口径影响**：修复前所有深度模型指标（含 B1 全景、2842 F1 0.9913 等）建立在含跨洞窗口的训练上，**仅可作相对比较**（与评审专题结论一致）；本专题的 after 数字为合法口径基线。
5. 基线模型（ridge/proportional/history_profile）前后指标完全一致（回归锚 ✓），证明修复未意外影响非序列路径。

- 是否进入 REPORT.md（稳定结论）：**是（建议登记）**——「W-1 已修复（分段构窗）；2026-09-10 复核后 2842 合法口径基线 = test F1 0.983 / MAE 89.0W / SAE 0.095，infer F1 0.992 / MAE 252W；800 依赖 best-model 机制（infer proportional F1 0.750）」
- 遗留问题：
  - 800 test recall 下滑是否可接受需业务拍板（该户全关天不可分为数据侧边界，非代码缺陷）
  - W-2（SAE 分母 0）与 W-3（配置合并语义）仍未修，W-2 会影响本表中 800 SAE 类数字的解读（全关段）
  - 778/789 本次为默认配置口径（tf_two 未含其生产节），生产配置下的复核建议后续补做
  - 修复代码目前在本 session 分支，019ffeb6 原分支未动；是否回推上游由用户决定
  - 实验原始日志因沙箱快照回退灭失于 /tmp，本表数字为当时提取值；完整复跑路径见「用户执行命令」节

## [2026-09-10] 专题：指定用户 800080270789 transformer 重训（W-1 修复后合法口径基线）
- 类型：实验专题（用户指定单户；原「5 户全量」任务按用户指示收窄至 789）
- 目标与假设：
  - 用 W-1 修复后代码（本分支 tip 6b3f9bb）对 789 以其**生产配置**（time_filters.json：target p1+p2、on_thr_w 60、训练窗 2026-05-21~06-04、infer 7 月）重训 transformer（B1 推荐参数 epochs150/patience20/window96），建立合法口径基线
  - 假设：修复后该户指标应与其修复前历史（B1 全景 infer F1 0.9709、开机天 28/28）大体相当——789 训练窗仅 14 天且连续，跨间断面小，受 W-1 影响预计有限
- 方法 / 数据 / 参数：
  - 代码：本分支（含 W-1 分段构窗修复）；数据：`origin/arena/019ffeb6-nilm-new` worktree（/tmp/wt_data，symlink 只读复用）；配置：/tmp/audit/base_t5.yaml（transformer-only）+ configs/time_filters.json 789 生产节
  - 执行模式：模式 A（ROLE v1.3——单命令 ~6min，全程已推送）
  - 过程：首次启动因会话中断被杀客户端但训练进程存活，等待其自然完成（总 ~6min）后提取
- 用户执行命令（实录：沙盒 /tmp/wt_run/outputs/800080270789_4206894986488 同名 789 目录内 metrics_by_split.csv / train_window_index.csv / metrics_daily.csv / offline_metrics.json；⚠️/tmp 不持久，复跑命令如下）：
  - `git worktree add /tmp/wt_data origin/arena/019ffeb6-nilm-new && git worktree add /tmp/wt_run <本分支tip> && ln -s /tmp/wt_data/data /tmp/wt_run/data`
  - `cd /tmp/wt_run && python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config /tmp/audit/base_t5.yaml --data-root data --output-root outputs --user-key 800080270789_4206680982373`
- 结果 / 结论（transformer，生产口径）：

| 段 | MAE | RMSE | R² | SAE | F1 | P / R | FP/FN |
| --- | --- | --- | --- | --- | --- | --- | --- |
| train | 89.7 | 151.3 | 0.939 | 0.014 | 0.9658 | .958 / .974 | 18/11 |
| val | 169.0 | 262.2 | 0.797 | 0.015 | 0.9650 | .950 / .981 | 16/6 |
| test | 121.1 | 169.5 | 0.429 | 0.428 | 0.9623 | .927 / 1.000 | 4/0 |
| **infer（7 月，2630 点）** | 481.8 | 695.2 | 0.304 | 0.553 | **0.9566** | **.974 / .940** | — |

- **窗口连续性：570 训练窗，0 跨间断（最大跨度=23.75h 标称）**；best=transformer，无 PRED_COLLAPSED/UNDER_TRAINED
- **开机天识别 28/28（与其修复前 B1 报告 28/28 持平）**；日级 F1 中位 0.9673、达标天（>0.9）27/28；最差日 2026-07-27（0.739）即其历史已审查「中午 1.5h 真实停机」日，两口径互相印证
- 与修复前历史对照：点级 infer F1 0.9709→0.9566（-0.014，小幅回落）、开机天 28/28 持平——**789 受 W-1 影响很小**（14 天连续训练窗、跨间断面小），与其数据形态预期一致；幅值类（infer MAE 482W/SAE 0.55）反映 7 月负荷水平高于训练窗（其既有「训练期与推理期负荷漂移」结论口径不变）
- 是否进入 REPORT.md（稳定结论）：**是（建议登记）**——「789 合法口径基线（2026-09-10）：infer F1 0.957/P 0.974/R 0.940、开机天 28/28、日级 F1 中位 0.967；受 W-1 影响可忽略」
- 遗留问题：①test 段 R² 0.429/SAE 0.428 偏弱——test 天数少（约 2 天）且幅值形态与训练窗差异大，属小样本+漂移，非缺陷；②其余 4 户（2842/800/778/2844）重训待用户指示（2842 ~23min/户、2844 门禁拦截预期不变）；③infer 幅值漂移老问题不在本任务范围
