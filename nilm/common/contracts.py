"""接口契约（《工商业负荷辨识算法开发指南 V2.1》）：正则、目录、状态码、配置规则。

指南 §0 规定：本契约是输入/输出/配置接口的最高优先级约束；
算法代码不得自行放宽或改写正则（§0 第 2 条）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 目录契约（§3.1）
TRAINS_DIR = "trains"
INFERS_DIR = "infers"

# user_key = <device>_<user> 复合业务键（§0/§3.1）；生产调度键
RE_USER_DIR = re.compile(r"^([0-9]+)_([0-9]+)$")


def split_user_key(user_key: str) -> tuple[str, str] | None:
    """把 user_key 拆成 (device, user)；不合法返回 None。"""
    m = RE_USER_DIR.match(user_key)
    return (m.group(1), m.group(2)) if m else None


# ---------------------------------------------------------------- 文件名正则（§3.2/§3.3，原文照抄不得改写）
RE_BUS = re.compile(
    r"^e241_(?P<device>[^_]+)_(?P<user>[^-]+)"
    r"-Ch(?P<ch>\d+)-(?P<start>\d{6})-(?P<end>\d{6})"
    r"(?P<suffix>(-1|-infer)?)\.csv$"
)

RE_BR = re.compile(
    r"^(?P<user>[^-]+)-(?P<start>\d{6})-(?P<end>\d{6})"
    r"(?P<suffix>(-1|-infer)?)\.csv$"
)


@dataclass(frozen=True)
class BusFileName:
    """RE_BUS 解析结果。ChN 只是通道标识，禁止假设其物理含义（§3.2）。"""
    device: str
    user: str
    ch: int
    start: str
    end: str
    suffix: str
    raw: str


@dataclass(frozen=True)
class BranchFileName:
    """RE_BR 解析结果。"""
    user: str
    start: str
    end: str
    suffix: str
    raw: str


def parse_bus_filename(name: str) -> BusFileName | None:
    m = RE_BUS.match(name)
    if not m:
        return None
    return BusFileName(m["device"], m["user"], int(m["ch"]), m["start"], m["end"],
                       m["suffix"], name)


def parse_branch_filename(name: str) -> BranchFileName | None:
    m = RE_BR.match(name)
    if not m:
        return None
    return BranchFileName(m["user"], m["start"], m["end"], m["suffix"], name)


# ---------------------------------------------------------------- 合并脚本文件名契约
# 《多数据源用户数据批量合并脚本-功能需求文档》§2.2 严格格式：
#   e241_<终端号>_<用户号>-Ch<通道号>-<起始时间>-<截止时间>.csv（无任何后缀）
# 与 RE_BUS（指南 §3.2，允许可选后缀）是两个不同契约：合并脚本必须严格遵守本格式，
# 带 -1 / -infer 等后缀的文件不是合并对象（示例：...-260604-260611-1.csv 不符合）。
RE_MERGE_FILE = re.compile(
    r"^e241_(?P<device>[^_]+)_(?P<user>[^-]+)"
    r"-Ch(?P<ch>\d+)-(?P<start>\d{6})-(?P<end>\d{6})\.csv$"
)


def parse_merge_filename(name: str) -> BusFileName | None:
    """严格合并格式解析：带后缀或不符格式一律返回 None。"""
    m = RE_MERGE_FILE.match(name)
    if not m:
        return None
    return BusFileName(m["device"], m["user"], int(m["ch"]), m["start"], m["end"], "", name)


# 分路用户数据文件严格合并格式（与 RE_BR 的差异：不允许任何后缀）：
#   <用户号>-<起始时间>-<截止时间>.csv，时间格式 YYmmdd
# 示例：4206894986488-260604-260611.csv（符合）；4206894986488-260604-260611-1.csv（不符合）
RE_MERGE_BRANCH = re.compile(
    r"^(?P<user>[^-]+)-(?P<start>\d{6})-(?P<end>\d{6})\.csv$"
)


def parse_merge_branch_filename(name: str) -> BranchFileName | None:
    """分路严格合并格式解析：带后缀或不符格式一律返回 None。"""
    m = RE_MERGE_BRANCH.match(name)
    if not m:
        return None
    return BranchFileName(m["user"], m["start"], m["end"], "", name)


# ---------------------------------------------------------------- 状态码（§13 + 扩展）
class Status:
    """批量执行状态码。前 8 个为指南 §13 原文规定，其余为工程扩展（见决策记录）。"""
    # —— 指南 §13 原文 ——
    OK = "OK"
    INVALID_USER_DIR = "INVALID_USER_DIR"
    DATA_MISSING_BUS = "DATA_MISSING_BUS"
    DATA_MISSING_BRANCH_LABEL = "DATA_MISSING_BRANCH_LABEL"
    INVALID_FILENAME = "INVALID_FILENAME"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    INSUFFICIENT_TIME_RANGE = "INSUFFICIENT_TIME_RANGE"
    DATA_QUALITY_FAILED = "DATA_QUALITY_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    # —— 扩展（记录于 STATUS.md 决策记录）——
    IDENTIFIABILITY_LOW = "IDENTIFIABILITY_LOW"   # §9 风险标记（警告，不阻断训练）
    SCHEMA_UNCONFIRMED = "SCHEMA_UNCONFIRMED"     # §4 字段映射未确认
    DATA_UNIT_UNKNOWN = "DATA_UNIT_UNKNOWN"       # §4 单位不明（警告）
    SKIPPED_RESUME = "SKIPPED_RESUME"             # 断点续跑跳过
    FAILED = "FAILED"                             # 未分类内部错误


# ---------------------------------------------------------------- 用户 JSON 配置契约（§12.3）
# split_strategy（数据集按日划分策略）：
#   auto                : 基于每日数据质量（bus/branch 得分、有效点数、天然离线断口）与日开/关工况
#                         自动搜索连续高质量块并保障 train/val/test 开关双覆盖，支持池级过剩全关天治理；
#   stratified_day      : 按星期几分层，每层内部按时间切比例（硬编码缺省，保持既有实验 100% 向后兼容）；
#   stratified          : 按月份分层，每层内部按时间切比例；
#   time                : 纯时间顺序连续切分（train→val→test）；
#   global_stratified   : 按日轮转交错分配；
#   stratified_by_state : 按日开/关状态分层，每层内部按时间切比例。
SPLIT_STRATEGIES = ("auto", "stratified_day", "stratified", "time", "global_stratified",
                    "stratified_by_state")
# pbus_mode（按户选/自动判定双路径）：
#   auto（全局默认）：三步零泄漏验证闭环——① 取消静态千瓦阈值盲跳，默认统一先跑抗漂移基态 raw
#     （仅统计 train 集物理量留痕）；② 若 train/val 检测到序列模型真实欠拟合（R² 与 MAE 双双劣于基线，
#     或预测坍缩），启动候选 scaled 重训；③ 在 val 验证集（严禁看 test）执行双向验收，仅当同时满足
#     「val MAE 实质改善（>=5%）」与「val F1 不退化（跌幅<=0.015）」时输出红色日志切换为 scaled，
#     否则拒绝切换并自动保留抗漂移的 raw（如 778）；推理自动继承训练产物模式。
#   raw（退回/忽略电平）：pbus 保留在 NON_SCALED_COLS 不做 z-score 缩放，
#     proportional 按物理占比分摊（pred = pbus × share）；适用于主导单分路、双峰同步负荷或
#     跨期背景基荷易漂移的规律负荷（如 2842/2844/856/870/778）。
#   scaled（方向2/学比例）：pbus 做 z-score 标准化，proportional 改学最小二乘
#     线性映射（y ≈ a·pbus_z + b）；适用于 raw 真欠拟合且验证集双向改善的用户（如 733）。
PBUS_MODES = ("auto", "raw", "scaled")
ON_THR_MODES = ("auto",)
DECISION_THR_MODES = ("auto", "on_thr")
POSTPROC_MODES = ("auto",)

CONFIG_RULES: dict[str, dict] = {
    # field: default, validator 说明（具体校验见 pipeline/user_config.py）
    "target_col":            {"default": None},
    # 规范目标语义：不同用户可使用不同 target_col，但须经业务确认代表同一物理目标。
    "target_semantic":       {"default": None},
    # 阈值口径 kW（全链路功率 kW）：总线一次侧 kW、分路一次侧 kW、阈值 kW。
    # 支持显式正数或 "auto"（基于训练范围分路功率对数域双峰寻谷自动定阈，黄色/红色日志提示；
    # 硬编码缺省保持 10.0 完全向后兼容，显式配置 100% 优先）。
    "on_thr_w":              {"default": 10.0, "min": 0.001, "max": 5000.0,
                              "special_modes": ON_THR_MODES},
    # PT/CT 变比（与 on_thr_w 同级，用户级）：总线二次侧->一次侧 kW。
    # pt_ratio 缩电压（ua/ub/uc，V），ct_ratio 缩电流（ia/ib/ic，A），
    # 有功（pa/pb/pc/ptotal）缩 pt*ct/1000 转 kW；默认 1.0=仅 W->kW（/1000）。
    "pt_ratio":              {"default": 1.0, "min": 0.001, "max": 10000.0},
    "ct_ratio":              {"default": 1.0, "min": 0.001, "max": 10000.0},
    # 分路一次侧单位换算到 kW（与 on_thr_w 同级，用户级）：CSV 为 kW 时 1.0（默认），
    # 为 W 时 0.001（W->kW）。仅缩功率列（pN）。
    "branch_scale":          {"default": 1.0, "min": 0.001, "max": 10000.0},
    # 总线总有功 pbus 缩放与 proportional 基线联动模式（"auto" | "raw" | "scaled"，默认 "auto"）
    "pbus_mode":             {"default": "auto", "choices": PBUS_MODES},
    "split_ratios":          {"default": [0.6, 0.2, 0.2]},
    "split_strategy":        {"default": "stratified_day", "choices": SPLIT_STRATEGIES},
    # 游程后处理参数（15min 点数）：支持显式非负整数或 "auto"（两阶段自动定参：
    # ① 训练全池真值游程画像锁定物理安全上界 (M_max, K_max)；② 验证集带护栏与奥卡姆正则协同寻优；
    # 硬编码缺省保持 1 / 3 完全向后兼容，显式整数 100% 优先）。
    "post_min_on":           {"default": 1, "min": 0, "max": 96,
                              "special_modes": POSTPROC_MODES},
    "post_fill_short_off":   {"default": 3, "min": 0, "max": 96,
                              "special_modes": POSTPROC_MODES},
    # 决策阈值（kW）：仅作用于预测功率→开机状态的判决（pred_state/状态策略评估）；
    # 支持显式正数、"auto"（默认：训练阶段在 val 验证集带护栏零泄漏寻优并落盘 meta.json，
    # 推理阶段自动继承，全程输出黄色/红色提示日志；显式数值 100% 优先）或 "on_thr"（锁定沿用 on_thr_w）。
    # 真值判态（target_state/分类指标）恒用 on_thr_w。
    "decision_thr_w":        {"default": "auto", "min": 0.001, "max": 5000.0,
                              "special_modes": DECISION_THR_MODES},
    # 推理模型（用户级）：显式指定该用户推理用的模型名；缺省 None = 走
    # base_cfg.infer_model（全局）→ 训练综合最优 best_model 的既有链路。
    # 用途：综合最优被退化指标（如 recall=1 的全漏报基线）带偏时人工锁定。
    "infer_model":           {"default": None},
    # 用户级模型参数覆盖（⑯ 全关天虚报治理，2026-09-15）：{model_name: {param: value}}，
    # 按模型名浅合并到 base_cfg.models[].params 之上（用户级 > base；逐键合并非整节
    # 替换，W-3 教训）。仅写在用户键或 _default 下（非顶级键）。典型用途：
    # off_day_weight / off_day_thr_w（seq 模型全关日样本加权）。
    "model_params":          {"default": {}},
    "weather_latitude":      {"default": 30.59, "min": -90.0, "max": 90.0},
    "weather_longitude":     {"default": 114.31, "min": -180.0, "max": 180.0},
    "use_weather_features":  {"default": True},
    "use_temp_based_season": {"default": True},
}

# 以下划线开头的顶级配置键不得作为用户数据名加载（§12.1）
RESERVED_CONFIG_KEYS = ("_default",)

# 顶级全局配置键（非用户键、非 _ 保留键）：作用于所有用户的全局默认，
# 沿既有优先级链插入在硬编码默认之上、_default 之下：
#   用户级 infer_model > _default.infer_model > 顶级全局 infer_model
#   > base_cfg.infer_model（default.yaml）> 训练综合最优 best_model。
# 即：配置了顶级 infer_model 则所有用户默认用它推理；未配置走原有逻辑。
GLOBAL_CONFIG_KEYS = ("infer_model",)


def is_reserved_config_key(key: str) -> bool:
    return key.startswith("_")


# ---------------------------------------------------------------- 输出契约（§2.3/§0）
INFERENCE_RESULT_REL = "predictions/inference_result.csv"   # 最终预测 CSV（§2.3）
# 注：日级指标 23 字段清单与启动段字段契约在指南附件中定义；附件未随 PDF 提供，
#     当前按已知字段实现并记录为「接口待确认项」（指南 §0 同口径处理）。
# target_state = 状态真值（target 按 on_thr_w 二值化；无分路真值时为空）；
# pred_prob    = 开态概率（以 decision_thr_w 为中心的 sigmoid 伪概率）。
# 状态判定阈值列（口径自描述，防跨产物误读）：
#   on_thr_w       = 真值判态阈值（target_state 与分类指标 F1/TP… 均用它）；
#   decision_thr_w = 预测判态阈值（pred_state 的判决链：该阈值+游程后处理）。
INFER_RESULT_COLUMNS = ["timestamp", "user_id", "target", "target_state",
                        "on_thr_w", "pred", "pred_state", "decision_thr_w",
                        "pred_prob"]
