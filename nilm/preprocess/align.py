"""时间同步与 5min→15min 聚合（指南 §5）：策略可配置且必须记录。

规则：
- 统一 15min canonical timeline；分路保持真实 15min 采样点，
  **严禁把 15min 分路标签插值成 5min 训练真值**（§1 红线）；
- 聚合策略可配置（§5.2）：U/I/P 默认 mean；PF 优先 P/S 重算，
  禁止无说明直接平均；实际采用策略必须记录（返回 strategy_record）；
- 时间偏移：相关性搜索候选滞后 τ，不得无证据修改时间戳（§5.3）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common.logging import get_logger
from nilm.common.schema import FREQ_MAIN, validate_bus_frame

log = get_logger("preprocess.align")

DEFAULT_AGG_STRATEGY = {"u": "mean", "i": "mean", "p": "mean", "pf": "recompute"}


def resample_bus(bus: pd.DataFrame, freq: str = FREQ_MAIN,
                 strategy: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """总线 5min → 15min。返回 (聚合后 DataFrame, 策略记录)。"""
    validate_bus_frame(bus)
    st = {**DEFAULT_AGG_STRATEGY, **(strategy or {})}

    def agg_of(prefix: str) -> str:
        return st.get(prefix, "mean")

    agg = {}
    for c in bus.columns:
        if c.startswith("u"):
            agg[c] = agg_of("u")
        elif c.startswith("i"):
            agg[c] = agg_of("i")
        elif c.startswith("pf"):
            agg[c] = "mean"  # PF 占位，后面按策略重算
        else:
            agg[c] = agg_of("p")
    out = bus.resample(freq).agg(agg)

    # PF 策略：recompute = 聚合后按 P/(U*I) 重算（§5.2 推荐）；
    # 无法重算时（如电压列置 0）回退文件 PF 均值，仍无数据则置 0（与点位表置 0 规则一致）
    if st.get("pf") == "recompute":
        for ph in ("a", "b", "c"):
            s = out[f"u{ph}"] * out[f"i{ph}"]
            recomp = (out[f"p{ph}"] / s.where(s > 0)).clip(-1.0, 1.0)
            fallback = out[f"pf{ph}"]  # 原始 PF 的窗口均值（agg 阶段已算）
            out[f"pf{ph}"] = recomp.where(recomp.notna(), fallback).fillna(0.0)
    elif st.get("pf") != "mean":
        raise ValueError(f"不支持的 PF 聚合策略: {st.get('pf')}（禁止无说明直接平均以外的黑盒策略）")

    record = {"freq": freq, "strategy": st, "n_in": len(bus), "n_out": len(out),
              "note": "PF 按 P/(U*I) 重算" if st.get("pf") == "recompute" else "PF 直接平均（需在报告中说明理由）"}
    return out, record


def estimate_time_offset(pbus: pd.Series, target: pd.Series,
                         max_lag: int = 8) -> dict:
    """候选滞后 τ 搜索（§5.3）：仅报告证据，绝不修改时间戳。"""
    best = {"tau": 0, "corr": float("nan")}
    scan = {}
    for tau in range(-max_lag, max_lag + 1):
        d = pd.concat([pbus.shift(tau), target], axis=1).dropna()
        if len(d) < 3 or d.iloc[:, 0].std() < 1e-12 or d.iloc[:, 1].std() < 1e-12:
            continue
        c = float(np.corrcoef(d.iloc[:, 0], d.iloc[:, 1])[0, 1])
        scan[tau] = round(c, 4)
        if c == c and (best["corr"] != best["corr"] or c > best["corr"]):
            best = {"tau": tau, "corr": round(c, 4)}
    return {"scan": {str(k): v for k, v in scan.items()}, "best_tau": best["tau"],
            "best_corr": best["corr"],
            "action": "report_only（不得无证据修改时间戳，指南 §5.3）"}


def align_frames(bus: pd.DataFrame, branch_or_target, min_overlap: float = 0.5):
    """把 15min 总线与分路（DataFrame 或 Series）对齐到同一时间网格（内连接）。

    门禁口径（按实测数据修订，记录于 STATUS.md 决策记录）：
    采用「分路标签点被总线覆盖率」= |交集|/|分路|，而非对称 Jaccard |∩|/|∪|——
    总线稀疏度远高于分路时 Jaccard 会系统性偏低，但标签点全覆盖即满足训练需要；
    真正的时钟错位（如 7min 偏移）会使该覆盖率趋近 0，仍可检出（§5.3）。
    """
    b_index = branch_or_target.index
    inter = bus.index.intersection(b_index)
    union = bus.index.union(b_index)
    jaccard = float(len(inter) / len(union)) if len(union) else 0.0
    branch_coverage = float(len(inter) / len(b_index)) if len(b_index) else 0.0
    if branch_coverage < min_overlap:
        raise ValueError(f"总线对分路标签点的重叠率仅 {branch_coverage:.2%}（阈值 {min_overlap:.2%}），"
                         "请检查时间同步/时区（指南 §5.3：先找证据，不改时间戳）")
    log.info("对齐后公共时间点: %d（分路覆盖率 %.2f%%，Jaccard %.2f%%）",
             len(inter), branch_coverage * 100, jaccard * 100)
    return bus.loc[inter], branch_or_target.loc[inter]
