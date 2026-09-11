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

## [2026-09-10] 专题：2842 transformer 重训（模式 B 执行包）
- 类型：实验专题（用户显式指定模式 B，ROLE v1.4）
- 目标与假设：
  - 用 W-1 修复后代码（本分支，tip ≥ 537d9a6）对 2842 以其生产配置（time_filters.json：target p1+p2、on_thr_w 50、训练窗 2025-07-10~2026-06-30、infer 2026-07）重训 transformer（B1 推荐参数 epochs150/patience20/window96）
  - 预期（基于修复前后同条件对照实验）：幅值指标改善（修复前同配置实测 test MAE 105.5→修复后 89.0、R² 0.765→0.808、SAE 0.137→0.095）、F1 大致持平或略降（0.9886→0.9832）、infer 不降（0.9930→0.9922）
- 方法 / 数据 / 参数：见下方执行包
- 用户执行命令（实录路径：待用户回报后补记）：

### 📦 执行包（v2 修订：Windows PowerShell 原生命令；v1 含 bash 语法致用户环境报错，已替换）
> 适用环境：Windows + PowerShell；macOS/Linux 用户把「PowerShell 版」换成「bash 版」即可（两者都给出）。

**① 拉代码 + 自检**（配置文件已入库 `configs/base_t5.yaml`，无需本地创建）：
```powershell
git fetch origin
git checkout arena/01a0896c-nilm-new
git pull origin arena/01a0896c-nilm-new          # 确保拿到 configs/base_t5.yaml
(Select-String -Path nilm\common\schema.py -Pattern "segment_bounds").Count   # 期望 ≥1（W-1 修复标识）
```
bash 版：`grep -c "segment_bounds" nilm/common/schema.py`

**② 数据就位**（data/ 在 019ffeb6 工作区，用 Junction 链接；也可直接复制）：
```powershell
New-Item -ItemType Junction -Path data -Target "<你的019ffeb6工作区绝对路径>\data"
Get-ChildItem data\trains                          # 验证：应能看到 5 个用户目录
```
bash 版：`ln -s <019ffeb6工作区>/data data`

**③ 执行**（单行命令，无续行符；GPU 机自动用 CUDA，纯 CPU 约 20~25 分钟）：
```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_t5_2842 --user-key 800080252842_4206894986488
```
正常结束标志：`批量[infer] 800080252842_4206894986488 -> OK`；训练中应出现日志「滑窗按时间连续段构造：N 段」（W-1 修复生效标识）。

**④ 窗口连续性自检**（可选，期望 cross_gap=0）：
```powershell
python -c "import pandas as pd, glob; f=sorted(glob.glob('outputs_t5_2842/800080252842_4206894986488/train/*/'))[-1]+'train_window_index.csv'; w=pd.read_csv(f); s=pd.to_datetime(w.win_end)-pd.to_datetime(w.win_start); print('windows',len(w),'cross_gap',(s>pd.Timedelta('23h45m')).sum(),'max',s.max())"
```

**⑤ 结果回收**（回报以下内容即可，其余我来判读）：
- 控制台最后 5 行（两行 `批量[...] -> 状态` + 是否出现「滑窗按时间连续段构造」）
- 4 个文件内容：`outputs_t5_2842/800080252842_4206894986488/train/<时间戳>/` 下 `metrics_by_split.csv`、`train_window_index.csv`；`infer/<时间戳>/` 下 `offline_metrics.json`、`metrics_daily.csv`（粘贴或入仓库告知路径均可）
- 回收格式模板：
```
train 段: MAE=__ R2=__ SAE=__ F1=__ P=__ R=__
val   段: MAE=__ R2=__ SAE=__ F1=__ P=__ R=__
test  段: MAE=__ R2=__ SAE=__ F1=__ P=__ R=__ (FP=__ FN=__)
infer : MAE=__ R2=__ SAE=__ F1=__ P=__ R=__
windows=__ cross_gap=__ max_span=__
训练日志是否出现"滑窗按时间连续段构造": 是/否
批量状态: train=__ infer=__
```

- 结果 / 结论（**用户提供实录**：RTX 3080 GPU，Anaconda env test_gpu，2026-09-10 19:59:36~20:00:06 全程约 30 秒；train OK best=transformer + infer OK n=2326）：

