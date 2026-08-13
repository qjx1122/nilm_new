# 负荷辨识算法开发流程技术方案（NILM Pipeline）

- 版本：v1.0（2026-08-13）
- 状态：草案，待评审
- 关联代码骨架：仓库根目录 `nilm/`（与本方案同步落地，接口已定义）

---

## 1. 背景与目标

### 1.1 任务背景

在总母线侧可采集到完整的三相电气量，在各分路侧仅有较粗粒度的有功功率计量。目标是以**母线数据为输入、分路数据为监督标签**，建立负荷辨识（负荷分解）算法开发流水线，并对多个候选模型进行统一对比验证，最终产出推荐模型与评估报告。

### 1.2 数据资产

| 数据 | 采样密度 | 通道 | 说明 |
| --- | --- | --- | --- |
| 母线时序 | 每天 288 点（5 分钟） | 三相电压、三相电流、有功功率、功率因素 | 输入特征来源；分辨率较高 |
| 分路时序 | 每天 96 点（15 分钟） | 三相总有功功率（每分路一个标量） | 监督标签来源；分辨率较低 |

**关键矛盾**：两路数据采样密度不同（288 vs 96），且分路侧只有有功功率、无电流/功率因素。这决定了：
- 训练主分辨率以 **15 分钟（分路侧）** 为准，母线数据向 15 分钟聚合（信息无损方向）；
- 母线 5 分钟数据用于**窗口统计特征**（均值/方差/峰谷/不平衡度），而不是直接把 288 点展开成标签；
- 辨识粒度上限为**分路级有功功率分解**，不做分路内部设备级辨识（标签不支持）。

### 1.3 任务定义

**主任务（分支级负荷辨识 / 分解）**：给定窗口内母线电气量序列，估计同一时间网格下各分路的有功功率：

```
输入:  X(t-W..t) = [U_a,U_b,U_c, I_a,I_b,I_c, P_bus, PF_a,PF_b,PF_c]  @15min（含 5min→15min 统计特征）
输出:  ŷ(t)    = [P_branch_1, P_branch_2, ..., P_branch_K]           @15min
约束:  Σ_k ŷ_k ≈ P_bus（总和一致性）、ŷ_k ≥ 0（非负性）
```

**辅助任务（预留）**：基于母线 5 分钟数据的大功率投切事件检测（`nilm/events` 模块），作为特征增强与事件级评估的扩展点。

### 1.4 交付物

1. 模块化、可复现的算法开发流水线（代码骨架已在 `nilm/`）；
2. ≥3 类候选模型（基线 / 传统机器学习 / 深度学习）的统一接入与对比；
3. 对比验证报告（指标矩阵 + 图表 + 推荐结论，沉淀至 `REPORT.md` / `REPORT_TEST.md`）。

---

## 2. 总体架构

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  CLI / 编排层   scripts/run_pipeline.py + nilm/pipeline      │
│  （config 驱动：train → evaluate → compare → report）        │
├──────────────┬──────────────┬──────────────┬────────────────┤
│  data_io     │  preprocess  │    models    │   evaluation   │
│  数据接入     │  清洗/对齐/   │  模型适配层   │   指标与对比    │
│  与模式校验   │  特征工程     │  (注册表)    │                │
├──────────────┴──────────────┴──────────────┴────────────────┤
│  events（预留：事件检测）        reporting（图表与报告生成）   │
├─────────────────────────────────────────────────────────────┤
│  common：类型约定 / 列模式 / 通用注册表 / 日志 / IO 工具      │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 解耦隔离原则（六条硬约束）

