"""质量报告的清洗后数据统计：总天数 / 全关天数量 / 全关天日期清单。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.data_io.validator import (cleaned_daily_stats, quality_report,
                                    write_quality_html)


def _frame(days: int = 4, on_days: list[int] | None = None,
           thr_val: float = 100.0) -> pd.DataFrame:
    """构造 15min 数据：on_days 中的天有开机（p1=thr_val），其余全 0。"""
    idx = pd.date_range("2026-01-01", periods=96 * days, freq="15min")
    p1 = np.zeros(len(idx))
    for d in (on_days or []):
        p1[96 * d + 40: 96 * d + 48] = thr_val  # 该日 10:00–12:00 开机
    return pd.DataFrame({"p1": p1, "pfa": np.full(len(idx), 0.9)}, index=idx)


def test_cleaned_daily_stats_counts_and_dates():
    df = _frame(days=4, on_days=[0, 2])  # 第 0/2 天开机，第 1/3 天全关
    st = cleaned_daily_stats(df, on_thr_w=50.0)
    assert st["total_days"] == 4
    assert st["all_off_days"] == 2
    assert st["all_off_dates"] == ["2026-01-02", "2026-01-04"]


def test_all_off_uses_threshold():
    """低于阈值的功率不算开机（与 on_thr_w 口径一致）。"""
    df = _frame(days=2, on_days=[0], thr_val=30.0)  # 峰值 30 < 阈值 50
    st = cleaned_daily_stats(df, on_thr_w=50.0)
    assert st["all_off_days"] == 2                  # 两天都判全关
    st2 = cleaned_daily_stats(df, on_thr_w=20.0)    # 阈值降到 20 → 第 0 天算开
    assert st2["all_off_days"] == 1


def test_pf_column_not_counted_as_power():
    """pf 列不是功率列：仅 pfa 有值时仍判全关。"""
    idx = pd.date_range("2026-01-01", periods=96, freq="15min")
    df = pd.DataFrame({"p1": np.zeros(96), "pfa": np.full(96, 0.95)}, index=idx)
    st = cleaned_daily_stats(df, on_thr_w=10.0)
    assert st["all_off_days"] == 1


def test_empty_and_no_power_columns():
    idx = pd.date_range("2026-01-01", periods=4, freq="15min")
    assert cleaned_daily_stats(pd.DataFrame(index=idx), 10.0)["total_days"] == 0
    df = pd.DataFrame({"ua": [220.0] * 4}, index=idx)
    st = cleaned_daily_stats(df, 10.0)
    assert st["total_days"] == 0 and st["actual_days"] == 0
    assert st["all_off_days"] == 0 and st["all_off_dates"] == []


def test_quality_report_embeds_cleaned_stats():
    df = _frame(days=3, on_days=[1])
    rep = quality_report(df, "branch", 96, on_thr_w=50.0)
    cs = rep["cleaned_stats"]
    assert cs["total_days"] == 3 and cs["all_off_days"] == 2
    # 不传 on_thr_w 保持旧行为（无 cleaned_stats 键，向后兼容）
    rep2 = quality_report(df, "branch", 96)
    assert "cleaned_stats" not in rep2


def test_quality_html_contains_cleaned_section(tmp_path):
    df = _frame(days=3, on_days=[0])
    rep = quality_report(df, "bus", 96, on_thr_w=50.0)
    p = write_quality_html(tmp_path / "q.html", [rep])
    html = p.read_text(encoding="utf-8")
    assert "清洗后数据统计" in html
    assert "全关天数量" in html
    assert "2026-01-02" in html and "2026-01-03" in html   # 全关天清单
    # 无 cleaned_stats 时不渲染该段
    p2 = write_quality_html(tmp_path / "q2.html", [quality_report(df, "bus", 96)])
    assert "清洗后数据统计" not in p2.read_text(encoding="utf-8")


def test_series_daily_stats():
    """单序列（切分目标功率）日级统计：口径与 cleaned_daily_stats 一致。"""
    from nilm.data_io.validator import series_daily_stats

    idx = pd.date_range("2026-01-01", periods=96 * 2, freq="15min")
    s = pd.Series(0.0, index=idx)
    s.iloc[40:48] = 100.0                       # 第 1 天开机，第 2 天全关
    st = series_daily_stats(s, on_thr_w=50.0)
    assert st["total_days"] == 2
    assert st["all_off_days"] == 1
    assert st["all_off_dates"] == ["2026-01-02"]
    # 整天 NaN → 记全天缺失天，不计入实际天数、更不算全关天
    s2 = s.copy(); s2.iloc[96:] = np.nan
    st2 = series_daily_stats(s2, on_thr_w=50.0)
    assert st2["total_days"] == 2 and st2["actual_days"] == 1
    assert st2["missing_days"] == 1 and st2["missing_dates"] == ["2026-01-02"]
    assert st2["all_off_days"] == 0


def test_quality_html_renders_split_stats(tmp_path):
    """split_stats 键存在时 HTML 渲染切分级行与清单段。"""
    df = _frame(days=3, on_days=[0])
    rep = quality_report(df, "branch", 96, on_thr_w=50.0)
    rep["split_stats"] = {
        "train": {"total_days": 2, "all_off_days": 1,
                  "all_off_dates": ["2026-01-02"]},
        "test": {"total_days": 1, "all_off_days": 0, "all_off_dates": []},
    }
    html = write_quality_html(tmp_path / "q.html", [rep]).read_text(encoding="utf-8")
    assert "branch·train" in html and "branch·test" in html
    assert "branch·train 全关天日期清单（1 天）" in html
    assert "（无）" in html                       # test 无全关天


def _frame_with_missing(days: int = 4, missing_days: list[int] | None = None,
                        partial: dict[int, int] | None = None) -> pd.DataFrame:
    """构造 15min 数据：missing_days 全天 NaN；partial={天序号: 有效点数}。"""
    idx = pd.date_range("2026-01-01", periods=96 * days, freq="15min")
    p1 = np.full(len(idx), 100.0)
    for d in (missing_days or []):
        p1[96 * d: 96 * (d + 1)] = np.nan
    for d, keep in (partial or {}).items():
        p1[96 * d + keep: 96 * (d + 1)] = np.nan
    return pd.DataFrame({"p1": p1}, index=idx)


def test_missing_days_not_counted_as_all_off():
    """全天数据缺失的天：计入 missing_days，不计入实际天/全关天。"""
    # 第 1 天全 NaN；第 2 天有数据但全 0（真全关）
    df = _frame_with_missing(days=3, missing_days=[1])
    df.iloc[96 * 2: 96 * 3, 0] = 0.0
    st = cleaned_daily_stats(df, on_thr_w=50.0)
    assert st["total_days"] == 3
    assert st["actual_days"] == 2                      # 实际天数不含全缺失天
    assert st["missing_days"] == 1
    assert st["missing_dates"] == ["2026-01-02"]
    assert st["all_off_days"] == 1                     # 只有真全关天
    assert st["all_off_dates"] == ["2026-01-03"]


def test_invalid_data_days_full_missing_and_threshold():
    """invalid_data_days：全天缺失必入选；缺失率阈值生效。"""
    from nilm.data_io.validator import invalid_data_days

    # 第 0 天全缺失；第 1 天仅 5 点有效（缺失率 94.8%）；第 2/3 天完整
    df = _frame_with_missing(days=4, missing_days=[0], partial={1: 5})
    bad_any = invalid_data_days(df, 96, max_daily_missing_rate=1.0)
    assert [d.strftime("%Y-%m-%d") for d in bad_any] == ["2026-01-01"]  # 仅全缺失
    bad_thr = invalid_data_days(df, 96, max_daily_missing_rate=0.9)
    assert [d.strftime("%Y-%m-%d") for d in bad_thr] == ["2026-01-01", "2026-01-02"]
    assert invalid_data_days(pd.DataFrame(), 96) == []


def test_quality_html_shows_actual_and_missing_days(tmp_path):
    """HTML 渲染实际天数/全天缺失天列与缺失日期清单。"""
    df = _frame_with_missing(days=3, missing_days=[1])
    rep = quality_report(df, "bus", 96, on_thr_w=50.0)
    html = write_quality_html(tmp_path / "q.html", [rep]).read_text(encoding="utf-8")
    assert "实际天数" in html and "全天缺失天" in html
    assert "bus 全天数据缺失日期清单（1 天）" in html
    assert "2026-01-02" in html


def test_daily_quality_table_and_both_qualified():
    """逐天质量表：得分/阈值/合格列；双达标口径 = 两侧同日均 ≥ 阈值。"""
    from nilm.data_io.validator import daily_quality_table

    idx = pd.date_range("2026-01-01", periods=96 * 3, freq="15min")
    bus = pd.DataFrame({"pa": np.full(len(idx), 100.0)}, index=idx)
    br = pd.DataFrame({"p1": np.full(len(idx), 50.0)}, index=idx)
    br.loc[br.index.normalize() == pd.Timestamp("2026-01-02"), "p1"] = np.nan  # 分路第2天全缺
    d = daily_quality_table(bus, br, 96, min_score=50.0)
    assert list(d.columns) == ["date", "bus_score", "bus_missing_rate",
                               "branch_score", "branch_missing_rate",
                               "score_threshold", "qualified"]
    assert len(d) == 3
    assert (d["score_threshold"] == 50.0).all()
    assert d.loc[d.date == "2026-01-01", "qualified"].iloc[0] == 1
    assert d.loc[d.date == "2026-01-02", "qualified"].iloc[0] == 0   # 分路0分→不合格
    assert d.loc[d.date == "2026-01-02", "branch_score"].iloc[0] == 0.0
    assert d.loc[d.date == "2026-01-02", "bus_score"].iloc[0] >= 50  # 总线仍高分
    assert int((d["qualified"] == 1).sum()) == 2                     # 双达标 2 天


def test_daily_quality_missing_side_days():
    """总线∪分路的并集口径：只在一侧出现的天，另一侧记 0 分不合格。"""
    from nilm.data_io.validator import daily_quality_table

    idx_b = pd.date_range("2026-01-01", periods=96, freq="15min")
    idx_r = pd.date_range("2026-01-02", periods=96, freq="15min")
    bus = pd.DataFrame({"pa": np.full(96, 100.0)}, index=idx_b)
    br = pd.DataFrame({"p1": np.full(96, 50.0)}, index=idx_r)
    d = daily_quality_table(bus, br, 96, min_score=50.0)
    assert len(d) == 2 and (d["qualified"] == 0).all()


def test_quality_advice_rules():
    """建议规则：合格天不足/较少/充足 与 合格率分层。"""
    from nilm.data_io.validator import quality_advice

    def mk(n_ok, n_bad):
        rows = []
        for i in range(n_ok):
            rows.append({"date": f"2026-01-{i+1:02d}", "bus_score": 99.0,
                         "bus_missing_rate": 0, "branch_score": 99.0,
                         "branch_missing_rate": 0, "score_threshold": 50.0,
                         "qualified": 1})
        for i in range(n_bad):
            rows.append({"date": f"2026-02-{i+1:02d}", "bus_score": 10.0,
                         "bus_missing_rate": 0.9, "branch_score": 10.0,
                         "branch_missing_rate": 0.9, "score_threshold": 50.0,
                         "qualified": 0})
        return pd.DataFrame(rows)

    t1 = "\n".join(quality_advice(mk(1, 5), min_days=3))
    assert "不建议训练" in t1
    t2 = "\n".join(quality_advice(mk(6, 1), min_days=3))
    assert "time 顺序切分" in t2
    t3 = "\n".join(quality_advice(mk(30, 2), min_days=3))
    assert "stratified_day" in t3 and "全模型对比" in t3
    t4 = "\n".join(quality_advice(mk(10, 12), min_days=3))
    assert "优先修数" in t4


def test_quality_html_daily_table_and_advice(tmp_path):
    """HTML 渲染逐天质量表、双达标统计与建议段。"""
    from nilm.data_io.validator import daily_quality_table, quality_advice

    idx = pd.date_range("2026-01-01", periods=96 * 2, freq="15min")
    bus = pd.DataFrame({"pa": np.full(len(idx), 100.0)}, index=idx)
    br = pd.DataFrame({"p1": np.full(len(idx), 50.0)}, index=idx)
    d = daily_quality_table(bus, br, 96, 50.0)
    adv = quality_advice(d, 1)
    rep = quality_report(bus, "bus", 96, on_thr_w=10.0)
    html = write_quality_html(tmp_path / "q.html", [rep],
                              daily_quality=d, advice=adv).read_text(encoding="utf-8")
    assert "总线与分路数据同时达标统计" in html
    assert "每天数据质量情况" in html
    assert "总线质量得分" in html and "目标分路质量得分" in html
    assert "得分阈值" in html and "当天是否合格" in html
    assert "训练数据集划分与模型训练建议" in html
    # 不传 daily/advice 时不渲染（向后兼容）
    html2 = write_quality_html(tmp_path / "q2.html", [rep]).read_text(encoding="utf-8")
    assert "每天数据质量情况" not in html2