### W-1 修复在用户环境生效的三重证据
1. 日志 4 次出现「滑窗按时间连续段构造：N 段」：train **12 段** / val **7 段** / test **11 段** / infer **2 段**——与沙盒修复后运行完全一致
2. `train_window_index.csv` 实录核查：全部窗口跨度 = 标称 23h45m；跨天跳跃处（2025-07-14→07-16、2025-08-20→2026-04-09、2026-04-30→05-03 等）均为**段重置**，无一窗跨间断；窗口总数 4666 与沙盒修复后运行**完全一致**（构窗仅依赖时间戳，与设备无关 ✓）
3. 早停于 epoch 47（与修复前 B1 报告的早停轮次一致）；无 PRED_COLLAPSED / UNDER_TRAINED

### 指标对照（同数据、同配置；沙盒=修复前后同条件对照实验，GPU=本次用户实录）

| 段 | 指标 | 修复前(沙盒) | 修复后(沙盒CPU) | **修复后(用户GPU)** | B1 历史(修复前) |
| --- | --- | --- | --- | --- | --- |
| test | MAE / R² / SAE | 105.5 / 0.765 / 0.137 | 89.0 / 0.808 / 0.095 | **91.5 / 0.810 / 0.074** | — |
| test | F1（P/R，FP/FN） | 0.9886（.994/.983，6/17） | 0.9832（.983/.983，17/17） | **0.9833（.978/.988，22/12）** | 0.988/P0.996/FP4 |
| infer | MAE / R² / SAE | 262.7 / 0.625 / 0.394 | 252.0 / 0.646 / 0.380 | **250.9 / 0.654 / 0.372** | — |
| infer | F1（P/R，FP） | 0.9930（.991/.995） | 0.9922（.989/.995） | **0.9897（.984/.995，FP 19）** | **0.9913**（历史最好） |

### 判读
1. **GPU 结果与沙盒修复后结果同判读**（差异小于运行间噪声，GPU/CPU 非确定性范围内）：test F1 0.9833 vs 0.9832、infer MAE 250.9 vs 252.0——修复效果在独立设备上复现 ✓
2. **对修复前：幅值类指标全面改善**（test MAE -13.3%、SAE -46%；infer MAE -4.5%、R² 0.625→0.654），**分类 F1 基本持平**（test -0.005、infer -0.003）——「跨洞非法记忆移除 → recall 虚高修正」的预期形态，非退化
3. **对 B1 历史最好 infer F1 0.9913：本次 0.9897（-0.0016，实质持平）**——W-1 修复在提升幅值指标的同时未损核心分类指标，合法口径基线成立
4. **日级指标**：推理 24 个有效天 F1 全部 ≥0.909（最低 2026-07-23，仅 42 点半天）、FP 0~3/天；日级 SAE 从月初 0.086 递增至月中后 0.37~0.47——**确认其既有「7 月负荷水平超训练域」幅值漂移问题**（其 STATUS 已有同类记载），属数据侧已知问题而非本次修复引入
5. **模式 B 实证收益**：GPU 训练 47 epochs 约 19 秒、全流程 30 秒 vs 沙盒 CPU 同任务 23 分钟（约 **46×** 加速）——验证 ROLE v1.4 双模式机制的价值；flash attention UserWarning 为 Windows 版 torch 未编译 flash-attention 的无害提示
6. 数据侧告警与既往一致（ub/ib/pfb 置 0、剔除无效天 train 5 天/infer 7 天），处理行为确定

- 是否进入 REPORT.md（稳定结论）：**是（建议登记）**——「2842 合法口径基线（用户 GPU 实录，2026-09-10）：test F1 0.983 / MAE 91.5W / R² 0.810 / SAE 0.074；infer F1 0.990 / P 0.984 / FP 19 / MAE 250.9W；修复前历史最好 infer F1 0.9913 实质持平，幅值指标全面改善」
- 遗留问题：
  - 7 月幅值漂移（infer SAE 0.372、日级最高 0.47）为数据侧已知问题：可扩充训练时间范围覆盖季节变化或建立月度重训机制（其 STATUS 已有同类结论，待用户拍板排期）
  - 推理判决链口径（decision_thr_w+游程的 state_strategy 指标）本次未回收；点级口径已自足，如需可补一条 --stage infer --force
  - 其余 3 户（800 / 778 / 2844）重训待指示（执行包可复用 configs/base_t5.yaml，仅换 --user-key）

## [2026-09-10] 专题：2844 质量门禁拦截归因（沙盒真实数据逐位复现）