1. **依赖方向单向**：`data_io / preprocess / models / evaluation / events / reporting` 只依赖 `common`，彼此之间**零横向导入**；只有 `pipeline` 编排层允许组合各模块。
2. **面向接口编程**：每个模块对外暴露抽象基类（`DataSource` / `Transformer` / `BaseModel` / `EventDetector`），编排层只面向接口调用，不感知具体实现。
3. **配置驱动装配**：数据路径、预处理参数、模型清单与超参、指标清单全部写在 `configs/*.yaml`，由工厂 + 注册表实例化，新增模型不改编排代码。
4. **注册表模式**：模型/指标通过装饰器注册（`@MODEL_REGISTRY.register("ridge")`），对比实验 = 配置里加一行。
5. **数据契约显式化**：跨模块传递的 DataFrame 必须符合 `common/schema.py` 定义的标准列模式与频率，`data_io` 出口与 `preprocess` 各步骤做校验，非法数据在边界处快速失败。
6. **产物隔离**：输入数据只进 `data/`（不入库），模型/指标/图表只出 `outputs/`（不入库），代码与产物物理分离。

---

## 3. 代码目录结构（按功能模块规划）

```
nilm_new/
├── configs/                          # 实验配置（唯一装配入口）
│   └── default.yaml                  # 数据路径/频率/模型清单/指标/输出目录
├── data/                             # 原始与缓存数据（.gitignore，不入库）
│   ├── raw/bus/                      # 母线原始时序（288 点/天）
│   └── raw/branch/                   # 分路原始时序（96 点/天）
├── outputs/                          # 实验产物（.gitignore：模型/指标/图表/报告）
│   └── <exp_name>/<timestamp>/
├── docs/                             # 技术方案等文档
│   └── TECH_DESIGN_LOAD_ID_PIPELINE.md
├── nilm/                             # 源码包（按功能模块划分）
│   ├── common/                       # 共享内核：不依赖任何业务模块
│   │   ├── schema.py                 #   标准列模式、频率约定、schema 校验
│   │   ├── registry.py               #   通用注册表（模型/指标复用）
│   │   └── logging.py                #   统一日志
│   ├── data_io/                      # 模块①：数据接入层
│   │   ├── base.py                   #   DataSource 抽象接口
│   │   ├── csv_source.py             #   CSV/Parquet 实现（列映射 → 标准 schema）
│   │   └── validator.py              #   点数/缺失/异常值质量报告
│   ├── preprocess/                   # 模块②：预处理与特征工程
│   │   ├── base.py                   #   Transformer 抽象接口（可串联）
│   │   ├── clean.py                  #   去重、缺口标记、异常裁剪
│   │   ├── align.py                  #   288↔96 对齐：降采样/网格化/时间同步校验
│   │   ├── features.py               #   母线特征工程（三相不平衡/滑窗统计/滞后）
│   │   └── dataset.py                #   滑窗样本构造 + 时序划分（防泄漏）
│   ├── events/                       # 模块③：事件检测（预留，M3+ 启用）
│   │   └── base.py                   #   EventDetector 抽象接口
│   ├── models/                       # 模块④：模型层（多模型对比的核心）
│   │   ├── base.py                   #   BaseModel 抽象接口（fit/predict/save/load）
│   │   ├── registry.py               #   MODEL_REGISTRY 实例
│   │   ├── baselines.py              #   历史画像基线、岭回归（含约束投影）
│   │   ├── tree_models.py            #   GBDT/RF 多输出回归（M2 实现）
│   │   ├── seq_models.py             #   Seq2Point CNN / LSTM / TCN（M2 实现）
│   │   └── constraints.py            #   总和一致性/非负投影（后处理，模型无关）
│   ├── evaluation/                   # 模块⑤：评估与对比
│   │   ├── metrics.py                #   MAE/RMSE/R²/SAE/MAPE（逐分路+系统级）
│   │   └── compare.py                #   实验矩阵汇总、排序、显著性备注
│   ├── reporting/                    # 模块⑥：报告生成
│   │   └── report.py                 #   指标表 + 预测对比图 + Markdown 报告
│   └── pipeline/                     # 编排层（唯一允许组合各模块的地方）
│       ├── context.py                #   配置加载 + 组件工厂装配（DI）
│       └── runner.py                 #   train / evaluate / compare 三阶段流程
├── scripts/
│   └── run_pipeline.py               # CLI 入口：--config --stage train|evaluate|compare
├── tests/                            # 单元测试（与模块一一对应，合成数据夹具）
│   ├── test_schema.py
│   ├── test_registry.py
│   └── test_metrics.py
├── requirements.txt                  # 核心依赖（numpy/pandas/pyyaml）
├── requirements-ml.txt               # 模型扩展依赖（sklearn/lightgbm/torch/matplotlib）
└── README.md
```

