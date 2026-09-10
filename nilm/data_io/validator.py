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


def _day_score(df: pd.DataFrame, points_per_day: int,
               allow_negative_power: bool = False) -> tuple[float, float]:
    """单日质量得分与缺失率（与 quality_report 同一公式，按日窗口计算）。

    缺失率口径 = 1 − 有效单元格/(points_per_day×列数)——把当日行数不足
    （设备离线缺口）也计入缺失，比整段口径更严格、更贴近"这一天可用性"。
    """
    n_cols = max(1, df.shape[1])
    total_cells = points_per_day * n_cols
    valid_cells = int(df.notna().sum().sum())
    missing_rate = 1.0 - min(1.0, valid_cells / total_cells)
    outliers = 0
    for col in df.columns:
        s = df[col]
        if not np.issubdtype(s.dtype, np.number):
            continue
        vals = s.dropna()
        if col in BOUNDS:
            lo, hi = BOUNDS[col]
            outliers += int(((vals < lo) | (vals > hi)).sum())
        if is_power_column(col) and not allow_negative_power:
            outliers += int((vals < 0).sum())
    outlier_rate = outliers / valid_cells if valid_cells else 0.0
    score = float(np.clip(100.0 * (1 - missing_rate) * (1 - min(1.0, 5 * outlier_rate)),
                          0, 100))
    return round(score, 2), round(missing_rate, 4)


def daily_quality_table(bus: pd.DataFrame, branch: pd.DataFrame,
                        points_per_day: int, min_score: float,
                        allow_negative_power: bool = False) -> pd.DataFrame:
    """逐天数据质量表：总线得分 / 目标分路得分 / 阈值 / 当天是否合格。

    - 日期集合 = 总线∪分路出现过数据行的天（并集，缺一侧记 0 分）；
    - 合格判定：bus_score ≥ min_score 且 branch_score ≥ min_score；
    - 返回列：date / bus_score / bus_missing_rate / branch_score /
      branch_missing_rate / score_threshold / qualified(0|1)。
    """
    days = sorted(set(bus.index.normalize()) | set(branch.index.normalize()))
    rows = []
    for day in days:
        b = bus[bus.index.normalize() == day]
        r = branch[branch.index.normalize() == day]
        # 纯日历缺口天（两侧均无任何有效数据，如重采样填出的全 NaN 网格）不进表：
        # 已由覆盖率/实际天数反映，进表会把缺口天刷成大量 0 分行干扰阅读
        if int(b.notna().sum().sum()) == 0 and int(r.notna().sum().sum()) == 0:
            continue
        bs, bm = _day_score(b, points_per_day, allow_negative_power) \
            if len(b) else (0.0, 1.0)
        rs, rm = _day_score(r, points_per_day, allow_negative_power) \
            if len(r) else (0.0, 1.0)
        ok = int(bs >= float(min_score) and rs >= float(min_score))
        rows.append({"date": day.strftime("%Y-%m-%d"),
                     "bus_score": bs, "bus_missing_rate": bm,
                     "branch_score": rs, "branch_missing_rate": rm,
                     "score_threshold": float(min_score), "qualified": ok})
    return pd.DataFrame(rows, columns=["date", "bus_score", "bus_missing_rate",
                                       "branch_score", "branch_missing_rate",
                                       "score_threshold", "qualified"])


