# TUNING_GUIDE.md — 工商业负荷辨识调参运维人话版（详细版）

> **给谁看**：完全不懂算法的工程师、运维、交付、现场实施同学。只要会 `复制粘贴 PowerShell 命令`、`用 Excel 打开 CSV`、`看懂“开/关”`，就能按本手册把一个从未见过的新设备从 0 接到生产，并判断能不能上线。  
> **版本**：v1.2 详细版（2026-09-10，对齐 `REPORT.md v1.1 + NILM_DATA_DICT v0.2.10 + REPORT_TEST 专题集 + base_optimal lag5[5,1,2,3,4]`）+ 五大核心参数（`pbus_mode / on_thr_w / decision_thr_w / post_min_on / post_fill_short_off`）自动定参专题详解与 `901` 边界复盘｜协议：`BOOTSTRAP.md v2.2`｜分支：`arena/01a0896c-nilm-new`  
> **一句话定位**：用总线侧 5 分钟电表（电压电流有功功率因数）去猜分路侧 15 分钟电表（某一路 `p1/p2/p3...` 的有功）此刻是“开 1”还是“关 0”、功率是多少瓦。模型只看过去 24 小时（96 点）的功率走势，不看高频谐波。

**怎么用本手册**：按顺序读，`Step 0-7` 是必做流水线（`Step1` 拆 `1a探数定阈(5min)+1b写配置`，仍计8步）；`§4-6` 是解释为什么；`§7-10` 是上线门禁与排障；`附录` 是可直接复制的模板。遇到报错先查 `§10 战史`，再查 `§11 FAQ`。

---

## 目录