---

## 4. 模块设计（职责 / 接口 / 输入输出）

### 4.1 接口契约（已落入代码骨架）

```python
# data_io：数据接入抽象
class DataSource(ABC):
    def load(self, start, end) -> pd.DataFrame: ...   # 输出必须符合 common.schema

# preprocess：可串联变换抽象
class Transformer(ABC):
    def fit_transform(self, df) -> pd.DataFrame: ...
    def transform(self, df) -> pd.DataFrame: ...

# models：模型适配抽象（sklearn 风格，DL 模型同样适配此接口）
class BaseModel(ABC):
    name: str
    def fit(self, X, y, X_val=None, y_val=None) -> None: ...
    def predict(self, X) -> np.ndarray: ...           # shape: (n_samples, n_branches)
    def save(self, path) -> None: ...
    @classmethod
    def load(cls, path) -> "BaseModel": ...
```

要点：
- **DL 与 ML 同构**：深度模型在适配器内部消化窗口张量，对外仍是 `fit/predict`，评估层无需区分模型族；
- **约束后处理独立**：`constraints.py` 提供 `project_sum_consistency(y_hat, p_bus)` 与 `clip_nonnegative`，任何模型输出都可挂接，不侵入模型实现；
- **指标注册表与模型注册表同构**：新增指标同样用装饰器注册，配置里按名引用。

### 4.2 各模块边界一览

| 模块 | 输入 | 输出 | 禁止 |
| --- | --- | --- | --- |
| data_io | 原始文件路径（config） | 标准 schema DataFrame + 质量报告 | 不做特征工程、不碰模型 |
| preprocess | 标准 schema DataFrame | 对齐后的特征矩阵 X、标签矩阵 y | 不读原始文件、不做评估 |
| models | X, y（numpy 矩阵） | 预测矩阵 ŷ | 不感知数据来源与指标含义 |
| evaluation | y, ŷ（+ 分路名） | 指标字典 / 指标表 | 不画图、不碰模型 |
| reporting | 指标表、序列片段 | 图表文件 + Markdown 报告 | 不重算指标 |
| pipeline | YAML 配置 | 按阶段编排上述模块 | 不含业务逻辑实现 |

---

## 5. 数据管线设计

### 5.1 标准列模式（common/schema.py）

| 数据集 | 频率 | 必备列 | 可选列 |
| --- | --- | --- | --- |
| 母线 BUS | 5min / 15min | `timestamp, u_a,u_b,u_c, i_a,i_b,i_c, p_total, pf_a,pf_b,pf_c` | `p_a,p_b,p_c` |
| 分路 BRANCH | 15min | `timestamp` + 每分路一列 `branch_<id>`（三相总有功） | — |

### 5.2 质量校验（data_io/validator.py）

- 点数校验：期望 288/96 点每天，输出缺失率与异常天清单；
- 数值校验：电压/电流/功率越界、负功率、PF ∈ [-1,1]；
- 时间校验：时间戳单调、网格对齐（5min/15min 整格）、母线与分路时区一致；
- 输出：质量报告 JSON（写入 `outputs/`），严重缺陷直接快速失败。

### 5.3 288 ↔ 96 对齐策略（preprocess/align.py）