def qualified_days_detail(daily_quality: pd.DataFrame, target: pd.Series,
                          on_thr_w: float,
                          split_index: dict[str, "pd.DatetimeIndex"] | None = None,
                          infer_days: set | None = None) -> pd.DataFrame:
    """双达标天明细表：每天是否全关日、全关阈值、所属数据集。

    - 只针对总线与分路**同时达标**（qualified=1）的天；
    - all_off：该日目标功率日峰值 < on_thr_w（有有效数据的前提下）；
    - dataset：该日样本所属数据集——训练集/验证集/测试集（按切分索引归属，
      一天样本可能跨多个切分时并列显示）/ 推理集（infer_days）/ 未使用
      （质量合格但样本构建阶段被剔除，如特征 NaN / 窗口不足 / 时间过滤）。
    返回列：date / all_off / on_thr_w / dataset。
    """
    ok = daily_quality[daily_quality["qualified"] == 1]
    if ok.empty:
        return pd.DataFrame(columns=["date", "all_off", "on_thr_w", "dataset"])
    # 全关判定：目标功率日峰值
    t = pd.Series(target).dropna()
    day_max = t.groupby(t.index.normalize()).max() if len(t) else pd.Series(dtype=float)
    day_max.index = day_max.index.strftime("%Y-%m-%d")
    # 切分归属：date -> [数据集名]
    name_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    day_sets: dict[str, list[str]] = {}
    for split, idx in (split_index or {}).items():
        for d in set(pd.DatetimeIndex(idx).strftime("%Y-%m-%d")):
            day_sets.setdefault(d, []).append(name_map.get(split, split))
    for d in (infer_days or set()):
        day_sets.setdefault(str(d), []).append("推理集")

    rows = []
    for date in ok["date"]:
        mx = day_max.get(date)
        all_off = int(mx is not None and not pd.isna(mx) and mx < float(on_thr_w))
        ds = "/".join(sorted(set(day_sets.get(date, [])))) or "未使用"
        rows.append({"date": date, "all_off": all_off,
                     "on_thr_w": float(on_thr_w), "dataset": ds})
    return pd.DataFrame(rows, columns=["date", "all_off", "on_thr_w", "dataset"])


def qualified_days_summary(detail: pd.DataFrame) -> dict:
    """双达标天汇总：总天数/全关天数量/训练集/验证集/测试集天数（含推理集）。"""
    if detail is None or detail.empty:
        return {"total_days": 0, "all_off_days": 0, "train_days": 0,
                "val_days": 0, "test_days": 0, "infer_days": 0, "unused_days": 0}
    ds = detail["dataset"].astype(str)
    return {
        "total_days": int(len(detail)),
        "all_off_days": int((detail["all_off"] == 1).sum()),
        "train_days": int(ds.str.contains("训练集").sum()),
        "val_days": int(ds.str.contains("验证集").sum()),
        "test_days": int(ds.str.contains("测试集").sum()),
        "infer_days": int(ds.str.contains("推理集").sum()),
        "unused_days": int((ds == "未使用").sum()),
    }


def quality_advice(daily: pd.DataFrame, min_days: float = 3.0) -> list[str]:
    """基于逐天质量表生成训练数据集划分与模型训练建议（规则式，供报告呈现）。

    daily 若含 all_off / dataset 列（双达标天明细），追加切分覆盖性建议。
    """
    tips: list[str] = []
    if daily.empty:
        return ["无逐天质量数据，无法给出建议"]
    n = len(daily)
    ok = daily[daily["qualified"] == 1]
    bad = daily[daily["qualified"] == 0]
    ratio = len(ok) / n
    tips.append(f"总线与分路同时达标 {len(ok)}/{n} 天（{ratio:.0%}）")

    # —— 数据集划分建议
    if len(ok) < min_days:
        tips.append(f"合格天不足 {min_days:.0f} 天：不建议训练，优先补数据")
    elif len(ok) < 14:
        tips.append("合格天 <14 天：建议 time 顺序切分并锁定 splits 锚点，"
                    "test 至少保留 2 个完整合格天；结论仅作参考基线")
    else:
        tips.append("合格天充足：建议 stratified_day 分层切分（按天打散），"
                    "使 train/val/test 各含开机日与停机日")
    if len(bad):
        # 不合格天的连片性：连续段 ≥3 天提示设备离线期
        d = pd.to_datetime(bad["date"])
        gaps = (d.diff().dt.days.fillna(1) == 1)
        max_run = (gaps.groupby((~gaps).cumsum()).cumsum().max() or 0) + 1
        head = "、".join(bad["date"].head(8)) + ("…" if len(bad) > 8 else "")
        tips.append(f"不合格天 {len(bad)} 天（{head}）：已由日级无效天机制剔除口径覆盖，"
                    "建议在 time_filters 中显式 exclude 其中的连续离线段")
        if max_run >= 3:
            tips.append(f"存在连续 ≥{int(max_run)} 天的不合格段：疑似设备离线/换表，"
                        "切分时避免跨该段（time 切分锚点不落在段内）")
    # —— 模型训练建议
    if ratio < 0.5:
        tips.append("合格率 <50%：优先修数不调参；模型仅跑基线（ridge）验证通路")
    elif ratio < 0.8:
        tips.append("合格率 50%~80%：建议树模型（random_forest/xgboost，对缺口鲁棒）；"
                    "深度序列模型窗口会跨缺口，暂不推荐")
    else:
        tips.append("合格率 ≥80%：可启用全模型对比（含深度序列模型）；"
                    "关注 val/test 指标差距判断过拟合")
    lo_branch = daily[daily["branch_score"] < daily["bus_score"] - 20]
    if len(lo_branch) > n * 0.2:
        tips.append("分路质量显著低于总线（>20 分差的天超 20%）：标签侧是短板，"
                    "训练前重点核查分路采集；评估结论对标签缺口敏感")
    return tips


