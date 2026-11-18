"""跨用户归一化（pooled 通用模型专用，不影响单用户链路）。

动机：单用户链路直接用绝对瓦数（pbus/y 均为 W），不同用户 CT/PT 变比、
负荷幅值差一个量级，池化混训会让大用户梯度淹没小用户。本模块提供
「按用户容量 cap 归一化」：cap 仅由总线 pbus 分位数估计（新用户推理时
只需总线即可计算，无需分路标签），特征与标签同除 cap，推理时再乘回。

约定：
- cap = pbus.quantile(cap_quantile)，默认 0.95；cap<=0 时回退 1.0 并告警；
- 归一化列：功率类绝对量（pbus/pa/pb/pc/d_p/roll_mean_p_*/roll_std_p_*/p_lag_*）；
  电压/pf/不平衡/占比/时间列保持原值（已是无量纲或稳定量纲）；
- 标签 y_norm = y / cap；on_thr_norm = on_thr_w / cap（仅供诊断/加权）；
- 评估一律在物理域（denormalize 后）按各用户 on_thr_w 计算。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common.logging import get_logger
from nilm.common.schema import bus_total, is_power_column

log = get_logger("preprocess.normalize")

# 功率类列前缀/精确名：归一化时除以 cap
_POWER_EXACT = {"pbus", "pa", "pb", "pc", "ptotal", "d_p"}
_POWER_PREFIX = ("roll_mean_p_", "roll_std_p_", "p_lag_")
# 电流也随负荷幅值缩放，一并归一（电压/pf 不归一）
_CURRENT_EXACT = {"ia", "ib", "ic", "d_i"}
_CURRENT_PREFIX = ("roll_mean_i_", "roll_std_i_")


def compute_cap(pbus: pd.Series, quantile: float = 0.95) -> float:
    """由总线 pbus 估计用户容量 cap（仅需总线，新用户可算）。"""
    s = pd.Series(pbus).dropna()
    if len(s) == 0:
        log.warning("pbus 为空，cap 回退 1.0")
        return 1.0
    cap = float(s.quantile(float(quantile)))
    if not np.isfinite(cap) or cap <= 1e-9:
        log.warning("cap 非法（%.6g），回退 1.0", cap)
        return 1.0
    return cap


def _is_power_col(col: str) -> bool:
    c = str(col)
    if c in _POWER_EXACT or c in _CURRENT_EXACT:
        return True
    return c.startswith(_POWER_PREFIX) or c.startswith(_CURRENT_PREFIX)


def normalize_features(feat: pd.DataFrame, cap: float) -> pd.DataFrame:
    """功率类列除以 cap，返回新 DataFrame（不改原表）。"""
    out = feat.copy()
    if cap <= 1e-9:
        return out
    for c in list(out.columns):
        if _is_power_col(c):
            out[c] = out[c] / cap
    return out


def normalize_target(y: pd.Series, cap: float) -> pd.Series:
    """标签归一化 y_norm = y / cap。"""
    out = pd.Series(np.asarray(y, dtype=np.float64) / cap, index=y.index, name=y.name)
    return out


def denormalize_target(y_norm: np.ndarray, cap: float) -> np.ndarray:
    """归一化预测还原为物理瓦数。"""
    return np.asarray(y_norm, dtype=np.float64) * cap


def cap_from_bus(bus: pd.DataFrame, quantile: float = 0.95) -> float:
    """便捷入口：由 15min 总线 DataFrame 计算 cap。"""
    return compute_cap(bus_total(bus), quantile=quantile)


def apply_pt_ct(bus: pd.DataFrame, pt_ratio: float = 1.0,
                ct_ratio: float = 1.0) -> pd.DataFrame:
    """按 PT/CT 变比校正总线物理量到 kW（与 on_thr_w 同级的用户配置）。

    - 电压 ua/ub/uc × pt_ratio（V）；电流 ia/ib/ic × ct_ratio（A）；
    - 有功 pa/pb/pc/ptotal × (pt_ratio*ct_ratio/1000) 转 kW（P=U·I/1000）；
    - pf* 不动（无量纲）；pt/ct 均为 1.0 时仅功率 /1000（W->kW）。
    """
    pt, ct = float(pt_ratio), float(ct_ratio)
    out = bus.copy()
    for c in ("ua", "ub", "uc"):
        if c in out.columns:
            out[c] = out[c] * pt
    for c in ("ia", "ib", "ic"):
        if c in out.columns:
            out[c] = out[c] * ct
    for c in ("pa", "pb", "pc", "ptotal"):
        if c in out.columns:
            out[c] = out[c] * (pt * ct / 1000.0)
    return out


def apply_branch_scale(branch: pd.DataFrame, scale: float = 1.0) -> pd.DataFrame:
    """分路一次侧单位换算到 kW（与 on_thr_w 同级的用户配置 branch_scale）。

    分路 CSV 为 kW 时 1.0（默认，直通）；为 W 时 0.001（W->kW）。
    仅缩功率列（pN/pa/pb/pc/ptotal），为 1.0 时原样返回。
    """
    s = float(scale)
    if s == 1.0:
        return branch
    out = branch.copy()
    for c in list(out.columns):
        if is_power_column(c):
            out[c] = out[c] * s
    return out