- **主策略（推荐）**：母线 5min → 15min 聚合。功率/电流取 15min 窗口**均值**（能量守恒），电压取均值，PF 用 P 与视在功率重算（禁止对比相量 PF 直接平均）；
- 时间网格对齐：`floor` 到 15min 整格；对两侧时间戳做重叠率检查，重叠 < 阈值则报错；
- 缺失处理：短缺口（≤1 点）线性插值并打 `imputed` 标记，长缺口整段剔除、不参与训练；
- **禁用**：不得把分路 15min 上采样成 5min 当作真实标签（会制造虚假分辨率）；如需 5min 粒度研究，标签必须标注 `synthetic`。

### 5.4 特征工程（preprocess/features.py）

| 特征族 | 内容 | 目的 |
| --- | --- | --- |
| 基础量 | 15min 聚合后的 U/I/P/PF（每相 + 总量） | 直接输入 |
| 三相不平衡 | 电流/电压不平衡度、零序近似项 | 捕捉分路单相负荷投切 |
| 滑窗统计 | 过去 1h/6h/24h 的均值、标准差、峰谷差 | 引入日周期上下文 |
| 滞后特征 | y 的自回归滞后（t-1..t-4 个 15min 点） | 时序惯性 |
| 日历特征 | 小时、星期、是否节假日（sin/cos 编码） | 日/周周期 |
| 事件特征 | （预留）事件检测输出作为 0/1 触发特征 | M3+ |

### 5.5 数据集构造与防泄漏（preprocess/dataset.py）

- 滑窗：序列模型窗口默认 96 点（1 天），步长 1；回归模型用展平特征矩阵；
- 划分：**按时间顺序** 训练/验证/测试（默认 70/15/15），测试集必须含连续完整天；
- 滑窗跨越划分边界时截断（窗口不跨 split），滞后/滑窗特征只用过去信息，防未来泄漏;
- 可选滚动重训（expanding window）评估稳定性，配置开关控制。

---

## 6. 模型层设计（多模型对比的核心）

### 6.1 候选模型矩阵

| 族 | 模型 | 说明 | 里程碑 |
| --- | --- | --- | --- |
| 基线 | `history_profile` | 分路历史均值画像（按时段）；sanity baseline | M1 |
| 基线 | `proportional` | 按历史功率占比把 P_bus 分摊到各分路 | M1 |
| 线性 | `ridge` | 多输出岭回归 + 非负/总和一致性投影 | M1 |
| 优化 | `nnls` | 非负最小二乘（带总和约束） | M2 |
| 机器学习 | `gbdt` / `rf` | LightGBM/XGBoost/RF 多输出回归，滞后+日历特征 | M2 |
| 深度学习 | `seq2point_cnn` | 1D-CNN 窗口 → 中心点输出 | M2 |
| 深度学习 | `seq2seq_lstm` | LSTM 编码器-解码器 | M2 |
| 深度学习 | `tcn` / `transformer` | 长上下文备选 | M3 |

### 6.2 注册与装配（配置加一行即接入新模型）

```yaml
# configs/default.yaml（节选）
models:
  - name: ridge
    params: { alpha: 1.0 }
  - name: gbdt
    params: { n_estimators: 800, learning_rate: 0.05 }
  - name: seq2seq_lstm
    params: { hidden: 128, window: 96, epochs: 100 }
```

```python
@MODEL_REGISTRY.register("ridge")
class RidgeDisaggregator(BaseModel): ...
```

### 6.3 物理约束（模型无关后处理）

- 非负投影：`ŷ ← max(ŷ, 0)`；
- 总和一致性：`ŷ ← ŷ · P_bus / Σŷ`（或带约束的 QP 投影），作为可选开关写入配置；
- 约束前后指标都记录，用于分析约束收益。

---

## 7. 评估与对比验证方案

### 7.1 指标体系