def split_coverage_advice(detail: pd.DataFrame) -> list[str]:
    """基于双达标天明细（all_off/dataset）的切分覆盖性建议。"""
    tips: list[str] = []
    if detail is None or detail.empty:
        return tips
    s = qualified_days_summary(detail)
    tips.append(f"双达标天 {s['total_days']} 天中：全关天 {s['all_off_days']} 天、"
                f"训练集 {s['train_days']} / 验证集 {s['val_days']} / "
                f"测试集 {s['test_days']} 天"
                + (f"、推理集 {s['infer_days']} 天" if s['infer_days'] else "")
                + (f"、未使用 {s['unused_days']} 天" if s['unused_days'] else ""))
    # 全关天在各集的覆盖：训练集缺全关天 → 模型学不到停机模式（2842 教训）
    off = detail[detail["all_off"] == 1]
    if len(off):
        off_train = int(off["dataset"].str.contains("训练集").sum())
        off_test = int(off["dataset"].str.contains("测试集").sum())
        if s["train_days"] and off_train == 0:
            tips.append("训练集不含任何全关天：模型无法学习停机模式，"
                        "预计全关天将整段误报——建议调整切分锚点或对全关天过采样")
        elif s["train_days"]:
            tips.append(f"训练集含全关天 {off_train} 天"
                        f"（占训练天 {off_train / max(s['train_days'],1):.0%}）；"
                        "若 <15% 建议全关天样本加权（3~5 倍）强化停机模式学习")
        if s["test_days"] and off_test == 0:
            tips.append("测试集不含全关天：F1/SAE 结论未覆盖停机场景，"
                        "指标可能偏乐观")
    else:
        tips.append("双达标天中无全关天：无法评估停机辨识能力；"
                    "如业务存在停机场景，建议扩数据窗覆盖")
    if s["unused_days"] > s["total_days"] * 0.2:
        tips.append(f"未使用天占比 {s['unused_days']}/{s['total_days']}：质量合格但"
                    "未进入任何数据集（时间过滤排除或特征构建剔除），"
                    "如需更多样本可放宽 time_filters 的 include 范围")
    return tips


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


