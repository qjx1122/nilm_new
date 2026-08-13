"""288 ↔ 96 对齐：母线 5min → 15min 聚合（信息无损方向），并与分路时间网格对齐。

策略（技术方案 §5.3）：
- 功率/电压/电流取 15min 窗口均值（能量守恒）；
- PF 优先用聚合后的分相 P/(U*I) 重算，缺分相 P 时退化为均值；
- 禁止把分路上采样成 5min 伪造标签分辨率。
"""

from __future__ import annotations

import pandas as pd

from nilm.common import schema
from nilm.common.logging import get_logger

log = get_logger("preprocess.align")


def resample_bus_to_branch_freq(bus: pd.DataFrame, freq: str = schema.BRANCH_FREQ) -> pd.DataFrame:
    """母线 5min → 15min：均值聚合 + PF 重算。"""
    schema.validate_bus_frame(bus)
    agg = {c: "mean" for c in bus.columns}
    bus_rs = bus.resample(freq).agg(agg)

    # PF 重算：有分相有功时用 pf = p_phase / (u_phase * i_phase)
    for ph in ("a", "b", "c"):
        p_col, u_col, i_col, pf_col = f"p_{ph}", f"u_{ph}", f"i_{ph}", f"pf_{ph}"
        if p_col in bus_rs.columns:
            s = bus_rs[u_col] * bus_rs[i_col]
            bus_rs[pf_col] = (bus_rs[p_col] / s.where(s > 0)).clip(-1.0, 1.0)
    return bus_rs


def check_time_overlap(idx_a: pd.DatetimeIndex, idx_b: pd.DatetimeIndex) -> float:
    """两个时间索引的重叠率（交集 / 并集），用于对齐前的合理性检查。"""
    inter = idx_a.intersection(idx_b)
    union = idx_a.union(idx_b)
    return float(len(inter) / len(union)) if len(union) else 0.0


def align_frames(bus: pd.DataFrame, branch: pd.DataFrame,
                 min_overlap: float = 0.5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """把母线（已 15min）与分路对齐到同一时间网格（内连接）。

    返回 (bus_aligned, branch_aligned)，两者索引完全一致。
    重叠率低于 ``min_overlap`` 时抛错（大概率是时区/时钟不同步，需先在 M0 排查）。
    """
    overlap = check_time_overlap(bus.index, branch.index)
    if overlap < min_overlap:
        raise ValueError(
            f"母线与分路时间重叠率仅 {overlap:.2%}（阈值 {min_overlap:.2%}），"
            "请检查时间同步/时区问题"
        )
    idx = bus.index.intersection(branch.index)
    log.info("对齐后公共时间点: %d（重叠率 %.2f%%）", len(idx), overlap * 100)
    return bus.loc[idx], branch.loc[idx]
