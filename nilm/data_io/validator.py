"""数据质量与 schema 报告（指南 §4/§6）：
data_schema_report.json、data_quality_report.html，以及质量门禁。

指标（§6）：quality_score、missing_rate、outlier_rate、coverage_rate。
原则：原始数据不可覆盖（只读 data/，报告写 outputs/）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from nilm.common.logging import get_logger
from nilm.common.schema import is_power_column

log = get_logger("data_io.validator")

BOUNDS = {
    "ua": (0, 1000), "ub": (0, 1000), "uc": (0, 1000),
    "ia": (0, 10000), "ib": (0, 10000), "ic": (0, 10000),
    "pfa": (-1.0, 1.0), "pfb": (-1.0, 1.0), "pfc": (-1.0, 1.0),
}


class QualityError(RuntimeError):
    """质量门禁不通过（映射为状态码 DATA_QUALITY_FAILED）。"""


def _daily_stats_from_pmax(pmax: pd.Series, on_thr_w: float) -> dict:
    """由「行级最大功率」序列出日级统计。

    - total_days   : 有数据行的天数（含全天 NaN 的天）；
    - actual_days  : 实际天数 = 总天数 − 全天数据缺失（当日功率全 NaN）的天；
    - missing_days / missing_dates : 全天数据缺失的天（不计入全关天）；
    - all_off_days / all_off_dates : 全关天（仅在实际天中判定，日峰值 < on_thr_w）。
    """
    empty = {"total_days": 0, "actual_days": 0, "missing_days": 0,
             "missing_dates": [], "all_off_days": 0, "all_off_dates": []}
    if len(pmax) == 0:
        return empty
    daily_max = pmax.groupby(pmax.index.normalize()).max()  # 全 NaN 天 → NaN
    missing = daily_max[daily_max.isna()]
    present = daily_max.dropna()                            # 实际天（有有效数据）
    all_off = present[present < float(on_thr_w)]            # 全关只在实际天中判定
    return {
        "total_days": int(daily_max.size),
        "actual_days": int(present.size),
        "missing_days": int(missing.size),
        "missing_dates": [d.strftime("%Y-%m-%d") for d in missing.index],
        "all_off_days": int(all_off.size),
        "all_off_dates": [d.strftime("%Y-%m-%d") for d in all_off.index],
    }


def cleaned_daily_stats(df: pd.DataFrame, on_thr_w: float) -> dict:
    """清洗后数据的逐天统计：总天数/实际天数/全天缺失天/全关天（含日期清单）。

    全关天判定：该日所有功率列（p 开头，pf 除外）的行最大值均 < on_thr_w
    ——与状态判据（§12.3）同一二值化口径；全天数据缺失的天不计入全关天。
    """
    p_cols = [c for c in df.columns
              if is_power_column(c) and np.issubdtype(df[c].dtype, np.number)]
    if len(df) == 0 or not p_cols:
        return _daily_stats_from_pmax(pd.Series(dtype=float), on_thr_w)
    # 行级最大功率（任一相/分路开即视为开）；skipna=True——行内有值即有效
    return _daily_stats_from_pmax(df[p_cols].max(axis=1), on_thr_w)


def series_daily_stats(s: pd.Series, on_thr_w: float) -> dict:
    """单条功率序列（如切分后的目标功率）的日级统计，口径同 cleaned_daily_stats。"""
    return _daily_stats_from_pmax(pd.Series(s), on_thr_w)


def invalid_data_days(df: pd.DataFrame | pd.Series, points_per_day: int,
                      max_daily_missing_rate: float = 1.0) -> list:
    """无效天清单：全天数据缺失，或当日缺失率超过配置阈值。

    - 有效点按**功率列**判定（该行任一功率列非 NaN）——与全关天口径一致；
      PF 兜底/插值等派生填充不改变功率缺失事实，避免全缺失天被误判有效；
      无功率列时回退全部数值列；
    - 当日缺失率 = 1 − 有效点数/points_per_day（下限 0）；
    - 无效判定：有效点数 = 0（全天缺失）或 缺失率 > max_daily_missing_rate；
    - 返回归一化日期（Timestamp）升序列表，供训练/评估剔除。
    """
    if len(df) == 0:
        return []
    frame = df.to_frame() if isinstance(df, pd.Series) else df
    num = frame.select_dtypes("number")
    if num.shape[1] == 0:
        return []
    p_cols = [c for c in num.columns if is_power_column(c)]
    core = num[p_cols] if p_cols else num
    valid = core.notna().any(axis=1)
    per_day = valid.groupby(frame.index.normalize()).sum()
    miss_rate = (1.0 - per_day / float(points_per_day)).clip(lower=0.0)
    bad = per_day.index[(per_day == 0) | (miss_rate > float(max_daily_missing_rate))]
    return sorted(bad)


def quality_report(df: pd.DataFrame, kind: str, points_per_day: int,
                   allow_negative_power: bool = False,
                   on_thr_w: float | None = None) -> dict:
    """生成 §6 四项指标 + 明细。覆盖率按真实日历跨度计算（含设备离线缺口）。

    on_thr_w 非空时附加清洗后数据统计（cleaned_stats）：
    总天数 / 全关天数量 / 全关天日期清单（按 on_thr_w 二值化口径）。
    """
    n_rows = len(df)
    span_days = int((df.index.max() - df.index.min()).days) + 1 if n_rows else 0
    expected = span_days * points_per_day

    missing_rate = float(df.isna().mean().mean()) if n_rows else 1.0
    coverage_rate = float(min(1.0, n_rows / expected)) if expected else 0.0

    outliers = 0
    total_cells = 0
    for col in df.columns:
        s = df[col]
        if not np.issubdtype(s.dtype, np.number):
            continue
        total_cells += int(s.notna().sum())
        vals = s.dropna()
        if col in BOUNDS:
            lo, hi = BOUNDS[col]
            outliers += int(((vals < lo) | (vals > hi)).sum())
        if is_power_column(col) and not allow_negative_power:
            outliers += int((vals < 0).sum())
    outlier_rate = float(outliers / total_cells) if total_cells else 0.0
    quality_score = float(np.clip(100.0 * (1 - missing_rate) * (1 - min(1.0, 5 * outlier_rate)), 0, 100))

    report = {
        "kind": kind,
        "n_rows": n_rows,
        "n_days_approx": span_days,
        "expected_points_per_day": points_per_day,
        "missing_rate": round(missing_rate, 6),
        "outlier_rate": round(outlier_rate, 6),
        "coverage_rate": round(coverage_rate, 4),
        "quality_score": round(quality_score, 2),
    }
    if on_thr_w is not None:  # 清洗后数据统计（总天数/全关天数量/全关天清单）
        report["cleaned_stats"] = cleaned_daily_stats(df, on_thr_w)
    return report


def assert_quality(report: dict, max_missing_rate: float = 0.3,
                   min_coverage: float = 0.5, min_score: float = 50.0) -> None:
    """质量门禁：不满足抛 QualityError（由批量层映射为 DATA_QUALITY_FAILED）。"""
    if report["n_rows"] == 0:
        raise QualityError(f"{report['kind']} 数据为空")
    if report["missing_rate"] > max_missing_rate:
        raise QualityError(f"{report['kind']} 缺失率 {report['missing_rate']:.2%} > {max_missing_rate:.2%}")
    if report["coverage_rate"] < min_coverage:
        raise QualityError(f"{report['kind']} 覆盖率 {report['coverage_rate']:.2%} < {min_coverage:.2%}")
    if report["quality_score"] < min_score:
        raise QualityError(f"{report['kind']} 质量分 {report['quality_score']} < {min_score}")


def write_schema_report(path: str | Path, bus_report: dict, branch_report: dict,
                        extra: dict | None = None) -> Path:
    """data_schema_report.json（§4 输出物）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"bus": bus_report, "branch": branch_report, **(extra or {})}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("schema 报告: %s", path)
    return path