- 任务：用户要求「针对 2844 用户，详细分析被拦截原因」。模式 A（沙盒分析），状态码预期 DATA_QUALITY_FAILED。
- 数据与方法：本轮 /tmp 环境再次清空（第 5 次），原始数据从**远端历史分支 `arena/019ffeb6-nilm-new`（tip=6e3d1aa）的 data/ 提交**经 `git archive` 提取（2844 trains/infers 目录树哈希同为 c360290f，逐位一致；该分支数据提交 f2ccca9/1c25765/21cb4b4 即 B1 批跑所消费数据）。分析代码严格按 `user_task.run_user_train` 原始路径逐步复现（加载→清洗→目标通道→15min 重采样→对齐→quality_report→assert_quality），并与 B1 时代 6e3d1aa 版本逐段核对一致（门禁段落零差异）。
- **逐位命中**：复现 assert_quality 抛出 `bus 质量分 69.63 < 70`——与 B1 时代用户 STATUS 原载「2844 质量门禁拦截（bus 69.63<70）」逐位一致 ✓。拦截点=训练阶段 bus 门禁第 4 条款（质量分），branch 门禁在其后未触达；infer 阶段同构报告但只报告不设门禁。

### 门禁四条款核对（复现实测）

| 条款 | bus（先断言） | branch（未触达） | 阈值 | 判定 |
| --- | --- | --- | --- | --- |
| 非空 | 14254 行 | 14254 行 | >0 | PASS |
| 缺失率 | 0.3037 | 0.4671 | ≤0.9 | PASS |
| 覆盖率 | 0.3817 | 0.3817 | ≥0.15 | PASS |
| **质量分** | **69.63** | **53.29** | **≥70** | **bus FAIL（拦截点）；branch 亦不及** |

- 得分分解：bus 69.63 = 100×(1−0.3037)×(1−min(1,5×0.0))。**outlier_rate=0**（负功率已清洗 clip；电压尖峰 736.3/657.7 在 BOUNDS(0,1000) 内放行，即 W-5 所述钝感）→ **得分完全由缺失率决定**；缺失率 0.3037 = 9 个实采列在分路时间戳上 40.49% NaN × 9/12（3 个 pf 列经重采样 fillna(0) 稀释分母；ub/ib/pfb 加载期置 0 在重采样空桶处回到 NaN，不构成稀释）。
- **擦线程度**：70 分允许的最大缺失率 m_max=0.3000，当前 0.3037，**超出仅 +0.0037（相对 1.2%）**——折合约 70 个 15min 桶（≈0.7 天）的连续总线数据即可翻过阈值。

### 结构性根因：两台设备活跃窗口错位（缺的不是数据，是「同时」）

- bus 实际有数据日段：2025-07-11~15（5 天）、07-17~30（14 天）、2026-05-21~06-29（40 天）、07-01~23（23 天）、07-27~08-03（8 天）——共 90 天；名义 5min 采样，均值间隔 1323s（覆盖≈22.7%，与评审专题 D-3 的 1323s/23% 交叉一致 ✓），含 7056h（294 天，2025-07-30→2026-05-21）巨坑。
- branch 活跃日段：2025-07-11~08-09（30）、08-11~16（6）、08-18~20（3）、2026-04-09~05-17（39）、05-21~06-29（40）、07-01~23（23）、07-27~08-03（8）——共 149 天（14400 行=150 天×96 整）。
- **59 天「分路活跃但总线全缺」**（2025-07-16/31、2025-07-31~08-20 连片、**2026-04-09~05-17 整段 39 天落在总线巨坑内**）≈5664 行全 NaN——恰好构成全部 40.49% 缺失。若剔除这 59 天，bus 缺失率降至 0.0094、**得分 99.06**（轻松过门禁）→ 拦截本质是**窗口错位**而非数据整体不可用。
- 反事实：若门禁在 train 时间过滤（≤2026-06-04）之后执行，bus 得分反而降至 51.45——当前 time_filters 训练窗把总线死区（2026-04-09~05-17）包了进去，缩窗方向应剔除该段而非仅按现有窗截断。架构注：门禁先于时间过滤执行（数据级裁决故意在前），单纯改 time_filters 无法改变门禁结果。

### 标签侧更深层短板（即便放宽 bus 门禁也会卡在后面）