def write_quality_html(path: str | Path, reports: list[dict],
                       daily_quality: pd.DataFrame | None = None,
                       advice: list[str] | None = None,
                       qualified_detail: pd.DataFrame | None = None) -> Path:
    """data_quality_report.html（§4 输出物）：质量简表 + 双达标统计 +
    逐天质量表 + 双达标天清洗后统计（总/全关/训练/验证/测试天数）+
    双达标天每天明细（全关日/阈值/所属数据集）+ 训练建议。"""
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

    # —— 清洗后数据统计（双达标口径优先）：总/全关/训练/验证/测试天数 + 每天明细
    cleaned_html = ""
    if qualified_detail is not None and len(qualified_detail):
        s = qualified_days_summary(qualified_detail)
        detail_rows = "\n".join(
            f"<tr{' style=background:#eef' if r.all_off else ''}>"
            f"<td>{r.date}</td><td>{'是' if r.all_off else '否'}</td>"
            f"<td>{r.on_thr_w}</td><td>{r.dataset}</td></tr>"
            for r in qualified_detail.itertuples())
        cleaned_html = f"""
<h2>清洗后数据统计（总线与分路同时达标的天）</h2>
<table><tr><th>总天数</th><th>全关天数量</th><th>训练集天数</th>
<th>验证集天数</th><th>测试集天数</th><th>推理集天数</th><th>未使用天数</th></tr>
<tr><td>{s['total_days']}</td><td>{s['all_off_days']}</td><td>{s['train_days']}</td>
<td>{s['val_days']}</td><td>{s['test_days']}</td><td>{s['infer_days']}</td>
<td>{s['unused_days']}</td></tr>
</table>
<h2>双达标天每天数据详细情况</h2>
<table><tr><th>日期</th><th>是否为全关日</th><th>全关日阈值(W)</th><th>所属数据集</th></tr>
{detail_rows}
</table>"""
    else:  # 兼容旧口径：无双达标明细时按 cleaned_stats 渲染
        cleaned_rows, off_sections = [], []
        for r in reports:
            cs = r.get("cleaned_stats")
            if not cs:
                continue
            cleaned_rows.append(_stat_row(r["kind"], cs))
            off_sections.append(_list_section(r["kind"], cs))
            for split, ss in (r.get("split_stats") or {}).items():
                cleaned_rows.append(_stat_row(f"{r['kind']}·{split}", ss))
                off_sections.append(_list_section(f"{r['kind']}·{split}", ss))
        if cleaned_rows:
            cleaned_html = f"""
<h2>清洗后数据统计</h2>
<table><tr><th>数据集</th><th>总天数</th><th>实际天数</th><th>全天缺失天</th><th>全关天数量</th></tr>
{chr(10).join(cleaned_rows)}
</table>
{chr(10).join(off_sections)}"""

    # 逐天质量表 + 双达标统计 + 建议（daily_quality / advice 参数存在才输出）
    daily_html = ""
    if daily_quality is not None and len(daily_quality):
        d = daily_quality
        n_ok = int((d["qualified"] == 1).sum())
        day_rows = "\n".join(
            f"<tr{' style=background:#fdd' if r.qualified == 0 else ''}>"
            f"<td>{r.date}</td><td>{r.bus_score}</td><td>{r.branch_score}</td>"
            f"<td>{r.score_threshold}</td><td>{'合格' if r.qualified else '不合格'}</td></tr>"
            for r in d.itertuples())
        daily_html = f"""
<h2>总线与分路数据同时达标统计</h2>
<p>同时达标天数：<b>{n_ok}</b> / {len(d)} 天（得分阈值 {d['score_threshold'].iloc[0]}）</p>
<h2>每天数据质量情况</h2>
<table><tr><th>日期</th><th>总线质量得分</th><th>目标分路质量得分</th>
<th>得分阈值</th><th>当天是否合格</th></tr>
{day_rows}
</table>"""
    advice_html = ""
    if advice:
        advice_html = ("\n<h2>训练数据集划分与模型训练建议</h2>\n<ul>"
                       + "".join(f"<li>{a}</li>" for a in advice) + "</ul>")

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>数据质量报告</title>
<style>table{{border-collapse:collapse}}td,th{{border:1px solid #999;padding:4px 8px}}</style>
</head><body>
<h1>数据质量报告</h1>
<table><tr><th>数据集</th><th>行数</th><th>天数</th><th>缺失率</th>
<th>异常率</th><th>覆盖率</th><th>质量分</th></tr>
{rows}
</table>{daily_html}{cleaned_html}{advice_html}
</body></html>"""
    path.write_text(html, encoding="utf-8")
    log.info("质量报告: %s", path)
    return path