def write_quality_html(path: str | Path, reports: list[dict]) -> Path:
    """data_quality_report.html（§4 输出物：质量简表 + 清洗后数据统计）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        "<tr>" + "".join(f"<td>{r.get(k)}</td>" for k in
                          ("kind", "n_rows", "n_days_approx", "missing_rate",
                           "outlier_rate", "coverage_rate", "quality_score")) + "</tr>"
        for r in reports)

    # 清洗后数据统计段（总天数/实际天数/全天缺失天/全关天，有 cleaned_stats 才输出）
    def _stat_row(label: str, cs: dict) -> str:
        return (f"<tr><td>{label}</td><td>{cs['total_days']}</td>"
                f"<td>{cs.get('actual_days', cs['total_days'])}</td>"
                f"<td>{cs.get('missing_days', 0)}</td>"
                f"<td>{cs['all_off_days']}</td></tr>")

    def _list_section(label: str, cs: dict) -> str:
        parts = []
        m_dates = cs.get("missing_dates") or []
        if m_dates:
            parts.append(f"<h3>{label} 全天数据缺失日期清单（{len(m_dates)} 天）</h3>"
                         f"<p>{'、'.join(m_dates)}</p>")
        dates = cs["all_off_dates"]
        listing = "、".join(dates) if dates else "（无）"
        parts.append(f"<h3>{label} 全关天日期清单（{cs['all_off_days']} 天）</h3>"
                     f"<p>{listing}</p>")
        return "\n".join(parts)

    cleaned_rows, off_sections = [], []
    for r in reports:
        cs = r.get("cleaned_stats")
        if not cs:
            continue
        cleaned_rows.append(_stat_row(r["kind"], cs))
        off_sections.append(_list_section(r["kind"], cs))
        # 切分级统计（train/val/test 或 infer，split_stats 键存在才渲染）
        for split, ss in (r.get("split_stats") or {}).items():
            cleaned_rows.append(_stat_row(f"{r['kind']}·{split}", ss))
            off_sections.append(_list_section(f"{r['kind']}·{split}", ss))
    cleaned_html = ""
    if cleaned_rows:
        cleaned_html = f"""
<h2>清洗后数据统计</h2>
<table><tr><th>数据集</th><th>总天数</th><th>实际天数</th><th>全天缺失天</th><th>全关天数量</th></tr>
{chr(10).join(cleaned_rows)}
</table>
{chr(10).join(off_sections)}"""

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>数据质量报告</title>
<style>table{{border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px 8px}}</style>
</head><body>
<h1>数据质量报告</h1>
<table><tr><th>数据集</th><th>行数</th><th>天数</th><th>缺失率</th>
<th>异常率</th><th>覆盖率</th><th>质量分</th></tr>
{rows}
</table>{cleaned_html}
</body></html>"""
    path.write_text(html, encoding="utf-8")
    log.info("质量报告: %s", path)
    return path