| 层级 | 指标 | 说明 |
| --- | --- | --- |
| 逐分路 | MAE、RMSE、R² | 主指标；按分路输出并给出宏平均 |
| 逐分路 | SAE | NILM 惯例信号聚合误差 `|Σŷ-Σy|/Σy`（按评估天） |
| 逐分路 | MAPE(ε) | 分母加 ε 保护，近零负荷仅作参考 |
| 系统级 | 重构误差 | `Σ_k ŷ_k` 对 `P_bus` 的偏差（一致性诊断） |
| 系统级 | 总分解 R² | 全分路拼接后的整体拟合度 |
| 事件级（预留） | 检出率/误报率 | 依赖 events 模块 |

### 7.2 对比流程（pipeline 的 compare 阶段）

1. 对配置中的每个模型：固定相同的 数据划分 / 特征 / 指标（控制变量）；
2. 统一在测试集上产出指标 → 汇总为「模型 × 指标」矩阵（CSV + Markdown 表）;
3. 输出：指标矩阵、逐分路排名、最佳模型在每个分路上的胜负统计、预测曲线对比图（选 3 个代表日）；
4. 结论沉淀：稳定结论进 `REPORT.md`，本次对比细节进 `REPORT_TEST.md`。

### 7.3 验收口径（KPI 草案）

- 必须超越 `history_profile` 与 `proportional` 两个基线（MAE 相对下降）才允许进入推荐候选；
- 系统级重构误差（相对 P_bus）目标 ≤ 5%（首个稳定版本后按实测修订）；
- 测试集为**从未参与训练/调参**的连续天。

---

## 8. 开发阶段与里程碑

| 里程碑 | 内容 | 验收标准 |
| --- | --- | --- |
| M0 数据摸底 | 真实数据接入、质量报告、288/96 对齐验证、缺失率评估 | 质量报告产出；对齐重叠率达标 |
| M1 流水线 MVP | data_io→preprocess→2 个基线+ridge→指标全流程跑通 | `run_pipeline.py --stage train/evaluate` 端到端无手工步骤 |
| M2 多模型 | GBDT/NNLS/Seq2Point/LSTM 接入，约束后处理上线 | ≥5 个模型在同一实验矩阵下可比 |
| M3 对比选型 | 实验矩阵、超参扫描、对比报告、推荐模型定版 | 对比报告进 REPORT_TEST.md，推荐版本进 REPORT.md |
| M4 工程化 | 滚动重训、缓存与增量训练、事件检测扩展、部署评估 | 重训流程一键化 |

每里程碑结束：小步提交 + STATUS.md 更新 + 会话纪要（见 BOOTSTRAP.md）。

---

## 9. 工程约定

- **可复现**：一次实验 = 一份 YAML + 一个 `outputs/<exp>/<timestamp>/`（配置快照、指标 JSON、模型文件、图表、日志）；
- **随机种子**：全局 seed 写入配置（数据划分/模型初始化）；
- **日志**：`common/logging.py` 统一格式，同时落 `outputs/.../run.log`；
- **测试**：每个模块有对应 `tests/test_<module>.py`，用合成数据夹具，CI 可离线跑通；
- **依赖分层**：核心依赖 `requirements.txt`（numpy/pandas/pyyaml），模型扩展 `requirements-ml.txt`（sklearn/lightgbm/torch/matplotlib），避免强绑定。

---

## 10. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 288/96 分辨率失配 | 标签粗，细粒度辨识不可行 | 以 15min 为主分辨率；5min 仅做特征；禁止上采样造标签 |
| 分路侧只有有功功率 | 无法辨识无功/谐波相关特征对应的负荷成分 | 明确任务上限为分路有功分解；事件模块用母线侧量辅助 |
| 母线与分路时间不同步 | 标签错位导致训练失效 | 对齐模块做互相关时滞估计与校正（M0 验证项） |
| 缺失/坏点 | 指标虚高或训练不稳 | 质量门禁 + 缺口标记 + 长缺口剔除 |
| 数据泄漏（滑窗/滞后） | 离线虚高、上线崩盘 | dataset 层强制窗口不跨 split、特征仅用过去信息 |
| 分路数量/拓扑变更 | 输出维度漂移 | schema 与模型输出维度由配置声明，变更走配置而非改代码 |
