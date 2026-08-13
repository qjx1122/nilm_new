"""data_io.merge：两级合并（先内源后跨源）、重叠告警跳过、结构复刻（需求文档 §3–§6）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from nilm.data_io.merge import BusFile, discover_source, ranges_overlap, run_merge

DEV, USR, UK = "8001", "9001", "8001_9001"


def _write_bus_csv(d: Path, ch: int, s: str, e: str, rows: list[str],
                   suffix: str = "", device: str = DEV, user: str = USR) -> Path:
    """写一个 RE_BUS 命名的总线 CSV（event_time 时间列）。"""
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"e241_{device}_{user}-Ch{ch}-{s}-{e}{suffix}.csv"
    p.write_text("event_time,val\n" + "\n".join(f"{t},{i}" for i, t in enumerate(rows)) + "\n",
                 encoding="utf-8")
    return p


def _day_rows(start: str, n: int) -> list[str]:
    return [f"{start} {h:02d}:00:00" for h in range(n)]


def _setup_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    """srcA: userX 两组(Ch1 可合并/Ch2 单文件) + userY 重叠组
    srcB: userX Ch1（与 srcA 无重叠，可跨源合并）
    srcC: userX Ch1（与 srcA 跨源重叠 → 告警跳过）"""
    srcA, srcB, srcC = tmp_path / "srcA", tmp_path / "srcB", tmp_path / "srcC"
    # srcA userX Ch1：两段无重叠 260101-260110 / 260111-260120
    _write_bus_csv(srcA / UK, 1, "260101", "260110", _day_rows("2026-01-01", 5))
    _write_bus_csv(srcA / UK, 1, "260111", "260120", _day_rows("2026-01-11", 5))
    # srcA userX Ch2：单文件（无需合并，保留）
    _write_bus_csv(srcA / UK, 2, "260101", "260105", _day_rows("2026-01-01", 3))
    # srcA 分路文件（非合并目标，应被跳过）
    (srcA / UK / f"{USR}-260101-260120.csv").write_text("time,p1\n2026-01-01 00:00:00,1\n",
                                                         encoding="utf-8")
    # srcA userY：同通道时间重叠 → 整组告警跳过（文件名身份与目录一致）
    _write_bus_csv(srcA / "8002_9002", 1, "260101", "260110", _day_rows("2026-01-01", 4),
                   device="8002", user="9002")
    _write_bus_csv(srcA / "8002_9002", 1, "260105", "260115", _day_rows("2026-01-05", 4),
                   device="8002", user="9002")
    # srcB userX Ch1：260201-260210，与 srcA 合并结果无重叠
    _write_bus_csv(srcB / UK, 1, "260201", "260210", _day_rows("2026-02-01", 5))
    # srcC userX Ch1：260115-260125，与 srcA 的 260101-260120 重叠
    _write_bus_csv(srcC / UK, 1, "260115", "260125", _day_rows("2026-01-15", 5))
    return srcA, srcB, srcC


def test_ranges_overlap():
    from datetime import date
    d = lambda s: date(*map(int, s.split("-")))
    assert ranges_overlap(d("2026-01-01"), d("2026-01-10"), d("2026-01-10"), d("2026-01-20"))
    assert not ranges_overlap(d("2026-01-01"), d("2026-01-10"), d("2026-01-11"), d("2026-01-20"))


def test_discover_source(tmp_path):
    srcA, _, _ = _setup_sources(tmp_path)
    found, skipped = discover_source(srcA)
    assert len(found) == 5                     # 5 个 RE_BUS 文件
    assert len(skipped) == 1                   # 分路 CSV 不参与合并
    assert all(isinstance(f, BusFile) for f in found)
    assert {f.group_key for f in found} == {(UK, 1), (UK, 2), (("8002_9002"), 1)}


def test_two_level_merge_end_to_end(tmp_path):
    srcA, srcB, srcC = _setup_sources(tmp_path)
    out = tmp_path / "merged"
    report = run_merge([srcA, srcB, srcC], output_root=out)

    # —— 阶段一：内源合并，复刻「数据源/用户目录」层级 ——
    merged_ch1 = out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260120.csv"
    assert merged_ch1.exists()                       # 无重叠 → 合并，起止取最早/最晚
    df = pd.read_csv(merged_ch1)
    assert len(df) == 10                             # 5 + 5 行
    assert df["event_time"].is_monotonic_increasing

    kept_ch2 = out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch2-260101-260105.csv"
    assert kept_ch2.exists()                         # 单文件组直接保留原名

    assert not (out / "srcA" / "8002_9002").exists()  # 重叠组整组跳过，不生成合并文件

    # —— 阶段二：跨源合并（srcA+srcB 无重叠；srcC 重叠告警跳过）——
    assert report["warnings"] >= 2                   # userY 内源重叠 + userX 跨源重叠
    cross_groups = report["phase2_cross_source"][UK]
    statuses = {(g["ch"], g["status"]) for g in cross_groups}
    assert (1, "SKIPPED_OVERLAP") in statuses        # srcC 触发跨源重叠 → 跳过
    assert (2, "OK") in statuses                     # Ch2 仅 srcA 有，无需跨源
    assert not (out / "cross_source" / UK).joinpath(
        f"e241_{DEV}_{USR}-Ch1-260101-260210.csv").exists()  # 重叠组不生成跨源文件

    # —— 告警日志：精准记录用户目录/文件名/冲突区间（§6.2）——
    wlog = (out / "logs" / "merge_warnings.log").read_text(encoding="utf-8")
    assert "8002_9002" in wlog and "2026-01-05" in wlog     # 内源重叠记录
    assert "srcC" in wlog or "跨源" in wlog                 # 跨源重叠记录
    assert (out / "logs" / "merge_run.log").exists()
    assert (out / "logs" / "merge_report.json").exists()


def test_cross_source_merge_when_no_overlap(tmp_path):
    """去掉 srcC 后，跨源合并应成功并更新起止时间（§4.2）。"""
    srcA, srcB, _ = _setup_sources(tmp_path)
    out = tmp_path / "merged"
    report = run_merge([srcA, srcB], output_root=out)
    cross_out = out / "cross_source" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260210.csv"
    assert cross_out.exists()                        # 260101~260120 + 260201~260210
    df = pd.read_csv(cross_out)
    assert len(df) == 15                             # 10 + 5
    g = [g for g in report["phase2_cross_source"][UK] if g["ch"] == 1][0]
    assert g["status"] == "OK" and g["action"] == "merged"


def test_original_sources_readonly(tmp_path):
    """原始数据源只读：合并后源目录文件清单不变（§5/指南 §13）。"""
    srcA, srcB, _ = _setup_sources(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in srcA.rglob("*") if p.is_file())
    run_merge([srcA, srcB], output_root=tmp_path / "merged")
    after = sorted(str(p.relative_to(tmp_path)) for p in srcA.rglob("*") if p.is_file())
    assert before == after


def test_no_keep_original_option(tmp_path):
    srcA, _, _ = _setup_sources(tmp_path)
    out = tmp_path / "merged"
    run_merge([srcA], output_root=out, keep_original=False)
    # 单文件组（Ch2）不保留；多文件合并结果仍输出
    assert not (out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch2-260101-260105.csv").exists()
    assert (out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260120.csv").exists()
