"""数据质量报告：点数校验 / 缺失率 / 数值越界。

在数据边界（data_io 出口）执行，属于「快速失败」门禁的一部分。
"""

from __future__ import annotations

import pandas as pd

from nilm.common.logging import get_logger

log = get_logger("data_io.validator")

# 数值合理性范围（越界计入 anomalies）
BOUNDS = {
    "u_a": (0, 1000), "u_b": (0, 1000), "u_c": (0, 1000),
    "i_a": (0, 10000), "i_b": (0, 10000), "i_c": (0, 10000),
    "pf_a": (-1.0, 1.0), "pf_b": (-1.0, 1.0), "pf_c": (-1.0, 1.0),
}


class QualityError(RuntimeError):
    """数据质量不满足门禁要求。"""


def quality_report(df: pd.DataFrame, kind: str, points_per_day: int) -> dict:
    """产出质量报告字典：天数、点数、缺失率、越界计数。"""
    n_days = max(1, int(round(len(df) / points_per_day)))
    missing_ratio = float(df.isna().mean().mean()) if len(df) else 1.0

    anomalies = 0
    for col, (lo, hi) in BOUNDS.items():
        if col in df.columns:
            s = df[col].dropna()
            anomalies += int(((s < lo) | (s > hi)).sum())
    # 有功功率不允许为负（出现即计入 anomalies）
    for col in [c for c in df.columns if str(c).startswith(("p_", "branch_"))]:
        s = df[col].dropna()
        anomalies += int((s < 0).sum())

    return {
        "kind": kind,
        "n_rows": len(df),
        "n_days_approx": n_days,
        "expected_points_per_day": points_per_day,
        "missing_ratio": round(missing_ratio, 6),
        "anomalies": anomalies,
    }


def assert_quality(report: dict, max_missing_ratio: float = 0.1) -> None:
    """质量门禁：缺失率超限或完全无数据时快速失败。"""
    if report["n_rows"] == 0:
        raise QualityError(f"{report['kind']} 数据为空")
    if report["missing_ratio"] > max_missing_ratio:
        raise QualityError(
            f"{report['kind']} 缺失率 {report['missing_ratio']:.2%} 超过阈值 {max_missing_ratio:.2%}"
        )