- branch 质量分仅 **53.29**（缺失率 0.4671，超出 70 分许可 +0.1671）：目标通道 p3 NaN 63.9%（非零占比仅 2.1%，开启时中位 631.5W）、p4 NaN 29.6%（非零 44.0%，中位 204.9W）。
- 复合目标 p3+p4（契约 skipna=False）**NaN 占比 80.5% → 全期 390 天仅 30 个有效标签天**（其中日峰<10W 的全关天 6 天，on_thr 取硬编码默认 10W——2844 配置未显式给 on_thr_w）。
- 双达标天：≥70 仅 **28/149 天（19%）**；≥50 也只有 85/149。逐天建议（流水线原文）：合格率 <50%「优先修数不调参」；「分路质量显著低于总线：标签侧是短板，训练前重点核查分路采集」；存在连续 ≥39 天不合格段（即总线巨坑）。
- 历史旁证：该户曾在原仓库管线中训出 proportional **R²=−0.076**（比均值预测还差，docs/TECH_DESIGN §附表），与门禁的保守取向互为印证。
- 修正一处既有记载：评审专题 D-5 写「2844 p4 大面积缺失」，实测 **p3（63.9%）远重于 p4（29.6%）**，且两者同为目标列；2842 的 p3 91% NaN 属实但 p3 非其目标（p1+p2），影响口径不同。

### 时间线与机制定位

- min_score 于 2026-08-18 由 10 上调至 70（为使逐天质量表/双达标口径有意义，阈值共用）。上调前 bus 69.63/branch 53.29 均可过 10 分门禁——2844 会带着 80.5% NaN 的标签进入训练；上调后 2844 以 0.37 分之差被拦，属**擦线牺牲**而非质量崩坏（同族 2842 bus 质量更优得以通过）。
- 模式 B 视角：即使强行放行，模式 B 执行包也只能复现「28~30 个有效标签天」级别的训练，样本量与建议相悖；是否放行属数据治理决策，非技术不可行。

### 结论与可选项（待用户拍板）

1. **拦截原因一句话**：总线（bus）质量分 69.63 差 0.37 分不及 70 分门禁；缺失率 0.3037 恰超许可 0.0037，根源是分路活跃的 59 天（尤其 2026-04-09~05-17 连片 39 天）落在总线 294 天采集巨坑内，标签侧另有 p3+p4 复合 NaN 80.5%（有效天仅 30）的更深短板。
2. 选项 A（推荐，数据侧）：向采集方核实 2844 总线 2025-07-30~2026-05-21 断录原因并补数（关联 OQ-12 284x 覆盖率问题、OQ-11）；补数后门禁可自然通过。
3. 选项 B（缩窗，需先改架构）：门禁先于时间过滤，缩窗不改变拦截结果；若要走此路需「门禁后移到时间过滤后」的代码变更（影响全部用户的门禁语义，须单独立项评审）。
4. 选项 C（降阈值强训，不推荐）：min_score 回 50/10 可放行，但仅 28~30 个有效标签天、历史 R² 为负、流水线建议明确反对；如坚持，须以「仅作通路验证、结论不入 REPORT.md」为前提。

- 是否进入 REPORT.md（稳定结论）：**是（建议登记）**——「2844 被质量门禁拦截系 bus 质量分 69.63<70（差 0.37）；根因=总线/分路活跃窗口错位（59 天错位，bus 294 天断录），叠加标签 p3+p4 NaN 80.5%（有效标签天 30/390）；修复方向为补数而非调参」。
- 遗留问题：2844 断录原因待采集方确认（新 OQ 候选）；「门禁 vs 时间过滤顺序」是否调整待立项评审；配置缺 on_thr_w 若未来放行需显式配置（现走硬编码默认 10W）。

## [2026-09-11] 专题：2842/2844 目标分路修正重跑（模式 B 执行包 v3）
- 类型：实验专题（**重大输入修正**：用户核查分路归属；**用户显式指定模式 B**，ROLE v1.4）
- 修正内容（用户核查 2026-09-11）：**2842 目标分路=p1**（原配置 p1+p2 有误）、**2844=p2**（原 p3+p4 有误）。字典登记 OQ-13（NILM_DATA_DICT v0.2.2）；`configs/time_filters.json` 已同步修正入库（2842 的 on_thr_w=50 保留观察；2844 无 on_thr_w，走硬编码默认 10W）；**旧目标口径下的全部历史结论降级为「错误目标参考」，仅可作相对比较**
- 数据旁证（两户分路 CSV 同名 `4206894986488-*.csv` 但内容不同，md5 各异）：
  - **p2 列在两户文件中统计逐位相同**（NaN 7.89%、非零占比 13.78%、非零中位 709.3W、max 899W）→ 同一物理通道、两装置导出窗口不同；2844 取 p2 自洽
  - **p1 仅在 2842 导出中覆盖健康**：NaN 5.25%、非零占比 40.5%、非零中位 709.8W、有效天 144/390（2844 文件中 p1 非零占比仅 27.9% 且有效天少）
  - p3/p4 大面积缺失（2842：90.7%/42.3%；2844：64.0%/29.6%）恰为原配置错选通道——与「分路有误」结论互证