1. [三个前置灵魂问题](#1-三个前置灵魂问题)
2. [数据：最少要什么、放哪、叫什么、要不要洗](#2-数据最少要什么放哪叫什么要不要洗)
3. [从 0 到上线的 8 步（粘贴即跑，含输出怎么验）](#3-从-0-到上线的-8-步粘贴即跑含输出怎么验)
4. [两个阈值与五大参数自动定参专题详解（pbus_mode / on_thr_w / decision_thr_w / post_min_on / post_fill_short_off）](#4-两个阈值与五大参数自动定参专题详解pbus_mode--on_thr_w--decision_thr_w--post_min_on--post_fill_short_off)
5. [模型怎么选（4 选 1 自动，为什么不能单押）](#5-模型怎么选4-选-1-自动为什么不能单押)
6. [推理后看哪两个表（能力 vs 交付）](#6-推理后看哪两个表能力-vs-交付)
7. [生产 7 项放行（缺一不可，含不合格长什么样）](#7-生产-7-项放行缺一不可含不合格长什么样)
8. [部署架构与日常运维（批量、断点、月历）](#8-部署架构与日常运维批量断点月历)
9. [通用模型：跨用户泛化验证与调优流程（pooled）](#9-通用模型跨用户泛化验证与调优流程pooled)
10. [战史：我们犯过的 8 个错（别再踩）](#10-战史我们犯过的-8-个错别再踩)
11. [常见问题 18 问](#11-常见问题-18-问)
12. [附录：模板与速查卡](#12-附录模板与速查卡)

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

> 以新用户 `900080270900_4200000000001` 猜 `p2`（`Step1a` 已定 `on_thr 10W`）、初始 `decision 30W`，用最稳的 `base_optimal.yaml`（`lags[5,1,2,3,4] + 4模型择优`，已合入 `default.yaml` 全局，对 4 户零污染已验）为例。Windows 用 PowerShell，Linux/macOS 把 `Select-String` 换 `grep`。
> **懒人一键（等价 Step 2→3→5→4，含阈值双链）：** `python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219` 或 `.\scripts\one_click_733.ps1`（缺 `time_filters.json` 条目自动补 `p1/10/30/day_gate`，`--help` 看 `--stage/--force/--skip-*`；`*` 通配已用 `threshold_sweep v2026-09-19 glob` 兼容 PowerShell `OSError`）——单行即走完下述 4 步。

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

### Step 1a 探数定 `on_thr_w`（5分钟，冻结答案，Step1前必做）

> **为什么单列一步**：`on_thr_w` 是**改答案**（`target→target_state`），改后需重训重审计；而 `decision` 是改判卷可离线扫。`§1②` 已讲三档，新户常默认抄 `10W` 致高功率路 `P 0.44 / SAE 0.37` 返工（`OQ-13`）。本步 `5分钟粘贴即跑` 把 `铭牌+直方图+branch_sessions` 固化为可审计动作，产 `on_thr` 再进 `Step1b`。

```powershell
# 1) 分路直方图与分位数（看双峰：0附近尖峰 vs 开机峰；5min）
python -c "import pandas as pd; p='data/trains/900080270900_4200000000001/4200000000001-250710-260630.csv'; s=pd.read_csv(p)['p2'].dropna(); print(s.describe(percentiles=[.5,.9,.95,.99])); print('NaN',s.isna().mean()); print('非零中位',s[s>10].median(),'5%分位',s[s>10].quantile(0.05)); s.hist(bins=80, range=(0,1000))"
# 2) 已有一次训练后看开机段最小/均值（与直方图互证）
# outputs/900080270900_4200000000001/train/*/branch_sessions.csv  看 p2 开机段 min/mean 功率
# 3) 台账铭牌最小档对照
```

**判据（按 §1② 三档速查）：**

| 场景 | `on_thr` | 何时用 | 本项目锚 |
|---|---|---|---|
| 低功率/有无电 | **10W** | 待机 `0-10W`，开机 `≈700W` 双峰清晰 | `2844 p2 / 778 p2 / 800080270815 p1` |
| 高功率/干不干活 | **50W** | 待机纹波 `20-40W` | `2842 p1` |
| 复合和 `p1+p2` / `p1+…p6` | **60W / 10×N** | 和的最小档 | `789 p1+p2 60W`；`p1+…p6` 先看和的直方图 |

* **取法**：`待机带上沿+余量` 且 `≤ 最小开机功率×0.3`。例 `非零中位709W / 待机0-8W →10W`；`待机0-35W→50W`。
* **自动模式（`"on_thr_w": "auto"`）**：新户不确定铭牌/待机幅值时，可配 `"auto"`（`auto_run_steps2to5.py` 新户默认），训练前自动在对数功率域做 Otsu 双峰寻谷并输出**黄色高亮日志**（双峰清晰）或**红色告警日志**（多档辅机/低分离度，提示人工按台账复核），显式数值配置 100% 优先。
* **冻结**：定后即入 `Step1b`，后续 `Step4` 只扫 `decision`，勿为追 `F1` 再动 `on_thr`。

### Step 1b 写配置（唯一可信源 `configs/time_filters.json`，优先级 `user_key > _default`）

```json
"900080270900_4200000000001": {
  "target_col": "p2",
  "on_thr_w": "auto",
  "decision_thr_w": "auto",
  "pbus_mode": "auto",
  "post_min_on": "auto",
  "post_fill_short_off": "auto",
  "quality": {"day_gate": true, "min_on_day_ratio": 0.2},
  "train": {"include": [["2025-07-10","2026-06-30"]]},
  "infer": {"include": [["2026-07-01","2026-07-31"]]}
}
```

* `target_col` 必填；`on_thr_w` 与 `decision_thr_w` 均遵循**完全向后兼容、显式优先、全程留痕**：
  * **显式数值 100% 优先**：已确认数值（如 `"on_thr_w": 50.0, "decision_thr_w": 30.0`）原样生效，绝不被自动改写；`on_thr_w` 未写时保持历史硬编码默认 `10.0` 向后兼容；
  * **`"decision_thr_w": "auto"`（默认，双阶段收口保证 `train` 与 `infer` 100% 一致）**：
    1. 训练阶段在选定推理模型的 `val` 验证集（零测试集泄漏）执行带物理护栏（`val_recall ≥ 0.95`、`≤ 0.80 × P10(y_on)` 兼容多路求和局部启停、最小增益门 `0.003` + 全关日 FP 优先；`pbus_mode="scaled"` 另设高分平台期抗漂移裕度）的判决链寻优，全量候选表写入 `train/meta.json`；
    2. 一键脚本 `auto_run_steps2to5.py` 完成 Step 5 推理后，若 `infer` 含分路真值（离线验收场景），自动执行 `(val + infer)` 双链联合寻优（统一共享训练集物理上限 `train_upper_guard_w = 0.80 × P10(y_train_on)`；双链同峰/宽交集时联合稳健选点，**跨季双链异峰时自动解除历史旧季节 `val` 的窄缝否决、遵循 Step 4 铁律按当期 `infer` 链定阈**）并**零重训原子同步刷新** `train` 与 `infer` 双侧全部状态列、日表、策略表与 `meta.json`（保留 `decision_thr_w_val_initial` 留痕，不触碰 `test` 折），确保双侧 `decision_thr_w` 完全一致且通过 `audit_user_run.py` 全绿门禁；也可随时用 `--stage sync-thr [--decision-thr 400]` 毫秒级零重训同步，或加 `--save-config` 自动备份并写回 `configs/time_filters.json`；常规微调输出**黄色提示日志**，跨季双链异峰、大跨度跨期偏移、显式覆盖或召回退化时输出**红色/黄色高亮日志**（也可配 `"on_thr"` 显式锁定沿用 `on_thr_w`）。
* `pbus_mode` 默认 `"auto"`（推荐）：采用**零泄漏三步验证闭环**——① 不依赖易误伤的静态千瓦阈值盲跳，所有用户默认统一先跑抗漂移基态 `"raw"`（`pbus` 未缩放 + 物理零点锚定，绝大多数用户如 `2842/2844/856/870/778` 一遍即完稳留 `"raw"`）；② 仅当 `train + val` 检测到序列模型真实欠拟合信号（`R²` 与 `val MAE` 双双显著劣于 `ridge/history_profile` 基线，或预测坍缩，如 `733`）时，才启动候选 `"scaled"` 重训；③ 候选 `"scaled"` 在 `val` 验证集（严禁看 `test`）与刚跑完的 `"raw"` 正面对决，**必须同时满足「`val MAE` 实质改善（相对下降 ≥5%）」与「`val F1` 不退化（跌幅 ≤0.015）」才打印红色高亮日志切换为 `"scaled"`**（若像 `778` 出现关机虚报激增、`val F1` 下跌超限，则自动拒绝切换并保留抗漂移的 `"raw"`）；推理自动继承训练产物模式。也可手工指定 `"raw"` 或 `"scaled"` 锁死（切换后须加 `--force` 重训重推）。
* `post_min_on` 与 `post_fill_short_off` 支持 `"auto"`（`auto_run_steps2to5.py` 新用户默认 `"auto"`，未写时保持历史硬编码默认 `1/3` 向后兼容，显式整数 `0..96` 100% 优先且二者可独立解耦配置）：配置 `"auto"` 时执行**零泄漏两阶段自动确定**——① **阶段一（训练连续时间块真值物理定界）**：在非测试集连续 `15min` 时间块真值上统计开态游程与日内短关态游程，用真值自回放约束（`GT_On_Loss(m) ≤ 0.5%` 锁死 $M_{\max}$；`GT_Bridge_Ratio(k) ≤ 1.0%` 及日内真停机桥接率锁死 $K_{\max}$）为间歇循环/午休停机负荷收窄 $K_{\max}$、为连续长班次工业机组放开 $M_{\max}$；② **阶段二（验证集按模型协同校准 + 奥卡姆正则）**：在安全盒 $\{ \le M_{\max} \} \times \{ \le K_{\max} \}$ 内与 `decision_thr_w` 协同寻优，施加召回护栏（`≥0.95`）、**全关日防桥接护栏**（`off_day_fp(c,m,k) ≤ off_day_fp(c,m,0)`）与惰性保持门（无实质增益保持锚点 `(1, min(3, K_max))`），推理阶段按模型自动继承并支持双链收口同步（`--stage sync-thr [--min-on M] [--fill-off K]`）。
* `day_gate true` 全局默认（优先级 `day > train_range > full`），`min_on_day_ratio 0.2` 防“无可学开机模式”（2844旧池 31%→71%已验）。
* `train/include` 为闭区间，`YYYY-MM-DD` 自动扩到 00:00-23:59:59。

### Step 1c 数据集切分：什么时候用 `auto`，如何读懂天数和指标

在一键训练命令中显式启用自动切分：

```powershell
python scripts/auto_run_steps2to5.py --user-key 900080270900_4200000000001 --split-strategy auto --stage train --force
```

也可在该用户的 `configs/time_filters.json` 条目中配置 `"split_strategy": "auto"`；显式 CLI 参数会通过本次 overlay 覆盖配置。数据集切分以**自然日**为单位，日内 15 分钟点不会随机拆散。切分只决定哪些日期用于模型拟合（train）、早停/自动定参（val）和最终评估（test）；因此它也会间接改变 transformer 早停结果、`pbus_mode="auto"` 的验证集胜负、以及 `decision_thr_w` / 后处理参数的自动校准结果。

#### 可选策略与默认比例

| `split_strategy` | 划分方式 | 适用/注意 |
|---|---|---|
| `stratified_day` | 按星期几分组，每组内按时间顺序按比例分配 | 历史默认；不同星期组分别取整，汇总天数不一定精确等于整体比例，也不会保证每折是单一连续时间块 |
| `time` | 按日期整体顺序切成 train→val→test | 简单时序基线；不主动照顾开/关覆盖与每日质量 |
| `auto` | 结合每日质量、连续块/天然断口、目标日开关状态搜索 val/test 连续块，剩余日期进入 train | 推荐用于希望验证/测试保持时间连续、且需要质量与工况约束的场景；不保证永远优于其他策略 |
| `stratified_by_state` | 按日开/关状态分组，再在状态子序列内按时间分配 | 有足够开日、关日时用于改善工况覆盖；仍受状态稀缺与日历聚集限制 |

`split_ratios` 是**目标比例**，缺省为 `[0.6, 0.2, 0.2]`，不是实际天数的硬承诺。日数取整、弹性配额、连续块约束、质量/工况覆盖以及各策略的分组方式都会使实际比例偏离目标。特别是：

* `stratified_day` 是“每个星期几的子组分别取整后合并”；不能用整体 `N×比例` 直接推断汇总日数。
* `auto` 要搜索符合约束的连续 `val/test` 日期块。遇到开/关日集中在特定时期、天然缺数断口或锚定日期时，最优可行配额可能与目标比例不同；剩余日期分给 train。
* **看到 40/11/11、23/6/6 等分配，先核对本次生效的 `split_ratios` 和切分诊断，不要仅凭 `auto` 名称认定是 6:2:2。** CLI 未显式传比例时，仍可能由用户配置或 overlay 覆盖；应检查本次训练目录的 `meta.json`、split 决策报告和实际 overlay 配置。日志中的 `train=3827, val=1054, test=1054` 是有效点/样本口径，不等于天数；以 `[AUTO-SPLIT] train=...d | val=...d | test=...d` 或诊断中的 `quotas_days` 读天数。

#### 如何判断“自动切分后更差”是真退化还是评估集换了

1. **先核对样本池**：看 `day_gate.json` / 质量报告里的双达标天数、剔除日期、开机日/全关日数。门禁后有效天数才是切分分母；全关日为 0 时，任何策略都不可能提供“全关日覆盖”。日内仍可能有夜间关态点，所以“没有全关日”不等于“没有负样本”。
2. **核对日期边界**：以每日 CSV 的 `split,date` 列比较两次运行。不同策略可能把同一天放进不同折；两份 test 的均值若来自不同日期，不能视为同一测试集上的模型优劣。
3. **核对自动参数**：若 `on_thr_w`、`decision_thr_w`、`post_min_on`、`post_fill_short_off` 或 `pbus_mode` 也设为 `auto`，不同 val 日期可能选出不同参数/模式。阈值和后处理会直接改变 F1、precision、recall；`pbus_mode` 与早停会改变模型预测。此时不是只比较了切分策略。
4. **做配对比较**：要估算切分本身的净影响，固定随机种子、目标阈值、后处理参数和 `pbus_mode`，只改变 `split_strategy`；按相同日期、相同模型比较每日 MAE/RMSE/F1。对同一测试集之外的日期，不要混算平均值。自动切分显示较低分也可能只是它选中了更难、时间更靠后的连续评估段；这可能是更严格、更贴近未来的测试，而不必然说明模型整体能力下降。

**752 用户案例（2026-11-26 日志）**：门禁后为 **35 个双达标日，35 开 / 0 关**；`auto` 报告 train/val/test=`23/6/6`，非自动 `stratified_day` 的点数对应约 `21/7/7`。因此本例没有可供折间均衡的全关日，差异主要是日期区块与折大小变化。两次最终的 `state_thr` 约 `16.33/16.312`、`decision_thr` 约 `18.7/18.84`、`post_min_on` 分别为 `1/2`，`pbus_mode` 都是 `raw`；仍非完全同参数对照。控制台 test 汇总中 transformer MAE/F1 是自动 `5.858/0.9909`、非自动 `5.621/0.9974`，只是小幅差异；ridge MAE 分别 `9.661/5.943`，相对差距较大。每日配对后有升有降，且测试日期并不完全相同，故不能仅凭这两组汇总认定 `auto` 造成普遍退化。

**排查清单**：保留每次运行的 overlay JSON、`meta.json`、`day_gate.json`、每日指标 CSV；先确认有效日期与实际比例，再比较重叠测试日期。若想验证上线风险，应同时报告连续后期 test，而不能只选择容易的散布日期来抬高平均分。

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
# * 通配由脚本内部自动展开（v2026-09-19已兼容 PowerShell/bash；建议加引号防止 bash 提前展开）
python scripts/threshold_sweep.py --csv "outputs/900080270900_4200000000001/train/*/predictions/train_predictions.csv" --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500 --min-on 1 --fill-off 3
# infer 链（inference_result.csv）
python scripts/threshold_sweep.py --csv "outputs/900080270900_4200000000001/infer/*/predictions/inference_result.csv" --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500
# 若仍报 OSError: Invalid argument: '.../*/predictions/...'：PowerShell 未展开通配，请升级 scripts/threshold_sweep.py（已支持 *?[] 通配取最新）
# 或用显式路径：
# $csv=(Get-ChildItem outputs/800080270733_4206673297219/train/*/predictions/train_predictions.csv | Sort-Object | Select-Object -Last 1).FullName; python scripts/threshold_sweep.py --csv $csv --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500 --min-on 1 --fill-off 3
```

**怎么选（以 2844 p2 为例，人话）：**
* 扫完看 `F1 - R - off_fp` 曲线：`10→150 F1 0.8904→0.9292` 单调升，`off_fp 140→56 (-60%)`，`R≥0.993` 全程不破 `0.95`。
* 峰在 500（`F1 0.9585`）但贴 `R 0.95` 且边界贴真值密集带 `[500,700) 205点`，季节一漂就破。稳健点 `400`（`F1 0.9548 让0.0037，R余量0.03，fn 21 vs 41减半，off 18`）更抗漂。
* **结论**：`R≥0.95` 且 `test链不净降` 的最大 `F1` 档即稳健点。定后改 `time_filters.json` 该户 `decision_thr_w` 并 `git commit`，下月自动用新阈（月扫 SOP）。
* **铁律（2026-11-18 733 实证）**：`decision_thr_w` 必须以 **infer 链**扫出的最优为准，**禁止套用训练 test 链最优值**。训练期（4–6 月）与推理期（8 月）`pbus` 基线漂移时，两链最优可差数倍（733：test 最优 150、infer 最优 400；用 150 推理 F1 0.967，用 400 回 0.985 与旧版持平）。双链同峰（如 815 的 50）可直接落盘；双链异峰时以 infer 链为准，test 链仅作能力对照。

**实战解读：800080270815 双链（test 670点7天 / infer 1423点16天，2026-09-20）——逐行读日志、五步定阈（本例最优 50W）：**

> 日志来源：`python scripts/auto_run_steps2to5.py --user-key 800080270815_4206668913762` 自动触发的两段 `threshold_sweep`（一键 `Step4 test链 + infer链`）。

```text
[RUN] ... threshold_sweep.py --csv outputs/800080270815_.../train/.../train_predictions.csv --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500 --min-on 1 --fill-off 3
split 过滤: test（4211 → 670 行）
文件: outputs/800080270815_.../train/.../train_predictions.csv
自描述 decision_thr_w: [30.0] | on_thr_w: [10.0]
数据: n=670 点（剔除 NaN 0 行），7 天（全关天 2：2026-07-11, 2026-07-12）
判决链复现校验（thr=30, min_on=1, fill_off=3）: pred_state 逐位一致 ✓
== 阈值扫描（判决链 = decision_thr_w + min_on=1 + fill_off=3）==
  thr  tp  fp  fn  tn  precision  recall     f1  off_day_fp  on_day_fp
 10.0 241 255   0 174     0.4859  1.0000 0.6540         137        118
 30.0 241 110   0 319     0.6866  1.0000 0.8142          89         21
 50.0 241  73   0 356     0.7675  1.0000 0.8685          63         10
100.0 138   0 103 429     1.0000  0.5726 0.7282           0          0
150.0   0   0 241 429     0.0000  0.0000 0.0000           0          0
...500 同 150
== fp 幅值分布（ts=0 且 pred≥10W，raw 判态）==
      band  count  pct
  [10, 20)     94 42.7
  [20, 30)     16  7.3
  [30, 50)     41 18.6
 [50, 100)     69 31.4
```

```text
[RUN] ... threshold_sweep.py --csv outputs/800080270815_.../infer/.../inference_result.csv --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500
数据: n=1423 点，16 天（全关天 3：2026-07-19, 2026-08-01, 2026-08-02）
  thr  tp  fp  fn  tn  precision  recall     f1  off_day_fp  on_day_fp
 10.0 499 612   0 312     0.4491  1.0000 0.6199         225        387
 30.0 499 227   0 697     0.6873  1.0000 0.8147         125        102
 50.0 498 130   1 794     0.7930  0.9980 0.8838          54         76
100.0 121   1 378 923     0.9918  0.2425 0.3897           0          1
150.0   0   0 499 924     0.0000  0.0000 0.0000           0          0
...500 同 150
      band  count  pct
  [10, 20)    236 47.0
  [20, 30)     41  8.2
  [30, 50)    104 20.7
 [50, 100)    120 23.9
```

*人话翻译：*
* `n=670 7天 / 1423 16天` 中各有 `2/3` 全关天，用于算 `off_day_fp`（全关天仍报开，最招投诉）；`670@15min≈7天` 即 `test` 折大小。
* `自描述 [30.0]` 是库里现阈（待优化），`on_thr 10` 是真值线；`复现校验 ✓` 说明 `pred + 30W + 1/3 游程` 重算的 `pred_state` 与产物逐位一致，可信。
* `precision=TP/(TP+FP)` 报开准不准，`recall=TP/(TP+FN)` 该开漏多少，`F1` 综合；`off/on_day_fp` 分全关/开机天的虚报。
* `幅值分布` 只统计 `真关(ts=0)且raw pred≥10W` 的裸虚报分桶，预判 `30→50` 能砍掉哪段。

*五步定阈（本例 50W 最优，30W 可回退，≥100W 禁用）：*
1. **定天花板**：`100→150` 时 `TP 241→0 / 499→0，F1 0.72/0.38→0` 瞬间归零 → 模型值域不过100W（`[50,100) 69/120个FP，[100,200) 0/1`），可用区间仅 `10-100`，`150-500` 五档直接不可用；
2. **守召回底线**：`recall<0.95` 即漏报（`100W` 时 `0.57/0.24，FN 103/378` 不达标，排除）；
3. **在 `recall≥0.95` 中取 `F1` 最高**：`10/30/50` 均 `1.0/0.998`，`50W F1 0.8685/0.8838 最高`；
4. **同 `F1` 比全关虚报**：`30→50` 时 `FP 110→73 /227→130，off 89→63(-29%)/125→54(-57%)，precision 0.68→0.76/0.79`，`FN 0` 零代价，属帕累托更优；
5. **幅度复核**：`42.7%FP在[10,20) +7.3%[20,30)=50%` 低幅幻觉，`30→50` 砍 `[30,50)18.6%` 后仍留 `[50,100)31.4%` 难切部分，再往 `100` 切必误伤真开点。

| thr | test F1 | infer F1 | FP总量 | off+on | recall | 结论 |
|---|---|---|---|---|---|---|
| 10 |0.654|0.619|255|612|1.0|虚报过半|
| **30 现状** |0.814|0.814|110|227|1.0|可用但非最优|
| **50 推荐** |**0.868**|**0.883**|**73**|**130**|**1.0/0.998**|**F1+0.05/+0.06，FP-33%/-42%零漏报**|
| 100 |0.728|0.389|0|1|0.57/0.24|漏 `103/378` 不可用|

*两链同峰 `50` 且同向增益，无跨期漂移，可放心落盘 `time_filters.json: "decision_thr_w": 50.0`（改判卷线不需重训，下次 `infer` 自动用 50W，重跑 `audit` 见 `metrics_daily_chain @50W`）。`50W` 仅对本户有效，别抄他户；按月历每月1号双链重扫，漂移`>20W`即更新。

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

> **⚠️ 期望值是快照，不是抄作业（I8 可省，必读）**  
> ` --expect-n / --expect-confusion tp,fp,fn,tn / --expect-off-day-fp ` 是 **I8 回归门禁**，**可省略**（不传即跳过该门）。它用于把**该用户该次**的真实结果钉住，下次静默漂移立刻 `✗`。**新用户第1次切勿抄模板** `1036,77,21,1495/18`（仅对 `2844/9000` 示例有效），否则必 `✗` 如 `800080270856 580,19,9,2021/0`。  
> **正确两步：**
> ```powershell
> # 第1次：不带期望，先看真实值
> python scripts/audit_user_run.py --run-root outputs/800080270856_4206810972139
> # → 记下控制台 “· 总混淆: tp=580 fp=19 fn=9 tn=2021；全关日 fp=0” 与 “行数 2629”
> # 第2次：固化为门禁（以后漂移即告警）
> python scripts/audit_user_run.py --run-root outputs/800080270856_4206810972139 --expect-n 2629 --expect-confusion 580,19,9,2021 --expect-off-day-fp 0 --baseline-run outputs/800080270856_4206810972139
> ```
> **删不删 I8？不删。** 删了失去漂移告警；留着且按需传参，`T1-T7/I1-I7/I9-I10` 已覆盖核心正确性，`I8` 仅为可选的“钉住”层。本次 `800080270856` 的 `2✗` 即典型抄模板所致，改实测值即 `全部通过 ✅`，生产可放行。


### Step 7 上线判断（见 §7，7项全绿）

### Step 8 全量批量（上线后）

```powershell
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs
# 扫 data/trains|infers 全目录，outputs/batch/<ts>/batch_status.csv 按 user_id 出状态，失败隔离，_DONE 断点续跑，--force 刷写新时间戳
```

---

## 4. 两个阈值与五大参数自动定参专题详解（pbus_mode / on_thr_w / decision_thr_w / post_min_on / post_fill_short_off）

### 4.1 两个阈值到底是什么（`on_thr_w` vs `decision_thr_w`，人话+图）

```
真值侧（考卷答案）              预测侧（判卷标准 + 游程整形）
target --on_thr--> target_state   pred --decision_thr--> 裸0/1 --min_on(去毛刺)--> --fill_off(填短坑)--> 最终 pred_state
  物理开机边界（冻结）               按模型底噪与跨月基荷校准，支持零重训离线重扫/双链同步
```

* **`on_thr_w`（真值阈）**：什么算真的开。`target ≥ on_thr_w` 才算真开 `1`。**改它 = 改考卷标准答案**，会影响开机段统计、分层切分、游程物理上界与评估基准，定准后必须冻结。按电器最小工作档定：`10W` 是“有没有电/单台洁净待机”，`50W` 是“是不是在干活”（`2842 p1` 稳态 `700W` 取 `50W`），多路合并（如 `p1+p2+p3+p4`）需越过总待机功率带且低于单台最小工作档。
* **`decision_thr_w`（预测阈）**：模型猜多少瓦才报开。先按 `pred ≥ decision_thr_w` 得初判 `0/1`，再依次经 **`enforce_min_on(post_min_on)`（抹除不足 `m` 个点的短开脉冲）** 与 **`fill_short_off(post_fill_short_off)`（回填两侧皆真开且 `≤ k` 个点的短关凹坑）** 产出最终交付列 `pred_state`。**改它与游程参数 = 改判卷标准**，不改模型权重，可毫秒级零重训重算上千次。
* **为什么必须分开**：`2844` 真开机 `500~1000W`、关态底噪幻觉 `<500W`，`10W` 真值线与 `400W` 预测线差 `40` 倍。能力口径（`metrics_daily.csv`，`pred ≥ on_thr_w` 裸判）看模型本身准不准；交付口径（`metrics_daily_chain.csv`，`pred_state`）看交付给业务方的 `0/1` 准不准。两口径混在一起就既无法客观选模型，也无法消除回归底噪虚报。

---

### 4.2 五大参数自动定参全景架构与执行时序耦合链

系统支持对 **`on_thr_w`、`pbus_mode`、`decision_thr_w`、`post_min_on`、`post_fill_short_off`** 五个核心参数独立或全量配置 `"auto"`（**铁律：显式数值配置 100% 优先于 `"auto"`，未写时保持历史硬编码默认值 `10.0 / auto / 10.0 / 1 / 3` 向后兼容，全链路决策写入 `meta.json` 留痕**）。

五个参数并非孤立运行，而是严格遵循**“真值定界 → 特征缩放仲裁 → 零泄漏选校准模型 → 三维判决链校准 → 推理双链收口”**的单向因果时序链：

```
[Step A: 真值物理定阈]  on_thr_w="auto"
   ├─ 训练时间窗预估（排除 splits.test.include 零泄漏）
   └─ invalid_data_days + quality.day_gate 剔除缺测坏天后自动重刷 [AUTO-ON-THR-REFRESH]
         │
         ├──► 锁死真值标签 target_state = (target >= on_thr_w)
         ├──► 锁死后续护栏上界基准 P10(y_train_on) -> upper_guard_w = 0.80 × P10(y_train_on)
         ▼
[Step B: 游程阶段一定界]  post_min_on / post_fill_short_off = "auto"
   └─ 在严格连续 15min 真值块上自回放，锁死物理安全盒 (M_max, K_max)
         │   · enforce_min_on 真开折损率 ≤ 0.5%  --> M_max
         │   · fill_short_off 真关膨胀率 ≤ 1.0% 且短停机桥接占比 ≤ 10% --> K_max
         ▼
[Step C: 母线特征缩放仲裁]  pbus_mode="auto"
   ├─ 第1轮：默认先跑抗漂移基态 pbus_mode="raw"（pbus 原值 + 物理零点锚定）
   ├─ 第2轮：仅当序列模型在 train+val 出现真实欠拟合/坍缩时，启动候选 pbus_mode="scaled" 重训
   └─ 第3轮：验证集双向门禁（MAE 增益 ≥5% 或 R²+RMSE 重度欠拟合救援，且 F1 不退化 ≤0.015）决出胜出模式
         │
         ▼
[Step D: 零泄漏选校准模型 + 三维协同校准]  decision_thr_w + (post_min_on, post_fill_short_off) = "auto"
   ├─ 目标模型：显式 infer_model 优先，否则按 val 集综合投票选 best_val（严禁偷看 test）
   └─ 在 c ∈ [0.6·on_thr_w, upper_guard_w] × m ∈ [1..M_max] × k ∈ [0..K_max] 内联合寻优：
         · 召回硬底线 recall ≥ 0.95 + 反全开坍缩特异性护栏（剔除 Spec < 0.25）
         · 全关日防桥接硬约束 off_day_fp(c,m,k) ≤ off_day_fp(c,m,0) + 奥卡姆惰性锚点
         │
         ▼
[Step E: 推理继承 + (val + infer) 双链联合收口]  auto_run_steps2to5.py
   ├─ 推理默认继承 train/meta.json 中对应模型的阈值、游程与 pbus_mode
   └─ 若 infer 含分路真值：执行 (val + infer) 三维联合复核（兼容跨季异峰按当期 infer 定阈），
      毫秒级零重训原子重写 train + infer 双侧 CSV/JSON，确保 audit_user_run.py 100% 全绿对齐
```

> **核心工程直觉**：`on_thr_w` 处于整条链的最上游——它一旦偏低（如卡在待机底噪 `15W`），会把 `upper_guard_w` 锁死在 `0.80×60W=48~96W`，导致下游 `decision_thr_w` 跨不过回归底噪；反之它一旦偏高（如越过单机轻载切到 `300W`），又会把轻载真开点翻转为“真关态”，抬高 `upper_guard_w` 并倒逼 `decision_thr_w` 冲高。理解这条耦合链，就能一眼看透所有自动定参日志。

---

### 4.3 参数 ①：`on_thr_w="auto"`（分路真值物理开机阈值自动定参）

* **代码入口**：`nilm/pipeline/auto_thresholds.py::resolve_auto_on_thr_w`、`nilm/pipeline/user_task.py`（训练初算 + `day_gate` 后重刷）。
* **人话目标**：自动在分路功率直方图的“关机/待机底噪堆”与“真正干活功率堆”之间找到干净谷底，把阈值定在**待机上沿加一点安全余量**的位置。
* **四步核心逻辑**：
  1. **零泄漏样本池与坏天剔除二次重刷**：仅统计 `train` 时间窗且主动剔除 `splits.test.include` 锚定区间；当 `invalid_data_days` 或 `quality.day_gate` 剔除了通讯中断/全零缺测坏天后，自动在纯净合格天上二次重算（输出黄色 `[AUTO-ON-THR-REFRESH]`），隔绝假零值坏天对首谷的污染，并同步刷新 `branch_sessions.csv` 与质量报告。
  2. **一级对数域 Otsu 双峰寻谷**：对非负分路功率做 $\log(1+P)$ 变换（兼顾几瓦待机与数千瓦运行跨越 3 个数量级的尺度），最大化类间方差比 $\eta \in [0, 1]$ 切出谷底 $y_{\text{otsu}}$：
     * 初值：`raw_thr = max(min_floor, 1.40 × P99(y_off), P99(y_off) + 0.01 × P50(y_on))`；
     * 走廊夹逼与刻度圆整：常规单设备夹逼在 `[1.15 × P99(y_off), 0.30 × P05(y_on)]`；当多路合并出现“多台同时待机接近单台轻载 30%”（`1.15 × P99(y_off) > 0.30 × P05(y_on)` 且 `< 0.60 × P05(y_on)`）时自动放宽上界至 `min(max(0.50 × P05, y_otsu), 0.70 × P05)` 防止走廊倒挂切穿待机带，再圆整到 `10/15/20/50/100/200...` 等标准刻度得 `thr_1`。
  3. **多峰寄生待机跨越仲裁 + 母线同向阶跃反向否决制**：
     * 工商业多联机/多路合并常呈三模态甚至四模态：`0W（拉闸断电）` + `60~150W（电控板/曲轴加热器寄生待机）` + `450~2500W（单台/多台压缩机制冷）`。一级 Otsu 会先切在 `0W` 与 `60W` 之间的 `15W`；
     * 算法自动对 `y_on` 执行二级 Otsu（若次级低簇 `c1` 内部仍含“待机 + 单台轻载”双峰且 `sub1_eta ≥ 0.76`，再做三级下探剥离单台轻载），提取候选主工作阈 `thr_2`；
     * **怎么区分 `60~150W` 是“不算开的寄生待机”还是“必须算开的真低速档（如双速水泵低频档）”？——物理守恒反向否决制**：
       计算分路跳变时刻母线**同向有效阶跃** `dir_dbus = sign(Δy) × Δpbus` 相对平稳背景波动 `P90(|Δpbus|)` 的信噪比 `snr_sub` 与同号一致率 `sub_sign_rate`：
       - **一票否决（保护真多档设备）**：若 `snr_sub ≥ 1.2` 且 `sub_sign_rate ≥ 0.65` 且主簇 `snr_thr2 ≥ 1.2`，证明该低功率档在总母线上有清晰同向阶跃，属可辨识真低速档，**严禁跨越，保留低阈 `thr_1`**（记录 `IDENTIFIABLE_MULTIGEAR_KEPT_LOW_THR`）；
       - **放行跨越（过滤寄生待机）**：只要未触发真多档否决，无论是母线清晰印证待机被淹没（`snr_sub < 1.0` 且 `snr_thr2 ≥ 1.0`），还是大厂总母线背景波动过大将整条分路淹没（`TARGET_EDGE_BURIED_IN_BUS`，`snr_thr2 < 1.0`）但分路自身呈强三峰且主簇能量绝对主导（`sub_eta ≥ 0.78`、`P50(c2) ≥ 4.5 × P50(c1)`、`P90(c1) ≤ 0.25 × P50(c2)`、`c2 能量占比 ≥ 90%`），均自动晋升至 `thr_2`（记录 `stage="auto_trimodal_standby_promoted"`）。
  4. **分级告警机制**：分离度高且谷底干净时打**亮黄日志 `[AUTO-ON-THR]`**；若全常开/全常关、低分离度（`η < 0.55`）、谷底稠密或检出多档位歧义，打**亮红日志 `[AUTO-ON-THR-WARN]`** 提示人工结合铭牌复核。

---

### 4.4 参数 ②：`pbus_mode="auto"`（母线功率特征缩放模式三步验证闭环）

* **代码入口**：`nilm/pipeline/user_task.py`（`_resolve_pbus_mode_initial`、`_should_fallback_raw_to_scaled`、`_accept_scaled_candidate`）。
* **两种物理模式的本质取舍**：
  * **`"raw"`（默认抗漂移基态）**：母线有功相关特征保持原始物理量纲（不进 `z-score`），`proportional` 按物理总功率非负分摊。优点是**天然锚定 `0 kW` 物理零点**，7 月/8 月工厂基荷整体抬升时不会把关机段拉出大面积虚报（`2842/2844/778/856` 均稳留 `raw`）。
  * **`"scaled"`（小信号/未配变比救场态）**：`pbus` 纳入训练集 `z-score` 标准化（均值 0、方差 1），`proportional` 改为带截距最小二乘线性回归。优点是**消除“大母线微跳变”或“`PT=1, CT=1` 二次侧 `0.1~0.2 kW` 微小数值”导致的深度序列模型梯度消失与预测全 0 坍缩**（如 `733` 从 `test F1=0.0` 救回 `0.985`）；代价是对跨月母线基荷均值漂移更敏感（如 `778` 切 `scaled` 后虚报翻倍）。
* **零泄漏三步闭环（废除静态千瓦阈值盲跳）**：
  1. **Step 1 统一先试 `raw`**：所有 `"auto"` 用户无条件先完整跑一轮 `raw`（记录 `DEFAULT_RAW_BEFORE_VAL_CHECK`），若各模型在 `train + val` 拟合健康，一遍即完零额外耗时。
  2. **Step 2 欠拟合触发检测（仅看 `train + val`）**：检查序列模型（`lstm / cnn1d / transformer`）是否存在以下任一真实病灶：
     * 预测带宽坍缩：`train` 或 `val` 上 `P95(pred) - P5(pred) < on_thr_w`（记录 `*_RAW_COLLAPSED`）；
     * 负决定系数：`train R² < 0` 或 `val R² < 0`（记录 `*_NEGATIVE_R2`）；
     * 显著劣于同场基线：当同场非坍缩 `ridge / history_profile` 基线 `val R² ≥ 0.25` 时，序列模型同时满足 `train R² < base_train_R² - 0.03`、`val R² < base_val_R² - 0.02` 且 `val MAE > base_val_MAE`（记录 `*_UNDERFIT_VS_BASELINE`）。
  3. **Step 3 验证集双向验收门 + 二次侧救援门（严禁看 `test`）**：启动候选 `scaled` 重训后，在 `val` 上逐一验收触发重训的序列模型（全部通过才切换，任一不过则留用 `raw`）：
     * **坍缩恢复通道**：若 `raw` 发生带宽坍缩，`scaled` 解除坍缩且 `val F1 (@on_thr)` 不低于 `raw` → **通过**（`RECOVERED_FROM_COLLAPSE`）；
     * **常规双向门禁**：`val MAE` 相对降低 **`≥ 5.0%`**，且 `val F1 (@on_thr)` 下跌 **`≤ 0.015`** → **通过**（`VAL_DUAL_GATE_PASS`）；
     * **二次侧重度欠拟合 `R²+RMSE` 救援门**：针对 `PT=1, CT=1` 微弱母线场景，若 `train R²` 跃升 **`≥ 0.20`**、`val R²` 提升 **`≥ 0.03`**、`val RMSE` 相对下降 **`≥ 5.0%`**、`val MAE ≤ 1.005 × raw_MAE` 且 `val F1 (@on_thr)` 下跌 **`≤ 0.015`** → **通过**（`VAL_R2_RMSE_RESCUE_PASS`）；
     * **一票否决留用 `raw`**：凡出现 `scaled` 仍坍缩、`MAE/RMSE` 增益不足或 `val F1 (@on_thr)` 退化 `> 0.015`（如 `778` 跌 `0.0377`），打印 `[AUTO-REJECT-SCALED]` 自动退回 `raw`；通过则打印**亮红 `[AUTO-FALLBACK]`** 切换为 `scaled`，推理端通过 `meta.json["pbus_mode"]` 自动继承。

---

### 4.5 参数 ③：`decision_thr_w="auto"`（预测侧判卷阈值双阶段校准与跨季收口）

* **代码入口**：`resolve_auto_decision_thr_w`（训练期单链）、`resolve_joint_val_infer_decision_thr_w` + `sync_decision_thr_artifacts`（推理后双链联合收口）。
* **阶段一（训练期零泄漏验证集校准）**：
  1. **零泄漏选校准模型（`select_calibration_model_zero_leakage`）**：若配置了显式 `infer_model` 则以该模型为准；否则**仅用 `val` 集指标**（过滤 `val` 坍缩模型后按 8 指标投票选 `best_val`，绝不触碰 `test`），同时为场内每个模型分别算出专属 `model_decision_thr_w[model]` 存入 `meta.json`。
  2. **候选网格生成**：覆盖 `[0.6 × on_thr_w, upper_guard_w]` 内固定刻度、分路量级比例点及关态预测噪声高分位点（`P75 ~ P99.5 × 1.05` 抗噪余量）。
  3. **四道硬护栏选优**：
     * **物理上限护栏**：`upper_guard_w = max(on_thr_w, 0.80 × P10(y_train_on))`，严防高阈值切穿真开机最低功率档；
     * **召回底线护栏**：`val_recall ≥ 0.95`（宁可留少量虚报，绝不把真开机砍成大面积漏报）；
     * **反全开坍缩特异性护栏（`_apply_anti_collapse_guard`）**：当验证集负样本 `≥ 12` 且存在特异性 `Spec = TN/(TN+FP) ≥ 0.50` 的健康候选点时，强制剔除 `Spec < 0.25`（`TN → 0` 全报 1 坍缩）的低阈值点，驱动阈值自动跨过 MSE 回归模型的关态非零均值地板；
     * **惰性保持门与 `scaled` 抗漂移锚点**：若最优候选相对 `on_thr_w` 的 `F1` 增益 `< 0.003`、无 `≥ 35%` 虚报大幅削减且 `on_thr_w` 未发生特异性坍缩，则保持 `decision_thr_w = on_thr_w`（`KEEP_ON_THR_NEAR_OPTIMAL`）；若 `pbus_mode == "scaled"`，则在 `F1` 峰值容差 `0.008`、`recall ≥ 0.96` 平台期内向 `min(max(1.35 × P99(off_preds), 1.25 × thr), 0.70 × P10(y_on))` 右移留足抗跨月基荷漂移裕度（`SCALED_ANTI_DRIFT_MARGIN`）。
* **阶段二（Step 5 推理后 `(val + infer)` 双链联合收口与零重训原子同步）**：
  * 当 `infer` 目录含分路真值（离线验收/月扫调优场景）时，`auto_run_steps2to5.py` 在 Step 5 后自动调用 `calibrate_and_sync_joint_decision_thr`：
    - **双链同峰/有交集**：在同时满足 `val` 与 `infer` 双侧高分容差带（`best_f1 - 0.015`）的交集中，选双链综合 `F1` 最高、全关日 `FP` 最低的稳健点；
    - **跨季双链异峰（`CROSS_SEASON_HETERO_PEAK`，如 `2844` 6月 vs 7月、`733` 4-6月 vs 8月）**：当历史季节 `val` 与当期 `infer` 最优带无交集时，自动解除过时 `val` 的窄缝否决，在训练集物理护栏上限 `train_upper_guard_w`、`infer_recall ≥ 0.95` 及防下探坍缩护栏（`ANTI_DOWNWARD_COLLAPSE_GUARD`）约束下**以当期 `infer` 链为准定阈**（严格落实 Step 4 铁律）；
    - **毫秒级零重训原子同步**：若定稿值与训练初值不同，自动重写 `train_predictions.csv`、`metrics_daily_chain.csv`、`state_strategy_metrics.csv`、`inference_result.csv` 及双侧 `meta.json`（完整保留 `decision_thr_w_val_initial` 历史留痕），确保双侧 `decision_thr_w` 100% 对齐并通过 `audit_user_run.py` 全部一致性门禁。

---

### 4.6 参数 ④ & ⑤：`post_min_on="auto"` 与 `post_fill_short_off="auto"`（游程两阶段物理定界 + 三维协同校准）

* **代码入口**：`resolve_postprocess_physical_bounds`（阶段一真值定界）、`resolve_auto_postprocess_and_decision_thr`（阶段二协同寻优）。
* **人话职责**：
  * **`post_min_on`（最小开机持续点数，单位：15min 点）**：抹除关机期间偶尔冒头 `1~m-1` 个点的孤立预测尖峰毛刺（`m=1` 表示不去毛刺；`m=4` 表示不足 1 小时的短脉冲全抹零）。**设太大会把真短开机段抹掉！**
  * **`post_fill_short_off`（最大短关回填点数，单位：15min 点）**：把**左右紧邻皆为开机 `1`** 且长度 `≤ k` 个点的短暂功率跌落凹坑填回 `1`（`k=0` 表示不回填；`k=3` 表示回填 `≤ 45min` 的短凹坑）。**设太大会把启停循环冷机的真实停机间隙或午休停机桥接成常开！**
* **两阶段零泄漏算法**：
  1. **阶段一：连续 15min 真值块物理安全定界 `(M_max, K_max)`（零模型依赖）**
     * 先按相邻时间差 `== 15min` 切分严格连续块（`_split_contiguous_15m_blocks`），杜绝跨日抽样或通讯缺口拼出假游程；
     * **定 `M_max`**：提取两端未截断的完整真开游程 `L_on`，取分位数初界（`≥5` 段取 `clip(floor(0.75 × P10(L_on)), 1, 8)`，`3~4` 段取 `clip(floor(0.60 × min(L_on)), 1, 4)`，`<3` 段锁 `1`），再在连续真值块上自回放 `enforce_min_on(s_gt, m)`，强制要求**真开态点数折损率 `≤ 0.5%`**（只要存在真实 15~30min 短促开机，自动锁死 `M_max = 1`）；
     * **定 `K_max`**：在 `k ∈ {0..6}` 上对连续真值块自回放 `fill_short_off(s_gt, k)`，强制要求**被错误桥接的真关态膨胀率 `≤ 1.0%`**，且当日内短停机间隙 `≥ 5` 段时**真实停机次数被桥接占比 `≤ 10%`**（遇到间歇启停压缩机或高频循环泵时自动把 `K_max` 收窄至 `0~1`，遇到连续全天长班次空调则放开至 `2~3`）。
  2. **阶段二：验证集 `(decision_thr_w, post_min_on, post_fill_short_off)` 三维协同寻优 + 奥卡姆正则**
     * 支持单参数解耦配置（如 `post_min_on=2, post_fill_short_off="auto"` 时仅在 `k ∈ {0..K_max}` 上搜索）；
     * 在安全盒 `{1..M_max} × {0..K_max}` 内与 `decision_thr_w` 联合评估，施加**全关日零新增桥接护栏** `off_day_fp(c, m, k) ≤ off_day_fp(c, m, 0)`（严禁 `fill_short_off` 在全关天把两个孤立噪点连成一片大片虚报！）；
     * 施加奥卡姆复杂度罚分 `0.0004 × (m - 1) + 0.0003 × |k - k_anchor|` 与最小净增益门（`F1` 净增 `< 0.002` 且 `FP` 净减 `< 3` 时保持低复杂度锚点 `(1, min(3, K_max))`，记录 `KEEP_POSTPROC_ANCHOR`）。

---

### 4.7 控制台日志标识、落盘留痕与一键同步命令速查

| 日志标签 | 颜色 | 触发阶段与含义 | 对应 `meta.json` 审计字段 |
|---|---|---|---|
| `[AUTO-ON-THR]` | 亮黄 | `on_thr_w="auto"` 高置信度双峰/三峰跨越定阈生效 | `on_thr_w`, `on_thr_w_decision` |
| `[AUTO-ON-THR-REFRESH]` | 亮黄 | `bad_days / day_gate` 剔除坏天后在纯净合格天重校准 `on_thr_w` | `on_thr_w_decision.refreshed_after_day_filter` |
| `[AUTO-ON-THR-WARN]` | 亮红 | 低分离度、常开/常关或真多档歧义告警，提示人工核对铭牌 | `on_thr_w_decision.reasons` |
| `[AUTO-FALLBACK]` | 亮红 | `pbus_mode="auto"` 检出 `raw` 欠拟合且 `scaled` 通过验证集验收，切换 `scaled` | `pbus_mode`, `pbus_mode_decision.val_acceptance` |
| `[AUTO-REJECT-SCALED]` | 默认 WARN | 候选 `scaled` 未过验证集 `MAE/R²+RMSE` 或 `F1` 退化门，安全留用 `raw` | `pbus_mode_decision.stage="post_fallback_rejected"` |
| `[AUTO-DEC-THR]` | 亮黄/亮红 | 训练期验证集 `decision_thr_w` 自动校准结果（低召回/低F1变红） | `decision_thr_w_decision`, `model_decision_thr_w` |
| `[AUTO-POSTPROC]` | 亮黄/亮红 | 游程 `(post_min_on, post_fill_short_off)` 两阶段定界与协同校准结果 | `postprocess_bounds`, `postprocess_decision`, `model_postprocess_params` |
| `[AUTO-DEC-THR-SYNC]` | 亮黄/亮红 | Step 5 后 `(val + infer)` 双链三维收口对齐或跨季异峰同步重写完成 | `infer/meta.json["joint_calibration_decision"]` |

```powershell
# 1) 新用户或存量用户全参数自动定参一键跑通（Step 2→3→5→双链收口→Step 4 扫描）：
python scripts/auto_run_steps2to5.py --user-key <device>_<user> --target-col p1 --on-thr auto --decision-thr auto --min-on auto --fill-off auto --pbus-mode auto --force

# 2) 自动跑完确认指标满意后，一键将定稿数值写回 configs/time_filters.json（自动备份 .bak.auto）：
python scripts/auto_run_steps2to5.py --user-key <device>_<user> --stage sync-thr --save-config

# 3) 人工微调判卷侧参数（无需重训模型！毫秒级同步刷新 train + infer 全部报表并通过审计）：
python scripts/auto_run_steps2to5.py --user-key <device>_<user> --stage sync-thr --decision-thr 200 --min-on 1 --fill-off 3 --save-config
```

---

### 4.8 自动定参已知边界与人工覆写实战判据（`901` 深度复盘）

自动定参覆盖了 90% 以上的常规双峰与标准三峰工况，但在**“多路合并变频轻载 + 大厂总母线淹没 + 未配 PT/CT + 历史强绑劣质 `infer_model`”**四重极端叠加时（典型代表：`800080270901_4200008005677`，目标 `p1+p2+p3+p4`，`pt=1, ct=1`），会出现从 `on_thr_w=300` 到 `decision_thr_w=500` 的连锁偏移。遇到此类现象请按以下三步秒级定位与处置：

1. **现象特征**：`on_thr_w="auto"` 定在 `300W`（日志显示 `待机P99=300.0, 工况P10=660.0`），`decision_thr_w="auto"` 冲顶至 `500W, post=(4,2)`，全局 `infer_F1=0.867` 看似尚可，但逐日表 `metrics_daily_chain.csv` 多天 `F1=0` 崩盘。
2. **三层真实根因**：
   * **根因 ①（`on_thr_w` 受 15min 跨刻爬坡与单机低频尾端抬升）**：二级 Otsu 谷底 `v_2 ≈ 350W` 把 `[160, 350)W` 内的 15 分钟启停爬坡均值与单台变频低频巡航点划入了 `< v_2` 池，令 `c01_p99 = 300W`，定阈 `300W` 误将占全样本约 `30%~35%` 的 `[15, 300)W` 轻载时段翻转为“真关态（`0`）”，单台低频运行日整天变成“伪全关天（`TP=0 → 日 F1=0`）”；
   * **根因 ②（`time_filters.json` 残留 `"infer_model": "transformer"` 强绑劣质模型）**：该户 `pt=1, ct=1` 且分路跳变完全淹没在大厂母线背景中（`TARGET_EDGE_BURIED_IN_BUS`），同场 `ridge`（`val F1=0.9929, fp=0`）与 `history_profile`（`val F1=0.9958, fp=1`）表现极佳且训练已评定 `best=ridge`，但历史配置写死了 `"infer_model": "transformer"`，强制用重度欠拟合的 `transformer(raw)`（关态预测 100% 落在 `200~550W`）做校准与推理；
   * **根因 ③（`@on_thr` 裸切拒切 `scaled` + 反坍缩护栏推高 `decision_thr_w`）**：`pbus_mode="auto"` 在未校准 `@300W` 切点上裸算 `val F1` 因验证集母线偏移拒切 `scaled`；而在 `transformer(raw)` 上 `200W` 处 `TN≈0`（`Spec < 0.25`），反坍缩护栏依法剔除 `200W` 并顶到 `upper_guard_w=512W` 选出 `500W`。
3. **标准处置口诀（遇到多路合并 + 母线淹没必查）**：
   * **一查 `infer_model`**：打开 `configs/time_filters.json` 检查该户是否残留了手工写死的 `"infer_model"`——删掉它，让 4 模型自动择优（`best_model`）选出 `ridge / history_profile`；
   * **二看 `PT/CT` 与轻载档**：核实现场互感器变比填入 `pt_ratio / ct_ratio`；若多路合并存在 `200~300W` 单机变频低频档，按台账单机最小工作功率显式固化 `"on_thr_w": 200.0`；
   * **三用 `sync-thr` 秒级验证**：改判卷侧参数直接跑 `--stage sync-thr --decision-thr 200 --min-on 1 --fill-off 3` 零重训对比日表，改真值侧 `on_thr_w` 则加 `--force` 重训重推。

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

> 7项全绿即可与 4 户同口径并入 `outputs/batch` 调度。任一项红→按 `§10 战史` 定向排障，不盲调参。

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

## 9. 通用模型：跨用户泛化验证与调优流程（pooled）

本专章针对“用部分用户训练共享模型，再推理未参与训练的新用户，或已有用户未来的新数据”。它使用独立链路 `scripts/run_pooled.py` + `configs/base_universal.yaml`，**不是**单用户命令 `auto_run_steps2to5.py` 的 `--split-strategy auto`，两者不可混为一谈。

> **适用前提**：池中用户的 `target_col` 必须代表同一种物理负荷/设备语义（例如都确认 `p1` 是同类设备）；各用户总线字段映射、PT/CT 变比、分路单位和标签阈值也必须可信。只因 CSV 列名同为 `p1`，不能证明设备语义相同。

#### A. 建立训练用户池与独立最终留出用户

建议先做用户级清单，至少记录 `user_key / target_col及设备语义 / PT·CT / branch_scale / 有效双达标天 / on_thr来源 / 是否有全关日 / 目标功率范围`。按设备类型与工况选有代表性的开发用户；另留至少一个**不参与模型/超参/阈值选择**的最终审计用户。若用户数少，必须在报告中说明结论的外推范围很有限。

`--users` 指定此次池化训练使用的用户；未指定时脚本会扫描 `data/trains` 下所有合法用户。不同 `target_col` 不应混池；当前脚本按列名过滤，配置不一致会跳过用户并写告警。

```powershell
# 使用三个开发用户做 LOUO；每次轮流把一个完整用户留作 test
python scripts/run_pooled.py `
  --time-filter-config configs/time_filters.json `
  --base-config configs/base_universal.yaml `
  --data-root data --output-root outputs --target-col p1 `
  --stage train --users <开发用户A> <开发用户B> <开发用户C> --loo
```

配置的 `pooled.val_ratio` 默认 `0.2`，`pooled.split_strategy` 默认 `stratified_by_state`。LOUO 每折的用户关系是：训练用户内部逐用户按日切 train/val；留出用户的全部可用日期只进 test，不参与模型拟合。记录输出目录和各折留出用户身份，检查：

- `louo_summary.csv/json`：按留出用户聚合的 LOUO 总结；
- `loo_hold_<user_key>/metrics_by_user.csv`：每个模型在各用户上的逐用户指标；
- 每折 `meta.json`、`metrics_by_split.csv`、`metrics_daily.csv`、`models/`：切分、scaler 与模型产物。

LOUO 目录是**验证产物，不是部署模型目录**。代码中的 `latest_universal_dir()` 查找带顶层 `_DONE` 的普通 pooled 训练目录；LOUO 模型保存在折子目录，不应直接当作最终通用模型使用。

#### B. 评估泛化，不要只看池化平均分

按以下顺序验收：

1. **先查每折数据和用户身份**：每个用户在 train/val/test 的日期、有效点数、质量剔除日期、`cap`、`on_thr_w`；确认 held-out user 没被放进该折 train。
2. **看逐用户、逐模型**：重点检查最差用户及不同量级用户，不只看 macro 平均。一个大用户表现很好不能抵消另一用户完全失效；当前训练损失按拼接后的逐点样本计算，没有按用户均衡损失权重，因此有效点多的用户会贡献更多梯度。
3. **区分回归泛化与状态阈值泛化**：`MAE/RMSE` 衡量瓦数预测；`F1/precision/recall` 还依赖每用户 `on_thr_w`。当前 `_load_user_preprocessed()` 对 `on_thr_w="auto"` 会用该用户标签估值；因此 LOUO 的自动阈值指标不是严格的“完全无新用户标签”状态泛化测试。严格零样本评估须为 held-out 用户提供事先冻结、独立于 test 标签确定的物理阈值，并在报告中记录来源。
4. **看日级与负样本**：检查全关日、夜间关态、休息日及不同开机时长；池里若没有全关日，LOUO 也无法证明模型能识别整天停机。`metrics_daily.csv` 的 pooled 日级 F1 使用池内 `on_thr` 中位数作诊断阈值（代码注释标为近似口径）；异质用户的精确分类汇总优先看 `metrics_by_user.csv`。
5. **保留最终未触碰用户**：用开发用户 LOUO 选模型/调参数之后，再对未参与这些决策的审计用户做一次最终留出评估。用同一份 LOUO 结果反复选模型、调参数再汇报该分数，会产生测试集选择偏差。

**推荐放行门槛不是固定一条 F1 数字**：先由业务定义用户可接受的 MAE 上限、最低 recall、误报/漏报代价和最差用户底线；再检查最差留出用户是否满足。没有达到底线时，不要把模型标记为通用，应缩小适用用户范围、补充代表性训练用户或改进特征/模型后重新做独立 LOUO。

#### C. 比较模型与调参（先比较，再定部署模型）

`configs/base_universal.yaml` 当前示例模型是 `ridge` 与 `transformer`。建议逐轮只改一类因素，并重复 LOUO：

1. **基线**：固定开发用户、字段/阈值、`seed`、质量策略与特征，先跑默认 ridge + transformer，保存完整产物。
2. **模型选择**：比较每用户及留出用户最差结果，而非仅比较点级池化分数。`history_profile`/`proportional` 不在通用配置的默认候选模型中；它们不等价于可迁移的共享参数模型。
3. **特征与模型参数**：每次只调整 `features.lags`、`rolling_windows` 或具体模型参数，记下 LOUO 每用户结果和变动；避免因同一批 LOUO 用户反复调参后仍把其作为无偏最终测试。
4. **用户采样与均衡**：检查各用户有效训练点占比；若一个用户支配样本池，先从数据构成或训练权重层面处理，并重新做 LOUO。当前实现没有用户等权 loss，宏平均评估不能补救训练时的样本权重失衡。
5. **模型落盘选择必须显式**：`run_pooled_infer()` 当前默认使用 `user_cfg.infer_model`、`base_cfg.infer_model`，否则取 `meta["models"][0]`。示例配置里 `ridge` 排在第一，因此不会自动根据 LOUO 选出 transformer。选定模型后，在顶层 `time_filters.json` 配 `"infer_model": "ridge"`（共享默认）或目标用户配置中指定；再确认推理日志中的实际模型名与 LOUO 决策一致。

**不要把单用户的 `--on-thr/--decision-thr/--min-on/--fill-off` 自动定参能力假定为已迁移到 pooled。** 当前 pooled 训练保存 `caps`、`on_thr_map`、全局 scaler 和模型，但没有生成完整的通用 `decision_thr_map/postprocess_map` 校准表；推理时 `decision_thr_w="auto"` 若找不到对应模型阈值会回退到 `on_thr_w`。无标签新用户的 `on_thr_w="auto"` 也会回退到 `10.0`，不是从新用户分路标签自动校准出的阈值。正式交付状态判决前，应使用设备台账给定并显式配置的 `on_thr_w` 与合适的 `decision_thr_w`；如果没有物理阈值或少量标注校准数据，应只把 `pred` 当回归输出，不能宣称 `pred_state` 已跨用户校准。

#### D. 训练最终通用模型，再推理新用户

通过开发 LOUO 后，使用纳入的开发用户训练**非 LOO**最终模型（此时 train/val 来自各个开发用户，模型保存于 `outputs/universal/<target_col>/train/<时间戳>/`）：

```powershell
python scripts/run_pooled.py `
  --time-filter-config configs/time_filters.json `
  --base-config configs/base_universal.yaml `
  --data-root data --output-root outputs --target-col p1 `
  --stage train --users <最终纳入用户A> <最终纳入用户B> <最终纳入用户C>
```

新用户总线放在 `data/infers/<user_key>/`，配置中明确 `target_col`、`pt_ratio`、`ct_ratio`、`branch_scale` 与人工核实的状态阈值。按选定训练目录显式推理：

```powershell
python scripts/run_pooled.py `
  --time-filter-config configs/time_filters.json `
  --base-config configs/base_universal.yaml `
  --data-root data --output-root outputs --target-col p1 `
  --stage infer --user-key <新用户key> `
  --universal-dir outputs/universal/p1/train/<最终训练时间戳>
```

检查 `outputs/universal/p1/infer/<时间戳>/predictions/inference_result.csv`、推理 `meta.json` 中的模型名/cap/阈值，以及若有标签时的 `offline_metrics.json`。新用户若没有分路标签，无法计算真实 MAE/F1；应另行收集人工/仪表核验期，不能把输出文件成功生成当成泛化验证通过。

#### E. 老用户未来新数据：当前能力边界与验证要求

老用户未来工况是**时间外泛化**，与 LOUO 的“整户未见”问题不同。当前 pooled 切分器为池内用户分 train/val（无独立时间外 test），且 `pooled_task._load_user_preprocessed()` 目前在训练与推理路径都读取 `user_cfg["train"]` 时间过滤。因此，不能不核对配置就假设 `infer` 时间窗会自动按 `infer.include` 正确筛选；若训练时间窗把未来日期排除，可能造成推理样本被过滤为空或不符合预期。

在代码完成推理期 `infer` 时间窗过滤与“已知用户历史训练 / 未来日期独立 test”切分支持前：

- 将“LOUO 未见用户”与“老用户未来日期”分开报告，不用前者代替后者；
- 推理前核对训练产物的 `meta.json`、用户 `train.include/exclude`、待推理日期范围和实际输出行数；
- 用独立的未来标注期做时间外测试，且不能用该期挑模型、调特征或校准阈值后再报告同一测试分数；
- 如果发现日期过滤、阈值来源或目标量纲不清楚，先停止上线验收，不要用零行/少量行产物或训练集分数宣称已验证泛化。

**最终交付检查卡**：① 目标设备语义一致；② 单位与 PT/CT 对齐；③ 开发用户 LOUO 逐用户通过；④ 留有未触碰审计用户；⑤ 最终模型为非 LOO 产物；⑥ `infer_model` 显式匹配验证结论；⑦ 状态阈值有物理/标注来源；⑧ 新用户与老用户未来数据分别做了真实标签验证；⑨ 当前推理时间过滤边界已逐项核实。

## 10. 战史：我们犯过的 8 个错（别再踩）

1. **W-1 跨天拼窗（高危）**：按数组位置滑窗，把切分锚定造成的时间洞拼进 24h 窗，800有 50.5% 窗被污染最长 4.99 天，`transformer` 位置编码全错。现按 `间隔≠15min` 即分段，段头用段内首行填充，`common.schema.segment_bounds` 全局原语，守卫 13 项。
2. **目标错（OQ-13）**：2842 `p1+p2`、2844 `p3+p4` 训完才发现真目标 `p1/p2`，旧结论 `SAE 0.372→0.0039` 全降级。**先定 target_col 再跑。**
3. **800 暂停（OQ-16）**：分路不在 `p1/p2/p3`，12 篇 `p1` 分析全作废待 `p4`。**新用户第一天就定 pN。**
4. **加权证伪**：训练池 3 个全关天加 `off_day_weight 3.0`，`fp 258→299` 更差（不产生新信息，还把开机日权重挤到 0.89 伤幅值 `+16%`）。数据少就补数，别加权。
5. **阈值看错表**：`decision 10` 看 `offline F1` 说无效，实则 `offline` 契约上就不进 `decision`。**看能力调模型，看交付调阈值。**
6. **单文件加载**：`--base-config` 只读一个文件，`default.yaml` 不参与（曾致 2844 试点被全范围门禁拦）。`day_gate` 必须各 base 文件同步，`test_config_defaults` 守卫。
7. **多工作副本**：`nilm_model_tune` 与 `.../nilm_new` 并存致运行时缺键，以产物 `decision_thr_w` 列为准，执行包必含 `Select-String` 预检（`仓库预检≠运行时生效`，`audit` 以产物自描述为准）。
8. **`901` 四路合并强绑 `infer_model` + 轻载过渡带连锁冲高**：`800080270901`（`p1+p2+p3+p4`，`pt=1, ct=1` 大厂母线淹没）在本地 `time_filters.json` 残留了 `"infer_model": "transformer"`，旁路了同场 `val F1=0.993` 的冠军 `ridge`；叠加 15min 爬坡与单机低频尾端把 `on_thr_w="auto"` 推至 `300W`（翻转 `30%+` 轻载真值），使 `transformer(raw)` 在反坍缩护栏下冲顶 `decision_thr_w=500W`，导致单机轻载日逐日 `F1=0`。**切记：除特殊指定外勿在 `time_filters.json` 写死 `infer_model`，多路变频轻载回路须核对单台最小档显式锚定 `on_thr_w`（见 `§4.8`）。**

---

## 11. 常见问题 18 问

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
**Q14: Step 6 审计2项 `总混淆/全关日fp` 没过，要改文档还是删掉？** **不删，留作可选回归门禁（I8），文档加注即可。** `I8` 的 `1036,77,21,1495/18` 是 `2844` 快照，`800080270856` 实测 `580,19,9,2021/0`（0全关天）必然 `✗`——这是抄模板所致，不是缺陷。正确用法见 Step 6 黄框：新用户第1次不带 `--expect-*` 看真实值，第2次再固化；也可一直省略 `I8`，`T1-T7/I1-I7/I9-I10` 已保核心正确性。本次 `2✗` 已判 `800080270856` 生产可放行（`F1 0.976`），改实测期望即全绿。  
**Q15: Step 4 选阈值报 `OSError: Invalid argument: '.../train/*/predictions/train_predictions.csv'` 怎么办？** 这是 **Windows PowerShell 未展开 `*` 通配**（bash 会展开，PowerShell 不会），`pd.read_csv('.../*/...')` 直接当文件名打开必然 `Errno 22`。已在 `scripts/threshold_sweep.py v2026-09-19` 修复：脚本内部 `glob` 自动展开 `*?[]`，排序取最新时间戳目录，PowerShell/bash 均可（建议 `--csv ".../*/..."` 加引号防 bash 提前展开）。你的 `800080270733_4206673297219` 即此例：`python scripts/threshold_sweep.py --csv "outputs/800080270733_4206673297219/train/*/predictions/train_predictions.csv" --pred-col pred_transformer --state-col pred_state_transformer --split test --thresholds 10,30,50,100,150,200,300,400,500 --min-on 1 --fill-off 3` 升级后即过；旧版请手动替换 `*` 为真实时间戳如 `train/20260103_000000/predictions/train_predictions.csv` 或用 `$csv=(Get-ChildItem ... | Sort | Select -Last 1).FullName` 取最新。  
**Q16: 一键脚本 `Step5 推理` 报 `KeyError: STATUS_COLUMNS / DATA_MISSING_BUS : data/infers/<user> 不存在` 怎么办？** 这是 **该用户无 `data/infers/<user>` 推理数据**（如 `800080270750_4206668913762` `trains 18 / infers 16` 中缺该目录），一键第二段 `--stage infer` 空批次所致。已在 `nilm/pipeline/batch.py v2026-09-18` 修复：空批次不再 `KeyError`，改为写 `batch_status.csv DATA_MISSING_BUS` 并正常结束（`batch_status 1 行 0 OK`）；`scripts/auto_run_steps2to5.py` 已改为 `--stage train` 分段 + 缺 `infer` 自动 `WARN 跳过`（`WARN Step5 跳过：data/infers/<user> 不存在`），`阈值 infer 链` 同步 `WARN 跳过`，`train 链阈值` 仍正常。**无需处理，按提示继续即可；需推理请另备 `data/infers/<user>/e241_...-Ch1-*.csv` 后 `--stage infer` 或 `--skip-infer` 仅训。** 旧版临时绕过：`python scripts/auto_run_steps2to5.py --user-key <key> --stage train` 或 `--skip-infer`。  
**Q17: 新用户接入时，五个核心参数（`pbus_mode / on_thr_w / decision_thr_w / post_min_on / post_fill_short_off`）可以全部填 `"auto"` 吗？什么时候必须改回手工数值？** **可以且推荐新用户首跑全填 `"auto"`**（`auto_run_steps2to5.py` 对新用户默认全 `"auto"`）。首跑完成后看两条线索：① 若控制台全是亮黄提示（无亮红 `[AUTO-ON-THR-WARN]`）且 `metrics_daily_chain.csv` 逐日 `F1` 稳定达标，可直接加 `--stage sync-thr --save-config` 将定稿值固化入库；② 若出现亮红 `[AUTO-ON-THR-WARN]`、或像 `901` 那样多路变频合并回路存在 `200~300W` 轻载巡航档导致逐日表出现伪全关日 `F1=0`，则按台账最小工作档显式指定 `--on-thr 200`（显式数值永远 100% 优先于 `"auto"`）。  
**Q18: 为什么命令行传了 `--on-thr auto --decision-thr auto`，推理时还是没用训练评出的 `best=ridge`，反而用了 `transformer`？** 因为 `auto_run_steps2to5.py` 生成临时 `overlay` 时只覆盖命令行显式传入的阈值/缩放键，原 `configs/time_filters.json` 该用户节点中若存有历史遗留的 `"infer_model": "transformer"` 会原样保留生效（详见 `§4.8`）。删掉 `time_filters.json` 中该用户的 `"infer_model"` 字段后重跑，校准与推理即自动对齐 `best_model`。

---

## 12. 附录：模板与速查卡

### A. 新用户 `time_filters.json` 模板（推荐首跑全 `"auto"`，跑通验收后 `--save-config` 固化）

```json
{
  "900080270900_4200000000001": {
    "target_col": "p2",
    "on_thr_w": "auto",
    "decision_thr_w": "auto",
    "pbus_mode": "auto",
    "post_min_on": "auto",
    "post_fill_short_off": "auto",
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
# 一键 Step 2-5（新用户缺配置自动补全 auto + day_gate；已有用户可显式传 auto 走临时 overlay 试跑）
python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219
python scripts/auto_run_steps2to5.py --user-key 800080270901_4200008005677 --target-col p1+p2+p3+p4 --on-thr auto --decision-thr auto --min-on auto --fill-off auto --pbus-mode auto --force
# 零重训双链收口同步 / 固化写回 configs/time_filters.json（自动备份 .bak.auto）
python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219 --stage sync-thr --save-config
python scripts/auto_run_steps2to5.py --user-key 800080270901_4200008005677 --stage sync-thr --decision-thr 200 --min-on 1 --fill-off 3 --save-config
.\scripts\one_click_733.ps1                                   # 等价 PowerShell 封装
python scripts/auto_run_steps2to5.py --help                  # --stage all|train|infer|threshold|sync-thr / --save-config / --force
# 单户训练+推理（分步，同上一键的底层）
python scripts/run_batch_users.py --time-filter-config configs/time_filters.json --base-config configs/base_optimal.yaml --data-root data --output-root outputs --user-key 900080270900_4200000000001
# 阈值扫（* 通配已兼容 PowerShell，v2026-09-19）
python scripts/threshold_sweep.py --csv "outputs/900080270900_4200000000001/infer/*/predictions/inference_result.csv" --pred-col pred --state-col pred_state --thresholds 10,30,50,100,150,200,300,400,500
# 审计（形式 A 用户目录；批量多用户用 --run-root outputs --user-key <user>）
# 新用户第1次不带期望：
python scripts/audit_user_run.py --run-root outputs/900080270900_4200000000001
# 固化期望（例 2844 模板值，换用户请用实测值 580,19,9,2021）：
python scripts/audit_user_run.py --run-root outputs/900080270900_4200000000001 --expect-n 2629 --expect-confusion 1036,77,21,1495 --expect-off-day-fp 18
# 审计（形式 B 父目录+指名）
# python scripts/audit_user_run.py --run-root outputs --user-key 900080270900_4200000000001 --expect-n 2629 --expect-confusion 1036,77,21,1495 --expect-off-day-fp 18
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
