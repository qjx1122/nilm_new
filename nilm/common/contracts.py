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
SPLIT_STRATEGIES = ("stratified_day", "stratified", "time", "global_stratified")

CONFIG_RULES: dict[str, dict] = {
    # field: default, validator 说明（具体校验见 pipeline/user_config.py）
    "target_col":            {"default": None},
    "on_thr_w":              {"default": 10.0, "min": 0.001, "max": 5000.0},
    "split_ratios":          {"default": [0.6, 0.2, 0.2]},
    "split_strategy":        {"default": "stratified_day", "choices": SPLIT_STRATEGIES},
    "post_min_on":           {"default": 1, "min": 0},
    "post_fill_short_off":   {"default": 3, "min": 0},
    # 决策阈值（W）：仅作用于预测功率→开机状态的判决（pred_state/状态策略评估）；
    # 缺省 None = 沿用 on_thr_w。真值判态（target_state/分类指标）恒用 on_thr_w。
    "decision_thr_w":        {"default": None, "min": 0.001, "max": 5000.0},
    # 推理模型（用户级）：显式指定该用户推理用的模型名；缺省 None = 走
    # base_cfg.infer_model（全局）→ 训练综合最优 best_model 的既有链路。
    # 用途：综合最优被退化指标（如 recall=1 的全漏报基线）带偏时人工锁定。
    "infer_model":           {"default": None},
    "weather_latitude":      {"default": 30.59, "min": -90.0, "max": 90.0},
    "weather_longitude":     {"default": 114.31, "min": -180.0, "max": 180.0},
    "use_weather_features":  {"default": True},
    "use_temp_based_season": {"default": True},
}

# 以下划线开头的顶级配置键不得作为用户数据名加载（§12.1）
RESERVED_CONFIG_KEYS = ("_default",)


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