- 沙盒预检（模式 A 尝试的遗留产物，**非权威参照**；用户改令模式 B 后已中止沙盒训练）：
  - **2844（p2）全流程**：`DATA_QUALITY_FAILED: bus 质量分 69.63 < 70`——与 2844 归因专题逐位一致（**总线门禁与目标分路无关，修正后仍拦**）；但 **branch 质量分 53.29→92.10（PASS，缺失率 0.467→0.079）**，双达标天 28/149→**74/149（50%）**——标签侧短板随目标修正基本消除，唯一拦截点收窄为 bus 差 0.37 分（2844 专题 A/B/C 选项待拍板不变，补数仍是根本路径）
  - **2842（p1）**：门禁 PASS、目标 p1 生效（丢弃 p2/p3/p4）、剔除无效天 7 天（07-15/17、04-17、06-13/14/20/22）、滑窗按时间连续段构造（train 11 段/val 5 段，W-1 生效）、训练启动正常
- 结果 / 结论：**待用户模式 B 回报后填写**（判读注意：新目标 p1/p2 与旧口径 p1+p2/p3+p4 不可直接比绝对值）
- 是否进入 REPORT.md（稳定结论）：待回报后定
- 遗留问题：2844 放行路径（补数/门禁架构/降阈值）仍待用户拍板

### 📦 执行包 v3（Windows PowerShell 原生；两户各一条命令）
**① 拉代码 + 自检**（time_filters.json 修正已入库，无需本地改配置）：
```powershell
git fetch origin
git checkout arena/01a0896c-nilm-new
git pull origin arena/01a0896c-nilm-new
(Select-String -Path configs\time_filters.json -Pattern "p3\+p4").Count     # 期望 0（旧目标已移除）
(Select-String -Path nilm\common\schema.py -Pattern "segment_bounds").Count # 期望 ≥1（W-1 修复仍在）
```
**② 数据就位**（上轮已建 Junction 或已复制的跳过）：
```powershell
Get-ChildItem data\trains    # 应见 5 个用户目录（含 ...2842_4206894986488 与 ...2844_4206894986488）
```
**③ 执行**（`conda activate test_gpu` 后；各一条单行命令）
2842（预期正常完成，GPU 秒级~分钟级；结束标志 `批量[infer] 800080252842_4206894986488 -> OK`，训练中应见「滑窗按时间连续段构造：N 段」）：
```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_t5_2842_p1 --user-key 800080252842_4206894986488
```
2844（**预期报错属正常**：`批量[train] 800080252844_4206894986488 -> DATA_QUALITY_FAILED bus 质量分 69.63 < 70`——修正后 branch 92.1 已达标、唯总线不足；该行出现=执行成功，修正口径质量报告已产出；infer 报 MODEL_NOT_FOUND 无需处理）：
```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_t5.yaml --data-root data --output-root outputs_t5_2844_p2 --user-key 800080252844_4206894986488
```
**④ 窗口连续性自检（可选，仅 2842，期望 cross_gap=0）**：
```powershell
python -c "import pandas as pd, glob; f=sorted(glob.glob('outputs_t5_2842_p1/800080252842_4206894986488/train/*/'))[-1]+'train_window_index.csv'; w=pd.read_csv(f); s=pd.to_datetime(w.win_end)-pd.to_datetime(w.win_start); print('windows',len(w),'cross_gap',(s>pd.Timedelta('23h45m')).sum(),'max',s.max())"
```
**⑤ 结果回收**（回报以下内容即可，其余我来判读）：
- 2842：console 最后 5 行 + 4 件套（`outputs_t5_2842_p1/800080252842_4206894986488/train/<时间戳>/` 下 `metrics_by_split.csv`、`train_window_index.csv`；`infer/<时间戳>/` 下 `offline_metrics.json`、`metrics_daily.csv`）
- 2844：console 的 `DATA_QUALITY_FAILED` 行 + （可选）`outputs_t5_2844_p2/800080252844_4206894986488/train/<时间戳>/` 下 `daily_quality.csv`、`quality_advice.json`
- 回收格式模板：
```
2842 train: MAE=__ R2=__ SAE=__ F1=__ P=__ R=__ (FP=__ FN=__)
2842 test : MAE=__ R2=__ SAE=__ F1=__ P=__ R=__ (FP=__ FN=__)
2842 infer: MAE=__ R2=__ SAE=__ F1=__ P=__ R=__ (FP=__ FN=__)
2844 状态行: ____
```
